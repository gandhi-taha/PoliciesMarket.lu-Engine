"""Selector-capture (`--learn`) for the assisted collector.

How it works
------------
1. Open Chromium at the insurer's quote page.
2. Inject a tiny JS overlay that listens for click+focus events on
   every element, computes a stable CSS selector for the element the
   human just interacted with, and parks it on `window.__pmsLast`.
3. Drive a labeling loop in the terminal:
     - "Fill/click a form field, then come back and label what it was."
     - Python reads `window.__pmsLast` each time the human presses Enter.
     - The human types the logical field name (`driver_age`,
       `vehicle_value_eur`, ...).
     - We save (logical_name → selector) into a dict.
4. Repeat for the result page: human clicks each price/deductible cell,
   labels it, we record selectors keyed by `<level>_premium` /
   `<level>_deductible`.
5. Persist the result as JSON under
   `app/workers/scrapers/selectors/<provider_code>.json`.

The default `BaseQuoteScraper.prefill_form()` / `.extract_result()`
read that JSON at runtime so subsequent Tier 2 runs auto-fill the form
and auto-extract the prices without any new code per insurer.

What the JS produces
--------------------
A robust selector preferring (in order):
  1. `#id` if the element has one and the id is stable-looking
     (alphanumeric, no UUID-shaped substrings)
  2. `tag[name="x"]` for inputs/selects with a `name` attribute
  3. `tag[data-testid="x"]` (or any data-* attribute that looks stable)
  4. CSS path with stable class fragments (no auto-generated suffixes)
  5. Fallback: nth-child path from the closest landmark ancestor
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import Page


log = logging.getLogger("selector_capture")


SELECTOR_DIR = Path(__file__).resolve().parent / "selectors"

# Logical labels we accept. Keep aligned with CollectionProfile attrs
# the prefill loop fills from, and with extract levels.
FORM_FIELD_LABELS = (
    "driver_age",
    "vehicle_value_eur",
    "annual_mileage_km",
    "vehicle_power_kw",
    "claims_last_5y",
    "usage_type",
    "postcode",
    "date_of_birth",
    "vehicle_make",
    "vehicle_model",
    "license_year",
    "other",
)

RESULT_LABELS = (
    "basic_premium",
    "basic_deductible",
    "standard_premium",
    "standard_deductible",
    "premium_premium",
    "premium_deductible",
)


# Injected once via `page.add_init_script` so the listener survives
# navigations within the session.
_CAPTURE_JS = r"""
(() => {
  if (window.__pmsCaptureInstalled) return;
  window.__pmsCaptureInstalled = true;
  window.__pmsLast = null;

  function stableId(s) {
    if (!s) return false;
    if (s.length > 32) return false;
    // Reject auto-generated id-shaped strings: uuids, react-id-12345, etc.
    if (/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i.test(s)) return false;
    if (/^[a-z]+-?[0-9]{6,}$/i.test(s)) return false;
    return true;
  }

  function stableAttr(name) {
    if (!name) return false;
    if (name.length > 32) return false;
    if (/[0-9a-f]{8,}/.test(name)) return false;
    return true;
  }

  function classFragments(el) {
    if (!el.className || typeof el.className !== 'string') return [];
    return el.className.split(/\s+/).filter(c => {
      if (!c) return false;
      if (c.length > 24) return false;
      if (/_[a-zA-Z0-9]{5,}$/.test(c)) return false; // CSS-module hash
      if (/[0-9a-f]{6,}$/.test(c)) return false;
      return true;
    });
  }

  function selectorFor(el) {
    if (!el || el.nodeType !== 1) return null;
    const tag = el.tagName.toLowerCase();
    if (el.id && stableId(el.id)) return `#${CSS.escape(el.id)}`;
    if ((tag === 'input' || tag === 'select' || tag === 'textarea') && el.name && stableAttr(el.name)) {
      return `${tag}[name="${el.name}"]`;
    }
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'aria-label']) {
      const v = el.getAttribute(attr);
      if (v && stableAttr(v)) return `${tag}[${attr}="${CSS.escape(v)}"]`;
    }
    const frags = classFragments(el);
    if (frags.length) {
      return `${tag}.${frags.map(c => CSS.escape(c)).join('.')}`;
    }
    // Fallback: nth-of-type path up to the nearest landmark.
    let cur = el;
    const path = [];
    while (cur && cur.nodeType === 1 && cur !== document.body) {
      const idx = Array.from(cur.parentNode.children).filter(c => c.tagName === cur.tagName).indexOf(cur) + 1;
      path.unshift(`${cur.tagName.toLowerCase()}:nth-of-type(${idx})`);
      if (cur.id && stableId(cur.id)) {
        path.unshift(`#${CSS.escape(cur.id)}`);
        break;
      }
      cur = cur.parentNode;
    }
    return path.join(' > ');
  }

  function flash(el) {
    const orig = el.style.outline;
    el.style.outline = '3px solid #38bdf8';
    setTimeout(() => { el.style.outline = orig; }, 600);
  }

  function record(el) {
    const sel = selectorFor(el);
    if (!sel) return;
    let text = null;
    try { text = el.innerText ? el.innerText.slice(0, 100) : null; } catch (_) {}
    window.__pmsLast = {
      selector: sel,
      tag: el.tagName.toLowerCase(),
      type: el.type || null,
      text: text,
    };
    flash(el);
  }

  document.addEventListener('click', e => record(e.target), true);
  document.addEventListener('focusin', e => record(e.target), true);

  // Banner so the human knows the listener is alive.
  const banner = document.createElement('div');
  banner.textContent = '◉ PoliciesMarket selector-capture active — click any element to record';
  Object.assign(banner.style, {
    position: 'fixed', bottom: '12px', right: '12px', zIndex: 2147483647,
    background: '#0f172a', color: '#e2e8f0', padding: '8px 12px',
    fontFamily: 'system-ui, sans-serif', fontSize: '12px', borderRadius: '6px',
    boxShadow: '0 4px 12px rgba(0,0,0,0.3)', pointerEvents: 'none',
  });
  document.documentElement.appendChild(banner);
})();
"""


def install_capture(page: "Page") -> None:
    """Inject the capture overlay. Survives within-page navigations."""

    page.add_init_script(_CAPTURE_JS)
    # Also run it on the current page (add_init_script only takes effect
    # on the next navigation otherwise).
    try:
        page.evaluate(_CAPTURE_JS)
    except Exception:  # noqa: BLE001
        # Some pages reject script eval immediately after load; that's
        # fine, add_init_script handles the next navigation.
        log.warning("Initial script eval rejected; will re-attach on next nav.")


def run_capture(page: "Page", provider_code: str) -> Path:
    """Drive the labeling loop and persist a JSON file. Returns the path."""

    install_capture(page)

    print()
    print(f"== LEARN MODE — {provider_code} ==")
    print()
    print("Step 1: Form fields")
    print("-------------------")
    print("On the browser, fill in each form field (or click a dropdown).")
    print("After each field, come back here and press Enter — we'll ask")
    print("what that field maps to. Type 'done' to move to step 2.")
    print()
    form_fields = _label_loop(page, FORM_FIELD_LABELS, step="form field")

    print()
    print("Step 2: Submit a sample quote")
    print("-----------------------------")
    print("Submit the form to get to the result page. Solve any CAPTCHA.")
    input("Press Enter when the result page is visible: ")

    # Re-inject — most insurers navigate to a new URL on submit.
    install_capture(page)

    print()
    print("Step 3: Result-page prices")
    print("--------------------------")
    print("Click each price or deductible element on the result page.")
    print("After each click, come back here and label it. Type 'done' when finished.")
    print()
    result_elements = _label_loop(page, RESULT_LABELS, step="result element")

    payload: dict[str, Any] = {
        "provider_code": provider_code,
        "captured_at": date.today().isoformat(),
        "form_fields": form_fields,
        "result_elements": result_elements,
    }
    out = SELECTOR_DIR / f"{provider_code}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"✓ Saved {len(form_fields)} form field(s) + {len(result_elements)} result element(s)")
    print(f"  → {out}")
    return out


def _label_loop(
    page: "Page",
    valid_labels: tuple[str, ...],
    *,
    step: str,
) -> dict[str, str | dict[str, Any]]:
    captured: dict[str, str | dict[str, Any]] = {}
    labels_hint = " / ".join(valid_labels)

    while True:
        answer = input(f"  Click a {step}, then Enter (or 'done'): ").strip().lower()
        if answer in ("done", "d", "q"):
            return captured

        try:
            last = page.evaluate("window.__pmsLast")
        except Exception as exc:  # noqa: BLE001
            print(f"  !! Could not read last-clicked element: {exc!r}")
            continue
        if not last or not last.get("selector"):
            print("  !! Nothing recorded yet — click an element first.")
            continue

        sel = last["selector"]
        preview = (last.get("text") or "").replace("\n", " / ")[:60]
        print(f"  ↳ captured: {sel}   [{last.get('tag')}{' / ' + last.get('type', '') if last.get('type') else ''}]   {preview}")

        label = input(f"  Label? ({labels_hint}): ").strip()
        if not label:
            print("  -- skipped")
            continue
        if label not in valid_labels:
            print(f"  -- '{label}' is not a known label, saving as-is")

        # If the human labels the same field twice (overrode a misclick),
        # the later one wins. Captured value can optionally include a
        # value_map for selects — left empty here, edit the JSON by hand
        # if the insurer's option values don't match the profile fields.
        captured[label] = sel
    # unreachable

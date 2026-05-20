"""One-command data bootstrap with progress display and resume.

Sequences the full Tier 2 quote-collection plan into a single CLI:

  1. Install Playwright + Chromium if missing.
  2. For each enabled provider in providers.yaml:
       a. If no captured selectors yet → run `--learn` (interactive).
       b. Run a normal assisted collection round.
  3. Hit `/v1/recommend` and report offer counts.

What's new vs the first version
-------------------------------
- Progress display: a header per insurer ("[3/4] LALUX") and per-step
  progress within the run.
- Persistent state: `~/.policiesmarket/bootstrap_state.json` records
  which (provider, step) pairs are done, so re-running after a break
  picks up where you left off.
- Roll-forward resume: if the last run finished `lalux` but crashed
  mid-`baloise`, the next run skips `lalux`, finishes `baloise`, then
  continues to `axa`.

A human still has to sit at the browser through Tier 2. This script
removes the chore of remembering command sequences, the chore of
knowing what's next, and the chore of remembering where you stopped.

Flags
-----
  --skip-install        Don't install Playwright (assume already installed).
  --providers <list>    Comma-separated codes (default: all enabled).
  --engine-url <url>    Engine URL for verification (default localhost:8000).
  --no-verify           Skip the /v1/recommend smoke call at the end.
  --reset               Clear bootstrap state and start fresh.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from app.providers.loader import load_providers
from app.workers.scrapers.selector_capture import SELECTOR_DIR


STATE_PATH = Path.home() / ".policiesmarket" / "bootstrap_state.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--providers", help="Comma-separated codes (default: all enabled).")
    parser.add_argument("--engine-url", default="http://localhost:8000")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear bootstrap_state.json before starting.",
    )
    args = parser.parse_args(argv)

    if args.reset and STATE_PATH.exists():
        STATE_PATH.unlink()
        print("Bootstrap state cleared.")

    state = _load_state()

    _banner("Step 0/3 — Prerequisites")
    if not args.skip_install:
        _ensure_playwright()
    else:
        print("  (skipping install per --skip-install)")

    codes = _resolve_codes(args.providers)
    if not codes:
        print("!! No enabled providers in providers.yaml. Aborting.")
        return 2

    total_providers = len(codes)
    _print_progress_summary(codes, state)

    # ---- learn loop --------------------------------------------------
    _banner(f"Step 1/3 — Selector capture (one-time per insurer)")
    for idx, code in enumerate(codes, start=1):
        if _is_done(state, code, "learn"):
            print(f"  [{idx}/{total_providers}] {code} — ✓ selectors already captured (skipping)")
            continue
        print(f"  [{idx}/{total_providers}] {code} — running --learn")
        rc = _run_learn(code)
        if rc != 0:
            print(f"  !! --learn for {code} exited {rc}. Resume with the same command.")
            return rc
        _mark_done(state, code, "learn")

    # ---- collection loop --------------------------------------------
    _banner("Step 2/3 — Assisted collection")
    start_wall = time.time()
    for idx, code in enumerate(codes, start=1):
        if _is_done_today(state, code, "collect"):
            print(f"  [{idx}/{total_providers}] {code} — ✓ collected today (skipping)")
            continue

        elapsed = time.time() - start_wall
        eta = _eta_for_remaining(state, codes[idx - 1 :])
        eta_str = _format_duration(eta) if eta else "?"
        print()
        print(f"  ▶ [{idx}/{total_providers}] {code} — starting (elapsed {_format_duration(elapsed)}, est remaining {eta_str})")

        t0 = time.time()
        rc = _run_collection(code, args.engine_url)
        duration = time.time() - t0

        if rc != 0:
            print(f"  !! Collection for {code} exited {rc}. Re-run to resume from here.")
            return rc

        _mark_done(state, code, "collect", duration_seconds=duration)
        _print_provider_done(idx, total_providers, code, duration)

    # ---- verification -----------------------------------------------
    if args.no_verify:
        print("\n✓ Bootstrap complete (verification skipped per --no-verify).")
        return 0

    _banner("Step 3/3 — Verify engine returns offers")
    return _verify(args.engine_url)


# ---- step implementations -------------------------------------------


def _ensure_playwright() -> None:
    if importlib.util.find_spec("playwright") is None:
        print("  Installing Playwright (~30s, one-time)...")
        _run([sys.executable, "-m", "pip", "install", "playwright>=1.45.0"])
    print("  Ensuring Chromium binary is installed (idempotent)...")
    _run([sys.executable, "-m", "playwright", "install", "chromium"])


def _resolve_codes(providers_flag: str | None) -> list[str]:
    if providers_flag:
        return [c.strip() for c in providers_flag.split(",") if c.strip()]
    return [entry.code for entry in load_providers() if entry.enabled]


def _run_learn(code: str) -> int:
    selector_file = SELECTOR_DIR / f"{code}.json"
    if selector_file.exists():
        return 0
    return _run_check(
        [
            sys.executable,
            "-m",
            "app.workers.assisted_collector",
            "--provider",
            code,
            "--learn",
        ]
    )


def _run_collection(code: str, engine_url: str) -> int:
    return _run_check(
        [
            sys.executable,
            "-m",
            "app.workers.assisted_collector",
            "--provider",
            code,
            "--engine-url",
            engine_url,
        ]
    )


def _verify(engine_url: str) -> int:
    payload = {
        "country": "LU",
        "driver_age": 30,
        "years_licensed": 12,
        "vehicle_value_eur": 22000,
        "vehicle_power_kw": 95,
        "vehicle_age_years": 4,
        "annual_mileage_km": 12000,
        "claims_last_5y": 0,
        "usage_type": "private",
        "coverage_preference": "standard",
    }
    url = engine_url.rstrip("/") + "/v1/recommend"
    print(f">> POST {url}")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
            break
        except urllib.error.URLError as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    else:
        print(f"!! Could not reach engine: {last_error}")
        print("   Is it running? `docker compose up` or `uvicorn app.main:app --reload`.")
        return 1

    offers = body.get("all_matching_offers", [])
    by_provider: dict[str, int] = {}
    for offer in offers:
        by_provider[offer["provider_code"]] = by_provider.get(offer["provider_code"], 0) + 1

    print(f">> Got {len(offers)} offer(s) from {len(by_provider)} provider(s).")
    for code, n in sorted(by_provider.items()):
        print(f"   {code}: {n}")
    if not offers:
        print("!! No offers — did the upload step write rows?")
        return 1
    print()
    print("✓ Bootstrap complete. Engine is serving real quotes.")
    return 0


# ---- state persistence ----------------------------------------------


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"providers": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"providers": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _is_done(state: dict[str, Any], code: str, step: str) -> bool:
    return bool(state["providers"].get(code, {}).get(step))


def _is_done_today(state: dict[str, Any], code: str, step: str) -> bool:
    """For the collection step, "done" means done within the last 12 hours.

    This lets `--reset`-less resumes work after a lunch break or a
    sleep + tomorrow restart. After 12 hours we assume the data is
    stale enough that a fresh round is what the operator wants.
    """

    entry = state["providers"].get(code, {})
    ts = entry.get(f"{step}_at")
    if not ts:
        return False
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return (datetime.now() - when).total_seconds() < 12 * 3600


def _mark_done(
    state: dict[str, Any], code: str, step: str, *, duration_seconds: float | None = None
) -> None:
    pp = state["providers"].setdefault(code, {})
    pp[step] = True
    pp[f"{step}_at"] = datetime.now().isoformat(timespec="seconds")
    if duration_seconds is not None:
        pp[f"{step}_duration_seconds"] = round(duration_seconds, 1)
    _save_state(state)


def _eta_for_remaining(state: dict[str, Any], remaining_codes: list[str]) -> float | None:
    """Estimate remaining seconds from prior per-provider collection durations.

    Uses the mean of previously-recorded durations as the expected
    time per remaining provider. Returns None until at least one
    provider has been collected (no data to estimate from).
    """

    durations = [
        pp.get("collect_duration_seconds")
        for pp in state["providers"].values()
        if pp.get("collect_duration_seconds")
    ]
    if not durations:
        return None
    mean = sum(durations) / len(durations)
    return mean * len(remaining_codes)


# ---- display --------------------------------------------------------


def _banner(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _print_progress_summary(codes: list[str], state: dict[str, Any]) -> None:
    total = len(codes)
    learned = sum(1 for c in codes if _is_done(state, c, "learn"))
    collected = sum(1 for c in codes if _is_done_today(state, c, "collect"))
    bar_learn = _progress_bar(learned, total)
    bar_coll = _progress_bar(collected, total)
    print()
    print(f"  Selectors captured  {bar_learn}  {learned}/{total}")
    print(f"  Quotes collected    {bar_coll}  {collected}/{total}   (within last 12 h)")


def _print_provider_done(
    idx: int, total: int, code: str, duration_seconds: float
) -> None:
    print(
        f"  ✓ [{idx}/{total}] {code} done in {_format_duration(duration_seconds)} "
        f"({_progress_bar(idx, total)})"
    )


def _progress_bar(done: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[" + "─" * width + "]"
    filled = int(round(width * done / total))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


# ---- subprocess wrappers --------------------------------------------


def _run(cmd: list[str]) -> None:
    print(f"   $ {_shorten(cmd)}")
    subprocess.check_call(cmd)


def _run_check(cmd: list[str]) -> int:
    print(f"   $ {_shorten(cmd)}")
    return subprocess.call(cmd)


def _shorten(cmd: list[str]) -> str:
    pretty = list(cmd)
    if pretty and (pretty[0].endswith("python") or pretty[0].endswith("python.exe")):
        pretty[0] = "python"
    return " ".join(pretty)


if __name__ == "__main__":
    sys.exit(main())

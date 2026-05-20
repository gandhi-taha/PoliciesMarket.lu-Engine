"""Generate `app/workers/profile_grid.yaml` with a tuned distribution.

The grid is the matrix of (driver × car × usage) profiles ops quotes
for, per insurer, per round. Re-running this script overwrites the
yaml — adjust `TEMPLATES` below to change which bands you care about
and how heavily each is sampled.

Usage:
    python scripts/generate_profile_grid.py            # default 100 profiles
    python scripts/generate_profile_grid.py --count 30 # smaller grid

Bands are kept aligned with the defaults in
`app/services/provider_config.py:default_payload()`. If you change band
labels there, change them here too — otherwise the assisted collector
will fill forms with values that the engine then can't band-match
against.
"""

from __future__ import annotations

import argparse
from pathlib import Path

# (age_band, vehicle_value_band, mileage_band, claims_band, power_band, usage_type, weight)
# Weights are sampling counts — a row with weight=3 contributes 3 profiles
# to the output. Tune to match the LU driver-population shape.
TEMPLATES: list[tuple[str, str, str, str, str, str, int]] = [
    # ---- Young drivers (18-24): high-premium segment ----
    ("18-24", "0-15000",      "8001-15000",  "0",  "0-80",    "private", 3),
    ("18-24", "0-15000",      "8001-15000",  "0",  "0-80",    "commute", 3),
    ("18-24", "0-15000",      "15001-25000", "0",  "0-80",    "commute", 2),
    ("18-24", "0-15000",      "8001-15000",  "1",  "0-80",    "private", 2),
    ("18-24", "15001-30000",  "8001-15000",  "0",  "81-110",  "private", 2),
    ("18-24", "15001-30000",  "8001-15000",  "0",  "81-110",  "commute", 2),
    ("18-24", "0-15000",      "0-8000",      "0",  "0-80",    "private", 1),
    ("18-24", "0-15000",      "25001+",      "0",  "0-80",    "commute", 1),

    # ---- 25-34: biggest customer segment ----
    ("25-34", "0-15000",      "0-8000",      "0",  "0-80",    "private", 1),
    ("25-34", "0-15000",      "8001-15000",  "0",  "0-80",    "private", 2),
    ("25-34", "0-15000",      "8001-15000",  "0",  "0-80",    "commute", 2),
    ("25-34", "15001-30000",  "0-8000",      "0",  "81-110",  "private", 2),
    ("25-34", "15001-30000",  "8001-15000",  "0",  "81-110",  "private", 3),
    ("25-34", "15001-30000",  "8001-15000",  "0",  "81-110",  "commute", 3),
    ("25-34", "15001-30000",  "15001-25000", "0",  "81-110",  "commute", 2),
    ("25-34", "15001-30000",  "15001-25000", "1",  "81-110",  "private", 2),
    ("25-34", "15001-30000",  "15001-25000", "1",  "81-110",  "commute", 2),
    ("25-34", "30001-50000",  "8001-15000",  "0",  "111-150", "commute", 2),
    ("25-34", "30001-50000",  "15001-25000", "0",  "111-150", "commute", 1),
    ("25-34", "30001-50000",  "8001-15000",  "0",  "81-110",  "private", 1),
    ("25-34", "15001-30000",  "8001-15000",  "0",  "81-110",  "business",1),
    ("25-34", "0-15000",      "25001+",      "0",  "0-80",    "commute", 1),
    ("25-34", "15001-30000",  "8001-15000",  "2+", "81-110",  "commute", 1),

    # ---- 35-49: highest absolute premium spend ----
    ("35-49", "0-15000",      "8001-15000",  "0",  "0-80",    "private", 1),
    ("35-49", "15001-30000",  "0-8000",      "0",  "81-110",  "private", 2),
    ("35-49", "15001-30000",  "8001-15000",  "0",  "81-110",  "private", 2),
    ("35-49", "15001-30000",  "8001-15000",  "0",  "81-110",  "commute", 2),
    ("35-49", "15001-30000",  "15001-25000", "0",  "81-110",  "commute", 2),
    ("35-49", "30001-50000",  "0-8000",      "0",  "111-150", "private", 1),
    ("35-49", "30001-50000",  "8001-15000",  "0",  "111-150", "private", 2),
    ("35-49", "30001-50000",  "8001-15000",  "0",  "111-150", "commute", 3),
    ("35-49", "30001-50000",  "15001-25000", "0",  "111-150", "commute", 3),
    ("35-49", "30001-50000",  "15001-25000", "0",  "111-150", "business",2),
    ("35-49", "30001-50000",  "15001-25000", "1",  "111-150", "commute", 1),
    ("35-49", "30001-50000",  "8001-15000",  "1",  "111-150", "private", 1),
    ("35-49", "50001+",       "8001-15000",  "0",  "111-150", "private", 1),
    ("35-49", "50001+",       "15001-25000", "0",  "151+",    "commute", 1),
    ("35-49", "50001+",       "8001-15000",  "0",  "151+",    "private", 1),
    ("35-49", "30001-50000",  "25001+",      "0",  "111-150", "commute", 1),

    # ---- 50-64: high-value, low-risk segment ----
    ("50-64", "15001-30000",  "0-8000",      "0",  "81-110",  "private", 2),
    ("50-64", "15001-30000",  "8001-15000",  "0",  "81-110",  "private", 2),
    ("50-64", "30001-50000",  "0-8000",      "0",  "111-150", "private", 2),
    ("50-64", "30001-50000",  "8001-15000",  "0",  "111-150", "private", 2),
    ("50-64", "30001-50000",  "8001-15000",  "0",  "111-150", "commute", 2),
    ("50-64", "30001-50000",  "15001-25000", "0",  "111-150", "commute", 1),
    ("50-64", "30001-50000",  "8001-15000",  "0",  "111-150", "business",1),
    ("50-64", "50001+",       "0-8000",      "0",  "111-150", "private", 1),
    ("50-64", "50001+",       "8001-15000",  "0",  "151+",    "private", 1),
    ("50-64", "15001-30000",  "0-8000",      "1",  "81-110",  "private", 1),

    # ---- 65-75: retiree segment ----
    ("65-75", "0-15000",      "0-8000",      "0",  "0-80",    "private", 1),
    ("65-75", "15001-30000",  "0-8000",      "0",  "81-110",  "private", 3),
    ("65-75", "15001-30000",  "8001-15000",  "0",  "81-110",  "private", 2),
    ("65-75", "30001-50000",  "0-8000",      "0",  "111-150", "private", 2),
    ("65-75", "30001-50000",  "0-8000",      "0",  "81-110",  "private", 1),

    # ---- 76+: niche but real ----
    ("76+",   "0-15000",      "0-8000",      "0",  "0-80",    "private", 1),
    ("76+",   "15001-30000",  "0-8000",      "0",  "81-110",  "private", 1),
]

# Numeric values for the form-fill (midpoint per band; deterministic so
# the same profile always submits the same numbers).
BAND_MIDPOINT = {
    "age_band": {
        "18-24": 22, "25-34": 30, "35-49": 42, "50-64": 57, "65-75": 70, "76+": 80,
    },
    "vehicle_value_eur": {
        "0-15000": 10000, "15001-30000": 22000, "30001-50000": 40000, "50001+": 65000,
    },
    "annual_mileage_km": {
        "0-8000": 6000, "8001-15000": 12000, "15001-25000": 20000, "25001+": 32000,
    },
    "claims_last_5y": {"0": 0, "1": 1, "2+": 2},
    "vehicle_power_kw": {
        "0-80": 70, "81-110": 95, "111-150": 125, "151+": 170,
    },
}


def expand(templates: list[tuple], target_count: int) -> list[dict]:
    """Expand weight-based templates into a list of `target_count` profile dicts.

    If the templates' total weight is less than target_count we round-robin
    extras across the heaviest weights; if more, we keep the first
    target_count after weight expansion.
    """

    profiles: list[dict] = []
    for age, val, mil, claim, power, usage, weight in templates:
        for _ in range(weight):
            profiles.append(
                {
                    "age_band": age,
                    "vehicle_value_band": val,
                    "mileage_band": mil,
                    "claims_band": claim,
                    "power_band": power,
                    "usage_type": usage,
                    "driver_age": BAND_MIDPOINT["age_band"][age],
                    "vehicle_value_eur": BAND_MIDPOINT["vehicle_value_eur"][val],
                    "annual_mileage_km": BAND_MIDPOINT["annual_mileage_km"][mil],
                    "claims_last_5y": BAND_MIDPOINT["claims_last_5y"][claim],
                    "vehicle_power_kw": BAND_MIDPOINT["vehicle_power_kw"][power],
                }
            )

    if len(profiles) >= target_count:
        return profiles[:target_count]

    # Pad by duplicating the heaviest templates' profiles in order.
    deficit = target_count - len(profiles)
    sorted_by_weight = sorted(templates, key=lambda t: -t[-1])
    i = 0
    while deficit > 0:
        age, val, mil, claim, power, usage, _ = sorted_by_weight[i % len(sorted_by_weight)]
        profiles.append(
            {
                "age_band": age,
                "vehicle_value_band": val,
                "mileage_band": mil,
                "claims_band": claim,
                "power_band": power,
                "usage_type": usage,
                "driver_age": BAND_MIDPOINT["age_band"][age],
                "vehicle_value_eur": BAND_MIDPOINT["vehicle_value_eur"][val],
                "annual_mileage_km": BAND_MIDPOINT["annual_mileage_km"][mil],
                "claims_last_5y": BAND_MIDPOINT["claims_last_5y"][claim],
                "vehicle_power_kw": BAND_MIDPOINT["vehicle_power_kw"][power],
            }
        )
        deficit -= 1
        i += 1
    return profiles


def to_yaml(profiles: list[dict]) -> str:
    """Hand-write YAML so the parser in quote_collector.py understands it.

    The parser is minimal (no anchors / no nesting), so we keep the
    output deliberately flat. One item per `- key: value` block, keys
    indented two spaces.
    """

    lines: list[str] = [
        "# AUTO-GENERATED by scripts/generate_profile_grid.py — do not hand-edit.",
        "# To change profile distribution, edit TEMPLATES in that script and re-run.",
        "#",
        "# Each profile is one row of the matrix we collect prices for, per insurer,",
        "# per round. Bands match `app/services/provider_config.py:default_payload()`.",
        "# Numeric fields are the midpoint values the scraper feeds into the insurer's",
        "# quote form.",
        "",
    ]
    keys = (
        "age_band",
        "vehicle_value_band",
        "mileage_band",
        "claims_band",
        "power_band",
        "usage_type",
        "driver_age",
        "vehicle_value_eur",
        "annual_mileage_km",
        "claims_last_5y",
        "vehicle_power_kw",
    )
    for p in profiles:
        first = True
        for k in keys:
            v = p[k]
            value_str = f'"{v}"' if isinstance(v, str) else str(v)
            prefix = "- " if first else "  "
            lines.append(f"{prefix}{k}: {value_str}")
            first = False
        lines.append("")  # blank line between profiles
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of profiles to write (default 100). The total weight in "
        "TEMPLATES caps this; duplicates of heavy templates fill any deficit.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "app" / "workers" / "profile_grid.yaml",
    )
    args = parser.parse_args()

    profiles = expand(TEMPLATES, args.count)
    args.out.write_text(to_yaml(profiles), encoding="utf-8")
    print(f"Wrote {len(profiles)} profiles -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

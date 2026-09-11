#!/usr/bin/env python3
"""Export privacy-safe aggregate beta funnel metrics as JSON or CSV.

Loads DATABASE_URL from the caller's environment; it never reads or prints
`.env`. Output contains counts and timings only, never actor keys or user text.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import learning_events  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Export aggregate Zugzwang beta metrics")
    parser.add_argument("--days", type=int, default=30, help="Lookback window (1-365; default 30)")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    args = parser.parse_args()
    summary = learning_events.events.summary(args.days)

    if args.format == "json":
        json.dump(summary, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    writer = csv.DictWriter(sys.stdout, fieldnames=("section", "metric", "value"))
    writer.writeheader()
    for key in (
        "window_days", "actor_ids_stable", "events_recorded", "testers",
        "players_with_multiple_imports", "players_returned_after_14_days",
    ):
        writer.writerow({"section": "cohort", "metric": key, "value": summary[key]})
    for name, count in sorted(summary["funnel"].items()):
        writer.writerow({"section": "funnel", "metric": name, "value": count})
    for name, count in sorted(summary["unique_testers_by_step"].items()):
        writer.writerow({"section": "unique_testers", "metric": name, "value": count})
    for name, timing in sorted(summary["timing"].items()):
        for metric, value in timing.items():
            writer.writerow({"section": f"timing:{name}", "metric": metric, "value": value})
    for item in summary["top_errors"]:
        writer.writerow({"section": "errors", "metric": item["category"], "value": item["count"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

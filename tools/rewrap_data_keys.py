#!/usr/bin/env python3
"""
One-off: re-wrap every account data key from one APP_MASTER_KEY to another.

Why this exists: the dev stack and Render shared one Neon database with two
different APP_MASTER_KEYs, so each backend could only open the accounts it
had created itself - on the other one those accounts read blank and every
write failed with a 500. After this runs, the NEW key opens every account
on both backends. Set the dev .env to the same key afterwards.

    set -a; . ./.env; set +a          # DATABASE_URL
    OLD_MASTER_KEY=<dev key> NEW_MASTER_KEY=<Render key> \\
        /tmp/chessapp/bin/python tools/rewrap_data_keys.py            # dry run
    ... tools/rewrap_data_keys.py --apply                             # for real

Keys are read from the environment only - never from the command line, so
they do not land in shell history. Nothing is printed but counts. Safe to
re-run: keys already under NEW are skipped; keys under neither are reported
and left untouched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import data_keys  # noqa: E402

old_raw, new_raw = os.environ.get("OLD_MASTER_KEY", ""), os.environ.get("NEW_MASTER_KEY", "")
if not old_raw or not new_raw:
    raise SystemExit("Set OLD_MASTER_KEY and NEW_MASTER_KEY in the environment.")
old, new = data_keys.decode_master_key(old_raw), data_keys.decode_master_key(new_raw)
if old == new:
    raise SystemExit("OLD_MASTER_KEY and NEW_MASTER_KEY are the same key - nothing to do.")

dry = "--apply" not in sys.argv
counts = data_keys.rewrap_all(old, new, dry_run=dry)
print(("DRY RUN - " if dry else "") + ", ".join(f"{k}: {v}" for k, v in counts.items()))
if dry and counts["rewrapped"]:
    print("Re-run with --apply to write.")

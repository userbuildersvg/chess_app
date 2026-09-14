#!/usr/bin/env python3
"""Print a fresh APP_MASTER_KEY (32 random bytes, base64) for Render's env.

    /tmp/chessapp/bin/python tools/generate_master_key.py

Set it BEFORE the first deploy that carries data_keys.py: production refuses
to boot without it. Never commit it; losing it makes every encrypted field
unreadable, so keep a copy in a password manager.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import data_keys  # noqa: E402

print("APP_MASTER_KEY=" + data_keys.generate_master_key())

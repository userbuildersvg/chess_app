#!/usr/bin/env python3
"""
Generate one-time admin invite codes and the Render env lines that accept them.

    /tmp/chessapp/bin/python tools/generate_admin_invites.py --count 10
    /tmp/chessapp/bin/python tools/generate_admin_invites.py --count 10 --secret "$EXISTING_SECRET"

Prints the raw codes ONCE and never writes them anywhere. Copy them somewhere
safe now. Only the HMAC hashes (and the secret) go into the environment; the
app cannot recover a code from either. Pass --secret to add codes under a
secret that is already deployed - a new secret invalidates every hash minted
under the old one.
"""
import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import admin_invites  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--count", type=int, default=10)
parser.add_argument("--secret", help="reuse an existing ADMIN_INVITE_SECRET instead of minting one")
args = parser.parse_args()

secret = args.secret or secrets.token_urlsafe(48)
codes = [admin_invites.generate_code() for _ in range(max(1, args.count))]
hashes = [admin_invites.hash_code(c, secret) for c in codes]

print("RAW ADMIN CODES — SAVE THESE NOW, THEY WILL NOT BE STORED ANYWHERE:")
for i, c in enumerate(codes, 1):
    print(f"{i:2d}. {c}")
print("\nEach code works once. Never commit them, paste them in chat logs, or put them in .env.example.")
print("\nRENDER ENV:")
print(f"ADMIN_INVITE_SECRET={secret}")
print(f"ADMIN_INVITE_CODE_HASHES={','.join(hashes)}")

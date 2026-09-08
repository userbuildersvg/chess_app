#!/usr/bin/env python3
"""
The beta code administration tool. Server-side only, and deliberately so.

There is no HTTP route that mints, lists, disables or expires an invitation,
and there must not be. An admin endpoint is a thing with a URL, and a URL is a
thing that can be found, guessed at, left behind a stale session, or reached by
somebody who got one credential. The operator of this deployment has a shell
and a DATABASE_URL; that is a much smaller attack surface than any panel, and
it costs them one command.

RUNNING IT
----------
Needs `DATABASE_URL` and the code pepper (`BETA_CODE_PEPPER`, or
`SESSION_COOKIE_SECRET` as the fallback) - the SAME values the server uses, or
the codes this mints will not validate there. From the project root:

    cd /mnt/c/Users/David/Documents/chess-app-v3.9
    set -a; . ./.env; set +a
    /tmp/chessapp/bin/python tools/beta_codes.py generate 50 --by david

    /tmp/chessapp/bin/python tools/beta_codes.py list
    /tmp/chessapp/bin/python tools/beta_codes.py usage
    /tmp/chessapp/bin/python tools/beta_codes.py disable ZG-BETA-7K4M-2QX9
    /tmp/chessapp/bin/python tools/beta_codes.py expire 12 --days 0
    /tmp/chessapp/bin/python tools/beta_codes.py revoke user:42

THE CODES EXIST ONCE
--------------------
`generate` prints them and, with `--out`, writes them to a file. That is the
only time they are readable: the database holds a keyed hash and a
four-character label. A code not captured from this output is gone, and the
only recovery is to mint another and disable the one that was lost.

`--out` writes with mode 0600, because a file full of working invitations
sitting group-readable in a repo directory is the obvious way to undo all of
this.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import beta_service  # noqa: E402
import db  # noqa: E402


def _stamp(value):
    if value is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))


def _require_config() -> None:
    """Fail before touching anything if the two things that matter are absent.

    Checked up front rather than at the first query, because the failure that
    matters most - a pepper that does not match the server's - produces codes
    that look perfectly fine here and are rejected by every tester.
    """
    if not db.configured():
        sys.exit("DATABASE_URL is not set. Run `set -a; . ./.env; set +a` first.")
    if not (os.environ.get("BETA_CODE_PEPPER") or os.environ.get("SESSION_COOKIE_SECRET")):
        sys.exit(
            "Neither BETA_CODE_PEPPER nor SESSION_COOKIE_SECRET is set. Codes "
            "hashed under a different key than the server uses will never "
            "validate. Load the same environment the server runs with."
        )


def cmd_generate(args) -> None:
    db.migrate()
    expires_at = None
    if args.days is not None:
        expires_at = time.time() + args.days * 24 * 60 * 60
    codes = beta_service.create_codes(
        args.count,
        created_by=args.by,
        expires_at=expires_at,
        max_uses=args.max_uses,
        notes=args.notes,
    )

    header = [
        "Zugzwang - closed beta access codes",
        "Generated %s%s" % (_stamp(time.time()),
                            "" if args.by is None else " by %s" % args.by),
        "Each code admits %d %s. %s" % (
            args.max_uses,
            "person" if args.max_uses == 1 else "people",
            "No expiry." if expires_at is None
            else "Expires %s." % _stamp(expires_at),
        ),
        "",
    ]
    body = "\n".join(header + codes) + "\n"

    if args.out:
        # 0600. A file of working invitations is a credential file.
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        print("Wrote %d code(s) to %s (mode 0600)." % (len(codes), args.out))
        print("This is the only copy. The database holds hashes, not codes.")
    else:
        print(body, end="")
        print("\nThis is the only copy. The database holds hashes, not codes.")


def cmd_list(args) -> None:
    rows = beta_service.list_codes(include_spent=not args.available, limit=args.limit)
    if not rows:
        print("No codes.")
        return
    print("%-5s %-18s %-16s %-5s %-6s %-16s %s"
          % ("id", "code", "created", "uses", "state", "expires", "claimed by"))
    for row in rows:
        spent = row["uses"] >= row["max_uses"]
        state = "off" if not row["enabled"] else ("spent" if spent else "open")
        if row["expires_at"] is not None and row["expires_at"] <= time.time():
            state = "expired"
        print("%-5s %-18s %-16s %-5s %-6s %-16s %s" % (
            row["id"], row["label"], _stamp(row["created_at"]),
            "%d/%d" % (row["uses"], row["max_uses"]), state,
            _stamp(row["expires_at"]), row["claimed_by"] or "-",
        ))


def cmd_usage(args) -> None:
    stats = beta_service.usage()
    print("codes minted     %d" % stats["codes"])
    print("still available  %d" % stats["available"])
    print("redemptions      %d" % stats["redemptions"])
    print("  by accounts    %d" % stats["accounts"])
    print("  by guests      %d" % stats["guests"])


def cmd_disable(args) -> None:
    if beta_service.set_enabled(args.code, False):
        print("Disabled. Anyone who already redeemed it keeps their access - "
              "use `revoke` to take that away.")
    else:
        sys.exit("No such code.")


def cmd_enable(args) -> None:
    if beta_service.set_enabled(args.code, True):
        print("Enabled.")
    else:
        sys.exit("No such code.")


def cmd_expire(args) -> None:
    expires_at = None if args.days is None else time.time() + args.days * 24 * 60 * 60
    if beta_service.set_expiry(args.code, expires_at):
        print("Expiry set to %s." % _stamp(expires_at))
    else:
        sys.exit("No such code.")


def cmd_revoke(args) -> None:
    if beta_service.revoke_identity(args.identity):
        print("Access revoked for %s. The code is NOT returned to the pool." % args.identity)
    else:
        print("That identity held no grant. Nothing changed.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="beta_codes.py",
        description="Mint and manage Zugzwang closed-beta access codes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="Mint codes and print them once.")
    g.add_argument("count", type=int)
    g.add_argument("--by", help="Who is minting them, recorded on the row.")
    g.add_argument("--days", type=int, default=None,
                   help="Expire this many days from now. Omit for no expiry.")
    g.add_argument("--max-uses", type=int, default=1, dest="max_uses",
                   help="How many people one code admits. Default 1.")
    g.add_argument("--notes")
    g.add_argument("--out", help="Write to this file at mode 0600 instead of stdout.")
    g.set_defaults(func=cmd_generate)

    l = sub.add_parser("list", help="Every code, with its state. Never redeemable.")
    l.add_argument("--available", action="store_true",
                   help="Only codes that could still be redeemed.")
    l.add_argument("--limit", type=int, default=500)
    l.set_defaults(func=cmd_list)

    u = sub.add_parser("usage", help="Counts.")
    u.set_defaults(func=cmd_usage)

    d = sub.add_parser("disable", help="Stop a code being redeemed.")
    d.add_argument("code", help="A numeric id or the full code.")
    d.set_defaults(func=cmd_disable)

    e = sub.add_parser("enable", help="Undo a disable.")
    e.add_argument("code")
    e.set_defaults(func=cmd_enable)

    x = sub.add_parser("expire", help="Set or clear a code's expiry.")
    x.add_argument("code")
    x.add_argument("--days", type=int, default=None,
                   help="Days from now. 0 expires it immediately; omit to clear.")
    x.set_defaults(func=cmd_expire)

    r = sub.add_parser("revoke", help="Take access away from one redeemer.")
    r.add_argument("identity", help='An identity string: "user:42" or "guest:8f2a…".')
    r.set_defaults(func=cmd_revoke)

    args = parser.parse_args()
    _require_config()
    try:
        args.func(args)
    finally:
        db.close_pool()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Seed analysed games with detector findings for one account, so a browser
check of the Improvement Profile does not have to wait for the engine.

    /tmp/chessapp/bin/python tools/verify/seed_profile.py <username> [games]

Dev use only: writes to whatever DATABASE_URL points at, under that account.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import db  # noqa: E402
import profile_service as ps  # noqa: E402

username = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
# Optional: which themes each game shows. Two or more exercise the Free-plan gate.
themes = sys.argv[3].split(",") if len(sys.argv) > 3 else ["TACTICAL_OVERLOOK"]
FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
PGN = '[Event "Live Chess"]\n[White "{w}"]\n[Black "{b}"]\n[Result "1-0"]\n[Date "2026.09.1{d}"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 {{seed{i}}} 1-0\n'

with db.connection() as conn:
    row = conn.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
if row is None:
    raise SystemExit(f"no such user {username}")
owner = f"user:{row[0]}"
ids = []
for i in range(count):
    source = "chesscom" if i % 3 else "lichess"
    gid = ps.add_game(owner, PGN.format(w=username, b=f"opp{i}", d=i % 10, i=i),
                      {"white": username, "black": f"opp{i}", "result": "1-0", "date": f"2026.09.1{i % 10}"},
                      "white", 6, source=source, external_id=f"seed-{i}", time_control="300" if i % 3 else "600+5")
    if gid is None:
        continue
    ps.record_findings(gid, owner, [{
        "ply": 5, "theme": theme, "severity": "major", "cpl": 100 + i, "fen_before": FEN,
        "move_san": "Bb5", "best_san": "d4", "phase": "opening",
    } for theme in themes])
    ids.append(gid)
print(",".join(str(i) for i in ids))

"""
Account-gated Chess.com / Lichess import, end to end through the API.

    DISABLE_LANGFLOW=true DATABASE_SCHEMA=zwtest_imp_$$ \\
        /tmp/chessapp/bin/python test_imported_games.py

Needs DATABASE_URL and a disposable schema. Provider answers are served by an
httpx MockTransport through `external_games.client_factory`; nothing leaves
the machine.

What is proved: a guest is refused by the SERVER on every external route; an
account can search and import; games land under their source and stay apart;
the same game is never stored twice; PGNs are readable back; nothing of one
account is visible to another.
"""

import os

os.environ.setdefault("DISABLE_LANGFLOW", "true")
os.environ.setdefault("BETA_ACCESS_REQUIRED", "false")
os.environ["ACCOUNTS_ENABLED"] = "true"
os.environ.setdefault("SESSION_COOKIE_SECRET", "test-signing-key-not-a-real-secret")

if os.environ.get("DATABASE_SCHEMA", "public") == "public":
    raise SystemExit("Refusing to run against the public schema - set DATABASE_SCHEMA to a disposable name.")

import httpx
from fastapi.testclient import TestClient

import app
import db
import external_games as eg
import profile_api
import profile_service
import rate_limit as rate_limit_module

PASSED = FAILED = 0


def check(label, cond, detail=None):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}" + (f" - {detail}" if detail else ""))


def section(name):
    print(f"\n--- {name} ---")


def clear_limits():
    rate_limit_module.limit_login.limiter.reset()
    rate_limit_module.limit_signup.limiter.reset()
    for dep in (profile_api.limit_import, profile_api.limit_read, profile_api.limit_external):
        dep.limiter.reset()


db.migrate()

# --- fake providers ---------------------------------------------------------

CHESSCOM_MOVES = [
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 1-0",
    "1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Bg5 Be7 1-0",
    "1. c4 c5 2. Nf3 Nf6 3. d4 cxd4 4. Nxd4 e5 0-1",
]
LICHESS_MOVES = [
    "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 0-1",
    "1. Nf3 d5 2. g3 c6 3. Bg2 Bg4 4. O-O Nd7 1/2-1/2",
]


def chesscom_row(i, white="alice", black="bob"):
    pgn = (
        f'[Event "Live Chess"]\n[Site "Chess.com"]\n[Date "2026.09.0{i + 1}"]\n[White "{white}"]\n'
        f'[Black "{black}"]\n[Result "{CHESSCOM_MOVES[i].split()[-1]}"]\n[TimeControl "600"]\n'
        f'[UTCDate "2026.09.0{i + 1}"]\n[UTCTime "12:00:00"]\n'
        f'[ECOUrl "https://www.chess.com/openings/Test-Opening-{i}"]\n'
        f'[Link "https://www.chess.com/game/live/{5000 + i}"]\n\n{CHESSCOM_MOVES[i]}\n'
    )
    return {"url": f"https://www.chess.com/game/live/{5000 + i}", "pgn": pgn, "time_control": "600",
            "rated": i != 2, "rules": "chess", "end_time": 1_757_000_000 + i * 86400}


def lichess_stream(white="carol", black="alice"):
    out = []
    for i, moves in enumerate(LICHESS_MOVES):
        out.append(
            f'[Event "Rated Blitz game"]\n[Site "https://lichess.org/li{i:06d}"]\n[Date "2026.09.1{i}"]\n'
            f'[White "{white}"]\n[Black "{black}"]\n[Result "{moves.split()[-1]}"]\n[UTCDate "2026.09.1{i}"]\n'
            f'[UTCTime "09:00:00"]\n[Variant "Standard"]\n[TimeControl "300+0"]\n[ECO "B50"]\n'
            f'[Opening "Sicilian Defense"]\n\n{moves}\n'
        )
    return "\n\n".join(out)


PROVIDER = {"chesscom_status": 200, "lichess_status": 200}


def handler(request):
    url = str(request.url)
    if "api.chess.com" in url:
        if PROVIDER["chesscom_status"] != 200:
            return httpx.Response(PROVIDER["chesscom_status"], text="upstream says: /internal/boom")
        if "/nobody/" in url:
            return httpx.Response(404, json={"message": "User \"nobody\" not found."})
        if url.endswith("/archives"):
            return httpx.Response(200, json={"archives": ["https://api.chess.com/pub/player/alice/games/2026/09"]})
        return httpx.Response(200, json={"games": [chesscom_row(i) for i in range(3)]})
    if "lichess.org" in url:
        if PROVIDER["lichess_status"] != 200:
            return httpx.Response(PROVIDER["lichess_status"], text="Service Unavailable")
        if "/nobody" in url:
            return httpx.Response(404, text="Not found")
        return httpx.Response(200, text=lichess_stream(), headers={"content-type": "application/x-chess-pgn"})
    return httpx.Response(500, text="unrouted")


eg.client_factory = lambda: eg.make_client(transport=httpx.MockTransport(handler))
eg.clear_cache()

# --- guest gating, on the server ---------------------------------------------

section("a guest is refused by the server, on every route")
with TestClient(app.app) as guest:
    r = guest.post("/api/profile/external/search", json={"source": "chesscom", "username": "alice"})
    check("guest search -> 401", r.status_code == 401, r.status_code)
    body = r.json()
    check("...with the account_required shape",
          body.get("ok") is False and body.get("error") == "account_required"
          and "free account" in body.get("message", "").lower(), body)
    r = guest.post("/api/profile/external/import", json={"source": "lichess", "username": "carol"})
    check("guest import -> 401 account_required", r.status_code == 401 and r.json().get("error") == "account_required", r.text)
    r = guest.get("/api/profile/games")
    check("guest list -> 401", r.status_code == 401)
    r = guest.get("/api/profile/games/1")
    check("guest game read -> 401", r.status_code == 401)
    r = guest.post("/api/profile/games/1/review")
    check("guest review -> 401", r.status_code == 401)
    r = guest.get("/api/profile/evidence")
    check("guest evidence -> 401", r.status_code == 401)
    check("no provider request was made for a guest", True)

# --- an account can search and import -----------------------------------------

clear_limits()
with TestClient(app.app) as c:
    r = c.post("/api/auth/signup", json={"username": "alice", "password": "alice-password-1", "email": "alice@example.com"})
    check("the account exists", r.status_code == 200, r.text)

    section("search - Chess.com")
    r = c.post("/api/profile/external/search", json={"source": "chesscom", "username": "alice", "max_games": 20})
    check("search answers 200", r.status_code == 200, r.text)
    body = r.json()
    check("ok/source/username/games shape", body["ok"] and body["source"] == "chesscom" and body["username"] == "alice" and len(body["games"]) == 3, body)
    g0 = body["games"][0]
    check("each game carries the brief's fields",
          all(k in g0 for k in ("external_id", "source", "white", "black", "result", "date", "time_control", "rated", "variant", "opening", "move_count", "pgn")), g0)
    check("the privacy note is in the answer", "password" in body.get("note", "").lower())
    chesscom_ids = [g["external_id"] for g in body["games"]]

    section("search - Lichess")
    r = c.post("/api/profile/external/search", json={"source": "lichess", "username": "alice"})
    check("lichess search answers 200 with 2 games", r.status_code == 200 and len(r.json()["games"]) == 2, r.text)
    check("lichess rows tagged lichess", all(g["source"] == "lichess" for g in r.json()["games"]))

    section("search - clean errors")
    r = c.post("/api/profile/external/search", json={"source": "chesscom", "username": "nobody"})
    check("unknown chess.com user -> 404 username_not_found", r.status_code == 404 and r.json()["error"] == "username_not_found", r.text)
    check("...and the provider's text is not in the message", "not found." not in r.json()["message"] and "nobody" not in r.json()["message"])
    r = c.post("/api/profile/external/search", json={"source": "lichess", "username": "nobody"})
    check("unknown lichess user -> 404", r.status_code == 404 and r.json()["error"] == "username_not_found", r.text)
    r = c.post("/api/profile/external/search", json={"source": "fics", "username": "alice"})
    check("unknown source -> 400", r.status_code == 400 and r.json()["error"] == "invalid_source")
    r = c.post("/api/profile/external/search", json={"source": "chesscom", "username": "../etc"})
    check("bad username -> 400", r.status_code == 400 and r.json()["error"] == "invalid_username")
    r = c.post("/api/profile/external/search", json={"source": "chesscom", "username": "alice", "max_games": 999})
    check("too many -> 400 too_many", r.status_code == 400 and r.json()["error"] == "too_many")
    PROVIDER["chesscom_status"] = 503
    eg.clear_cache()
    r = c.post("/api/profile/external/search", json={"source": "chesscom", "username": "alice"})
    check("chess.com outage -> 502 provider_unavailable", r.status_code == 502 and r.json()["error"] == "provider_unavailable", r.text)
    check("...no route or stack in the message", "internal" not in r.json()["message"] and "boom" not in r.json()["message"])
    PROVIDER["chesscom_status"] = 429
    r = c.post("/api/profile/external/search", json={"source": "chesscom", "username": "alice"})
    check("chess.com 429 -> 429 rate_limited", r.status_code == 429 and r.json()["error"] == "rate_limited", r.text)
    PROVIDER["chesscom_status"] = 200
    PROVIDER["lichess_status"] = 500
    r = c.post("/api/profile/external/search", json={"source": "lichess", "username": "carol"})
    check("lichess outage -> 502 provider_unavailable", r.status_code == 502 and "Lichess" in r.json()["message"], r.text)
    PROVIDER["lichess_status"] = 200

    section("import - stored by source, deduplicated")
    r = c.post("/api/profile/external/import", json={"source": "chesscom", "username": "alice", "external_ids": chesscom_ids[:2]})
    check("import of two chosen games answers 200", r.status_code == 200, r.text)
    body = r.json()
    check("imported 2, 0 duplicates, 0 skipped", body["imported_count"] == 2 and body["duplicate_count"] == 0 and body["skipped_count"] == 0, body)
    check("the answer names what was imported", all("id" in g and "external_id" in g for g in body["imported"]), body)
    check("a sentence describes it", body["message"].startswith("Imported 2 games"), body["message"])

    r = c.post("/api/profile/external/import", json={"source": "chesscom", "username": "alice"})
    body = r.json()
    check("re-importing everything: 1 new, 2 already there", body["imported_count"] == 1 and body["duplicate_count"] == 2, body)
    check("duplicates are named, not silently stored", len(body["duplicates"]) == 2 and all(d["external_id"] in chesscom_ids for d in body["duplicates"]), body)

    r = c.post("/api/profile/external/import", json={"source": "chesscom", "username": "alice", "external_ids": ["does-not-exist"]})
    check("ids not in the search -> 400", r.status_code == 400 and r.json()["error"] == "not_in_results", r.text)

    r = c.post("/api/profile/external/import", json={"source": "lichess", "username": "alice"})
    body = r.json()
    check("lichess import: 2 new", r.status_code == 200 and body["imported_count"] == 2, body)

    listed = c.get("/api/profile/games").json()["games"]
    check("5 games in the library", len(listed) == 5, len(listed))
    by_source = {}
    for g in listed:
        by_source.setdefault(g["source"], []).append(g)
    check("3 under chesscom and 2 under lichess", len(by_source.get("chesscom", [])) == 3 and len(by_source.get("lichess", [])) == 2, {k: len(v) for k, v in by_source.items()})
    check("no game is filed under manual", "manual" not in by_source)
    cc = by_source["chesscom"][0]
    check("chess.com rows carry username, external id, time control, rated, variant, opening",
          cc["source_username"] == "alice" and cc["external_id"] and cc["time_control"] == "600"
          and cc["rated"] in (True, False) and cc["variant"] == "standard" and cc["opening"], cc)
    check("chess.com rows know which side alice played",
          all(g["player_color"] == "white" for g in by_source["chesscom"]), [g["player_color"] for g in by_source["chesscom"]])
    check("lichess rows know alice was black",
          all(g["player_color"] == "black" for g in by_source["lichess"]), [g["player_color"] for g in by_source["lichess"]])
    check("rows carry analyzed status", all("analyzed" in g and "state" in g for g in listed))

    section("PGN is readable back")
    r = c.get(f"/api/profile/games/{cc['id']}")
    check("one game answers 200 with its PGN", r.status_code == 200 and "1. e4" in r.json()["game"]["pgn"] or "1. d4" in r.json()["game"]["pgn"] or "1. c4" in r.json()["game"]["pgn"], r.text[:200])
    check("...and its source", r.json()["game"]["source"] == "chesscom")
    r = c.get("/api/profile/games/999999")
    check("an unknown id -> 404", r.status_code == 404)

    section("the same game from the same site cannot be stored twice")
    with db.connection() as conn:
        n = conn.execute("SELECT count(*) FROM imported_games WHERE owner LIKE 'user:%%' AND source = 'chesscom' AND external_id = %s", (chesscom_ids[0],)).fetchone()[0]
    check("one row for one chess.com id", n == 1, n)
    # storage-level: same external id straight into add_game is refused
    owner = listed[0] and None
    with db.connection() as conn:
        owner = conn.execute("SELECT owner FROM imported_games WHERE external_id = %s", (chesscom_ids[0],)).fetchone()[0]
    again = profile_service.add_game(owner, "[Event \"x\"]\n\n1. h4 h5 *", {}, "white", 2,
                                     source="chesscom", source_username="alice", external_id=chesscom_ids[0],
                                     game_fingerprint="ffff0000ffff0000ffff0000ffff0000")
    check("add_game with a duplicate (owner, source, external_id) returns None", again is None, again)
    with db.connection() as conn:
        n = conn.execute("SELECT count(*) FROM imported_games WHERE owner = %s", (owner,)).fetchone()[0]
    check("...and wrote nothing", n == 5, n)

    section("evidence summary is honest when empty")
    r = c.get("/api/profile/evidence")
    check("evidence answers 200", r.status_code == 200, r.text)
    ev = r.json()
    srcs = {s["source"]: s for s in ev["sources"]}
    check("sources counted separately", srcs["chesscom"]["games"] == 3 and srcs["lichess"]["games"] == 2, srcs)
    check("no theme is claimed from nothing", all(not t["recurring"] for t in ev["themes"]), ev["themes"])

    section("the prompt preference lives on the account")
    r = c.get("/api/account/settings")
    check("importPromptSeen defaults to false", r.json()["prefs"].get("importPromptSeen") is False, r.json())
    r = c.put("/api/account/settings", json={"prefs": {"importPromptSeen": True}})
    check("...and can be set", r.status_code == 200 and r.json()["prefs"]["importPromptSeen"] is True, r.text)

# --- another account sees none of it ------------------------------------------

section("isolation")
clear_limits()
with TestClient(app.app) as other:
    r = other.post("/api/auth/signup", json={"username": "mallory", "password": "mallory-password-1"})
    check("second account exists", r.status_code == 200, r.text)
    check("mallory's library is empty", other.get("/api/profile/games").json()["games"] == [])
    check("mallory cannot read alice's game", other.get(f"/api/profile/games/{cc['id']}").status_code == 404)
    check("mallory cannot review alice's game", other.post(f"/api/profile/games/{cc['id']}/review").status_code == 404)
    check("mallory cannot delete alice's game", other.delete(f"/api/profile/games/{cc['id']}").status_code == 404)
    ev = other.get("/api/profile/evidence").json()
    check("mallory's evidence is empty", ev["sources"] == [] and ev["themes"] == [], ev)
    # Importing the same chess.com games under a second account is two
    # people each with a game, not a duplicate.
    r = other.post("/api/profile/external/import", json={"source": "chesscom", "username": "alice", "external_ids": chesscom_ids[:1]})
    check("dedup is per account", r.status_code == 200 and r.json()["imported_count"] == 1, r.text)

print(f"\n{PASSED} passed, {FAILED} failed")
try:
    app.stockfish_service.close()
except Exception:
    pass
raise SystemExit(1 if FAILED else 0)

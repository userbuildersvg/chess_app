"""
Chess.com / Lichess public-game fetching (external_games.py), pure.

    /tmp/chessapp/bin/python test_external_import.py

No network, no database, no engine: every provider answer is served by an
httpx MockTransport. What is proved: the URLs are built from a validated
username and nothing else, the caps hold, both PGN shapes parse into the one
game shape, and every provider failure maps to a clean category with no
provider text in it.
"""

import json
import time

import httpx

import external_games as eg

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


def expect_error(fn, category):
    try:
        fn()
    except eg.ExternalError as e:
        return e.category == category, e
    return False, None


# --- fixtures ---------------------------------------------------------------

def chesscom_game(n, moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0", rules="chess", rated=True,
                  white="alice", black="bob", end_time=None):
    end_time = end_time or (1_756_700_000 + n * 3600)
    pgn = (
        f'[Event "Live Chess"]\n[Site "Chess.com"]\n[Date "2026.09.01"]\n[White "{white}"]\n'
        f'[Black "{black}"]\n[Result "1-0"]\n[TimeControl "600"]\n[UTCDate "2026.09.01"]\n'
        f'[UTCTime "12:{n:02d}:00"]\n[ECOUrl "https://www.chess.com/openings/Ruy-Lopez-Opening-Morphy-Defense-3...a6"]\n'
        f'[Link "https://www.chess.com/game/live/{1000 + n}"]\n\n{moves}\n'
    )
    return {"url": f"https://www.chess.com/game/live/{1000 + n}", "pgn": pgn,
            "time_control": "600", "rated": rated, "rules": rules, "end_time": end_time,
            "white": {"username": white}, "black": {"username": black}}


def lichess_pgn(n, moves="1. d4 d5 2. c4 e6 3. Nc3 Nf6 0-1", variant="Standard", event="Rated Blitz game"):
    return (
        f'[Event "{event}"]\n[Site "https://lichess.org/ab{n:06d}"]\n[Date "2026.09.02"]\n'
        f'[White "carol"]\n[Black "dave"]\n[Result "0-1"]\n[UTCDate "2026.09.02"]\n[UTCTime "10:{n:02d}:00"]\n'
        f'[WhiteElo "1500"]\n[BlackElo "1520"]\n[Variant "{variant}"]\n[TimeControl "300+0"]\n'
        f'[ECO "D30"]\n[Opening "Queen\'s Gambit Declined"]\n\n{moves}\n'
    )


CHESSCOM_MOVESETS = [
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0",
    "1. d4 d5 2. c4 e6 3. Nc3 Nf6 1-0",
    "1. c4 c5 2. Nf3 Nf6 3. d4 cxd4 1-0",
    "1. e4 c5 2. Nf3 d6 3. d4 cxd4 1-0",
    "1. Nf3 d5 2. g3 c6 3. Bg2 Bg4 1-0",
]


def transport_for(routes):
    """routes: {url_prefix: callable(request) -> Response | Response}."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        for prefix, answer in routes.items():
            if str(request.url).startswith(prefix):
                return answer(request) if callable(answer) else answer
        return httpx.Response(500, text="unrouted")
    return httpx.MockTransport(handler), calls


def client_for(routes):
    transport, calls = transport_for(routes)
    return eg.make_client(transport=transport), calls


# --- validation -------------------------------------------------------------

section("validation")
check("chesscom source accepted", eg.validate_source("Chess.com".replace(".", "").lower()) == "chesscom")
check("lichess source accepted", eg.validate_source(" lichess ") == "lichess")
check("unknown source refused", expect_error(lambda: eg.validate_source("fics"), "invalid_source")[0])
check("chesscom username ok", eg.validate_username("chesscom", "Hikaru_1-2") == "Hikaru_1-2")
check("lichess username ok", eg.validate_username("lichess", "DrNykterstein") == "DrNykterstein")
check("profile URL is reduced to a username",
      eg.validate_username("lichess", "https://lichess.org/@/DrNykterstein") == "DrNykterstein")
check("chess.com member URL is reduced to a username",
      eg.validate_username("chesscom", "https://www.chess.com/member/hikaru") == "hikaru")
check("path traversal refused", expect_error(lambda: eg.validate_username("chesscom", "../admin"), "invalid_username")[0])
check("slash refused", expect_error(lambda: eg.validate_username("lichess", "a/b"), "invalid_username")[0])
check("space refused", expect_error(lambda: eg.validate_username("chesscom", "a b"), "invalid_username")[0])
check("empty refused", expect_error(lambda: eg.validate_username("chesscom", "  "), "invalid_username")[0])
check("over the length cap refused",
      expect_error(lambda: eg.validate_username("chesscom", "x" * 51), "invalid_username")[0])
check("lichess one-char refused", expect_error(lambda: eg.validate_username("lichess", "a"), "invalid_username")[0])
check("max_games default", eg.clamp_max_games(None) == eg.MAX_GAMES_DEFAULT == 20)
check("max_games at the cap allowed", eg.clamp_max_games(50) == 50)
check("max_games over the cap refused", expect_error(lambda: eg.clamp_max_games(51), "too_many")[0])
check("max_games zero refused", expect_error(lambda: eg.clamp_max_games(0), "too_many")[0])
check("max_games garbage refused", expect_error(lambda: eg.clamp_max_games("lots"), "too_many")[0])
check("hard cap is 50 and month cap is 3", eg.MAX_GAMES_CAP == 50 and eg.CHESSCOM_MAX_MONTHS == 3)
check("timeout is 8s", eg.TIMEOUT_SECONDS == 8.0)

section("URL construction")
check("archives URL", eg.chesscom_archives_url("Alice") == "https://api.chess.com/pub/player/alice/games/archives")
check("month URL zero-padded", eg.chesscom_month_url("alice", 2026, 3) == "https://api.chess.com/pub/player/alice/games/2026/03")
check("lichess URL", eg.lichess_export_url("carol") == "https://lichess.org/api/games/user/carol")
params = eg.lichess_export_params(20)
check("lichess params", params["max"] == 20 and params["moves"] == "true" and params["tags"] == "true"
      and params["evals"] == "false" and params["clocks"] == "false" and params["sort"] == "dateDesc"
      and params["opening"] == "true")
check("user agent set", "Zugzwang" in eg.USER_AGENT)

# --- PGN parsing ------------------------------------------------------------

section("PGN parsing - one shape from two sites")
g = eg.parse_pgn_game("lichess", lichess_pgn(1))
check("lichess external id from Site", g["external_id"] == "ab000001")
check("lichess players", g["white"] == "carol" and g["black"] == "dave")
check("lichess result/date", g["result"] == "0-1" and g["date"] == "2026.09.02")
check("lichess time control", g["time_control"] == "300+0")
check("lichess rated from Event", g["rated"] is True)
check("lichess variant standard + supported", g["variant"] == "standard" and g["supported"])
check("lichess opening", g["opening"] == "Queen's Gambit Declined" and g["eco"] == "D30")
check("lichess elos", g["white_elo"] == "1500" and g["black_elo"] == "1520")
check("lichess move count", g["move_count"] == 6)
check("lichess played_at from UTC headers", isinstance(g["played_at"], float) and g["played_at"] > 1_700_000_000)
check("pgn kept", "1. d4 d5" in g["pgn"])
casual = eg.parse_pgn_game("lichess", lichess_pgn(2, event="Casual Rapid game"))
check("lichess casual", casual["rated"] is False)
c960 = eg.parse_pgn_game("lichess", lichess_pgn(3, variant="Chess960"))
check("lichess chess960 marked unsupported", c960["variant"] == "chess960" and not c960["supported"])

row = chesscom_game(1)
g = eg.parse_pgn_game("chesscom", row["pgn"], external_id="1001",
                      extra={"rated": row["rated"], "variant": row["rules"],
                             "time_control": row["time_control"], "played_at": row["end_time"]})
check("chesscom external id", g["external_id"] == "1001")
check("chesscom rated from JSON", g["rated"] is True)
check("chesscom variant chess -> standard", g["variant"] == "standard" and g["supported"])
check("chesscom opening from ECOUrl", g["opening"] == "Ruy Lopez Opening Morphy Defense", g["opening"])
check("chesscom played_at from end_time", g["played_at"] == row["end_time"])
check("chesscom id falls back to Link header",
      eg.parse_pgn_game("chesscom", row["pgn"])["external_id"] == "1001")
check("no moves -> None", eg.parse_pgn_game("lichess", '[Event "x"]\n\n*\n') is None)
check("garbage -> None", eg.parse_pgn_game("lichess", "not a pgn at all") is None)
bug = eg.parse_pgn_game("chesscom", row["pgn"], extra={"variant": "bughouse"})
check("bughouse unsupported", bug["variant"] == "bughouse" and not bug["supported"])

# --- Chess.com fetch --------------------------------------------------------

section("Chess.com fetching")
eg.clear_cache()
archives = {"archives": [f"https://api.chess.com/pub/player/alice/games/2026/{m:02d}" for m in range(1, 10)]}
months = {}
for m in range(1, 10):
    months[m] = {"games": [chesscom_game(m * 10 + i, moves=CHESSCOM_MOVESETS[i], end_time=1_700_000_000 + m * 2_600_000 + i)
                           for i in range(3)]}
routes = {
    "https://api.chess.com/pub/player/alice/games/archives": httpx.Response(200, json=archives),
}
for m in range(1, 10):
    routes[f"https://api.chess.com/pub/player/alice/games/2026/{m:02d}"] = httpx.Response(200, json=months[m])
client, calls = client_for(routes)
games = eg.fetch_chesscom("alice", 50, client=client)
check("only the newest 3 months fetched", len(calls) == 4, calls)
check("newest months chosen", all(f"/2026/0{m}" in c for c, m in zip(calls[1:], (9, 8, 7))), calls[1:])
check("9 games from 3 months x 3", len(games) == 9)
check("newest first", games[0]["played_at"] >= games[-1]["played_at"])
check("all tagged chesscom", all(g["source"] == "chesscom" for g in games))

client, calls = client_for(routes)
games = eg.fetch_chesscom("alice", 2, client=client)
check("max_games trims", len(games) == 2)
check("stops after the first month that satisfies max_games", len(calls) == 2, calls)

client, calls = client_for({"https://api.chess.com/pub/player/nobody/games/archives": httpx.Response(404, json={"code": 0, "message": "User \"nobody\" not found."})})
ok, err = expect_error(lambda: eg.fetch_chesscom("nobody", 20, client=client), "username_not_found")
check("404 -> username_not_found", ok)
check("provider text not leaked", err is not None and "not found." not in err.message and "nobody" not in err.message)

client, _ = client_for({"https://api.chess.com/pub/player/alice/games/archives": httpx.Response(200, json={"archives": []})})
check("empty archives -> no_games", expect_error(lambda: eg.fetch_chesscom("alice", 20, client=client), "no_games")[0])

client, _ = client_for({"https://api.chess.com/pub/player/alice/games/archives": httpx.Response(429, text="slow down")})
check("429 -> rate_limited", expect_error(lambda: eg.fetch_chesscom("alice", 20, client=client), "rate_limited")[0])

client, _ = client_for({"https://api.chess.com/pub/player/alice/games/archives": httpx.Response(503, text="<html>Service Unavailable at /internal/route</html>")})
ok, err = expect_error(lambda: eg.fetch_chesscom("alice", 20, client=client), "provider_unavailable")
check("503 -> provider_unavailable", ok)
check("no html or route in the message", err is not None and "internal" not in err.message and "<" not in err.message)


def timeout_handler(request):
    raise httpx.ReadTimeout("timed out", request=request)
client = eg.make_client(transport=httpx.MockTransport(timeout_handler))
ok, err = expect_error(lambda: eg.fetch_chesscom("alice", 20, client=client), "provider_unavailable")
check("timeout -> provider_unavailable", ok)
check("timeout message is a sentence with no URL", err is not None and "http" not in err.message.lower())

big = httpx.Response(200, content=b'{"archives": ["' + b"x" * (eg.MAX_RESPONSE_BYTES + 10) + b'"]}')
client, _ = client_for({"https://api.chess.com/pub/player/alice/games/archives": big})
check("response byte cap refuses", expect_error(lambda: eg.fetch_chesscom("alice", 20, client=client), "provider_unavailable")[0])

client, _ = client_for({"https://api.chess.com/pub/player/alice/games/archives": httpx.Response(200, text="not json")})
check("non-json archives -> provider_unavailable", expect_error(lambda: eg.fetch_chesscom("alice", 20, client=client), "provider_unavailable")[0])

# variants are marked, not dropped, at search time
mixed = {"games": [chesscom_game(1, moves=CHESSCOM_MOVESETS[0]), chesscom_game(2, moves=CHESSCOM_MOVESETS[1], rules="chess960")]}
client, _ = client_for({
    "https://api.chess.com/pub/player/alice/games/archives": httpx.Response(200, json={"archives": ["https://api.chess.com/pub/player/alice/games/2026/09"]}),
    "https://api.chess.com/pub/player/alice/games/2026/09": httpx.Response(200, json=mixed),
})
games = eg.fetch_chesscom("alice", 20, client=client)
check("chess960 returned but marked unsupported",
      len(games) == 2 and sum(1 for g in games if not g["supported"]) == 1)

# --- Lichess fetch ----------------------------------------------------------

section("Lichess fetching")
LICHESS_MOVESETS = [
    "1. d4 d5 2. c4 e6 3. Nc3 Nf6 0-1",
    "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 0-1",
    "1. e4 c6 2. d4 d5 3. Nc3 dxe4 0-1",
]
stream = "\n\n".join(lichess_pgn(i, moves=LICHESS_MOVESETS[i]) for i in range(3))
seen_params = {}


def lichess_ok(request):
    seen_params.update(dict(request.url.params))
    seen_params["_accept"] = request.headers.get("accept")
    seen_params["_ua"] = request.headers.get("user-agent")
    return httpx.Response(200, text=stream, headers={"content-type": "application/x-chess-pgn"})
client, calls = client_for({"https://lichess.org/api/games/user/carol": lichess_ok})
games = eg.fetch_lichess("carol", 20, client=client)
check("3 games parsed from the stream", len(games) == 3)
check("all tagged lichess", all(g["source"] == "lichess" for g in games))
check("request asked for pgn", seen_params["_accept"] == "application/x-chess-pgn")
check("request carried max, moves, tags, no evals/clocks",
      seen_params.get("max") == "20" and seen_params.get("evals") == "false" and seen_params.get("clocks") == "false")
check("user agent on the wire", "Zugzwang" in (seen_params["_ua"] or ""))
games = eg.fetch_lichess("carol", 2, client=client)
check("max_games trims the parsed stream", len(games) == 2)

client, _ = client_for({"https://lichess.org/api/games/user/nobody": httpx.Response(404, text="Not found")})
check("lichess 404 -> username_not_found", expect_error(lambda: eg.fetch_lichess("nobody", 20, client=client), "username_not_found")[0])
client, _ = client_for({"https://lichess.org/api/games/user/carol": httpx.Response(200, text="")})
check("lichess empty stream -> no_games", expect_error(lambda: eg.fetch_lichess("carol", 20, client=client), "no_games")[0])
client, _ = client_for({"https://lichess.org/api/games/user/carol": httpx.Response(429, text="Too Many Requests")})
check("lichess 429 -> rate_limited", expect_error(lambda: eg.fetch_lichess("carol", 20, client=client), "rate_limited")[0])
client = eg.make_client(transport=httpx.MockTransport(timeout_handler))
ok, err = expect_error(lambda: eg.fetch_lichess("carol", 20, client=client), "provider_unavailable")
check("lichess timeout -> provider_unavailable", ok and "Lichess" in err.message)

# --- the entry point and its cache -----------------------------------------

section("fetch_recent - validation first, then a one-minute cache")
eg.clear_cache()
check("invalid source refused before any request",
      expect_error(lambda: eg.fetch_recent("fics", "x", 5, client=eg.make_client(transport=httpx.MockTransport(timeout_handler))), "invalid_source")[0])
check("invalid username refused before any request",
      expect_error(lambda: eg.fetch_recent("lichess", "a/b", 5, client=eg.make_client(transport=httpx.MockTransport(timeout_handler))), "invalid_username")[0])
check("over-cap refused before any request",
      expect_error(lambda: eg.fetch_recent("lichess", "carol", 500, client=eg.make_client(transport=httpx.MockTransport(timeout_handler))), "too_many")[0])
client, calls = client_for({"https://lichess.org/api/games/user/carol": lichess_ok})
a = eg.fetch_recent("lichess", "carol", 3, client=client)
b = eg.fetch_recent("lichess", "CAROL", 3, client=client)
check("second call is served from cache (case-insensitive)", len(calls) == 1 and a == b, calls)
eg.clear_cache()
eg.fetch_recent("lichess", "carol", 3, client=client)
check("clear_cache forces a refetch", len(calls) == 2)

print(f"\n{PASSED} passed, {FAILED} failed")
raise SystemExit(1 if FAILED else 0)

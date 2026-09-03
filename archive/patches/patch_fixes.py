#!/usr/bin/env python3
"""
Fixes two bugs in the chess AI platform:

1. langflow_service.py: _get_chess_flow_id() only retried for 15s total,
   which is way shorter than Langflow's real cold-start time (60-90s+ seen
   in the container logs). When the budget ran out it fell back to a
   hardcoded, no-longer-real flow_id, which 404'd on every move.
2. app.py: nothing stopped the background AI-move task and a manual
   "Make AI Move" click from both computing a move for the same turn.
   Whichever one applied second got rejected by game.make_langflow_move()
   because the board had already moved on - that's the source of the
   "Invalid AI move: c7c5 ... which is a valid move though" report: c7c5
   WAS legal when it was chosen, it just wasn't legal anymore by the time
   it tried to apply.

Run this from the repo root (same directory as app.py / langflow_service.py):
    python3 patch_fixes.py
"""
import sys

def patch_file(path, replacements):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    for old, new, label in replacements:
        count = src.count(old)
        if count != 1:
            print(f"❌ {path}: expected exactly 1 match for '{label}', found {count}. Aborting - no changes written.")
            sys.exit(1)
        src = src.replace(old, new, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✅ {path}: applied {len(replacements)} patch(es)")


# ---------------------------------------------------------------------------
# langflow_service.py
# ---------------------------------------------------------------------------
langflow_replacements = [
    (
        'class LangflowService:\n    def __init__(self, config: LangflowConfig):\n        self.config = config\n',
        'class LangflowService:\n    def __init__(self, config: LangflowConfig):\n        self.config = config\n        self._cached_flow_id: Optional[str] = None\n',
        "add flow_id cache field",
    ),
    (
'''            flow_id = await self._get_chess_flow_id()
            if not flow_id:
                flow_id = "8f2ac28a-4b1b-4bb0-8703-70e58cb00def"

            url = f"{self.config.url}/api/v1/run/{flow_id}"

            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["x-api-key"] = self.config.api_key

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 200:
                    result = response.json()
                    move_str = self._extract_uci_move(result, legal_moves)''',
'''            flow_id = await self._get_chess_flow_id()
            if not flow_id:
                return False, {
                    "outputs": {
                        "best_move": None,
                        "evaluation": 0.0,
                        "reasoning": "Chess flow not available yet (Langflow may still be starting up)"
                    },
                    "flow_used": "Langflow API (flow not ready)",
                    "fen": fen,
                    "legal_moves": legal_moves,
                    "error": "Chess flow not found"
                }

            url = f"{self.config.url}/api/v1/run/{flow_id}"

            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["x-api-key"] = self.config.api_key

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 200:
                    result = response.json()
                    move_str = self._extract_uci_move(result, legal_moves)''',
        "remove hardcoded fallback flow_id in generate_chess_move",
    ),
    (
'''            flow_id = await self._get_chess_flow_id()
            if not flow_id:
                flow_id = "8f2ac28a-4b1b-4bb0-8703-70e58cb00def"

            url = f"{self.config.url}/api/v1/run/{flow_id}"
            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["x-api-key"] = self.config.api_key

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code != 200:
                    return False, {"error": f"HTTP {response.status_code}"}''',
'''            flow_id = await self._get_chess_flow_id()
            if not flow_id:
                return False, {"error": "Chess flow not available yet (Langflow may still be starting up)"}

            url = f"{self.config.url}/api/v1/run/{flow_id}"
            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["x-api-key"] = self.config.api_key

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code != 200:
                    return False, {"error": f"HTTP {response.status_code}"}''',
        "remove hardcoded fallback flow_id in choose_from_candidates",
    ),
    (
'''    async def _get_chess_flow_id(self) -> Optional[str]:
        """Get Chess flow ID from Langflow with retry logic"""
        max_retries = 3
        retry_delay = 5

        for attempt in range(max_retries):
            try:
                headers = {"Content-Type": "application/json"}
                if self.config.api_key:
                    headers["x-api-key"] = self.config.api_key

                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{self.config.url}/api/v1/flows/",
                        headers=headers
                    )

                    if response.status_code == 200:
                        flows = response.json()
                        for flow in flows:
                            if flow.get("name") == "Chess":
                                logger.info(f"✅ Found Chess flow: {flow.get('id')}")
                                return flow.get("id")
                        logger.warning("⚠️ Chess flow not found in flows list")
                        return None
                    else:
                        logger.warning(f"⚠️ HTTP {response.status_code} when getting flows (attempt {attempt + 1})")

            except Exception as e:
                logger.warning(f"⚠️ Error getting Chess flow ID (attempt {attempt + 1}): {e}")

            if attempt < max_retries - 1:
                logger.info(f"🔄 Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)

        logger.error("❌ Failed to get Chess flow ID after all retries")
        return None''',
'''    async def _get_chess_flow_id(self) -> Optional[str]:
        """Get Chess flow ID from Langflow with retry logic.

        Langflow's own cold start (loading providers, importing the Chess
        flow) can easily take 60-90+ seconds inside a fresh container. A
        short retry budget here was causing us to give up early and fall
        back to a fake, hardcoded flow_id that no longer exists (HTTP 404) -
        that's why AI moves looked "slow" (wasted retries) and "invalid"
        (a legal move rejected because the run against the fake flow_id
        failed, not because the move itself was bad).
        """
        if self._cached_flow_id:
            return self._cached_flow_id

        max_retries = 30
        retry_delay = 5  # up to 150s total budget - covers slow cold starts

        for attempt in range(max_retries):
            try:
                headers = {"Content-Type": "application/json"}
                if self.config.api_key:
                    headers["x-api-key"] = self.config.api_key

                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{self.config.url}/api/v1/flows/",
                        headers=headers
                    )

                    if response.status_code == 200:
                        flows = response.json()
                        for flow in flows:
                            if flow.get("name") == "Chess":
                                logger.info(f"✅ Found Chess flow: {flow.get('id')}")
                                self._cached_flow_id = flow.get("id")
                                return self._cached_flow_id
                        logger.warning(f"⚠️ Chess flow not found yet (attempt {attempt + 1}/{max_retries}) - Langflow may still be importing it")
                    else:
                        logger.warning(f"⚠️ HTTP {response.status_code} when getting flows (attempt {attempt + 1}/{max_retries})")

            except Exception as e:
                logger.warning(f"⚠️ Error getting Chess flow ID (attempt {attempt + 1}/{max_retries}): {e}")

            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)

        logger.error("❌ Failed to get Chess flow ID after all retries - Langflow may still be starting up")
        return None''',
        "widen retry budget + cache flow_id",
    ),
]

# ---------------------------------------------------------------------------
# app.py
# ---------------------------------------------------------------------------
app_replacements = [
    (
        'ai_difficulty = 10\nDIFFICULTY_WINDOW_SIZE = 5\n',
        'ai_difficulty = 10\nDIFFICULTY_WINDOW_SIZE = 5\n\n'
        '# Guards against overlapping AI-move requests - e.g. the background task\n'
        '# scheduled by /api/move racing a manual /api/ai-move click, both computing\n'
        '# a move for the same turn. Whichever finishes second used to get rejected\n'
        '# by game.make_langflow_move() (board already advanced) and reported as an\n'
        '# "Invalid AI move" even though the move it picked was perfectly legal at\n'
        '# the moment it was chosen. Serializing on this lock (plus the staleness\n'
        '# check below) prevents that.\n'
        'ai_move_lock = asyncio.Lock()\n',
        "add ai_move_lock",
    ),
    (
'''async def make_ai_move_async():
    """Make AI move in background using the Stockfish-candidates + Gemini-choice flow"""
    try:
        logging.info("🤖 Background AI move starting...")

        current_fen = game.get_fen()
        legal_moves = game.get_legal_moves_uci()
        current_turn = game.get_current_turn()

        if current_turn != "black" or not legal_moves:
            logging.info(f"⚠️ Background AI move cancelled - turn: {current_turn}, moves: {len(legal_moves)}")
            return

        try:
            ai_move, explanation, source = await decide_ai_move(current_fen)
        except Exception as e:
            logging.error(f"❌ Background AI move decision failed: {e}")
            return

        move_result = game.make_langflow_move(ai_move, explanation=explanation)

        if move_result["success"]:
            logging.info(f"✅ Background AI move completed ({source}): {ai_move} - {explanation}")
        else:
            logging.error(f"❌ Background AI move invalid: {ai_move}")

    except Exception as e:
        logging.error(f"❌ Error in background AI move: {e}")''',
'''async def make_ai_move_async():
    """Make AI move in background using the Stockfish-candidates + Gemini-choice flow"""
    if ai_move_lock.locked():
        logging.info("⚠️ Background AI move skipped - an AI move is already in progress")
        return

    async with ai_move_lock:
        try:
            logging.info("🤖 Background AI move starting...")

            current_fen = game.get_fen()
            legal_moves = game.get_legal_moves_uci()
            current_turn = game.get_current_turn()

            if current_turn != "black" or not legal_moves:
                logging.info(f"⚠️ Background AI move cancelled - turn: {current_turn}, moves: {len(legal_moves)}")
                return

            try:
                ai_move, explanation, source = await decide_ai_move(current_fen)
            except Exception as e:
                logging.error(f"❌ Background AI move decision failed: {e}")
                return

            if game.get_current_turn() != "black" or game.get_fen() != current_fen:
                logging.info("ℹ️ Board changed while AI was thinking - discarding this move")
                return

            move_result = game.make_langflow_move(ai_move, explanation=explanation)

            if move_result["success"]:
                logging.info(f"✅ Background AI move completed ({source}): {ai_move} - {explanation}")
            else:
                logging.error(f"❌ Background AI move invalid: {ai_move}")

        except Exception as e:
            logging.error(f"❌ Error in background AI move: {e}")''',
        "serialize make_ai_move_async on ai_move_lock + staleness check",
    ),
    (
'''@app.post("/api/ai-move")
async def make_ai_move():
    """Manually trigger AI move using the Stockfish-candidates + Gemini-choice flow"""
    try:
        logger.info("🤖 Manual AI move requested")

        current_fen = game.get_fen()
        legal_moves = game.get_legal_moves_uci()
        current_turn = game.get_current_turn()

        logger.info(f"🎯 AI will play for {current_turn}")

        if current_turn != "black":
            return create_error_response("Not AI's turn", {
                "current_turn": current_turn,
                "message": "AI only plays as Black"
            })

        if not legal_moves:
            return create_error_response("No legal moves available", {
                "fen": current_fen,
                "game_over": game.is_game_over()
            })

        # Get Stockfish's full ranked list, window it by difficulty, then let Gemini choose from it
        try:
            ai_move, explanation, source = await decide_ai_move(current_fen)
        except Exception as e:
            logger.error(f"❌ AI move decision failed: {e}")
            return create_error_response("AI move generation failed", {
                "error": str(e),
                "fen": current_fen,
                "legal_moves": legal_moves
            })

        move_result = game.make_langflow_move(ai_move, explanation=explanation)

        if move_result["success"]:
            logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")

            return create_success_response(f"AI played {ai_move}", {
                "ai_move": ai_move,
                "reasoning": explanation,
                "source": source,
                "difficulty": ai_difficulty,
                "game_state": {
                    "fen": game.get_fen(),
                    "turn": game.get_current_turn(),
                    "is_game_over": game.is_game_over(),
                    "is_check": game.is_in_check(),
                    "is_checkmate": game.is_checkmate(),
                    "is_stalemate": game.is_stalemate(),
                    "legal_moves": game.get_legal_moves_uci()
                }
            })
        else:
            return create_error_response(f"Invalid AI move: {ai_move}", {
                "ai_move": ai_move,
                "error": move_result.get("message", "Unknown error")
            })

    except Exception as e:
        logger.error(f"❌ Error in manual AI move: {e}")
        return create_error_response("Error making AI move", {
            "error": str(e)
        })''',
'''@app.post("/api/ai-move")
async def make_ai_move():
    """Manually trigger AI move using the Stockfish-candidates + Gemini-choice flow"""
    if ai_move_lock.locked():
        return create_error_response("AI is already thinking", {
            "message": "An AI move is already in progress - please wait a moment and try again"
        })

    async with ai_move_lock:
        try:
            logger.info("🤖 Manual AI move requested")

            current_fen = game.get_fen()
            legal_moves = game.get_legal_moves_uci()
            current_turn = game.get_current_turn()

            logger.info(f"🎯 AI will play for {current_turn}")

            if current_turn != "black":
                return create_error_response("Not AI's turn", {
                    "current_turn": current_turn,
                    "message": "AI only plays as Black"
                })

            if not legal_moves:
                return create_error_response("No legal moves available", {
                    "fen": current_fen,
                    "game_over": game.is_game_over()
                })

            # Get Stockfish's full ranked list, window it by difficulty, then let Gemini choose from it
            try:
                ai_move, explanation, source = await decide_ai_move(current_fen)
            except Exception as e:
                logger.error(f"❌ AI move decision failed: {e}")
                return create_error_response("AI move generation failed", {
                    "error": str(e),
                    "fen": current_fen,
                    "legal_moves": legal_moves
                })

            if game.get_current_turn() != "black" or game.get_fen() != current_fen:
                logger.info("ℹ️ Board changed while AI was thinking - move no longer applies")
                return create_success_response("Board already advanced", {
                    "message": "The position changed while the AI was thinking (an auto-move likely already completed) - no action needed",
                    "game_state": {
                        "fen": game.get_fen(),
                        "turn": game.get_current_turn(),
                        "is_game_over": game.is_game_over(),
                        "is_check": game.is_in_check(),
                        "is_checkmate": game.is_checkmate(),
                        "is_stalemate": game.is_stalemate(),
                        "legal_moves": game.get_legal_moves_uci()
                    }
                })

            move_result = game.make_langflow_move(ai_move, explanation=explanation)

            if move_result["success"]:
                logger.info(f"✅ Manual AI move completed ({source}): {ai_move} - {explanation}")

                return create_success_response(f"AI played {ai_move}", {
                    "ai_move": ai_move,
                    "reasoning": explanation,
                    "source": source,
                    "difficulty": ai_difficulty,
                    "game_state": {
                        "fen": game.get_fen(),
                        "turn": game.get_current_turn(),
                        "is_game_over": game.is_game_over(),
                        "is_check": game.is_in_check(),
                        "is_checkmate": game.is_checkmate(),
                        "is_stalemate": game.is_stalemate(),
                        "legal_moves": game.get_legal_moves_uci()
                    }
                })
            else:
                return create_error_response(f"Invalid AI move: {ai_move}", {
                    "ai_move": ai_move,
                    "error": move_result.get("message", "Unknown error")
                })

        except Exception as e:
            logger.error(f"❌ Error in manual AI move: {e}")
            return create_error_response("Error making AI move", {
                "error": str(e)
            })''',
        "serialize /api/ai-move on ai_move_lock + staleness check",
    ),
]

patch_file("langflow_service.py", langflow_replacements)
patch_file("app.py", app_replacements)
print("🎉 All patches applied successfully.")

"""
Langflow service for chess move generation
"""

import uuid
import re
import httpx
import logging
import asyncio
from typing import Dict, List, Optional, Any, Tuple

from langflow_config import LangflowConfig

logger = logging.getLogger(__name__)

class LangflowService:
    def __init__(self, config: LangflowConfig):
        self.config = config
        self._cached_flow_id: Optional[str] = None

    async def generate_chess_move(self, fen: str, legal_moves: List[str]) -> Tuple[bool, Dict[str, Any]]:
        """Generate chess move using Langflow API"""
        try:
            input_text = f"Chess position analysis:\nFEN: {fen}\nLegal moves: {' '.join(legal_moves)}\nGame phase: {self._determine_game_phase(fen, len(legal_moves))}\nPlease suggest the best move in UCI format (e.g., e2e4)."

            chess_session_id = f"chess-{uuid.uuid4()}"
            payload = {
                "output_type": "chat",
                "input_type": "chat",
                "input_value": input_text,
                "session_id": chess_session_id
            }

            flow_id = await self._get_chess_flow_id()
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
                    move_str = self._extract_uci_move(result, legal_moves)
                    explanation = self._extract_explanation(result)

                    if move_str:
                        return True, {
                            "outputs": {
                                "best_move": move_str,
                                "evaluation": 0.0,
                                "reasoning": explanation
                            },
                            "flow_used": "Langflow API",
                            "fen": fen,
                            "legal_moves": legal_moves
                        }
                    else:
                        return False, {
                            "outputs": {
                                "best_move": None,
                                "evaluation": 0.0,
                                "reasoning": "Could not extract valid move from Langflow response"
                            },
                            "flow_used": "Langflow API (invalid response)",
                            "fen": fen,
                            "legal_moves": legal_moves,
                            "error": "Invalid move format in response"
                        }
                else:
                    error_text = ""
                    try:
                        error_data = response.json()
                        error_text = error_data.get("detail", error_data.get("message", f"HTTP {response.status_code}"))
                    except:
                        error_text = f"HTTP {response.status_code}: {response.text[:200]}"

                    return False, {
                        "outputs": {
                            "best_move": None,
                            "evaluation": 0.0,
                            "reasoning": f"Langflow API error - {error_text}"
                        },
                        "flow_used": "Langflow API (failed)",
                        "fen": fen,
                        "legal_moves": legal_moves,
                        "error": error_text
                    }

        except Exception as e:
            return False, {
                "outputs": {
                    "best_move": None,
                    "evaluation": 0.0,
                    "reasoning": f"Connection error: {str(e)}"
                },
                "flow_used": "Langflow API (exception)",
                "fen": fen,
                "legal_moves": legal_moves,
                "error": str(e)
            }

    async def choose_from_candidates(self, fen: str, candidates: list) -> Tuple[bool, Dict[str, Any]]:
        """Ask Gemini to pick one move from a Stockfish-ranked shortlist"""
        try:
            lines = []
            for i, c in enumerate(candidates):
                if c.get("score") is not None:
                    score_text = f"score: {c['score']}"
                else:
                    score_text = f"mate in {c['mate_in']}"
                lines.append(f"{i+1}. {c['move']} ({score_text})")
            candidates_text = "\n".join(lines)

            input_text = (
                f"Chess position (FEN): {fen}\n\n"
                f"Here are the top candidate moves, ranked by engine evaluation (higher score = better for you):\n"
                f"{candidates_text}\n\n"
                f"Choose ONE move from this exact list. Respond with ONLY the move in UCI format "
                f"(e.g. e2e4) on the first line, nothing else. On the next line, briefly explain your choice."
            )

            chess_session_id = f"chess-choice-{uuid.uuid4()}"
            payload = {
                "output_type": "chat",
                "input_type": "chat",
                "input_value": input_text,
                "session_id": chess_session_id
            }

            flow_id = await self._get_chess_flow_id()
            if not flow_id:
                return False, {"error": "Chess flow not available yet (Langflow may still be starting up)"}

            url = f"{self.config.url}/api/v1/run/{flow_id}"
            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["x-api-key"] = self.config.api_key

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)

                if response.status_code != 200:
                    return False, {"error": f"HTTP {response.status_code}"}

                result = response.json()
                candidate_ucis = [c["move"] for c in candidates]
                chosen_move = self._extract_uci_move(result, candidate_ucis)
                explanation = self._extract_explanation(result)

                if chosen_move:
                    return True, {"move": chosen_move, "explanation": explanation}
                else:
                    return False, {"error": "Could not extract valid move from response", "raw_explanation": explanation}

        except Exception as e:
            return False, {"error": str(e)}

    def _extract_uci_move(self, result: dict, legal_moves: List[str]) -> Optional[str]:
        """Extract UCI move from Langflow response"""
        try:
            if 'outputs' in result:
                for output in result['outputs']:
                    if 'outputs' in output:
                        for inner_output in output['outputs']:
                            if 'results' in inner_output and 'message' in inner_output['results']:
                                text = inner_output['results']['message'].get('text', '')
                                move_pattern = r'\b[a-h][1-8][a-h][1-8][qrbn]?\b'
                                matches = re.findall(move_pattern, text.lower())
                                for match in matches:
                                    if match in legal_moves:
                                        return match
            return None
        except Exception:
            return None

    def _extract_explanation(self, result: dict) -> str:
        """Extract explanation from Langflow response"""
        try:
            if 'outputs' in result:
                for output in result['outputs']:
                    if 'outputs' in output:
                        for inner_output in output['outputs']:
                            if 'results' in inner_output and 'message' in inner_output['results']:
                                return inner_output['results']['message'].get('text', '')
            return ""
        except Exception:
            return ""

    def _determine_game_phase(self, fen: str, legal_moves_count: int) -> str:
        """Determine game phase from FEN and move count"""
        parts = fen.split()
        fullmove_number = int(parts[5]) if len(parts) > 5 else 1

        if fullmove_number <= 10:
            return "opening"
        elif fullmove_number >= 40 or legal_moves_count < 15:
            return "endgame"
        else:
            return "middlegame"

    async def _get_chess_flow_id(self) -> Optional[str]:
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
        return None

class ChessLangflowManager:
    """High-level manager for chess-specific Langflow operations"""

    def __init__(self, config: LangflowConfig):
        self.service = LangflowService(config)

    async def get_best_move(self, fen: str, legal_moves: List[str]) -> Tuple[Optional[str], Dict[str, Any]]:
        """Get the best move for a chess position"""
        success, result = await self.service.generate_chess_move(fen, legal_moves)

        if success:
            output_data = result.get("outputs", {})
            best_move = output_data.get("best_move")
            return best_move, result
        else:
            return None, result

    async def choose_move_from_candidates(self, fen: str, candidates: list):
        """Ask Gemini to pick from a Stockfish-ranked shortlist"""
        success, result = await self.service.choose_from_candidates(fen, candidates)
        if success:
            return result.get("move"), result.get("explanation", ""), True
        else:
            return None, result.get("error", "Unknown error"), False

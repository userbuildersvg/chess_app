"""
Account preferences: settings that also need to follow a signed-in user.

WHY THIS EXISTS
---------------
Board theme, coordinates, engine numbers, move grading and the open rail
panel have always been `localStorage` keys. For a guest that is exactly
right - there is nowhere else to put them, and they should not outlive the
browser. For an account it was wrong in a way that only became obvious once
accounts were real: a person's games, profile and history now follow them to
another device, and their board would have arrived looking like a stranger's.

So: signed in, these live in Postgres and follow the account. Signed out,
`localStorage` still works exactly as before. The frontend picks which store
to use based on who it is talking to, and the answer to "what happens to a
guest's preferences" is unchanged - nothing, they stay in the browser.

AN ALLOWLIST, NOT A FREE-FORM DOCUMENT
--------------------------------------
The column is JSONB and the database constrains nothing that goes into it,
so this module is the constraint. `ALLOWED` names every key and the values it
may take, and `sanitise()` drops everything else. Two reasons that matters
more than it looks:

  * The write endpoint takes a JSON object from the browser. Without an
    allowlist, "preferences" becomes an arbitrary key-value store any signed-in
    user can fill with anything, at whatever size they like.
  * A preference the frontend has stopped using should stop being stored,
    and an allowlist makes that a deletion here rather than an archaeology
    exercise later.

Unknown keys are dropped silently rather than rejected. A browser running an
older build should not have its settings save fail because it sent a key this
version retired - it should simply have that key ignored.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import db

logger = logging.getLogger(__name__)

# name -> validator. Kept in step with the frontend's usePreferences hook; if
# you add one there, add it here or it will be silently dropped, which is the
# failure this comment exists to prevent.
ALLOWED = {
    # Which piece set and board colours (an id from pieceThemes.tsx).
    "pieceTheme": lambda v: isinstance(v, str) and 0 < len(v) <= 64,
    # File and rank labels around the board.
    "showCoordinates": lambda v: isinstance(v, bool),
    # The evaluation bar and its numbers.
    "showEngineNumbers": lambda v: isinstance(v, bool),
    # chess.com-style move grading badges.
    "showMoveQuality": lambda v: isinstance(v, bool),
    # Which rail panel was open, so a refresh does not dump you back on Coach.
    "activeSection": lambda v: isinstance(v, str) and 0 < len(v) <= 32,
    # Guided Play: after the AI moves, the coach also says what to watch for
    # before you reply (guided_play.py). A coaching preference, so it follows
    # the account like the display ones do.
    "guidedPlay": lambda v: isinstance(v, bool),
    # Communication style only. These values never enter engine or grading code.
    "coachBluntness": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 10,
    "coachCreativity": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 10,
    "coachStylePreset": lambda v: isinstance(v, str) and 0 < len(v) <= 64,
}

DEFAULTS = {
    "pieceTheme": None,
    "showCoordinates": True,
    "showEngineNumbers": False,
    "showMoveQuality": True,
    "activeSection": "analysis",
    "guidedPlay": False,
    "coachBluntness": 5,
    "coachCreativity": 5,
    "coachStylePreset": "balanced",
}


def sanitise(prefs) -> dict:
    """Only known keys, only valid values. Everything else is dropped."""
    if not isinstance(prefs, dict):
        return {}
    clean = {}
    for key, value in prefs.items():
        validator = ALLOWED.get(key)
        if validator is None:
            continue
        try:
            if validator(value):
                clean[key] = value
        except Exception:
            continue
    return clean


class SettingsService:
    def __init__(self, connect=None):
        # For tests, mirroring AuthService and LearningService.
        self._connect_fn = connect

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn()
        return db.connection()

    def get(self, user_id) -> dict:
        """
        This account's preferences, with defaults filled in.

        Always returns a complete set, so the frontend never has to reason
        about a half-populated object. An account that has never saved
        anything gets the defaults, which are the same values the app has
        always started with.
        """
        stored = {}
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT prefs FROM user_settings WHERE user_id = %s", (user_id,)
                ).fetchone()
            if row and row[0]:
                stored = sanitise(row[0])
        except Exception as e:
            # Preferences must never be the thing that stops someone playing.
            logger.warning(f"⚠️ Could not read settings for user {user_id}: {e}")
        merged = dict(DEFAULTS)
        merged.update(stored)
        return merged

    def update(self, user_id, prefs: dict) -> dict:
        """
        Merge `prefs` into this account's stored settings and return the
        result.

        A merge rather than a replace: the frontend saves the one preference
        that changed, and a replace would silently wipe the other four. The
        merge happens in Postgres (`prefs || excluded.prefs`) rather than in
        Python so that two tabs saving different preferences at the same
        moment cannot lose one to a read-modify-write race.
        """
        clean = sanitise(prefs)
        if not clean:
            return self.get(user_id)
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO user_settings (user_id, prefs, updated_at)"
                    " VALUES (%s, %s::jsonb, %s)"
                    " ON CONFLICT (user_id) DO UPDATE"
                    "   SET prefs = user_settings.prefs || excluded.prefs,"
                    "       updated_at = excluded.updated_at",
                    (user_id, json.dumps(clean), time.time()),
                )
        except Exception as e:
            logger.warning(f"⚠️ Could not save settings for user {user_id}: {e}")
        return self.get(user_id)


settings_service = SettingsService()

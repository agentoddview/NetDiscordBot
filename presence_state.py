# presence_state.py
"""
Simple in-memory tracking of which Discord users are currently in the Roblox game.

Used by:
- /startclock: via is_in_game(discord_id)
- /roblox/presence webhook: via mark_join / mark_leave

We keep it intentionally simple: the Roblox webhook tells us when a player joins
or leaves / becomes inactive, and we just mirror that here.
"""

from __future__ import annotations

import logging
from typing import Set

log = logging.getLogger(__name__)

# Set of Discord user IDs that are currently in the Roblox game.
_IN_GAME: Set[int] = set()


def mark_join(discord_user_id: int) -> None:
    """
    Mark a Discord user as being in the Roblox game.

    Called by the /roblox/presence webhook when Bloxlink successfully maps
    a Roblox userId to this Discord user and the event == "join".
    """
    if discord_user_id not in _IN_GAME:
        log.info("[presence] mark_join: %s", discord_user_id)
        _IN_GAME.add(discord_user_id)


def mark_leave(discord_user_id: int) -> None:
    """
    Mark a Discord user as no longer being in the Roblox game.

    Called by the /roblox/presence webhook when the event is "leave" or
    "inactive" (and Bloxlink has mapped the Roblox userId to this Discord user).
    """
    if discord_user_id in _IN_GAME:
        log.info("[presence] mark_leave: %s", discord_user_id)
        _IN_GAME.discard(discord_user_id)


def is_in_game(discord_user_id: int) -> bool:
    """
    Return True if this Discord user is currently marked as being in the game.

    /startclock uses this to enforce "you must be in the Roblox game to
    start your staff clock".
    """
    return discord_user_id in _IN_GAME

# presence_state.py
#
# Simple in-memory tracking of who is currently in-game.
# Used by the Roblox webhook handler (bot.py) and the shift_tracking cog.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict
import logging

log = logging.getLogger("presence_state")


@dataclass
class PresenceInfo:
    """Represents the presence state for a single Discord user."""
    discord_id: int
    in_game: bool = False


# Maps discord user ID -> PresenceInfo
_PRESENCE: Dict[int, PresenceInfo] = {}


def mark_join(discord_id: int) -> None:
    """Mark this Discord user as currently in the Roblox game."""
    info = _PRESENCE.get(discord_id)
    if info is None:
        info = PresenceInfo(discord_id=discord_id, in_game=True)
        _PRESENCE[discord_id] = info
    else:
        info.in_game = True

    log.info("[presence] mark_join: %s", discord_id)


def mark_leave(discord_id: int) -> None:
    """Mark this Discord user as no longer in the Roblox game."""
    info = _PRESENCE.get(discord_id)
    if info is None:
        info = PresenceInfo(discord_id=discord_id, in_game=False)
        _PRESENCE[discord_id] = info
    else:
        info.in_game = False

    log.info("[presence] mark_leave: %s", discord_id)


def is_in_game(discord_id: int) -> bool:
    """
    Return True if this Discord user is currently marked as in-game.

    This is what /startclock checks so we only allow starting a clock
    when the user is actually in the Roblox server.
    """
    info = _PRESENCE.get(discord_id)
    return bool(info and info.in_game)

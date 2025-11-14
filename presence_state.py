# presence_state.py

import logging

log = logging.getLogger("presence_state")

# discord_id -> in_game bool
_current_presence: dict[int, bool] = {}


def mark_join(discord_id: int) -> None:
    """Mark a Discord user as in-game."""
    discord_id = int(discord_id)
    _current_presence[discord_id] = True
    log.info("[presence] mark_join: %s", discord_id)


def mark_leave(discord_id: int) -> None:
    """Mark a Discord user as no longer in-game."""
    discord_id = int(discord_id)
    _current_presence[discord_id] = False
    log.info("[presence] mark_leave: %s", discord_id)


def is_in_game(discord_id: int) -> bool:
    """Return True if the user is currently marked as in-game."""
    discord_id = int(discord_id)
    return _current_presence.get(discord_id, False)

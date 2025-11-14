# presence_state.py
from typing import Set

# Set of Discord user IDs who are currently in the Roblox game
_IN_GAME_DISCORD_IDS: Set[int] = set()


def mark_join(discord_id: int) -> None:
    """Mark a Discord user as 'in game'."""
    _IN_GAME_DISCORD_IDS.add(discord_id)


def mark_leave(discord_id: int) -> None:
    """Mark a Discord user as having left the game."""
    _IN_GAME_DISCORD_IDS.discard(discord_id)


def is_in_game(discord_id: int) -> bool:
    """Return True if this Discord user is currently in the Roblox game."""
    return discord_id in _IN_GAME_DISCORD_IDS

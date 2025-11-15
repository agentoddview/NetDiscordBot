import logging

log = logging.getLogger("presence_state")

# Simple in-memory tracking of who is currently in-game.
_currently_present = set()  # set[int]

def mark_join(discord_id: int) -> None:
    """Mark this Discord user as present in the Roblox game."""
    try:
        did = int(discord_id)
    except (TypeError, ValueError):
        log.warning("[presence] mark_join called with non-int %r", discord_id)
        return
    _currently_present.add(did)
    log.info("[presence] mark_join: %s", did)

def mark_leave(discord_id: int) -> None:
    """Mark this Discord user as no longer present in the Roblox game."""
    try:
        did = int(discord_id)
    except (TypeError, ValueError):
        log.warning("[presence] mark_leave called with non-int %r", discord_id)
        return
    _currently_present.discard(did)
    log.info("[presence] mark_leave: %s", did)

def is_present(discord_id: int) -> bool:
    try:
        did = int(discord_id)
    except (TypeError, ValueError):
        return False
    return did in _currently_present

def debug_dump() -> set[int]:
    return set(_currently_present)

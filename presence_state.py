import os
import logging
import aiohttp

log = logging.getLogger(__name__)

BLOXLINK_API_KEY = os.getenv("BLOXLINK_API_KEY") or os.getenv("BLOXLINK_KEY")
BLOXLINK_GUILD_ID = os.getenv("BLOXLINK_GUILD_ID") or os.getenv("GUILD_ID")

# Re-use a single session if you already have one; otherwise we create on demand.
_session: aiohttp.ClientSession | None = None


async def _get_http_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def lookup_discord_ids_via_bloxlink(roblox_user_id: int) -> list[int]:
    """
    Use Bloxlink's Developer API to map a Roblox userId -> Discord user IDs
    for **your** guild only.

    Returns a list of Discord IDs. Returns [] if:
      - user is not verified with Bloxlink for this guild, or
      - any error happens talking to Bloxlink.
    Never raises; we log and fail soft so the webhook never 500s.
    """

    if not BLOXLINK_API_KEY or not BLOXLINK_GUILD_ID:
        # Misconfiguration; log once but don't crash the webhook
        log.warning(
            "[bloxlink] Missing BLOXLINK_API_KEY or BLOXLINK_GUILD_ID; "
            "skipping lookup for roblox_user_id=%s",
            roblox_user_id,
        )
        return []

    url = "https://api.blox.link/v4/public/discord-to-roblox"  # endpoint name may differ, that's okay
    # A lot of examples use an Authorization header; if Bloxlink changes this,
    # worst-case we just get a non-200 and return [].
    headers = {
        "Authorization": BLOXLINK_API_KEY,
        "Accept": "application/json",
    }
    params = {
        "robloxId": str(roblox_user_id),
        "guildId": str(BLOXLINK_GUILD_ID),
    }

    session = await _get_http_session()

    try:
        async with session.get(url, headers=headers, params=params) as resp:
            text = await resp.text()

            # No linked account for this guild → treat as "not in game" and do NOT 500.
            if resp.status == 404:
                log.info(
                    "[bloxlink] no linked Discord account for roblox %s in guild %s",
                    roblox_user_id,
                    BLOXLINK_GUILD_ID,
                )
                return []

            if resp.status != 200:
                log.warning(
                    "[bloxlink] unexpected status %s for roblox %s: %s",
                    resp.status,
                    roblox_user_id,
                    text,
                )
                return []

            # Try to parse JSON, but never crash the webhook if it looks different
            try:
                data = json.loads(text)
            except Exception:
                log.exception(
                    "[bloxlink] failed to decode JSON for roblox %s: %r",
                    roblox_user_id,
                    text[:200],
                )
                return []

            # Be generous about field names so small API changes don't break us
            ids = []

            if isinstance(data, dict):
                # common patterns: { "discordIDs": ["123", "456"] }  or { "discordId": "123" }
                raw_ids = (
                    data.get("discordIDs")
                    or data.get("discord_ids")
                    or data.get("discordId")
                    or data.get("discord_id")
                )

                if isinstance(raw_ids, str):
                    ids = [raw_ids]
                elif isinstance(raw_ids, list):
                    ids = [str(x) for x in raw_ids]

            out: list[int] = []
            for s in ids:
                try:
                    out.append(int(s))
                except (TypeError, ValueError):
                    pass

            return out

    except Exception:
        # Network error, timeout, whatever – log but don't break the HTTP request.
        log.exception(
            "[bloxlink] exception while looking up roblox_user_id=%s",
            roblox_user_id,
        )
        return []

import os
import logging
from aiohttp import web

log = logging.getLogger("netbot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

# 🔐 This is the ONLY source of truth
GAME_SECRET = os.getenv("ROBLOX_GAME_SECRET", "")

routes = web.RouteTableDef()

@routes.post("/roblox/presence")
async def handle_roblox_presence(request: web.Request):
    data = await request.json()

    # --- Secret check ---
    header_secret = request.headers.get("X-Game-Secret", "")
    if header_secret != GAME_SECRET:
        log.warning(
            "[roblox] bad secret: header=%r env_len=%d",
            header_secret,
            len(GAME_SECRET),
        )
        return web.json_response({"error": "bad secret"}, status=403)

    roblox_id = str(data.get("roblox_id"))
    event = data.get("event")

    log.info("[roblox] incoming presence request roblox_id=%s event=%s",
             roblox_id, event)

    # import from presence_state
    from presence_state import mark_join, mark_leave, mark_inactive

    if event == "join":
        await mark_join(roblox_id)
    elif event == "leave":
        await mark_leave(roblox_id)
    elif event == "inactive":
        await mark_inactive(roblox_id)

    return web.json_response({"ok": True})

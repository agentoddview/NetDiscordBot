import os
import sys
import logging
import asyncio
from typing import Optional

import aiohttp
from aiohttp import web
import discord
from discord.ext import commands

from presence_state import mark_join, mark_leave

# -------------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
log = logging.getLogger("netbot")

# -------------------------------------------------------------------------
# Config / env
# -------------------------------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_STR = os.getenv("GUILD_ID")
WEB_PORT_STR = os.getenv("WEB_PORT", "3000")
ROBLOX_GAME_SECRET = os.getenv("ROBLOX_GAME_SECRET")
BLOXLINK_API_KEY = os.getenv("BLOXLINK_API_KEY")
BLOXLINK_BASE_URL = os.getenv("BLOXLINK_BASE_URL", "https://api.blox.link/v4/public")

missing = []
if not DISCORD_TOKEN:
    missing.append("DISCORD_TOKEN")
if not GUILD_ID_STR:
    missing.append("GUILD_ID")
if not ROBLOX_GAME_SECRET:
    missing.append("ROBLOX_GAME_SECRET")
if not BLOXLINK_API_KEY:
    missing.append("BLOXLINK_API_KEY")

if missing:
    log.error("Missing required environment variables: %s", ", ".join(missing))
    sys.exit(1)

try:
    GUILD_ID = int(GUILD_ID_STR)
except ValueError:
    log.error("GUILD_ID must be an integer, got %r", GUILD_ID_STR)
    sys.exit(1)

try:
    WEB_PORT = int(WEB_PORT_STR)
except ValueError:
    log.error("WEB_PORT must be an integer, got %r", WEB_PORT_STR)
    sys.exit(1)

log.info("Starting bot for guild %s on web port %s", GUILD_ID, WEB_PORT)

# -------------------------------------------------------------------------
# Discord bot setup
# -------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.guild_id = GUILD_ID  # used by ShiftTracking


INITIAL_EXTENSIONS = [
    "cogs.net_commands",
    "cogs.shift_tracking",
    "cogs.loa",
    "cogs.config",
    "cogs.moderation",
    "cogs.gpcheck",
]


@bot.event
async def setup_hook():
    """Load cogs and sync slash commands to the guild."""
    for ext in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(ext)
            log.info("Loaded extension %s", ext)
        except Exception as exc:
            log.exception("Failed to load extension %s: %s", ext, exc)

    guild_obj = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild_obj)
    log.info("Synced application commands to guild %s", GUILD_ID)


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

# -------------------------------------------------------------------------
# Bloxlink helper
# -------------------------------------------------------------------------
async def get_discord_id_from_bloxlink(roblox_id: str) -> Optional[int]:
    """Look up the Discord ID for this roblox_id via Bloxlink."""
    url = f"{BLOXLINK_BASE_URL}/guilds/{GUILD_ID}/roblox-to-discord/{roblox_id}"
    headers = {"Authorization": BLOXLINK_API_KEY}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()
                if resp.status == 200:
                    try:
                        data = await resp.json()
                    except aiohttp.ContentTypeError:
                        log.warning("[bloxlink] non-JSON 200 response: %s", text)
                        return None

                    ids = (
                        data.get("discordIDs")
                        or data.get("discordIds")
                        or data.get("discordId")
                    )
                    if isinstance(ids, list) and ids:
                        return int(ids[0])
                    if isinstance(ids, str):
                        return int(ids)

                    log.warning("[bloxlink] 200 but no discordIDs in body: %s", data)
                    return None

                if resp.status == 404:
                    log.info(
                        "[bloxlink] roblox_id %s has no linked discord (404). body=%s",
                        roblox_id,
                        text,
                    )
                    return None

                log.warning(
                    "[bloxlink] error %s for roblox_id %s: %s",
                    resp.status,
                    roblox_id,
                    text,
                )
                return None

        except aiohttp.ClientError as e:
            log.warning("[bloxlink] network error looking up %s: %s", roblox_id, e)
            return None

# -------------------------------------------------------------------------
# Aiohttp web server
# -------------------------------------------------------------------------
routes = web.RouteTableDef()


@routes.post("/roblox/presence")
# --- Roblox presence webhook -------------------------------------------------

async def handle_roblox_presence(request: web.Request) -> web.Response:
    """Webhook from Roblox telling us join/leave/inactive for a roblox_id."""
    try:
        data = await request.json()
    except Exception:
        log.warning("[roblox] bad JSON payload from %s", request.remote)
        return web.json_response({"error": "invalid json"}, status=400)

    # Shared secret
    secret = request.headers.get("X-Game-Secret")
    if secret != ROBLOX_GAME_SECRET:
        log.warning("[roblox] bad secret from %s", request.remote)
        return web.json_response({"error": "bad secret"}, status=403)

    roblox_id = str(data.get("roblox_id") or "")
    event = data.get("event")

    log.info(
        "[roblox] incoming presence request roblox_id=%s event=%s",
        roblox_id,
        event,
    )

    if not roblox_id or event not in {"join", "leave", "inactive"}:
        return web.json_response({"error": "invalid payload"}, status=400)

    # Look up linked Discord ID via Bloxlink
    discord_id = await get_discord_id_from_bloxlink(roblox_id)
    if discord_id is None:
        # Player doesn't have a linked Discord account
        return web.json_response({"error": "no linked discord account"}, status=404)

    # Keep our in-memory "who is in game" map up to date
    if event == "join":
        mark_join(discord_id)
    else:
        # treat leave + inactive the same for presence
        mark_leave(discord_id)

        # Try auto-ending any active shift for this user
        cog = bot.get_cog("ShiftTracking")
        if cog is not None:
            try:
                await cog.auto_end_for_presence_leave(int(discord_id))
            except Exception:
                log.exception(
                    "[presence] auto_end_for_presence_leave failed for %s",
                    discord_id,
                )

    return web.json_response({"status": "ok"})

app = web.Application()
app.add_routes(routes)

# -------------------------------------------------------------------------
# Main entrypoint: run Discord bot and web server together
# -------------------------------------------------------------------------
async def main():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    log.info("[web] Listening on 0.0.0.0:%s", WEB_PORT)

    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")




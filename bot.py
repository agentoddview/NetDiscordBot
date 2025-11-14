import os
import json
import logging
import asyncio
import aiohttp
from aiohttp import web
import discord
from discord.ext import commands

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("netbot")

# -----------------------------
# ENVIRONMENT VARIABLES
# -----------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
WEB_PORT = int(os.getenv("WEB_PORT", "3000"))

BLOXLINK_API_KEY = os.getenv("BLOXLINK_API_KEY")  # REQUIRED

# -----------------------------
# DISCORD BOT SETUP
# -----------------------------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# State tracking
in_game_state = {}     # discord_id: timestamp
last_activity = {}     # discord_id: timestamp

# -----------------------------
# HELPERS FOR STATE
# -----------------------------
def mark_join(discord_id: int):
    log.info(f"[presence] mark_join {discord_id}")
    in_game_state[discord_id] = asyncio.get_event_loop().time()
    last_activity[discord_id] = asyncio.get_event_loop().time()

def mark_leave(discord_id: int):
    log.info(f"[presence] mark_leave {discord_id}")
    in_game_state.pop(discord_id, None)
    last_activity.pop(discord_id, None)

def is_in_game(discord_id: int) -> bool:
    return discord_id in in_game_state

# -----------------------------
# BLOXLINK: GET DISCORD ID
# -----------------------------
BLOXLINK_API_KEY = os.getenv("BLOXLINK_API_KEY")
BLOXLINK_BASE_URL = os.getenv("BLOXLINK_BASE_URL", "https://api.blox.link")


async def get_discord_id_from_bloxlink(roblox_id: int) -> int | None:
    """
    Look up Discord user ID(s) for a Roblox user ID via Bloxlink.

    Uses:
      GET https://api.blox.link/v4/public/guilds/:serverID/roblox-to-discord/:robloxID

    Returns:
      - first Discord ID as int, or
      - None if no link or any error.
    """
    if not BLOXLINK_API_KEY:
        log.warning("[bloxlink] BLOXLINK_API_KEY is not set; skipping lookup")
        return None

    # Build URL from docs:
    # https://api.blox.link/v4/public/guilds/:serverID/roblox-to-discord/:robloxID
    base = BLOXLINK_BASE_URL.rstrip("/")
    url = f"{base}/v4/public/guilds/{GUILD_ID}/roblox-to-discord/{roblox_id}"

    headers = {
        "Authorization": BLOXLINK_API_KEY,
        "Accept": "application/json",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()

                if resp.status == 404:
                    log.info(
                        "[bloxlink] roblox_id %s has no linked discord in guild %s (404). body=%s",
                        roblox_id,
                        GUILD_ID,
                        text[:200],
                    )
                    return None

                if resp.status != 200:
                    log.warning(
                        "[bloxlink] unexpected status %s for roblox_id %s; body=%s",
                        resp.status,
                        roblox_id,
                        text[:200],
                    )
                    return None

                try:
                    data = json.loads(text)
                except Exception:
                    log.warning(
                        "[bloxlink] JSON parse error for roblox_id %s; body=%s",
                        roblox_id,
                        text[:200],
                    )
                    return None

                # Per your screenshot: { "discordIDs": ["..."], "resolved": {} }
                ids = data.get("discordIDs") or data.get("discord_ids")
                if not isinstance(ids, list) or not ids:
                    log.warning(
                        "[bloxlink] no discordIDs array in response for roblox_id %s; data=%s",
                        roblox_id,
                        data,
                    )
                    return None

                # Just use the first ID
                first_id = ids[0]
                try:
                    return int(first_id)
                except (TypeError, ValueError):
                    log.warning(
                        "[bloxlink] unexpected discordID %r for roblox_id %s",
                        first_id,
                        roblox_id,
                    )
                    return None

    except aiohttp.ClientError as e:
        # Handles DNS / connection / SSL errors etc. without crashing the webhook
        log.warning(
            "[bloxlink] network error talking to %s for roblox_id %s: %r",
            url,
            roblox_id,
            e,
        )
    except Exception:
        log.exception("[bloxlink] unexpected error while looking up roblox_id %s", roblox_id)

    return None

# -----------------------------
# AIOHTTP WEB SERVER
# -----------------------------
routes = web.RouteTableDef()

@routes.post("/roblox/presence")
async def handle_roblox_presence(request):
    """
    Roblox server calls:
    POST /roblox/presence
    { "roblox_id": 123 }
    """
    data = await request.json()
    roblox_id = data.get("roblox_id")

    log.info(f"[roblox] incoming presence request roblox_id={roblox_id}")

    discord_id = await get_discord_id_from_bloxlink(roblox_id)

    if discord_id is None:
        return web.Response(status=404, text="no linked discord account")

    # Update presence timestamps
    last_activity[discord_id] = asyncio.get_event_loop().time()

    if not is_in_game(discord_id):
        mark_join(discord_id)

    return web.Response(status=200, text="ok")

# -----------------------------
# AUTO-AFK CHECKER
# -----------------------------
async def afk_loop():
    while True:
        now = asyncio.get_event_loop().time()

        to_remove = []

        for discord_id in list(in_game_state.keys()):
            last = last_activity.get(discord_id, 0)
            if now - last > 600:  # 10 minutes
                log.info(f"[presence] AFK logout {discord_id}")
                mark_leave(discord_id)

                guild = bot.get_guild(GUILD_ID)
                member = guild.get_member(discord_id)
                if member:
                    try:
                        await member.send(
                            "⏳ You were removed from your shift due to inactivity."
                        )
                    except:
                        pass

        await asyncio.sleep(30)

# -----------------------------
# DISCORD EVENTS
# -----------------------------
@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user}")
    asyncio.create_task(afk_loop())

# -----------------------------
# START WEB + DISCORD BOT
# -----------------------------
async def main():
    app = web.Application()
    app.add_routes(routes)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()

    log.info(f"[web] Listening on 0.0.0.0:{WEB_PORT}")

    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())


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
async def get_discord_id_from_bloxlink(roblox_id: int) -> int | None:
    """
    Returns Discord ID for a Roblox ID via Bloxlink.

    Always returns:
      - discord_id (int) or
      - None

    Never throws an exception.
    """

    if not BLOXLINK_API_KEY:
        log.warning("[bloxlink] Missing BLOXLINK_API_KEY!")
        return None

    url = "https://v3.blox.link/developer/discord"
    headers = {
        "Authorization": BLOXLINK_API_KEY,
        "Accept": "application/json",
    }
    params = {
        "robloxId": str(roblox_id),
        "guildId": str(GUILD_ID),
    }

    try:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=5,
                ) as resp:

                    text = await resp.text()

                    if resp.status == 404:
                        log.info(f"[bloxlink] No link for Roblox {roblox_id}")
                        return None

                    if resp.status != 200:
                        log.warning(
                            f"[bloxlink] Unexpected {resp.status}: {text[:200]}"
                        )
                        return None

                    try:
                        data = json.loads(text)
                    except Exception:
                        log.warning("[bloxlink] JSON decode error")
                        return None

                    raw_id = (
                        data.get("discordId")
                        or data.get("discord_id")
                        or data.get("id")
                    )

                    try:
                        return int(raw_id)
                    except Exception:
                        return None

            except asyncio.TimeoutError:
                log.warning("[bloxlink] Timeout")
                return None

    except aiohttp.ClientError as e:
        log.warning(f"[bloxlink] Network error: {e}")
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

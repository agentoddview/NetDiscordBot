import os
from pathlib import Path
import asyncio
import json

import aiohttp
from aiohttp import web
import discord
from discord.ext import commands
from dotenv import load_dotenv

from presence_state import mark_join, mark_leave

# ----------------- env + basic setup -----------------

load_dotenv(Path(__file__).parent / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set.")

# Shared secret used by Roblox game scripts
ROBLOX_GAME_SECRET = os.getenv("ROBLOX_GAME_SECRET", "")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

# NET guild ID (used for Bloxlink lookups)
NET_GUILD_ID = int(os.getenv("NET_GUILD_ID", "882441222487162912"))

# Bloxlink Developer API settings
BLOXLINK_API_KEY = os.getenv("BLOXLINK_API_KEY", "")
BLOXLINK_API_BASE = os.getenv("BLOXLINK_API_BASE", "https://v3.blox.link/developer")

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.reactions = True
intents.presences = True  # important for presence listener in ShiftTracking

bot = commands.Bot(command_prefix="!", intents=intents)


# ----------------- Bloxlink helper -----------------


async def get_discord_id_from_bloxlink(roblox_id: int) -> int | None:
    """
    Use Bloxlink Developer API to resolve a Roblox user ID to the linked
    Discord user for the NET guild.

    Returns Discord user ID as int, or None if not found.
    """
    if not BLOXLINK_API_KEY:
        print("[bloxlink] No API key configured")
        return None

    # NOTE: adjust this path to match your Bloxlink Dev API docs if needed.
    url = f"{BLOXLINK_API_BASE}/roblox/{roblox_id}"

    headers = {
        "Authorization": BLOXLINK_API_KEY,
        "Content-Type": "application/json",
    }
    params = {
        "guild": str(NET_GUILD_ID),
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            body = await resp.text()
            if resp.status != 200:
                print(f"[bloxlink] lookup failed ({resp.status}): {body}")
                return None

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                print("[bloxlink] invalid JSON response:", body)
                return None

            print(f"[bloxlink] response for Roblox {roblox_id}: {data}")

            # TODO: tweak this to match the exact structure you see printed
            # in logs from Bloxlink. This is a generic fallback.
            discord_id_str = str(
                data.get("discord_id")
                or data.get("id")
                or (data.get("user") or {}).get("id", "")
            )

            if not discord_id_str.isdigit():
                return None

            return int(discord_id_str)


# ----------------- Roblox webhook -----------------


async def handle_roblox_presence(request: web.Request) -> web.Response:
    """
    Endpoint called from Roblox:
    ...
    """
    print("[roblox] incoming presence request")

    # Auth check
    if ROBLOX_GAME_SECRET and request.headers.get("X-Game-Secret") != ROBLOX_GAME_SECRET:
        print("[roblox] bad or missing X-Game-Secret:", request.headers.get("X-Game-Secret"))
        return web.Response(text="unauthorized", status=401)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.Response(text="invalid json", status=400)

    event = (data.get("event") or "").lower()
    roblox_id_raw = data.get("roblox_id")

    if not roblox_id_raw or event not in {"join", "leave", "inactive"}:
        return web.Response(text="missing or invalid fields", status=400)

    try:
        roblox_id = int(roblox_id_raw)
    except (TypeError, ValueError):
        return web.Response(text="invalid roblox_id", status=400)

    # Roblox -> Discord via Bloxlink
    discord_id = await get_discord_id_from_bloxlink(roblox_id)
    if discord_id is None:
        return web.Response(text="no linked discord account", status=404)

    if event == "join":
        mark_join(discord_id)
        print(f"[roblox] join from Roblox {roblox_id} -> Discord {discord_id}")
        return web.Response(text="ok")

    if event == "leave":
        mark_leave(discord_id)
        print(f"[roblox] leave from Roblox {roblox_id} -> Discord {discord_id}")
        cog = bot.get_cog("ShiftTracking")
        if cog is not None:
            try:
                await cog.auto_end_for_inactivity(
                    discord_id,
                    reason="left the Roblox game",
                )
            except Exception as e:
                print(f"[roblox] auto_end_for_inactivity (leave) error: {e}")
        return web.Response(text="ok")

    # event == "inactive"
    print(f"[roblox] inactive from Roblox {roblox_id} -> Discord {discord_id}")
    cog = bot.get_cog("ShiftTracking")
    if cog is not None:
        try:
            await cog.auto_end_for_inactivity(
                discord_id,
                reason="were inactive in Roblox for 10 minutes",
            )
        except Exception as e:
            print(f"[roblox] auto_end_for_inactivity (inactive) error: {e}")
    return web.Response(text="ok")


async def start_webserver() -> None:
    """Start the aiohttp web server that Roblox talks to."""
    app = web.Application()
    app.add_routes([web.post("/roblox/presence", handle_roblox_presence)])

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    print(f"[web] Roblox presence webhook listening on 0.0.0.0:{WEB_PORT}")


# ----------------- Discord bot lifecycle -----------------


@bot.event
async def setup_hook():
    """Load cogs before the bot becomes ready."""
    initial_extensions = [
        "cogs.net_commands",  # slash commands + shift engine
        "cogs.shift_tracking",  # clock system
        "cogs.loa",  # LOA system
        "cogs.modlog",  # mod logs
        "cogs.config",  # /netconfig
        "cogs.moderation",  # /moderate
    ]

    for ext in initial_extensions:
        try:
            await bot.load_extension(ext)
            print(f"Loaded extension {ext}")
        except Exception as e:
            print(f"Failed to load extension {ext}: {e}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

    if not getattr(bot, "_webserver_started", False):
        bot.loop.create_task(start_webserver())
        bot._webserver_started = True


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")


if __name__ == "__main__":
    bot.run(TOKEN)


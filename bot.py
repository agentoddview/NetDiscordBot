# bot.py
import os
import json
from pathlib import Path

import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from aiohttp import web

from presence_state import mark_join, mark_leave

# ---------------- Env / basic setup ----------------

load_dotenv(Path(__file__).parent / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set.")

# Shared secret used by the Roblox server script
ROBLOX_GAME_SECRET = os.getenv("ROBLOX_GAME_SECRET", "")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

# Your NE Transit guild
GUILD_ID = 882441222487162912

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.reactions = True
intents.presences = True  # required for your presence listener

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Webhook from Roblox ----------------


async def handle_roblox_presence(request: web.Request) -> web.Response:
    """
    POST /roblox/presence
    JSON body:
        {
          "discord_id": "123456789012345678",
          "event": "join" | "leave" | "inactive"
        }
    Header:
        X-Game-Secret: <ROBLOX_GAME_SECRET>
    """
    # Simple shared-secret auth
    if ROBLOX_GAME_SECRET and request.headers.get("X-Game-Secret") != ROBLOX_GAME_SECRET:
        return web.Response(text="unauthorized", status=401)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.Response(text="invalid json", status=400)

    event = (data.get("event") or "").lower()
    discord_id_raw = data.get("discord_id")

    if not discord_id_raw or event not in {"join", "leave", "inactive"}:
        return web.Response(text="missing or invalid fields", status=400)

    try:
        discord_id = int(discord_id_raw)
    except (TypeError, ValueError):
        return web.Response(text="invalid discord_id", status=400)

    # Keep track of "who is in the game" for /startclock gating
    if event == "join":
        mark_join(discord_id)
        print(f"[roblox] join from {discord_id}")
        return web.Response(text="ok")

    if event == "leave":
        mark_leave(discord_id)
        print(f"[roblox] leave from {discord_id}")
        # Auto-end their clock if they had one
        cog = bot.get_cog("ShiftTracking")
        if cog is not None:
            try:
                await cog.auto_end_for_inactivity(
                    discord_id,
                    reason="left the Roblox game"
                )
            except Exception as e:
                print(f"[roblox] auto_end_for_inactivity (leave) error: {e}")
        return web.Response(text="ok")

    # event == "inactive"
    print(f"[roblox] inactive from {discord_id}")
    cog = bot.get_cog("ShiftTracking")
    if cog is not None:
        try:
            await cog.auto_end_for_inactivity(
                discord_id,
                reason="were inactive in Roblox for 10 minutes"
            )
        except Exception as e:
            print(f"[roblox] auto_end_for_inactivity (inactive) error: {e}")
    return web.Response(text="ok")


async def start_webserver() -> None:
    """Start a small aiohttp server for Roblox callbacks."""
    app = web.Application()
    app.add_routes([web.post("/roblox/presence", handle_roblox_presence)])

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    print(f"[web] Roblox presence webhook listening on 0.0.0.0:{WEB_PORT}")


# ---------------- Discord bot lifecycle ----------------

@bot.event
async def setup_hook():
    """Load cogs before the bot becomes ready."""
    initial_extensions = [
        "cogs.net_commands",    # /shift etc.
        "cogs.shift_tracking",  # clock system
        "cogs.loa",             # LOA system
        "cogs.modlog",          # mod logs
        "cogs.config",          # /netconfig
        "cogs.moderation",      # /moderate
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
    # Start the webserver exactly once
    if not getattr(bot, "_webserver_started", False):
        bot.loop.create_task(start_webserver())
        bot._webserver_started = True


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")


if __name__ == "__main__":
    bot.run(TOKEN)

import os
import asyncio
import logging
from typing import Optional

import aiohttp
from aiohttp import web

import discord
from discord.ext import commands

from dotenv import load_dotenv, find_dotenv

from presence_state import mark_join, mark_leave, is_in_game


# ------------------------------------------------------------
# basic setup / environment
# ------------------------------------------------------------

load_dotenv(find_dotenv())

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
logger = logging.getLogger("netbot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ROBLOX_GAME_SECRET = os.getenv("ROBLOX_GAME_SECRET")
WEB_PORT = int(os.getenv("WEB_PORT", "3000"))

# Discord guild we care about for slash commands + Bloxlink
GUILD_ID = int(os.getenv("GUILD_ID", "882441222487162912"))

# Bloxlink API
BLOXLINK_API_KEY = os.getenv("BLOXLINK_API_KEY")
# Usually the same as your Discord guild ID
BLOXLINK_GUILD_ID = os.getenv("BLOXLINK_GUILD_ID", str(GUILD_ID))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set")

if not ROBLOX_GAME_SECRET:
    raise RuntimeError("ROBLOX_GAME_SECRET is not set")


# ------------------------------------------------------------
# bot class (loads cogs + syncs slash commands)
# ------------------------------------------------------------

intents = discord.Intents.all()


class NetBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self) -> None:
        # Create a shared HTTP session for Bloxlink + web stuff
        self.http_session = aiohttp.ClientSession()

        # Load all the existing cogs (this is where /startclock lives)
        extensions = (
            "cogs.net_commands",
            "cogs.shift_tracking",
            "cogs.loa",
            "cogs.modlog",
            "cogs.config",
            "cogs.moderation",
        )

        for ext in extensions:
            try:
                await self.load_extension(ext)
                logger.info("Loaded extension %s", ext)
            except Exception:
                logger.exception("Failed to load extension %s", ext)

        # Sync app commands to the guild so /startclock etc. work
        guild_obj = discord.Object(id=GUILD_ID)
        await self.tree.sync(guild=guild_obj)
        logger.info("Synced application commands to guild %s", GUILD_ID)

    async def close(self) -> None:
        if self.http_session is not None:
            await self.http_session.close()
        await super().close()


bot = NetBot()


# ------------------------------------------------------------
# Bloxlink helper
# ------------------------------------------------------------

async def get_discord_id_from_bloxlink(roblox_id: int) -> Optional[int]:
    """
    Use Bloxlink v4 public API to resolve a Roblox ID to a Discord ID
    for *this* guild. Returns None if not linked.
    """
    if not BLOXLINK_API_KEY:
        # No key set, just behave as "no link"
        logger.warning("[bloxlink] BLOXLINK_API_KEY not set; skipping lookup")
        return None

    if bot.http_session is None:
        logger.warning("[bloxlink] HTTP session not ready")
        return None

    url = (
        f"https://api.blox.link/v4/public/guilds/"
        f"{BLOXLINK_GUILD_ID}/roblox-to-discord/{roblox_id}"
    )
    headers = {"Authorization": BLOXLINK_API_KEY}

    try:
        async with bot.http_session.get(url, headers=headers, timeout=8) as resp:
            text = await resp.text()

            if resp.status == 200:
                data = await resp.json()
                discord_ids = data.get("discordIDs") or []
                if not discord_ids:
                    return None
                # just take the first linked account
                return int(discord_ids[0])

            if resp.status == 404:
                logger.info(
                    "[bloxlink] roblox_id %s has no linked discord in guild %s (404). "
                    "body=%r",
                    roblox_id,
                    BLOXLINK_GUILD_ID,
                    text,
                )
                return None

            logger.warning(
                "[bloxlink] unexpected status %s for roblox_id %s. body=%r",
                resp.status,
                roblox_id,
                text,
            )
            return None

    except aiohttp.ClientError as e:
        logger.warning("[bloxlink] network error for roblox_id %s: %r", roblox_id, e)
        return None


# ------------------------------------------------------------
# Roblox presence webhook
# ------------------------------------------------------------

web_app = web.Application()


async def handle_roblox_presence(request: web.Request) -> web.Response:
    """
    Endpoint hit by the Roblox game.

    JSON body from Roblox script:

        {
            "secret": "<ROBLOX_GAME_SECRET>",
            "event": "join" | "leave",
            "roblox_id": <int>
        }
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    secret = data.get("secret")
    if secret != ROBLOX_GAME_SECRET:
        return web.json_response({"error": "bad secret"}, status=403)

    event = data.get("event")
    roblox_id = data.get("roblox_id")

    try:
        roblox_id = int(roblox_id)
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid roblox_id"}, status=400)

    logger.info("[roblox] incoming presence request roblox_id=%s", roblox_id)

    # Try to resolve Roblox → Discord via Bloxlink
    discord_id = await get_discord_id_from_bloxlink(roblox_id)

    if event == "join":
        if discord_id is None:
            # No linked Discord account for this Roblox user
            return web.json_response(
                {"error": "no linked discord account"}, status=404
            )

        # Mark them as "in game" so /startclock can check
        mark_join(discord_id)
        logger.info("[presence] mark_join %s", discord_id)
        return web.json_response({"ok": True})

    if event == "leave":
        if discord_id is not None:
            mark_leave(discord_id)
            logger.info("[presence] mark_leave %s", discord_id)
        return web.json_response({"ok": True})

    return web.json_response({"error": "unknown event"}, status=400)


web_app.router.add_post("/roblox/presence", handle_roblox_presence)


# ------------------------------------------------------------
# regular Discord events
# ------------------------------------------------------------

@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)


# ------------------------------------------------------------
# main entrypoint
# ------------------------------------------------------------

async def main() -> None:
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    logger.info("[web] Listening on 0.0.0.0:%s", WEB_PORT)

    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

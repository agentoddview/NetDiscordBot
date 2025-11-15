# cogs/gpcheck.py
from __future__ import annotations

import os
import logging
from typing import Optional, Dict

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
BLOXLINK_API_KEY = os.getenv("BLOXLINK_API_KEY")
BLOXLINK_BASE_URL = os.getenv(
    "BLOXLINK_BASE_URL",
    "https://api.blox.link/v4/public",
)

# ---------------------------------------------------------------------------
# CONFIG – replace these IDs with your real gamepass IDs
# ---------------------------------------------------------------------------

# Boston Bus Simulator gamepasses we care about
BBS_GAMEPASSES: Dict[int, str] = {
    1288712364: "Beta Teser Pack",        # TODO: replace with real ID + name
    1141373961: "Freedrive Access",
    1299347939: "2x Cash",
    1141341910: "Transit Police",
    1141911395: "Low Floor Access",
}

# If you want to support other games later, add more dicts like this and
# update the embed-building logic to show them.
# OTHER_GAMEPASSES: Dict[int, str] = { ... }

# Roblox inventory endpoint that works for checking ownership of a given asset.
# We'll use the legacy v1 inventory API:
#   https://inventory.roblox.com/v1/users/{userId}/items/GamePass/{gamePassId}
# Data array is non-empty if the user owns that gamepass. :contentReference[oaicite:0]{index=0}
INVENTORY_BASE_URL = "https://inventory.roblox.com/v1"


class GamepassCheck(commands.Cog):
    """Slash command /gpcheck that verifies ownership of configured gamepasses."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---------------------------------------------------------- helpers

    async def _get_roblox_id_from_bloxlink(self, discord_id: int) -> Optional[int]:
        """Resolve Discord user -> Roblox userId using Bloxlink server API."""
        if not BLOXLINK_API_KEY:
            log.warning("BLOXLINK_API_KEY is not set; /gpcheck will not work.")
            return None
        if not GUILD_ID:
            log.warning("GUILD_ID is not set; /gpcheck will not work.")
            return None

        url = (
            f"{BLOXLINK_BASE_URL}/guilds/{GUILD_ID}"
            f"/discord-to-roblox/{discord_id}"
        )
        headers = {"Authorization": BLOXLINK_API_KEY}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    text = await resp.text()

                    if resp.status == 200:
                        try:
                            data = await resp.json()
                        except aiohttp.ContentTypeError:
                            log.warning(
                                "[bloxlink] non-JSON 200 response for %s: %s",
                                discord_id,
                                text,
                            )
                            return None

                        # Example body from docs:
                        # { "robloxID": "146941966", "resolved": { ... } }
                        roblox_id_str = data.get("robloxID")
                        if not roblox_id_str:
                            log.warning(
                                "[bloxlink] 200 but no robloxID in body for %s: %s",
                                discord_id,
                                data,
                            )
                            return None

                        try:
                            return int(roblox_id_str)
                        except (TypeError, ValueError):
                            log.warning(
                                "[bloxlink] robloxID not an int for %s: %r",
                                discord_id,
                                roblox_id_str,
                            )
                            return None

                    if resp.status == 404:
                        # User just isn't linked.
                        log.info(
                            "[bloxlink] discord_id %s has no linked roblox (404). body=%s",
                            discord_id,
                            text,
                        )
                        return None

                    log.warning(
                        "[bloxlink] error %s looking up discord_id %s: %s",
                        resp.status,
                        discord_id,
                        text,
                    )
                    return None

            except aiohttp.ClientError as e:
                log.warning(
                    "[bloxlink] network error looking up discord_id %s: %s",
                    discord_id,
                    e,
                )
                return None

    async def _user_owns_gamepass(
        self,
        roblox_user_id: int,
        gamepass_id: int,
    ) -> Optional[bool]:
        """
        Return True/False if we can tell whether the user owns the gamepass,
        or None if the API call failed.
        """
        url = (
            f"{INVENTORY_BASE_URL}/users/{roblox_user_id}"
            f"/items/GamePass/{gamepass_id}"
        )

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=10) as resp:
                    text = await resp.text()

                    if resp.status == 200:
                        try:
                            data = await resp.json()
                        except aiohttp.ContentTypeError:
                            log.warning(
                                "[roblox inventory] non-JSON 200 response for user %s, gp %s: %s",
                                roblox_user_id,
                                gamepass_id,
                                text,
                            )
                            return None

                        # For this endpoint, "data" is a list. If it's empty,
                        # the user doesn't own the gamepass. :contentReference[oaicite:1]{index=1}
                        owned = bool(data.get("data"))
                        return owned

                    # 4xx/5xx – just log and return None so we can show "Unknown"
                    log.warning(
                        "[roblox inventory] error %s for user %s gp %s: %s",
                        resp.status,
                        roblox_user_id,
                        gamepass_id,
                        text,
                    )
                    return None

            except aiohttp.ClientError as e:
                log.warning(
                    "[roblox inventory] network error for user %s gp %s: %s",
                    roblox_user_id,
                    gamepass_id,
                    e,
                )
                return None

    # ---------------------------------------------------------- slash command

    @app_commands.command(
        name="gpcheck",
        description="Check configured gamepasses for a user's linked Roblox account.",
    )
    @app_commands.describe(user="Discord user to check")
    async def gpcheck(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """
        Usage: /gpcheck @User
        - Looks up their Roblox ID via Bloxlink
        - Checks ownership of each configured BBS gamepass
        - Responds with an embed showing ✅ / ❌ / ⚠️ for each pass
        """
        # Make sure we're in the configured guild
        if interaction.guild is None or interaction.guild.id != GUILD_ID:
            await interaction.response.send_message(
                "This command is not configured for this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Step 1 – Discord -> Roblox via Bloxlink
        roblox_id = await self._get_roblox_id_from_bloxlink(user.id)
        if roblox_id is None:
            await interaction.followup.send(
                f"❌ Could not find a linked Roblox account for {user.mention} "
                "via Bloxlink. Ask them to verify with Bloxlink first.",
                ephemeral=True,
            )
            return

        # Step 2 – check each configured gamepass
        lines = []
        for gp_id, gp_name in BBS_GAMEPASSES.items():
            owned = await self._user_owns_gamepass(roblox_id, gp_id)
            if owned is True:
                emoji = "✅"
                status = "Owned"
            elif owned is False:
                emoji = "❌"
                status = "Not owned"
            else:
                emoji = "⚠️"
                status = "Unknown (API error)"

            lines.append(f"{emoji} **{gp_name}** (`{gp_id}`) — {status}")

        if not lines:
            lines.append("_No gamepasses configured in the bot yet._")

        description = (
            f"Checking configured gamepasses for {user.mention}.\n"
            f"**Roblox user ID:** `{roblox_id}`\n\n"
            "**Boston Bus Simulator gamepasses:**\n"
            + "\n".join(lines)
        )

        embed = discord.Embed(
            title="Gamepass Check",
            description=description,
            colour=discord.Colour.blurple(),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------------------------------------------------------- cog lifecycle

    async def cog_load(self) -> None:
        if not GUILD_ID:
            log.warning(
                "GUILD_ID is not set; /gpcheck will not be registered."
            )

    # No extra teardown needed
    async def cog_unload(self) -> None:
        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GamepassCheck(bot))

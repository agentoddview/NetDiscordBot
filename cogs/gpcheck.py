# cogs/gpcheck.py
from __future__ import annotations

import os
import logging
from typing import Optional, Dict, Tuple

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

INVENTORY_BASE_URL = "https://inventory.roblox.com/v1"
ROBLOX_USERS_API = "https://users.roblox.com/v1"
ROBLOX_THUMBNAILS_API = "https://thumbnails.roblox.com/v1"

# ---------------------------------------------------------------------------
# ROLE CONFIG – Supervisor+
# ---------------------------------------------------------------------------

SUPERVISOR_ROLE_ID = 947288094804176957
SENIOR_SUPERVISOR_ROLE_ID = 1393088300239159467
LEAD_SUPERVISOR_ROLE_ID = 1351333124965142600

SUPERVISOR_PLUS_ROLES = {
    SUPERVISOR_ROLE_ID,
    SENIOR_SUPERVISOR_ROLE_ID,
    LEAD_SUPERVISOR_ROLE_ID,
}


def is_supervisor_plus():
    """App command check that ensures the user has Supervisor+."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("You must be Supervisor+ to use this command.")

        member: discord.Member = interaction.user
        if any(role.id in SUPERVISOR_PLUS_ROLES for role in member.roles):
            return True

        raise app_commands.CheckFailure("You must be Supervisor+ to use this command.")

    return app_commands.check(predicate)


# ---------------------------------------------------------------------------
# GAMEPASS CONFIG – fill these with your real IDs
# ---------------------------------------------------------------------------

# Boston Bus Simulator gamepasses
BBS_GAMEPASSES: Dict[int, str] = {
    1288712364: "Beta Teser Pack",
    1141373961: "Freedrive Access",
    1299347939: "2x Cash",
    1141341910: "Transit Police",
    1141911395: "Low Floor Access",
}

# Other game (e.g. under your boss’s account / group)
OTHER_GAMEPASSES: Dict[int, str] = {
    10454022: "WRTA Unlock All",
    1021966268: "WRTA TPD",
}


class GamepassCheck(commands.Cog):
    """Slash command /gpcheck that verifies ownership of configured gamepasses."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    # ---------------------------------------------------------- cog lifecycle

    async def cog_load(self) -> None:
        """Register /gpcheck as a guild command, like the shift commands, and create HTTP session."""
        if not GUILD_ID:
            log.warning(
                "GUILD_ID is not set; /gpcheck will not be registered."
            )
        else:
            guild_obj = discord.Object(id=GUILD_ID)
            self.bot.tree.add_command(self.gpcheck, guild=guild_obj)
            log.info(
                "Registered /gpcheck for guild %s via GamepassCheck.cog_load",
                GUILD_ID,
            )

        # Create a shared aiohttp session for this cog
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        # Close shared HTTP session on unload
        if self.session and not self.session.closed:
            await self.session.close()

    # ---------------------------------------------------------- helpers

    async def _get_roblox_id_from_bloxlink(self, discord_id: int) -> Optional[int]:
        """Resolve Discord user -> Roblox userId using Bloxlink server API."""
        if not BLOXLINK_API_KEY:
            log.warning("BLOXLINK_API_KEY is not set; /gpcheck will not work.")
            return None
        if not GUILD_ID:
            log.warning("GUILD_ID is not set; /gpcheck will not work.")
            return None

        session = self.session
        if session is None or session.closed:
            log.warning("[bloxlink] HTTP session is not available.")
            return None

        url = (
            f"{BLOXLINK_BASE_URL}/guilds/{GUILD_ID}"
            f"/discord-to-roblox/{discord_id}"
        )
        headers = {"Authorization": BLOXLINK_API_KEY}

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
        session = self.session
        if session is None or session.closed:
            log.warning("[roblox inventory] HTTP session is not available.")
            return None

        url = (
            f"{INVENTORY_BASE_URL}/users/{roblox_user_id}"
            f"/items/GamePass/{gamepass_id}"
        )

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

                    # v1/items endpoint: "data" is a list; non-empty means owned.
                    owned = bool(data.get("data"))
                    return owned

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

    async def _get_roblox_profile(
        self,
        roblox_user_id: int,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Fetch (username, display_name, avatar_url) for a Roblox user.
        avatar_url may be None if the thumbnail API fails.
        """
        username: Optional[str] = None
        display_name: Optional[str] = None
        avatar_url: Optional[str] = None

        session = self.session
        if session is None or session.closed:
            log.warning("[roblox profile] HTTP session is not available.")
            return username, display_name, avatar_url

        # 1) Get username / display name
        user_url = f"{ROBLOX_USERS_API}/users/{roblox_user_id}"
        try:
            async with session.get(user_url, timeout=10) as resp:
                text = await resp.text()
                if resp.status == 200:
                    try:
                        data = await resp.json()
                    except aiohttp.ContentTypeError:
                        log.warning(
                            "[roblox users] non-JSON 200 for user %s: %s",
                            roblox_user_id,
                            text,
                        )
                    else:
                        username = data.get("name")
                        display_name = data.get("displayName") or username
                else:
                    log.warning(
                        "[roblox users] error %s for user %s: %s",
                        resp.status,
                        roblox_user_id,
                        text,
                    )
        except aiohttp.ClientError as e:
            log.warning(
                "[roblox users] network error for user %s: %s",
                roblox_user_id,
                e,
            )

        # 2) Get avatar thumbnail
        thumb_url = (
            f"{ROBLOX_THUMBNAILS_API}/users/avatar-headshot"
            f"?userIds={roblox_user_id}&size=150x150&format=Png&isCircular=false"
        )
        try:
            async with session.get(thumb_url, timeout=10) as resp:
                text = await resp.text()
                if resp.status == 200:
                    try:
                        data = await resp.json()
                    except aiohttp.ContentTypeError:
                        log.warning(
                            "[roblox thumbs] non-JSON 200 for user %s: %s",
                            roblox_user_id,
                            text,
                        )
                    else:
                        items = data.get("data") or []
                        if items:
                            avatar_url = items[0].get("imageUrl")
                else:
                    log.warning(
                        "[roblox thumbs] error %s for user %s: %s",
                        resp.status,
                        roblox_user_id,
                        text,
                    )
        except aiohttp.ClientError as e:
            log.warning(
                "[roblox thumbs] network error for user %s: %s",
                roblox_user_id,
                e,
            )

        return username, display_name, avatar_url

    # ---------------------------------------------------------- slash command

    @is_supervisor_plus()
    @app_commands.command(
        name="gpcheck",
        description="Check configured gamepasses for a user's linked Roblox account.",
    )
    @app_commands.describe(user="Discord user to check")
    @app_commands.guild_only()
    async def gpcheck(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """
        Usage: /gpcheck @User
        - Looks up their Roblox ID via Bloxlink
        - Pulls their Roblox username, profile link, and avatar icon
        - Checks ownership of configured BBS + other gamepasses
        - Posts a visible embed in the channel
        """
        if interaction.guild is None or interaction.guild.id != GUILD_ID:
            await interaction.response.send_message(
                "This command is not configured for this server.",
                ephemeral=True,
            )
            return

        # Non-ephemeral so everyone in the ticket/channel can see it
        await interaction.response.defer(ephemeral=False)

        # Step 1 – Discord -> Roblox via Bloxlink
        roblox_id = await self._get_roblox_id_from_bloxlink(user.id)
        if roblox_id is None:
            await interaction.followup.send(
                f"❌ Could not find a linked Roblox account for {user.mention} "
                "via Bloxlink. Ask them to verify with Bloxlink first.",
                ephemeral=False,
            )
            return

        # Step 2 – Get Roblox profile info
        username, display_name, avatar_url = await self._get_roblox_profile(roblox_id)
        profile_url = f"https://www.roblox.com/users/{roblox_id}/profile"

        # Step 3 – check each configured gamepass
        bbs_lines = []
        other_lines = []

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

            bbs_lines.append(f"{emoji} **{gp_name}** (`{gp_id}`) — {status}")

        for gp_id, gp_name in OTHER_GAMEPASSES.items():
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

            other_lines.append(f"{emoji} **{gp_name}** (`{gp_id}`) — {status}")

        # Step 4 – Build embed
        if display_name or username:
            name_line = display_name or username
            profile_line = f"[{name_line}]({profile_url})"
        else:
            profile_line = profile_url

        header = (
            f"Checking configured gamepasses for {user.mention}.\n"
            f"**Roblox user ID:** `{roblox_id}`\n"
            f"**Roblox profile:** {profile_line}\n\n"
        )

        body_parts = []

        if bbs_lines:
            body_parts.append("**Boston Bus Simulator gamepasses:**")
            body_parts.append("\n".join(bbs_lines))

        if other_lines:
            body_parts.append("")
            body_parts.append("**Other gamepasses:**")
            body_parts.append("\n".join(other_lines))

        if not bbs_lines and not other_lines:
            body_parts.append("_No gamepasses configured in the bot yet._")

        description = header + "\n".join(body_parts)

        embed = discord.Embed(
            title="Gamepass Check",
            description=description,
            colour=discord.Colour.blurple(),
        )

        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GamepassCheck(bot))

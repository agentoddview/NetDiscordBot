# cogs/shift_tracking.py

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Optional, Dict, List

import discord
from discord import app_commands
from discord.ext import commands

from presence_state import is_in_game

import logging
import os

log = logging.getLogger(__name__)
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# --- Data structures --------------------------------------------------------


@dataclass
class ActiveShift:
    user_id: int
    started_at: dt.datetime
    channel_id: int
    message_id: Optional[int] = None
    manual: bool = False  # True if started with /startclock, False if auto


# In-memory shift store:
# key = discord user id, value = ActiveShift
ACTIVE_SHIFTS: Dict[int, ActiveShift] = {}


# --- Helper functions -------------------------------------------------------


def utcnow() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def fmt_dt(d: dt.datetime) -> str:
    return discord.utils.format_dt(d, style="t")


def fmt_duration(seconds: int) -> str:
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    parts: List[str] = []
    if hrs:
        parts.append(f"{hrs}h")
    if mins:
        parts.append(f"{mins}m")
    if sec and not parts:
        # Only show seconds if duration is < 1 minute
        parts.append(f"{sec}s")
    return " ".join(parts) or "0s"


# --- Cog --------------------------------------------------------------------


class ShiftTracking(commands.Cog):
    """Handles staff shift clocks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

class ShiftTracking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        """Ensure slash commands are registered for the guild."""
        if not GUILD_ID:
            log.warning("GUILD_ID is not set; shift commands will not be registered.")
            return

        guild_obj = discord.Object(id=GUILD_ID)

        # These are the @app_commands.command methods defined below
        self.bot.tree.add_command(self.startclock, guild=guild_obj)
        self.bot.tree.add_command(self.endclock, guild=guild_obj)

        log.info(
            "Registered /startclock and /endclock for guild %s via ShiftTracking.cog_load",
            GUILD_ID,
        )

    # ------------------------------------------------------------------ utils

    async def _ensure_guild(self, interaction: discord.Interaction) -> discord.Guild:
        if interaction.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")
        return interaction.guild

    async def _fetch_member(self, guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                return None
        return member

    # ---------------------------------------------------------- presence hook

    # -------------------------------------------------------------- presence hook

    async def auto_end_for_presence_leave(self, discord_user_id: int) -> None:
        """
        Automatically end a user's active shift when Roblox presence
        reports that they left the game.
    
        This only checks whether the user currently has an active shift.
        If they do, we end it and DM them. If not, we just return.
        """
    
        # 1) If they don't have an active shift, nothing to do.
        shift = ACTIVE_SHIFTS.get(discord_user_id)
        if shift is None:
            log.debug("[presence] auto-end: user %s has no active shift", discord_user_id)
            return
    
        # 2) Get guild
        guild = await self._ensure_guild(None)
    
        # 3) Fetch member
        member = guild.get_member(discord_user_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_user_id)
            except discord.HTTPException:
                log.warning(
                    "[presence] auto-end: failed to fetch member %s in guild %s",
                    discord_user_id,
                    guild.id,
                )
                return
    
        log.info(
            "[presence] auto-end: ending shift for %s because they left the Roblox game",
            member.id,
        )
    
        # 4) End shift
        await self._end_shift(member, reason="Left Roblox game (auto)")
    
        # 5) DM user
        try:
            await member.send(
                "⏰ Your staff clock has been automatically ended because you left the Roblox game."
            )
        except discord.Forbidden:
            log.info(
                "[presence] auto-end: unable to DM %s (DMs disabled or blocked)",
                member.id,
            )

    # -------------------------------------------------------------- core logic

    async def _start_shift(
        self,
        member: discord.Member,
        channel: discord.TextChannel,
        manual: bool,
        interaction: Optional[discord.Interaction] = None,
    ) -> None:
        existing = ACTIVE_SHIFTS.get(member.id)
        if existing:
            if interaction:
                await interaction.response.send_message(
                    f"❌ {member.mention} already has an active shift "
                    f"(started {fmt_dt(existing.started_at)}).",
                    ephemeral=True,
                )
            return

        started_at = utcnow()
        ACTIVE_SHIFTS[member.id] = ActiveShift(
            user_id=member.id,
            started_at=started_at,
            channel_id=channel.id,
            manual=manual,
        )

        log.info("Shift started: user=%s manual=%s", member.id, manual)

        if interaction:
            await interaction.response.send_message(
                f"✅ Shift started for {member.mention} at {fmt_dt(started_at)}.",
                ephemeral=True,
            )

    async def _end_shift(self, member: discord.Member, reason: str) -> None:
        shift = ACTIVE_SHIFTS.pop(member.id, None)
        if not shift:
            return

        ended_at = utcnow()
        delta = int((ended_at - shift.started_at).total_seconds())

        guild = member.guild
        channel = guild.get_channel(shift.channel_id)
        if isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="Shift Ended",
                description=(
                    f"**Staff:** {member.mention}\n"
                    f"**Started:** {fmt_dt(shift.started_at)}\n"
                    f"**Ended:** {fmt_dt(ended_at)}\n"
                    f"**Duration:** {fmt_duration(delta)}\n"
                    f"**Reason:** {reason}"
                ),
                colour=discord.Colour.blurple(),
                timestamp=ended_at,
            )
            await channel.send(embed=embed)

        log.info(
            "Shift ended: user=%s duration=%ss reason=%s",
            member.id,
            delta,
            reason,
        )

    # ------------------------------------------------------------- slash cmds

    @app_commands.command(name="startclock", description="Start your staff clock (must be in Roblox game).")
    async def startclock(self, interaction: discord.Interaction) -> None:
        guild = await self._ensure_guild(interaction)
        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)

        if member is None:
            await interaction.response.send_message("Could not resolve your member object.", ephemeral=True)
            return

        # Check presence_state: you must be in the Roblox game
        if not is_in_game(member.id):
            await interaction.response.send_message(
                "❌ I don't see you in the Roblox game. Join the game first, "
                "then run `/startclock` again.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Please use this command in a text channel.", ephemeral=True
            )
            return

        await self._start_shift(member, channel, manual=True, interaction=interaction)

    @app_commands.command(name="endclock", description="End your current staff clock.")
    async def endclock(self, interaction: discord.Interaction) -> None:
        guild = await self._ensure_guild(interaction)
        member = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)

        if member is None:
            await interaction.response.send_message("Could not resolve your member object.", ephemeral=True)
            return

        shift = ACTIVE_SHIFTS.get(member.id)
        if not shift:
            await interaction.response.send_message(
                "You don't have an active shift.", ephemeral=True
            )
            return

        await self._end_shift(member, reason="Manual /endclock")
        await interaction.response.send_message("✅ Your shift has been ended.", ephemeral=True)

    # ---------------------------------------------------------- admin helpers

    @app_commands.command(name="clockreset", description="(Lead+) Force end a user's shift.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clockreset(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        guild = await self._ensure_guild(interaction)
        if user.guild != guild:
            await interaction.response.send_message(
                "That user is not in this server.", ephemeral=True
            )
            return

        if user.id not in ACTIVE_SHIFTS:
            await interaction.response.send_message(
                "That user does not have an active shift.", ephemeral=True
            )
            return

        await self._end_shift(user, reason=f"Force-ended by {interaction.user.mention}")
        await interaction.response.send_message(
            f"✅ Force-ended shift for {user.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShiftTracking(bot))

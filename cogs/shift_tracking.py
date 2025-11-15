# cogs/shift_tracking.py
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, Dict, List

import os
import logging

import discord
from discord import app_commands
from discord.ext import commands

from presence_state import is_in_game
from database import get_connection

log = logging.getLogger(__name__)
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ActiveShift:
    user_id: int
    started_at: dt.datetime
    channel_id: int
    message_id: Optional[int] = None
    manual: bool = False  # True if started with /startclock, False if auto


# key = discord user id, value = ActiveShift
ACTIVE_SHIFTS: Dict[int, ActiveShift] = {}


class ClockStatusView(discord.ui.View):
    """Buttons for starting or ending a shift from /clock."""

    def __init__(self, cog: "ShiftTracking", member: discord.Member, has_active_shift: bool) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.member_id = member.id
        self.has_active_shift = has_active_shift

        if has_active_shift:
            end_button = discord.ui.Button(
                label="End Shift",
                style=discord.ButtonStyle.danger,
            )
            end_button.callback = self._end_shift_callback
            self.add_item(end_button)
        else:
            start_button = discord.ui.Button(
                label="Start Shift",
                style=discord.ButtonStyle.success,
            )
            start_button.callback = self._start_shift_callback
            self.add_item(start_button)

    async def _ensure_same_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "You can't control someone else's shift from this panel.",
                ephemeral=True,
            )
            return False
        return True

    async def _start_shift_callback(self, interaction: discord.Interaction) -> None:
        # Only the original user can use this
        if not await self._ensure_same_user(interaction):
            return

        guild = await self.cog._ensure_guild(interaction)
        member = guild.get_member(self.member_id)
        if member is None:
            await interaction.response.send_message(
                "Could not resolve your member profile.",
                ephemeral=True,
            )
            return

        # Re-check that they don't already have an active shift
        if member.id in ACTIVE_SHIFTS:
            await interaction.response.send_message(
                "You already have an active shift.",
                ephemeral=True,
            )
            return

        # Must be in the Roblox game
        if not is_in_game(member.id):
            await interaction.response.send_message(
                "You must be in the Roblox game to start your staff shift.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Please use this in a text channel.",
                ephemeral=True,
            )
            return

        await self.cog._start_shift(member, channel, manual=True, interaction=interaction)

    async def _end_shift_callback(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_same_user(interaction):
            return

        guild = await self.cog._ensure_guild(interaction)
        member = guild.get_member(self.member_id)
        if member is None:
            await interaction.response.send_message(
                "Could not resolve your member profile.",
                ephemeral=True,
            )
            return

        if member.id not in ACTIVE_SHIFTS:
            await interaction.response.send_message(
                "You don't have an active shift.",
                ephemeral=True,
            )
            return

        await self.cog._end_shift(
            member,
            reason="Ended via /clock panel",
        )
        await interaction.response.send_message(
            "✅ Your shift has been ended.",
            ephemeral=True,
        )


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
    # only show seconds if < 1 minute total
    if sec and not parts:
        parts.append(f"{sec}s")
    return " ".join(parts) or "0s"


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class ShiftTracking(commands.Cog):
    """Handles staff shift clocks and auto-end from Roblox presence."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---------------------------------------------------------- cog lifecycle

    async def cog_load(self) -> None:
        """Register slash commands for the specific guild."""
        if not GUILD_ID:
            log.warning(
                "GUILD_ID is not set; shift commands will not be registered."
            )
            return

        guild_obj = discord.Object(id=GUILD_ID)
        self.bot.tree.add_command(self.clock, guild=guild_obj)
        self.bot.tree.add_command(self.startclock, guild=guild_obj)
        self.bot.tree.add_command(self.endclock, guild=guild_obj)
        self.bot.tree.add_command(self.clockreset, guild=guild_obj)

        log.info(
            "Registered shift commands (/clock, /startclock, /endclock, /clockreset) "
            "for guild %s via ShiftTracking.cog_load",
            GUILD_ID,
        )

    # --------------------------------------------------------------- utilities

    async def _get_guild(self) -> Optional[discord.Guild]:
        """Resolve the configured guild from the bot cache or API."""
        if not GUILD_ID:
            return None

        guild = self.bot.get_guild(GUILD_ID)
        if guild is not None:
            return guild

        try:
            guild = await self.bot.fetch_guild(GUILD_ID)
        except discord.HTTPException:
            return None
        return guild

    async def _ensure_guild(
        self, interaction: Optional[discord.Interaction]
    ) -> discord.Guild:
        """
        For slash commands: make sure we know which guild to operate in.
        For presence webhooks: interaction will be None; we just use GUILD_ID.
        """
        guild = await self._get_guild()
        if guild is None:
            raise RuntimeError("Unable to resolve configured GUILD_ID")

        # For interactions, sanity-check that the command is actually in this guild.
        if interaction is not None and interaction.guild is not None:
            if interaction.guild.id != guild.id:
                raise commands.CheckFailure(
                    "This command is not configured for this server."
                )
        return guild

    async def _fetch_member(
        self, guild: discord.Guild, user_id: int
    ) -> Optional[discord.Member]:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    # ------------------------------------------------------------------ presence hook

    async def auto_end_for_presence_leave(self, discord_user_id: int) -> None:
        """Called by the /roblox/presence webhook when a user leaves/inactive."""

        shift = ACTIVE_SHIFTS.get(discord_user_id)
        if not shift:
            # No active shift for this user, nothing to do
            log.debug(
                "[presence] auto-end: user %s has no active shift",
                discord_user_id,
            )
            return

        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            log.warning(
                "[presence] auto-end: guild %s not found",
                self.bot.guild_id,
            )
            return

        member = guild.get_member(discord_user_id)
        if not member:
            log.warning(
                "[presence] auto-end: member %s not found in guild",
                discord_user_id,
            )
            return

        log.info(
            "[presence] auto-end: ending shift for %s because they left/AFK'd in game",
            member.id,
        )

        await self._end_shift(
            member,
            reason="Left or became inactive in the Roblox game (presence webhook)",
            dm_user=True,
        )

    # -------------------------------------------------------------- core logic

    async def _count_shift_moderations(
        self,
        guild_id: int,
        moderator_id: int,
        started_at: dt.datetime,
        until: Optional[dt.datetime] = None,
    ) -> int:
        """
        Return how many *unique players* this moderator has moderated
        between started_at and until (inclusive).
        """
        if until is None:
            until = utcnow()

        # Use ISO8601 strings so lexical order matches chronological order.
        start_iso = started_at.isoformat()
        end_iso = until.isoformat()

        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT target_roblox_id)
                    FROM moderations
                    WHERE guild_id = ?
                      AND moderator_id = ?
                      AND created_at >= ?
                      AND created_at <= ?
                    """,
                    (guild_id, moderator_id, start_iso, end_iso),
                )
                row = cur.fetchone()
        except Exception:
            # If for some reason the DB is unavailable, just treat as 0
            logging.exception("Failed to count moderations for shift")
            return 0

        if row is None or row[0] is None:
            return 0
        return int(row[0])

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

    async def _end_shift(
        self,
        member: discord.Member,
        reason: str,
        *,
        dm_user: bool = False,
    ) -> None:
        """
        Core helper to end a shift, log it, and announce it.

        If dm_user is True, the same "Shift Ended" embed will be sent as a DM
        to the staff member (used for auto-end via presence).
        """
        shift = ACTIVE_SHIFTS.pop(member.id, None)
        if not shift:
            return

        ended_at = utcnow()
        delta = int((ended_at - shift.started_at).total_seconds())

        # Build the embed once so we can reuse it in the channel and DMs.
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

        guild = member.guild
        channel = guild.get_channel(shift.channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(embed=embed)

        if dm_user:
            try:
                await member.send(embed=embed)
            except discord.HTTPException:
                # Can't DM them (DMs closed, blocked, etc.) – not fatal.
                log.info("Failed to DM shift-ended embed to %s", member.id)

        log.info(
            "Shift ended: user=%s duration=%ss reason=%s",
            member.id,
            delta,
            reason,
        )

    # -------------------------------------------------------------- slash cmds

    @app_commands.command(
        name="clock",
        description="Check your current shift and moderation stats.",
    )
    async def clock(self, interaction: discord.Interaction) -> None:
        """Show how long you've been on shift + how many people you've moderated."""
        guild = await self._ensure_guild(interaction)

        # Resolve the member object for the user invoking the command.
        if isinstance(interaction.user, discord.Member):
            member: Optional[discord.Member] = interaction.user
        else:
            member = guild.get_member(interaction.user.id)

        if member is None:
            await interaction.response.send_message(
                "Could not resolve your member profile in this guild.",
                ephemeral=True,
            )
            return

        now = utcnow()
        shift = ACTIVE_SHIFTS.get(member.id)

        if shift:
            duration_seconds = int((now - shift.started_at).total_seconds())
            moderated_count = await self._count_shift_moderations(
                guild.id,
                member.id,
                shift.started_at,
                now,
            )
            embed = discord.Embed(
                title="Current Shift Status",
                description=(
                    f"**Staff:** {member.mention}\n"
                    f"**Started:** {fmt_dt(shift.started_at)}\n"
                    f"**Duration:** {fmt_duration(duration_seconds)}\n"
                    f"**Players moderated this shift:** {moderated_count}"
                ),
                colour=discord.Colour.blurple(),
                timestamp=now,
            )
            has_active_shift = True
        else:
            embed = discord.Embed(
                title="No Active Shift",
                description=(
                    "You are not currently on a staff shift.\n"
                    "Use the button below to start your shift while you are in-game."
                ),
                colour=discord.Colour.blurple(),
                timestamp=now,
            )
            has_active_shift = False

        view = ClockStatusView(self, member, has_active_shift=has_active_shift)
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="startclock",
        description="Start your staff clock (must be in the Roblox game).",
    )
    async def startclock(self, interaction: discord.Interaction) -> None:
        guild = await self._ensure_guild(interaction)
        member: Optional[discord.Member]

        if isinstance(interaction.user, discord.Member):
            member = interaction.user
        else:
            member = guild.get_member(interaction.user.id)

        if member is None:
            await interaction.response.send_message(
                "Could not resolve your member object.", ephemeral=True
            )
            return

        # Must be in the Roblox game (presence_state)
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

        await self._start_shift(
            member, channel, manual=True, interaction=interaction
        )

    @app_commands.command(
        name="endclock",
        description="End your current staff clock.",
    )
    async def endclock(self, interaction: discord.Interaction) -> None:
        guild = await self._ensure_guild(interaction)

        if isinstance(interaction.user, discord.Member):
            member: Optional[discord.Member] = interaction.user
        else:
            member = guild.get_member(interaction.user.id)

        if member is None:
            await interaction.response.send_message(
                "Could not resolve your member object.", ephemeral=True
            )
            return

        if member.id not in ACTIVE_SHIFTS:
            await interaction.response.send_message(
                "You don't have an active shift.", ephemeral=True
            )
            return

        await self._end_shift(member, reason="Manual /endclock")
        await interaction.response.send_message(
            "✅ Your shift has been ended.", ephemeral=True
        )

    @app_commands.command(
        name="clockreset",
        description="Force-end another user's clock (staff only).",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clockreset(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        guild = await self._ensure_guild(interaction)
        if user.guild.id != guild.id:
            await interaction.response.send_message(
                "That user is not in this server.", ephemeral=True
            )
            return

        if user.id not in ACTIVE_SHIFTS:
            await interaction.response.send_message(
                "That user does not have an active shift.", ephemeral=True
            )
            return

        await self._end_shift(
            user, reason=f"Force-ended by {interaction.user.mention}"
        )
        await interaction.response.send_message(
            f"✅ Force-ended shift for {user.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShiftTracking(bot))

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from typing import List

from database import get_connection, init_db

GUILD_ID = 882441222487162912  # NE Transit guild

# Roles
SUPERVISOR_ROLE_ID = 947288094804176957          # "Supervisor"
SENIOR_SUPERVISOR_ROLE_ID = 1393088300239159467  # can use /clockadmin + /clockadjust
LEAD_SUPERVISOR_ROLE_ID = 1351333124965142600    # can use /clockreset

# Quota threshold (seconds)
WEEKLY_QUOTA_SECONDS = 4 * 60 * 60  # 4 hours


class ClockAdminView(discord.ui.View):
    """Ephemeral admin panel allowing Senior Supervisors to end other users' clocks."""

    def __init__(self, cog: "ShiftTracking", guild: discord.Guild, rows):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild

        options: List[discord.SelectOption] = []
        for row in rows:
            user_id = row["user_id"]
            start_dt = datetime.fromisoformat(row["start_time"])
            member = guild.get_member(user_id)
            name = member.display_name if member else str(user_id)
            label = f"{name} – since {start_dt.strftime('%Y-%m-%d %H:%M UTC')}"
            desc = f"User ID: {user_id}"
            options.append(
                discord.SelectOption(
                    label=label[:100], description=desc[:100], value=str(user_id)
                )
            )

        options = options[:25]  # Discord select max

        select = discord.ui.Select(
            placeholder="Select a user to end their clock",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.on_select  # type: ignore
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        user_id = int(self.children[0].values[0])
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Guild not found for this interaction.", ephemeral=True
            )
            return

        ok, duration = self.cog._force_end_shift(guild.id, user_id)
        if not ok:
            await interaction.response.send_message(
                "That user does not have an active clock.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"⏱ Ended clock for <@{user_id}>.\n"
            f"Duration this shift: **{duration}**",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False
            ),
        )


class ShiftTracking(commands.Cog):
    """Slash-command based clock system for staff."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()  # ensure tables exist

    # ---------- helpers: formatting ----------

    @staticmethod
    def _format_duration(seconds: int) -> str:
        minutes, sec = divmod(max(0, seconds), 60)
        hours, minutes = divmod(minutes, 60)
        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if sec or not parts:
            parts.append(f"{sec}s")
        return " ".join(parts)

    # ---------- helpers: DB access ----------

    def _get_open_shift(self, guild_id: int, user_id: int):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, start_time
                FROM shifts
                WHERE user_id = ? AND guild_id = ? AND end_time IS NULL
                """,
                (user_id, guild_id),
            )
            return cur.fetchone()

    def _end_shift(self, guild_id: int, user_id: int):
        """End a user's current shift. Returns (ok, duration_str or None)."""
        row = self._get_open_shift(guild_id, user_id)
        if not row:
            return False, None

        now_dt = datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(row["start_time"])
        duration_sec = int((now_dt - start_dt).total_seconds())
        duration_str = self._format_duration(duration_sec)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE shifts SET end_time = ? WHERE id = ?",
                (now_dt.isoformat(), row["id"]),
            )
            conn.commit()

        return True, duration_str

    def _force_end_shift(self, guild_id: int, user_id: int):
        """Admin version of end_shift (same logic, separated for clarity)."""
        return self._end_shift(guild_id, user_id)

    # ----- weekly period helpers -----

    def _get_reset_time(self, guild_id: int) -> datetime:
        """Return the start of the current period for this guild."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT reset_at FROM clock_periods WHERE guild_id = ?",
                (guild_id,),
            )
            row = cur.fetchone()

        if row and row["reset_at"]:
            return datetime.fromisoformat(row["reset_at"])

        # Default: very old time so everything counts until first reset
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _set_reset_time(self, guild_id: int, when: datetime):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO clock_periods (guild_id, reset_at)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    reset_at = excluded.reset_at
                """,
                (guild_id, when.isoformat()),
            )
            conn.commit()

    def _get_adjustment_seconds(
        self, guild_id: int, user_id: int, since: datetime
    ) -> int:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COALESCE(SUM(seconds), 0) AS total
                FROM clock_adjustments
                WHERE guild_id = ?
                  AND user_id = ?
                  AND created_at >= ?
                """,
                (guild_id, user_id, since.isoformat()),
            )
            row = cur.fetchone()
        return int(row["total"] if row and row["total"] is not None else 0)

    def _add_adjustment(
        self, guild_id: int, user_id: int, seconds: int, when: datetime
    ):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO clock_adjustments (user_id, guild_id, seconds, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, guild_id, seconds, when.isoformat()),
            )
            conn.commit()

    def _get_period_total_seconds(self, guild_id: int, user_id: int) -> int:
        """Total clocked seconds for current period (shifts + adjustments)."""
        period_start = self._get_reset_time(guild_id)
        now_dt = datetime.now(timezone.utc)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT start_time, end_time
                FROM shifts
                WHERE user_id = ?
                  AND guild_id = ?
                  AND start_time >= ?
                """,
                (user_id, guild_id, period_start.isoformat()),
            )
            rows = cur.fetchall()

        total_seconds = 0
        for row in rows:
            start_dt = datetime.fromisoformat(row["start_time"])
            if row["end_time"] is None:
                end_dt = now_dt
            else:
                end_dt = datetime.fromisoformat(row["end_time"])
            total_seconds += int((end_dt - start_dt).total_seconds())

        total_seconds += self._get_adjustment_seconds(
            guild_id, user_id, period_start
        )
        return total_seconds

    # ---------- slash commands ----------

    @app_commands.command(
        name="startclock",
        description="Start your staff clock for the current server.",
    )
    @app_commands.guild_only()
    async def startclock(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        user = interaction.user

        if self._get_open_shift(guild.id, user.id):
            await interaction.response.send_message(
                "⏱ You already have an active clock. Use `/endclock` first.",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO shifts (user_id, guild_id, start_time)
                VALUES (?, ?, ?)
                """,
                (user.id, guild.id, now),
            )
            conn.commit()

        local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await interaction.response.send_message(
            f"✅ Your clock has started at **{local}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="endclock",
        description="End your current staff clock and see your duration.",
    )
    @app_commands.guild_only()
    async def endclock(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        ok, duration_str = self._end_shift(guild.id, interaction.user.id)
        if not ok:
            await interaction.response.send_message(
                "❌ You do not have an active clock. Use `/startclock` first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Your clock has ended.\n⏱ This shift: **{duration_str}**",
            ephemeral=True,
        )

    @app_commands.command(
        name="clocktotal",
        description=(
            "View total clocked time for this period (quota week) "
            "for yourself or another member."
        ),
    )
    @app_commands.describe(
        member="Optional: pick a member to view their total. Defaults to yourself."
    )
    @app_commands.guild_only()
    async def clocktotal(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        target = member or interaction.user
        total_seconds = self._get_period_total_seconds(guild.id, target.id)
        duration_str = self._format_duration(total_seconds)

        period_start = self._get_reset_time(guild.id)
        msg = (
            f"📊 Clocked time for **{target.display_name}** "
            f"(since {period_start.strftime('%Y-%m-%d')}): **{duration_str}**"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="clockadmin",
        description="Open the clock admin panel to end active clocks.",
    )
    @app_commands.guild_only()
    async def clockadmin(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        member = interaction.user
        assert isinstance(member, discord.Member)
        has_role = any(r.id == SENIOR_SUPERVISOR_ROLE_ID for r in member.roles)
        if not (has_role or member.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ You must be a Senior Supervisor to use `/clockadmin`.",
                ephemeral=True,
            )
            return

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id, start_time
                FROM shifts
                WHERE guild_id = ? AND end_time IS NULL
                ORDER BY start_time ASC
                """,
                (guild.id,),
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                "There are currently **no active clocks** in this server.",
                ephemeral=True,
            )
            return

        view = ClockAdminView(self, guild, rows)
        await interaction.response.send_message(
            "### Clock Admin Panel\n"
            "Select a user below to end their active clock.\n"
            "This menu is visible only to you.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="clockboard",
        description=(
            "Show all supervisors and their clocked hours for this period, "
            "with who has met the 4-hour quota."
        ),
    )
    @app_commands.guild_only()
    async def clockboard(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        # Collect all members that are Supervisor or Senior Supervisor
        supervisors: list[discord.Member] = []
        for m in guild.members:
            if m.bot:
                continue
            role_ids = {r.id for r in m.roles}
            if (
                SUPERVISOR_ROLE_ID in role_ids
                or SENIOR_SUPERVISOR_ROLE_ID in role_ids
            ):
                supervisors.append(m)

        if not supervisors:
            await interaction.response.send_message(
                "No supervisors found in this server.",
                ephemeral=True,
            )
            return

        period_start = self._get_reset_time(guild.id)

        board = []
        for m in supervisors:
            total_secs = self._get_period_total_seconds(guild.id, m.id)
            board.append((m, total_secs))

        # Sort by most time clocked
        board.sort(key=lambda x: x[1], reverse=True)

        lines = []
        for member, secs in board:
            duration_str = self._format_duration(secs)
            met_quota = secs >= WEEKLY_QUOTA_SECONDS
            status = "✅ Met quota" if met_quota else "❌ Below 4 hours"
            lines.append(f"**{member.display_name}** — {duration_str} • {status}")

        embed = discord.Embed(
            title="Supervisor Clock Board",
            description="\n".join(lines)[:4000],
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Current period since {period_start.strftime('%Y-%m-%d')} • Quota: 4h"
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clockadjust",
        description=(
            "Set a member's total clocked hours for this period "
            "(Senior Supervisor+)."
        ),
    )
    @app_commands.describe(
        member="Member whose hours you want to adjust.",
        hours="New total hours for this period (e.g., 4, 4.5, 6).",
    )
    @app_commands.guild_only()
    async def clockadjust(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        hours: float,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        actor = interaction.user
        assert isinstance(actor, discord.Member)
        has_role = any(r.id == SENIOR_SUPERVISOR_ROLE_ID for r in actor.roles)
        if not (has_role or actor.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ You must be a Senior Supervisor to use `/clockadjust`.",
                ephemeral=True,
            )
            return

        if hours < 0:
            await interaction.response.send_message(
                "Hours cannot be negative.", ephemeral=True
            )
            return

        current_secs = self._get_period_total_seconds(guild.id, member.id)
        target_secs = int(hours * 3600)
        delta = target_secs - current_secs

        now = datetime.now(timezone.utc)
        self._add_adjustment(guild.id, member.id, delta, now)

        new_total_secs = self._get_period_total_seconds(guild.id, member.id)
        await interaction.response.send_message(
            f"✅ Set **{member.display_name}**'s total for this period to "
            f"**{self._format_duration(new_total_secs)}** "
            f"({hours:.2f} hours).",
            ephemeral=True,
        )

    @app_commands.command(
        name="clockreset",
        description=(
            "Reset the weekly clock period (used after checking quota). "
            "Lead Supervisor+ only."
        ),
    )
    @app_commands.guild_only()
    async def clockreset(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        actor = interaction.user
        assert isinstance(actor, discord.Member)
        has_role = any(r.id == LEAD_SUPERVISOR_ROLE_ID for r in actor.roles)
        if not (has_role or actor.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ You must be a Lead Supervisor to use `/clockreset`.",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        self._set_reset_time(guild.id, now)

        await interaction.response.send_message(
            f"✅ Clock period has been **reset**.\n"
            f"New period start: `{now.strftime('%Y-%m-%d %H:%M:%S UTC')}`.\n"
            "All further clock time will count toward the new week.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    cog = ShiftTracking(bot)
    await bot.add_cog(cog)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.startclock, guild=guild)
    bot.tree.add_command(cog.endclock, guild=guild)
    bot.tree.add_command(cog.clocktotal, guild=guild)
    bot.tree.add_command(cog.clockadmin, guild=guild)
    bot.tree.add_command(cog.clockboard, guild=guild)
    bot.tree.add_command(cog.clockadjust, guild=guild)
    bot.tree.add_command(cog.clockreset, guild=guild)

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from typing import List

from database import get_connection, init_db
from presence_state import is_in_game

GUILD_ID = 882441222487162912  # NE Transit guild

# Roles
SUPERVISOR_ROLE_ID = 947288094804176957  # "Supervisor"
SENIOR_SUPERVISOR_ROLE_ID = 1393088300239159467  # Senior Supervisor
LEAD_SUPERVISOR_ROLE_ID = 1351333124965142600  # Lead Supervisor
ONLINE_ROLE_ID = 1392996333073203211  # in-game online role

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
                    label=label[:100],
                    description=desc[:100],
                    value=str(user_id),
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


class ClockManageView(discord.ui.View):
    """Shift management panel for a single user: Start / End."""

    def __init__(self, cog: "ShiftTracking", member: discord.Member):
        super().__init__(timeout=300)
        self.cog = cog
        self.member_id = member.id
        self.guild_id = member.guild.id

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        assert isinstance(member, discord.Member)

        if member.id == self.member_id:
            return True

        # allow senior staff / admins to use others' panels
        if self.cog._is_senior_plus(member):
            return True

        await interaction.response.send_message(
            "❌ This panel belongs to someone else.", ephemeral=True
        )
        return False

    async def _refresh_embed(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return

        member = guild.get_member(self.member_id)
        if member is None:
            return

        new_embed = self.cog._build_clockmanage_embed(guild, member)
        await interaction.response.edit_message(embed=new_embed, view=self)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success)
    async def start_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self._check_owner(interaction):
            return

        ok, msg = self.cog._start_shift(self.guild_id, self.member_id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        await self._refresh_embed(interaction)

    @discord.ui.button(label="End", style=discord.ButtonStyle.danger)
    async def end_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self._check_owner(interaction):
            return

        ok, _ = self.cog._end_shift(self.guild_id, self.member_id)
        if not ok:
            await interaction.response.send_message(
                "You don't have an active clock to end.", ephemeral=True
            )
            return

        await self._refresh_embed(interaction)


class ShiftTracking(commands.Cog):
    """Slash-command based clock system for staff."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()  # ensure tables exist

    # ---------- auto-end webhook call from Roblox ----------

    async def auto_end_for_inactivity(self, user_id: int, reason: str) -> bool:
        """
        Called from the Roblox webhook when a player leaves the game or is
        inactive for 10 minutes.

        user_id is the Discord user ID.
        Returns True if a clock was ended, False if they had no active clock.
        """
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return False

        ok, duration_str = self._end_shift(guild.id, user_id)
        if not ok:
            # No active clock for this user
            return False

        member = guild.get_member(user_id)
        if member is not None:
            message = (
                f"⏱ Your staff clock in **{guild.name}** has been automatically ended.\n"
                f"Reason: you {reason}.\n"
                f"This shift lasted **{duration_str}**."
            )
            try:
                await member.send(message)
            except discord.Forbidden:
                # Can't DM them; ignore
                pass

        return True

    # ---------- helpers: role checks ----------

    def _is_supervisor_plus(self, member: discord.Member) -> bool:
        role_ids = {r.id for r in member.roles}
        return (
            SUPERVISOR_ROLE_ID in role_ids
            or SENIOR_SUPERVISOR_ROLE_ID in role_ids
            or LEAD_SUPERVISOR_ROLE_ID in role_ids
            or member.guild_permissions.administrator
        )

    def _is_senior_plus(self, member: discord.Member) -> bool:
        role_ids = {r.id for r in member.roles}
        return (
            SENIOR_SUPERVISOR_ROLE_ID in role_ids
            or LEAD_SUPERVISOR_ROLE_ID in role_ids
            or member.guild_permissions.administrator
        )

    def _is_lead_plus(self, member: discord.Member) -> bool:
        role_ids = {r.id for r in member.roles}
        return LEAD_SUPERVISOR_ROLE_ID in role_ids or member.guild_permissions.administrator

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
                SELECT id, start_time FROM shifts
                WHERE user_id = ? AND guild_id = ? AND end_time IS NULL
                """,
                (user_id, guild_id),
            )
            return cur.fetchone()

    def _start_shift(self, guild_id: int, user_id: int):
        if self._get_open_shift(guild_id, user_id):
            return False, "⏱ You already have an active clock."

        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO shifts (user_id, guild_id, start_time)
                VALUES (?, ?, ?)
                """,
                (user_id, guild_id, now),
            )
            conn.commit()

        local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return True, f"✅ Your clock has started at **{local}**."

    def _end_shift(self, guild_id: int, user_id: int):
        """End a user's current shift.

        Returns (ok, duration_str or None)."""
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
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _set_reset_time(self, guild_id: int, when: datetime):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO clock_periods (guild_id, reset_at)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE
                SET reset_at = excluded.reset_at
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
                SELECT start_time, end_time FROM shifts
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

        total_seconds += self._get_adjustment_seconds(guild_id, user_id, period_start)
        return total_seconds

    def _get_all_time_stats(self, guild_id: int, user_id: int):
        """Return (count, total_secs, average_secs) from all completed shifts."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT start_time, end_time FROM shifts
                WHERE user_id = ?
                  AND guild_id = ?
                  AND end_time IS NOT NULL
                """,
                (user_id, guild_id),
            )
            rows = cur.fetchall()

        count = 0
        total = 0
        for row in rows:
            start_dt = datetime.fromisoformat(row["start_time"])
            end_dt = datetime.fromisoformat(row["end_time"])
            total += int((end_dt - start_dt).total_seconds())
            count += 1

        avg = total // count if count else 0
        return count, total, avg

    def _build_clockmanage_embed(
        self, guild: discord.Guild, member: discord.Member
    ) -> discord.Embed:
        """Build the live stats embed used by /clockmanage."""
        count, total, avg = self._get_all_time_stats(guild.id, member.id)

        embed = discord.Embed(
            title="Shift Management",
            color=discord.Color.blurple(),
        )
        embed.set_author(
            name=member.display_name,
            icon_url=member.display_avatar.url,
        )
        embed.add_field(name="All Time Information", value="\u200b", inline=False)
        embed.add_field(name="Shift Count", value=str(count), inline=True)
        embed.add_field(
            name="Total Duration",
            value=self._format_duration(total),
            inline=True,
        )
        embed.add_field(
            name="Average Duration",
            value=self._format_duration(avg),
            inline=True,
        )
        embed.add_field(name="Shift Type", value="Supervisor Shifts", inline=False)
        return embed

    # ---------- presence → online role ----------

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """
        Give/remove the 'in-game online' role based on Discord presence.
        - If status is online -> add role
        - Otherwise -> remove role
        """
        if after.guild is None or after.guild.id != GUILD_ID:
            return
        if after.bot:
            return

        role = after.guild.get_role(ONLINE_ROLE_ID)
        if role is None:
            return  # role not found; silently ignore

        try:
            if after.status is discord.Status.online:
                if role not in after.roles:
                    await after.add_roles(
                        role, reason="In-game online role (status online)"
                    )
            else:
                if role in after.roles:
                    await after.remove_roles(
                        role, reason="In-game online role (status not online)"
                    )
        except discord.Forbidden:
            # Missing permissions to edit roles; nothing we can do
            pass

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

        member = interaction.user
        assert isinstance(member, discord.Member)

        if not self._is_supervisor_plus(member):
            await interaction.response.send_message(
                "❌ You must be a Supervisor or higher to use `/startclock`.",
                ephemeral=True,
            )
            return

        # Must be in the Roblox game to start a clock
        if not is_in_game(member.id):
            await interaction.response.send_message(
                "❌ You must be in the Roblox game to start your staff clock.\n"
                "Join the game first, then run `/startclock` again.",
                ephemeral=True,
            )
            return

        ok, msg = self._start_shift(guild.id, member.id)
        await interaction.response.send_message(msg, ephemeral=True)

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

        member = interaction.user
        assert isinstance(member, discord.Member)

        if not self._is_supervisor_plus(member):
            await interaction.response.send_message(
                "❌ You must be a Supervisor or higher to use `/endclock`.",
                ephemeral=True,
            )
            return

        ok, duration_str = self._end_shift(guild.id, member.id)
        if not ok:
            await interaction.response.send_message(
                "❌ You do not have an active clock.\nUse `/startclock` first.",
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

        actor = interaction.user
        assert isinstance(actor, discord.Member)
        if not self._is_supervisor_plus(actor):
            await interaction.response.send_message(
                "❌ You must be a Supervisor or higher to use `/clocktotal`.",
                ephemeral=True,
            )
            return

        target = member or actor
        total_seconds = self._get_period_total_seconds(guild.id, target.id)
        duration_str = self._format_duration(total_seconds)
        period_start = self._get_reset_time(guild.id)

        msg = (
            f" Clocked time for **{target.display_name}** "
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
        if not self._is_senior_plus(member):
            await interaction.response.send_message(
                "❌ You must be a Senior Supervisor or higher to use `/clockadmin`.",
                ephemeral=True,
            )
            return

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT user_id, start_time FROM shifts
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

        member = interaction.user
        assert isinstance(member, discord.Member)
        if not self._is_supervisor_plus(member):
            await interaction.response.send_message(
                "❌ You must be a Supervisor or higher to use `/clockboard`.",
                ephemeral=True,
            )
            return

        supervisors: List[discord.Member] = []
        for m in guild.members:
            if m.bot:
                continue
            role_ids = {r.id for r in m.roles}
            if (
                SUPERVISOR_ROLE_ID in role_ids
                or SENIOR_SUPERVISOR_ROLE_ID in role_ids
                or LEAD_SUPERVISOR_ROLE_ID in role_ids
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
            "(Lead Supervisor+)."
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
        if not self._is_lead_plus(actor):
            await interaction.response.send_message(
                "❌ You must be a Lead Supervisor or higher to use `/clockadjust`.",
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
        if not self._is_lead_plus(actor):
            await interaction.response.send_message(
                "❌ You must be a Lead Supervisor or higher to use `/clockreset`.",
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

    @app_commands.command(
        name="clockmanage",
        description="Open your shift management panel (Start / End).",
    )
    @app_commands.guild_only()
    async def clockmanage(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        assert isinstance(member, discord.Member)

        if not self._is_supervisor_plus(member):
            await interaction.response.send_message(
                "❌ You must be a Supervisor or higher to use `/clockmanage`.",
                ephemeral=True,
            )
            return

        embed = self._build_clockmanage_embed(guild, member)
        view = ClockManageView(self, member)
        await interaction.response.send_message(embed=embed, view=view)


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
    bot.tree.add_command(cog.clockmanage, guild=guild)

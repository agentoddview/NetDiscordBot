# cogs/shift_tracking.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
from typing import List

from database import get_connection, init_db

GUILD_ID = 882441222487162912  # NE Transit guild
SENIOR_SUPERVISOR_ROLE_ID = 1393088300239159467  # can use /clockadmin


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

        # If somehow there are >25 open shifts, Discord max is 25 options.
        options = options[:25]

        select = discord.ui.Select(
            placeholder="Select a user to end their clock",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.on_select  # type: ignore
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        user_id = int(self.children[0].values[0])  # value of selected option
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
            f"Duration: **{duration}**",
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

    # ---------- helpers ----------

    @staticmethod
    def _format_duration(seconds: int) -> str:
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if sec or not parts:
            parts.append(f"{sec}s")
        return " ".join(parts)

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
        """
        End the user's own shift, returns (ok, duration_str or None).
        """
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
        """
        Admin version of end_shift; same as _end_shift but separate for clarity.
        """
        return self._end_shift(guild_id, user_id)

    # ---------- Slash commands ----------

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
            f"✅ Your clock has ended.\n⏱ Duration: **{duration_str}**",
            ephemeral=True,
        )

    @app_commands.command(
        name="clocktotal",
        description="View total recorded clock time for yourself or another member.",
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
        user_id = target.id

        now_dt = datetime.now(timezone.utc)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT start_time, end_time
                FROM shifts
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild.id),
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

        duration_str = self._format_duration(total_seconds)
        await interaction.response.send_message(
            f"📊 Total clocked time for **{target.display_name}**: **{duration_str}**",
            ephemeral=True,
        )

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

        # Permission check: Senior Supervisor role or Administrator
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


async def setup(bot: commands.Bot):
    cog = ShiftTracking(bot)
    await bot.add_cog(cog)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.startclock, guild=guild)
    bot.tree.add_command(cog.endclock, guild=guild)
    bot.tree.add_command(cog.clocktotal, guild=guild)
    bot.tree.add_command(cog.clockadmin, guild=guild)

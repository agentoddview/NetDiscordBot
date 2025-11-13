import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
from typing import List

from database import get_connection, init_db

GUILD_ID = 882441222487162912
SENIOR_SUPERVISOR_ROLE_ID = 1393088300239159467  # LOA management


class LOAApprovalView(discord.ui.View):
    """Buttons for approving / denying a single LOA entry."""

    def __init__(self, cog: "LOATracking", loa_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.loa_id = loa_id
        self.guild_id = guild_id

    async def _process(
        self, interaction: discord.Interaction, decision: str
    ):
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            await interaction.response.send_message(
                "This LOA no longer belongs to this server.", ephemeral=True
            )
            return

        member = interaction.user
        assert isinstance(member, discord.Member)
        has_role = any(r.id == SENIOR_SUPERVISOR_ROLE_ID for r in member.roles)
        if not (has_role or member.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ You are not allowed to manage LOAs.", ephemeral=True
            )
            return

        ok, msg = await self.cog._decide_loa(
            self.loa_id, decision, moderator=member, log_message=interaction.message
        )
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        await interaction.response.send_message(msg, ephemeral=True)

        # Disable buttons after decision
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        custom_id="loa_approve",
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._process(interaction, "approved")

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        custom_id="loa_deny",
    )
    async def deny(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._process(interaction, "denied")


class LOAAdminView(discord.ui.View):
    """Dropdown list of active LOAs that can be ended early."""

    def __init__(self, cog: "LOATracking", guild: discord.Guild, rows):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild

        options: List[discord.SelectOption] = []
        for row in rows:
            loa_id = row["id"]
            user_id = row["user_id"]
            start_dt = datetime.fromisoformat(row["start_date"])
            end_dt = datetime.fromisoformat(row["end_date"])
            member = guild.get_member(user_id)
            name = member.display_name if member else str(user_id)
            label = (
                f"#{loa_id} {name} | "
                f"{start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}"
            )
            desc = f"User ID: {user_id}"
            options.append(
                discord.SelectOption(
                    label=label[:100], description=desc[:100], value=str(loa_id)
                )
            )

        options = options[:25]

        select = discord.ui.Select(
            placeholder="Select an LOA to end early",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.on_select  # type: ignore
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Guild not found for this interaction.", ephemeral=True
            )
            return

        loa_id = int(self.children[0].values[0])
        ok, msg = await self.cog._end_loa_early(loa_id, ended_by=interaction.user)
        await interaction.response.send_message(msg, ephemeral=True)


class LOATracking(commands.Cog):
    """LOA (Leave of Absence) tracking with slash commands + approval flow."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    # ---------- helpers: DB ----------

   # ---------- helpers: DB ----------

def _get_botlog_channel_id(self, guild_id: int) -> int | None:
    ...
    return row["botlog_channel_id"] if row and row["botlog_channel_id"] else None

def _get_loa_channel_id(self, guild_id: int) -> int | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT loa_channel_id FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )
        row = cur.fetchone()
    return row["loa_channel_id"] if row and row["loa_channel_id"] else None

def _get_loa(self, loa_id: int):
    ...

    # ---------- helpers: actions ----------

    async def _notify_user(
        self, guild: discord.Guild, row, message: str
    ):
        user = guild.get_member(row["user_id"])
        if user is None:
            try:
                user = await guild.fetch_member(row["user_id"])
            except Exception:
                user = None

        if user is None:
            return False

        embed = discord.Embed(
            title="Leave of Absence Update",
            description=message,
            color=discord.Color.blurple(),
        )
        try:
            await user.send(embed=embed)
            return True
        except discord.Forbidden:
            return False

    async def _decide_loa(
        self,
        loa_id: int,
        decision: str,
        moderator: discord.Member,
        log_message: discord.Message | None = None,
    ):
        row = self._get_loa(loa_id)
        if not row:
            return False, "That LOA no longer exists."

        if row["status"] != "pending":
            return False, f"LOA `#{loa_id}` is already {row['status']}."

        guild = moderator.guild

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE loas SET status = ? WHERE id = ?",
                (decision, loa_id),
            )
            conn.commit()

        start = datetime.fromisoformat(row["start_date"]).strftime("%Y-%m-%d")
        end = datetime.fromisoformat(row["end_date"]).strftime("%Y-%m-%d")
        base_msg = (
            f"Your LOA `#{loa_id}` ({start} → {end}) has been **{decision}**."
        )
        dm_ok = await self._notify_user(guild, row, base_msg)

        # Log to bot logs
        color = (
            discord.Color.green()
            if decision == "approved"
            else discord.Color.red()
        )
        log = discord.Embed(
            title=f"LOA {decision.capitalize()}",
            description=(
                f"LOA `#{loa_id}` for <@{row['user_id']}> has been **{decision}**.\n"
                f"Reason: `{row['reason']}`"
            ),
            color=color,
        )
        log.add_field(
            name="Dates",
            value=f"{start} → {end}",
            inline=False,
        )
        log.add_field(
            name="Moderator",
            value=moderator.mention,
            inline=True,
        )
        log.add_field(
            name="DM Sent",
            value="Yes" if dm_ok else "No (DMs closed?)",
            inline=True,
        )
        await self._send_botlog(guild, log)

        # Update original log message embed if we have it
        if log_message is not None and log_message.embeds:
            embed = log_message.embeds[0]
            embed.add_field(
                name="Status",
                value=f"{decision.capitalize()} by {moderator.mention}",
                inline=False,
            )
            try:
                await log_message.edit(embed=embed)
            except Exception:
                pass

        return True, f"LOA `#{loa_id}` has been **{decision}**."

    async def _end_loa_early(
        self, loa_id: int, ended_by: discord.Member
    ):
        row = self._get_loa(loa_id)
        if not row:
            return False, "That LOA no longer exists."

        if row["status"] not in ("approved",):
            return (
                False,
                f"LOA `#{loa_id}` is not an active approved LOA (status: {row['status']}).",
            )

        guild = ended_by.guild

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE loas SET status = ? WHERE id = ?",
                ("ended", loa_id),
            )
            conn.commit()

        start = datetime.fromisoformat(row["start_date"]).strftime("%Y-%m-%d")
        end = datetime.fromisoformat(row["end_date"]).strftime("%Y-%m-%d")
        base_msg = (
            f"Your LOA `#{loa_id}` ({start} → {end}) has been **ended early** "
            f"by {ended_by.mention}. You are now expected to return to activity."
        )
        dm_ok = await self._notify_user(guild, row, base_msg)

        log = discord.Embed(
            title="LOA Ended Early",
            description=(
                f"LOA `#{loa_id}` for <@{row['user_id']}> has been **ended early**.\n"
                f"Reason: `{row['reason']}`"
            ),
            color=discord.Color.orange(),
        )
        log.add_field(name="Moderator", value=ended_by.mention, inline=True)
        log.add_field(
            name="DM Sent",
            value="Yes" if dm_ok else "No (DMs closed?)",
            inline=True,
        )
        log.add_field(
            name="Original Dates",
            value=f"{start} → {end}",
            inline=False,
        )
        await self._send_botlog(guild, log)

        return True, f"LOA `#{loa_id}` has been ended early."

    # ---------- slash commands ----------

    @app_commands.command(
        name="loa",
        description="Show information on LOA commands.",
    )
    @app_commands.guild_only()
    async def loa_help(self, interaction: discord.Interaction):
        msg = (
            "**LOA Commands:**\n"
            "`/loarequest <days> <reason>` - Request an LOA.\n"
            "`/loalist` - List all LOAs for this server.\n"
            "`/loafeed` - Send pending LOAs to the Net Bot Logs channel with Approve/Deny buttons.\n"
            "`/loaadmin` - Admin panel to end approved LOAs early.\n"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="loarequest",
        description="Request an LOA for a number of days.",
    )
    @app_commands.describe(
        days="How many days you will be on LOA.",
        reason="Reason for your LOA.",
    )
    @app_commands.guild_only()
    async def loarequest(
        self,
        interaction: discord.Interaction,
        days: int,
        reason: str,
    ):
        user_id = interaction.user.id
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        if days <= 0:
            await interaction.response.send_message(
                "Days must be at least 1.", ephemeral=True
            )
            return

        start = datetime.utcnow().replace(tzinfo=timezone.utc)
        end = start + timedelta(days=days)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO loas (user_id, guild_id, reason, start_date, end_date, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (
                    user_id,
                    guild.id,
                    reason,
                    start.isoformat(),
                    end.isoformat(),
                ),
            )
            loa_id = cur.lastrowid
            conn.commit()

        await interaction.response.send_message(
            f"📅 LOA `#{loa_id}` requested for **{days} days**.\n"
            f"Reason: `{reason}`\n"
            f"From: `{start.strftime('%Y-%m-%d')}` To: `{end.strftime('%Y-%m-%d')}`\n"
            "Status: `pending`.\n"
            "A staff member will review your LOA shortly.",
            ephemeral=True,
        )

        # Log the new LOA request to Net Bot Logs channel
        embed = discord.Embed(
            title="New LOA Request",
            description=f"LOA `#{loa_id}` requested by {interaction.user.mention}",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Dates",
            value=f"{start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}",
            inline=False,
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        await self._send_botlog(guild, embed)

    @app_commands.command(
        name="loalist",
        description="List LOAs in this server.",
    )
    @app_commands.guild_only()
    async def loalist(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, user_id, reason, start_date, end_date, status
                FROM loas
                WHERE guild_id = ?
                ORDER BY start_date DESC
                """,
                (guild.id,),
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                "No LOAs recorded in this server.",
                ephemeral=True,
            )
            return

        lines = []
        for row in rows:
            member = guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            start = datetime.fromisoformat(row["start_date"]).strftime(
                "%Y-%m-%d"
            )
            end = datetime.fromisoformat(row["end_date"]).strftime("%Y-%m-%d")
            lines.append(
                f"`#{row['id']}` • **{name}** | {start} → {end} | "
                f"`{row['status']}` | Reason: {row['reason']}"
            )

        await interaction.response.send_message(
            "\n".join(lines)[:4000], ephemeral=True
        )

    @app_commands.command(
        name="loafeed",
        description=(
            "Send pending LOAs to the Net Bot Logs channel with Approve/Deny buttons."
        ),
    )
    @app_commands.guild_only()
    async def loafeed(self, interaction: discord.Interaction):
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
                "❌ You must be a Senior Supervisor to use `/loafeed`.",
                ephemeral=True,
            )
            return

        loa_channel_id = self._get_loa_channel_id(guild.id)
        if not botlog_channel_id:
            await interaction.response.send_message(
                "Net Bot Logs channel is not configured. Use `/netconfig` first.",
                ephemeral=True,
            )
            return

        botlog_channel = guild.get_channel(botlog_channel_id)
        if not isinstance(botlog_channel, discord.TextChannel):
            await interaction.response.send_message(
                "I can't access the configured Net Bot Logs channel.",
                ephemeral=True,
            )
            return

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, user_id, reason, start_date, end_date
                FROM loas
                WHERE guild_id = ? AND status = 'pending'
                ORDER BY start_date ASC
                """,
                (guild.id,),
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                "There are currently **no pending LOAs**.",
                ephemeral=True,
            )
            return

        count = 0
        for row in rows:
            loa_id = row["id"]
            start = datetime.fromisoformat(row["start_date"]).strftime(
                "%Y-%m-%d"
            )
            end = datetime.fromisoformat(row["end_date"]).strftime("%Y-%m-%d")
            member_obj = guild.get_member(row["user_id"])
            mention = (
                member_obj.mention
                if member_obj
                else f"<@{row['user_id']}>"
            )

            embed = discord.Embed(
                title=f"Pending LOA #{loa_id}",
                description=f"{mention} has requested an LOA.",
                color=discord.Color.yellow(),
            )
            embed.add_field(
                name="Dates", value=f"{start} → {end}", inline=False
            )
            embed.add_field(name="Reason", value=row["reason"], inline=False)
            embed.set_footer(
                text="Use the buttons below to approve or deny this LOA."
            )

            view = LOAApprovalView(self, loa_id, guild.id)
            await botlog_channel.send(embed=embed, view=view)
            count += 1

        await interaction.response.send_message(
            f"✅ Sent **{count}** pending LOA(s) to {botlog_channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="loaadmin",
        description="Open the LOA admin panel to end approved LOAs early.",
    )
    @app_commands.guild_only()
    async def loaadmin(self, interaction: discord.Interaction):
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
                "❌ You must be a Senior Supervisor to use `/loaadmin`.",
                ephemeral=True,
            )
            return

        now = datetime.utcnow().replace(tzinfo=timezone.utc)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, user_id, start_date, end_date
                FROM loas
                WHERE guild_id = ?
                  AND status = 'approved'
                  AND end_date >= ?
                ORDER BY start_date ASC
                """,
                (guild.id, now.isoformat()),
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                "There are no active approved LOAs to manage.",
                ephemeral=True,
            )
            return

        view = LOAAdminView(self, guild, rows)
        await interaction.response.send_message(
            "### LOA Admin Panel\n"
            "Select an LOA below to end it early. This panel is only visible to you.",
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    cog = LOATracking(bot)
    await bot.add_cog(cog)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.loa_help, guild=guild)
    bot.tree.add_command(cog.loarequest, guild=guild)
    bot.tree.add_command(cog.loalist, guild=guild)
    bot.tree.add_command(cog.loafeed, guild=guild)
    bot.tree.add_command(cog.loaadmin, guild=guild)

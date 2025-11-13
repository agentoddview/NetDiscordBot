# cogs/loa.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta

from database import get_connection, init_db

GUILD_ID = 882441222487162912  # NE Transit guild


class LOATracking(commands.Cog):
    """LOA (Leave of Absence) tracking with slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

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

        start = datetime.utcnow()
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
            "Status: `pending`.",
            ephemeral=True,
        )

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
            "\n".join(lines), ephemeral=True
        )


async def setup(bot: commands.Bot):
    cog = LOATracking(bot)
    await bot.add_cog(cog)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.loa_help, guild=guild)
    bot.tree.add_command(cog.loarequest, guild=guild)
    bot.tree.add_command(cog.loalist, guild=guild)

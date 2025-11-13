import discord
from discord.ext import commands
from discord import app_commands

from database import get_connection, init_db

GUILD_ID = 882441222487162912  # NE Transit guild


class NetConfig(commands.Cog):
    """Configuration for Net bot channels (mod log, bot log)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    def _get_settings(self, guild_id: int) -> dict:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT modlog_channel_id, botlog_channel_id
                FROM guild_settings WHERE guild_id = ?
                """,
                (guild_id,),
            )
            row = cur.fetchone()
        if not row:
            return {"modlog_channel_id": None, "botlog_channel_id": None}
        return {
            "modlog_channel_id": row["modlog_channel_id"],
            "botlog_channel_id": row["botlog_channel_id"],
        }

    def _save_settings(
        self,
        guild_id: int,
        modlog_channel_id: int | None,
        botlog_channel_id: int | None,
    ):
        current = self._get_settings(guild_id)
        if modlog_channel_id is None:
            modlog_channel_id = current["modlog_channel_id"]
        if botlog_channel_id is None:
            botlog_channel_id = current["botlog_channel_id"]

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO guild_settings (guild_id, modlog_channel_id, botlog_channel_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    modlog_channel_id = excluded.modlog_channel_id,
                    botlog_channel_id = excluded.botlog_channel_id
                """,
                (guild_id, modlog_channel_id, botlog_channel_id),
            )
            conn.commit()

    @app_commands.command(
        name="netconfig",
        description="Configure Net bot channels (mod logs, bot logs).",
    )
    @app_commands.describe(
        modlog_channel="Channel for moderation logs (joins, deletions, etc.).",
        botlog_channel="Channel for Net bot logs (LOAs, approvals, etc.).",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def netconfig(
        self,
        interaction: discord.Interaction,
        modlog_channel: discord.TextChannel | None = None,
        botlog_channel: discord.TextChannel | None = None,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        self._save_settings(
            guild.id,
            modlog_channel.id if modlog_channel else None,
            botlog_channel.id if botlog_channel else None,
        )

        settings = self._get_settings(guild.id)
        def _mention(cid: int | None):
            if not cid:
                return "`Not set`"
            ch = guild.get_channel(cid)
            return ch.mention if ch else f"`#{cid}`"

        await interaction.response.send_message(
            "✅ **Net bot configuration updated.**\n"
            f"• Mod log channel: {_mention(settings['modlog_channel_id'])}\n"
            f"• Bot log channel: {_mention(settings['botlog_channel_id'])}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    cog = NetConfig(bot)
    await bot.add_cog(cog)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.netconfig, guild=guild)

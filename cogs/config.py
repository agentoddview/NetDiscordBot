import discord
from discord.ext import commands
from discord import app_commands

from database import get_connection, init_db

GUILD_ID = 882441222487162912  # NE Transit guild


class NetConfig(commands.Cog):
    """Configure all Net bot channels in one command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    def _get_settings(self, guild_id: int):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT modlog_channel_id, botlog_channel_id, loa_channel_id
                FROM guild_settings WHERE guild_id = ?
            """, (guild_id,))
            row = cur.fetchone()

        if not row:
            return {
                "modlog_channel_id": None,
                "botlog_channel_id": None,
                "loa_channel_id": None
            }
        return dict(row)

    def _save(self, guild_id: int, mod, bot, loa):
        existing = self._get_settings(guild_id)

        if mod is None:
            mod = existing["modlog_channel_id"]
        if bot is None:
            bot = existing["botlog_channel_id"]
        if loa is None:
            loa = existing["loa_channel_id"]

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO guild_settings (guild_id, modlog_channel_id, botlog_channel_id, loa_channel_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    modlog_channel_id = excluded.modlog_channel_id,
                    botlog_channel_id = excluded.botlog_channel_id,
                    loa_channel_id = excluded.loa_channel_id
            """, (guild_id, mod, bot, loa))
            conn.commit()

    @app_commands.command(
        name="netconfig",
        description="Configure Net bot channels."
    )
    @app_commands.describe(
        modlog_channel="Channel for moderation logs",
        botlog_channel="Channel for general bot logs",
        loa_channel="Channel where LOA approval feed messages go"
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def netconfig(
        self,
        interaction: discord.Interaction,
        modlog_channel: discord.TextChannel | None = None,
        botlog_channel: discord.TextChannel | None = None,
        loa_channel: discord.TextChannel | None = None,
    ):
        guild = interaction.guild
        self._save(
            guild.id,
            modlog_channel.id if modlog_channel else None,
            botlog_channel.id if botlog_channel else None,
            loa_channel.id if loa_channel else None,
        )

        settings = self._get_settings(guild.id)

        def m(c): 
            return guild.get_channel(c).mention if c else "`Not set`"

        await interaction.response.send_message(
            "### ✅ Net Bot Configuration Updated\n"
            f"**Mod Log:** {m(settings['modlog_channel_id'])}\n"
            f"**Generic Bot Log:** {m(settings['botlog_channel_id'])}\n"
            f"**LOA Feed:** {m(settings['loa_channel_id'])}",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    cog = NetConfig(bot)
    await bot.add_cog(cog)
    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.netconfig, guild=guild)

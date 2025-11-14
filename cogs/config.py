import discord
from discord.ext import commands
from discord import app_commands

from database import get_connection, init_db

GUILD_ID = 882441222487162912

# Roles
SUPERVISOR_ROLE_ID = 947288094804176957          # Supervisor
SENIOR_SUPERVISOR_ROLE_ID = 1393088300239159467  # Senior Supervisor
LEAD_SUPERVISOR_ROLE_ID = 1351333124965142600    # Lead Supervisor


class Config(commands.Cog):
    """Guild configuration for the NET bot (/netconfig)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    # ---------- helpers: role checks ----------

    def _is_lead_plus(self, member: discord.Member) -> bool:
        role_ids = {r.id for r in member.roles}
        return (
            LEAD_SUPERVISOR_ROLE_ID in role_ids
            or member.guild_permissions.administrator
        )

    # ---------- helpers: db ----------

    def _get_settings(self, guild_id: int):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT guild_id, botlog_channel_id, loa_channel_id
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            return cur.fetchone()

    def _upsert_settings(
        self,
        guild_id: int,
        botlog_channel_id: int | None,
        loa_channel_id: int | None,
    ):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO guild_settings (guild_id, botlog_channel_id, loa_channel_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    botlog_channel_id = excluded.botlog_channel_id,
                    loa_channel_id   = excluded.loa_channel_id
                """,
                (guild_id, botlog_channel_id, loa_channel_id),
            )
            conn.commit()

    # ---------- /netconfig ----------

    @app_commands.command(
        name="netconfig",
        description="Configure the main channels for the NET bot (Lead Supervisor+).",
    )
    @app_commands.describe(
        botlog_channel="Channel where bot logs (moderations, LOAs, etc.) will be sent.",
        loa_channel="Channel where LOA approval/denial feed will be sent.",
    )
    @app_commands.guild_only()
    async def netconfig(
        self,
        interaction: discord.Interaction,
        botlog_channel: discord.TextChannel,
        loa_channel: discord.TextChannel,
    ):
        guild = interaction.guild
        user = interaction.user

        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        assert isinstance(user, discord.Member)
        if not self._is_lead_plus(user):
            await interaction.response.send_message(
                "❌ You must be a Lead Supervisor or higher to use `/netconfig`.",
                ephemeral=True,
            )
            return

        self._upsert_settings(
            guild.id,
            botlog_channel_id=botlog_channel.id,
            loa_channel_id=loa_channel.id,
        )

        embed = discord.Embed(
            title="NET Configuration Updated",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Bot Log Channel",
            value=botlog_channel.mention,
            inline=False,
        )
        embed.add_field(
            name="LOA Feed Channel",
            value=loa_channel.mention,
            inline=False,
        )
        embed.set_footer(text="Only Lead Supervisor+ can run /netconfig.")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    cog = Config(bot)
    await bot.add_cog(cog)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.netconfig, guild=guild)

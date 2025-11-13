import discord
from discord.ext import commands

from database import get_connection, init_db


class ModLog(commands.Cog):
    """Moderation logging and settings."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    def _get_modlog_channel_id(self, guild_id: int) -> int | None:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT modlog_channel_id FROM guild_settings WHERE guild_id = ?",
                (guild_id,),
            )
            row = cur.fetchone()
        return row["modlog_channel_id"] if row and row["modlog_channel_id"] else None

    def _set_modlog_channel_id(self, guild_id: int, channel_id: int | None):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO guild_settings (guild_id, modlog_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    modlog_channel_id = excluded.modlog_channel_id
                """,
                (guild_id, channel_id),
            )
            conn.commit()

    @commands.command(name="setmodlog")
    @commands.has_permissions(manage_guild=True)
    async def set_modlog(
        self, ctx: commands.Context, channel: discord.TextChannel | None
    ):
        """Set the mod log channel (or clear with no channel)."""
        if channel is None:
            self._set_modlog_channel_id(ctx.guild.id, None)
            await ctx.send("🗑️ Mod log channel cleared.")
        else:
            self._set_modlog_channel_id(ctx.guild.id, channel.id)
            await ctx.send(f"✅ Mod log channel set to {channel.mention}")

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed):
        channel_id = self._get_modlog_channel_id(guild.id)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel:
            await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        embed = discord.Embed(
            title="Member Joined",
            description=f"{member.mention} ({member.id})",
        )
        await self._send_log(member.guild, embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        embed = discord.Embed(
            title="Message Deleted",
            description=f"Author: {message.author.mention}\n"
            f"Channel: {message.channel.mention}",
        )
        if message.content:
            content = message.content
            if len(content) > 1000:
                content = content[:997] + "..."
            embed.add_field(name="Content", value=content, inline=False)

        await self._send_log(message.guild, embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModLog(bot))

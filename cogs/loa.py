import discord
from discord.ext import commands
from datetime import datetime, timedelta

from database import get_connection, init_db


class LOATracking(commands.Cog):
    """LOA (Leave of Absence) tracking."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    @commands.command(name="loa")
    async def loa_help(self, ctx: commands.Context):
        """Show LOA command usage."""
        msg = (
            "**LOA Commands:**\n"
            "`!loarequest <days> <reason...>` - Request an LOA.\n"
            "`!loalist` - List all LOAs for this server.\n"
        )
        await ctx.send(msg)

    @commands.command(name="loarequest")
    async def loa_request(
        self,
        ctx: commands.Context,
        days: int,
        *,
        reason: str = "No reason provided",
    ):
        """Request an LOA for a number of days."""
        user_id = ctx.author.id
        guild_id = ctx.guild.id

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
                    guild_id,
                    reason,
                    start.isoformat(),
                    end.isoformat(),
                ),
            )
            conn.commit()

        await ctx.send(
            f"📅 {ctx.author.mention} LOA requested for **{days} days**.\n"
            f"Reason: `{reason}`\n"
            f"From: `{start.strftime('%Y-%m-%d')}` To: `{end.strftime('%Y-%m-%d')}`\n"
            "Status: `pending`."
        )

    @commands.command(name="loalist")
    async def loa_list(self, ctx: commands.Context):
        """List LOAs for this server."""
        guild_id = ctx.guild.id

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, user_id, reason, start_date, end_date, status
                FROM loas
                WHERE guild_id = ?
                ORDER BY start_date DESC
                """,
                (guild_id,),
            )
            rows = cur.fetchall()

        if not rows:
            await ctx.send("No LOAs recorded in this server.")
            return

        lines = []
        for row in rows:
            member = ctx.guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            start = datetime.fromisoformat(row["start_date"]).strftime("%Y-%m-%d")
            end = datetime.fromisoformat(row["end_date"]).strftime("%Y-%m-%d")
            lines.append(
                f"`#{row['id']}` • **{name}** | {start} → {end} | "
                f"`{row['status']}` | Reason: {row['reason']}"
            )

        await ctx.send("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(LOATracking(bot))

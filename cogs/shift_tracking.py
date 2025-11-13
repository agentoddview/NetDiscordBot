import discord
from discord.ext import commands
from datetime import datetime, timezone

from database import get_connection, init_db


class ShiftTracking(commands.Cog):
    """Shift tracking system similar to Trident-style Roblox trackers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()  # ensure tables exist

    # Helper to format durations nicely
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

    @commands.command(name="shiftstart")
    async def shift_start(self, ctx: commands.Context):
        """Start a shift for yourself."""
        user_id = ctx.author.id
        guild_id = ctx.guild.id

        now = datetime.now(timezone.utc).isoformat()

        with get_connection() as conn:
            cur = conn.cursor()

            # Check if there's already an open shift
            cur.execute(
                """
                SELECT id FROM shifts
                WHERE user_id = ? AND guild_id = ? AND end_time IS NULL
                """,
                (user_id, guild_id),
            )
            row = cur.fetchone()
            if row:
                await ctx.send(
                    f"{ctx.author.mention} you already have an active shift! "
                    "Use `!shiftend` to end it first."
                )
                return

            # Create new shift
            cur.execute(
                """
                INSERT INTO shifts (user_id, guild_id, start_time)
                VALUES (?, ?, ?)
                """,
                (user_id, guild_id, now),
            )
            conn.commit()

        await ctx.send(
            f"✅ {ctx.author.mention} your shift has started at "
            f"**{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**."
        )

    @commands.command(name="shiftend")
    async def shift_end(self, ctx: commands.Context):
        """End your current shift and show duration."""
        user_id = ctx.author.id
        guild_id = ctx.guild.id

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        with get_connection() as conn:
            cur = conn.cursor()

            cur.execute(
                """
                SELECT id, start_time FROM shifts
                WHERE user_id = ? AND guild_id = ? AND end_time IS NULL
                """,
                (user_id, guild_id),
            )
            row = cur.fetchone()
            if not row:
                await ctx.send(
                    f"{ctx.author.mention} you don't have an active shift. "
                    "Use `!shiftstart` to begin one."
                )
                return

            shift_id = row["id"]
            start_dt = datetime.fromisoformat(row["start_time"])

            duration_sec = int((now_dt - start_dt).total_seconds())
            duration_str = self._format_duration(duration_sec)

            cur.execute(
                """
                UPDATE shifts
                SET end_time = ?
                WHERE id = ?
                """,
                (now_iso, shift_id),
            )
            conn.commit()

        await ctx.send(
            f"✅ {ctx.author.mention} your shift has ended.\n"
            f"⏱ Duration: **{duration_str}**"
        )

    @commands.command(name="shifttotal")
    async def shift_total(
        self, ctx: commands.Context, member: discord.Member | None = None
    ):
        """Show total logged shift time for a member (or yourself)."""
        target = member or ctx.author
        user_id = target.id
        guild_id = ctx.guild.id

        now_dt = datetime.now(timezone.utc)

        with get_connection() as conn:
            cur = conn.cursor()

            # Get all completed shifts
            cur.execute(
                """
                SELECT start_time, end_time FROM shifts
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            rows = cur.fetchall()

        total_seconds = 0
        for row in rows:
            start_dt = datetime.fromisoformat(row["start_time"])
            if row["end_time"] is None:
                end_dt = now_dt  # still active
            else:
                end_dt = datetime.fromisoformat(row["end_time"])
            total_seconds += int((end_dt - start_dt).total_seconds())

        duration_str = self._format_duration(total_seconds)

        await ctx.send(
            f"📊 Total shift time for **{target.display_name}**: **{duration_str}**"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ShiftTracking(bot))

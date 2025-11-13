import aiohttp
import sqlite3
from datetime import datetime, timezone
from typing import List

import discord
from discord.ext import commands
from discord import app_commands

from database import get_connection, init_db

GUILD_ID = 882441222487162912  # NE Transit guild

PUNISHMENTS = [
    "Warning",
    "Mute",
    "Kick",
    "Server Ban",
    "Time Ban",
    "Global Ban",
]


class ModerateConfirmView(discord.ui.View):
    """Confirm / Cancel buttons for a pending moderation card."""

    def __init__(self, cog: "Moderation", data: dict):
        super().__init__(timeout=180)
        self.cog = cog
        # data: guild_id, moderator_id, roblox_id, username, punishment, reason
        self.data = data

    async def _check_perms(self, interaction: discord.Interaction) -> bool:
        """Only the original moderator or someone with Manage Server can confirm/cancel."""
        member = interaction.user
        assert isinstance(member, discord.Member)
        if member.id == self.data["moderator_id"]:
            return True
        if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
            return True

        await interaction.response.send_message(
            "❌ You are not allowed to confirm or cancel this moderation.",
            ephemeral=True,
        )
        return False

    async def _do_confirm(self, interaction: discord.Interaction):
        if not await self._check_perms(interaction):
            return

        ok, msg = await self.cog._record_moderation(self.data, interaction.user)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        # Add a Status field to the original embed and remove buttons
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            embed.add_field(
                name="Status",
                value=f"✅ Confirmed by {interaction.user.mention}",
                inline=False,
            )
            try:
                await interaction.message.edit(embed=embed, view=None)
            except Exception:
                pass

        await interaction.response.send_message(msg, ephemeral=True)

    async def _do_cancel(self, interaction: discord.Interaction):
        if not await self._check_perms(interaction):
            return

        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            embed.add_field(
                name="Status",
                value=f"❌ Canceled by {interaction.user.mention}",
                inline=False,
            )
            try:
                await interaction.message.edit(embed=embed, view=None)
            except Exception:
                pass

        await interaction.response.send_message(
            "Moderation canceled. No record was saved.",
            ephemeral=True,
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._do_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._do_cancel(interaction)


class Moderation(commands.Cog):
    """Roblox-focused moderation logging with pretty confirmation cards."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    # ---------- helpers: settings / logging ----------

    def _get_botlog_channel_id(self, guild_id: int) -> int | None:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT botlog_channel_id FROM guild_settings WHERE guild_id = ?",
                (guild_id,),
            )
            row = cur.fetchone()
        return row["botlog_channel_id"] if row and row["botlog_channel_id"] else None

    async def _send_botlog(self, guild: discord.Guild, embed: discord.Embed):
        channel_id = self._get_botlog_channel_id(guild.id)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel:
            await channel.send(embed=embed)

    # ---------- helpers: Roblox API ----------

    async def _fetch_roblox_user(self, query: str):
        """
        Accepts either a Roblox user ID (digits) or a username.
        Returns dict:
        {
            id: str,
            name: str,
            displayName: str,
            created: str,
            thumbnail_url: str,
            profile_url: str
        }
        or None if not found.
        """
        async with aiohttp.ClientSession() as session:
            # 1) Resolve username -> ID if needed
            if query.isdigit():
                user_id = int(query)
            else:
                url = "https://users.roblox.com/v1/usernames/users"
                payload = {
                    "usernames": [query],
                    "excludeBannedUsers": False,
                }
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    if not data.get("data"):
                        return None
                    user_id = data["data"][0]["id"]

            # 2) Fetch user details
            async with session.get(
                f"https://users.roblox.com/v1/users/{user_id}"
            ) as resp:
                if resp.status != 200:
                    return None
                info = await resp.json()

            # 3) Fetch proper avatar headshot via thumbnails API
            thumb_url = None
            thumb_api = (
                "https://thumbnails.roblox.com/v1/users/avatar-headshot"
                f"?userIds={user_id}&size=420x420&format=Png&isCircular=false"
            )
            async with session.get(thumb_api) as resp:
                if resp.status == 200:
                    tdata = await resp.json()
                    if tdata.get("data"):
                        thumb_url = tdata["data"][0].get("imageUrl")

            # Fallback to classic URL if thumbnails API fails
            if not thumb_url:
                thumb_url = (
                    "https://www.roblox.com/headshot-thumbnail/image"
                    f"?userId={user_id}&width=420&height=420&format=png"
                )

            profile_url = f"https://www.roblox.com/users/{user_id}/profile"

            return {
                "id": str(user_id),
                "name": info.get("name") or "",
                "displayName": info.get("displayName") or "",
                "created": info.get("created") or "",
                "thumbnail_url": thumb_url,
                "profile_url": profile_url,
            }

    # ---------- helpers: DB ----------

    def _get_previous_moderations(
        self, guild_id: int, roblox_id: str, limit: int = 5
    ) -> List[sqlite3.Row]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT *
                FROM moderations
                WHERE guild_id = ? AND target_roblox_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (guild_id, roblox_id, limit),
            )
            return cur.fetchall()

    async def _record_moderation(self, data: dict, moderator: discord.Member):
        """
        Save the moderation and log it.
        DATA keys:
            guild_id, roblox_id, username, punishment, reason
        """
        guild_id = data["guild_id"]
        roblox_id = data["roblox_id"]
        username = data["username"]
        punishment = data["punishment"]
        reason = data["reason"]

        now = datetime.now(timezone.utc)

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO moderations (
                    guild_id, moderator_id, target_roblox_id, target_username,
                    punishment, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    moderator.id,
                    roblox_id,
                    username,
                    punishment,
                    reason,
                    now.isoformat(),
                ),
            )
            case_id = cur.lastrowid
            conn.commit()

        guild = moderator.guild
        created_str = now.strftime("%m/%d/%Y %I:%M %p")

        embed = discord.Embed(
            title=f"Moderation Logged (Case #{case_id})",
            description=f"**{username}** ({roblox_id})",
            color=discord.Color.red()
            if punishment.lower() in {"global ban", "server ban", "kick"}
            else discord.Color.orange(),
        )
        embed.add_field(name="Punishment", value=punishment, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=False)
        embed.add_field(name="Time", value=created_str, inline=False)

        await self._send_botlog(guild, embed)

        return True, f"Moderation recorded as **Case #{case_id}**."

    # ---------- slash command ----------

    @app_commands.command(
        name="moderate",
        description="Open a Roblox moderation card for confirmation.",
    )
    @app_commands.describe(
        roblox_user="Roblox username or numeric user ID.",
        punishment="Type of punishment.",
        reason="Reason for the moderation.",
    )
    @app_commands.choices(
        punishment=[app_commands.Choice(name=p, value=p) for p in PUNISHMENTS]
    )
    @app_commands.guild_only()
    async def moderate(
        self,
        interaction: discord.Interaction,
        roblox_user: str,
        punishment: app_commands.Choice[str],
        reason: str,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        info = await self._fetch_roblox_user(roblox_user)
        if not info:
            await interaction.followup.send(
                "❌ Could not find that Roblox user.",
                ephemeral=True,
            )
            return

        roblox_id = info["id"]
        username = info["name"] or info["displayName"] or roblox_id
        display_name = info["displayName"] or username

        # Account creation date
        created_raw = info["created"]
        try:
            created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            created_str = created_dt.strftime("%m/%d/%Y")
        except Exception:
            created_str = created_raw or "Unknown"

        # Previous moderations
        prev_rows = self._get_previous_moderations(guild.id, roblox_id, limit=5)
        prev_lines: List[str] = []
        for i, row in enumerate(prev_rows, start=1):
            when = datetime.fromisoformat(row["created_at"])
            when_str = when.strftime("%m/%d/%Y %I:%M %p")
            prev_lines.append(
                f"{i}. {when_str} • {row['punishment']} • {row['reason']}"
            )
        prev_text = "\n".join(prev_lines) if prev_lines else "None"

        profile_url = info["profile_url"]

        embed = discord.Embed(
            title=display_name,  # clickable to profile
            description="Pending Moderation",
            color=discord.Color.blurple(),
            url=profile_url,
        )
        embed.add_field(name="User ID", value=roblox_id, inline=True)
        embed.add_field(name="Display Name", value=display_name, inline=True)
        embed.add_field(name="Account Created", value=created_str, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Punishment", value=punishment.value, inline=False)
        embed.add_field(
            name="Previous Moderations",
            value=prev_text,
            inline=False,
        )
        embed.set_thumbnail(url=info["thumbnail_url"])

        data = {
            "guild_id": guild.id,
            "moderator_id": interaction.user.id,
            "roblox_id": roblox_id,
            "username": username,
            "punishment": punishment.value,
            "reason": reason,
        }
        view = ModerateConfirmView(self, data)

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    cog = Moderation(bot)
    await bot.add_cog(cog)

    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.moderate, guild=guild)

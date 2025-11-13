import csv
import os
import random
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

# ==================== CONFIG =====================

CSV_PATH = "results.csv"

# Roles
LEAD_SUPERVISOR_ROLE_ID = 1351333124965142600  # for /add, /reloadcsv
SUPERVISOR_ROLE_ID = 947288094804176957       # minimum role to run /shift

# Guild/Channels/Emojis
GUILD_ID = 882441222487162912
SHIFTS_CHANNEL_ID = 1329659267963420703  # #shifts
RUNS_NOTIFIED_ROLE_ID = 1332862724039774289  # @Runs Notified (or "run notifications")

# If :net: is a custom emoji, set the full tag like "<:net:123456789012345678>"
# If it's a standard Unicode emoji, you can put the emoji itself here.
NET_EMOJI = "<:net:1323882053858492437>"

DEFAULT_TZ = "America/New_York"  # MBTA/WRTA locale

# ---------- Footer text ----------
FOOTER_TEXT = (
    "More questions or concerns? Please open a ticket inside the New England Transit Discord Server."
)

# =================================================

# ---------- CSV helpers ----------


def load_results_csv(path: str = CSV_PATH):
    data = {}
    if not os.path.exists(path):
        return data
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            data[row["Username"].strip().lower()] = {
                "Result": row.get("Result", "").strip(),
                "Feedback": row.get("Feedback", "").strip(),
            }
    return data


def save_results_csv(data: dict, path: str = CSV_PATH):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Username", "Result", "Feedback"])
        w.writeheader()
        for username, v in data.items():
            w.writerow(
                {
                    "Username": username,
                    "Result": v.get("Result", ""),
                    "Feedback": v.get("Feedback", ""),
                }
            )


RESULTS = load_results_csv()

# ---------- Utilities ----------


def possible_keys_for_user(user: discord.abc.User):
    keys = {str(user).strip().lower()}
    if getattr(user, "name", None):
        keys.add(user.name.strip().lower())
    if getattr(user, "global_name", None):
        keys.add(user.global_name.strip().lower())
    disp = getattr(user, "display_name", None)
    if disp:
        keys.add(disp.strip().lower())
    return keys


def color_for_decision(decision: str) -> discord.Color:
    d = (decision or "").lower()
    if d == "accepted":
        return discord.Color.green()
    if d == "denied":
        return discord.Color.red()
    if d == "blacklisted":
        return discord.Color(0x000000)  # black
    return discord.Color.blurple()


def has_lead_supervisor_role(member: discord.Member) -> bool:
    return any(r.id == LEAD_SUPERVISOR_ROLE_ID for r in member.roles) or member.guild_permissions.administrator


# ---------- Time helpers for /shift ----------


def _fmt_date(dt: datetime) -> str:
    try:
        return dt.strftime("%A %B %-d, %Y")
    except ValueError:
        return dt.strftime("%A %B %#d, %Y")


def _fmt_time(dt: datetime) -> str:
    try:
        return dt.strftime("%-I:%M %p")
    except ValueError:
        return dt.strftime("%#I:%M %p")


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def _ts(dt: datetime, style: str = "R") -> str:
    """Return a Discord timestamp tag like <t:1234567890:R>."""
    return f"<t:{_epoch(dt)}:{style}>"


def parse_time_to_dt(time_str: str, tz_name: str = DEFAULT_TZ) -> datetime:
    """
    Accepts friendly inputs and returns a tz-aware datetime in tz_name.
    Examples: "2025-09-23 16:00", "9/23 4:00 PM", "today 4:00 PM", "tomorrow 16:00", "4:00 PM"
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    s = time_str.strip().lower()

    if s.startswith("today ") or s == "today":
        t = s.replace("today", "").strip()
        if not t:
            raise ValueError("Please include a time, e.g., 'today 4:00 PM'.")
        for p in ["%I:%M %p", "%H:%M"]:
            try:
                t_naive = datetime.strptime(t, p)
                return datetime(
                    now.year,
                    now.month,
                    now.day,
                    t_naive.hour,
                    t_naive.minute,
                    tzinfo=tz,
                )
            except ValueError:
                pass
        raise ValueError(
            f"Could not parse time in '{time_str}'. Try 'today 4:00 PM'."
        )

    if s.startswith("tomorrow ") or s == "tomorrow":
        t = s.replace("tomorrow", "").strip()
        if not t:
            raise ValueError("Please include a time, e.g., 'tomorrow 16:00'.")
        for p in ["%I:%M %p", "%H:%M"]:
            try:
                t_naive = datetime.strptime(t, p)
                nd = now + timedelta(days=1)
                return datetime(
                    nd.year,
                    nd.month,
                    nd.day,
                    t_naive.hour,
                    t_naive.minute,
                    tzinfo=tz,
                )
            except ValueError:
                pass
        raise ValueError(
            f"Could not parse time in '{time_str}'. Try 'tomorrow 4:00 PM'."
        )

    # Time-only -> today (or tomorrow if already passed)
    for p in ["%I:%M %p", "%H:%M"]:
        try:
            t_naive = datetime.strptime(s, p)
            dt = datetime(
                now.year,
                now.month,
                now.day,
                t_naive.hour,
                t_naive.minute,
                tzinfo=tz,
            )
            if dt <= now:
                dt = dt + timedelta(days=1)
            return dt
        except ValueError:
            pass

    # Full/partial date patterns
    patterns = [
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %I:%M %p",
        "%Y/%m/%d %I:%M %p",
        "%a %m/%d/%Y %H:%M",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
        "%m/%d %H:%M",
        "%m/%d %I:%M %p",
    ]

    last_err: Exception | None = None
    for p in patterns:
        try:
            dt_naive = datetime.strptime(time_str.strip(), p)
            if "%Y" not in p:
                dt_naive = dt_naive.replace(year=now.year)
            return dt_naive.replace(tzinfo=tz)
        except ValueError as e:
            last_err = e

    raise ValueError(
        f"Could not parse time '{time_str}'. "
        "Try '4:00 PM', 'today 4:00 PM', 'tomorrow 16:00', or '9/23 4:00 PM'. "
        f"Last error: {last_err}"
    )


# -------- Buttons / persistent view --------


class ShiftFollowupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        # Direct Join (link button; Discord controls its color)
        self.add_item(
            discord.ui.Button(
                label="Direct Join",
                style=discord.ButtonStyle.link,
                url="https://www.netransit.net/shift",
            )
        )

    @discord.ui.button(
        label="How to /joinshift",
        style=discord.ButtonStyle.primary,
        custom_id="shift_help_btn",  # needed for persistence across restarts
    )
    async def help_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        msg = (
            "Go To Roblox, click **Play**, and before going to **Lower Mystic** "
            "type **`/joinshift`**.\n"
            "It should teleport you directly to a server."
        )
        embed = discord.Embed(description=msg, color=discord.Color.blurple())
        embed.set_footer(
            text="Any extra issues? Contact the host or make a ticket."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# In-memory shift tracker: message_id -> info
SHIFT_TRACK: dict[int, dict] = {}


# --------------- COG -----------------


class NetCommands(commands.Cog):
    """Cog that holds all the existing slash commands and shift logic."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- helper task used by /shift follow-up ---

    async def schedule_run_followup(self, message_id: int):
        """Runs at the scheduled time; posts the Shift Happening follow-up."""
        info = SHIFT_TRACK.get(message_id)
        if not info:
            print(f"[shift] followup: message {message_id} not found in tracker")
            return

        # Guard for canceled shifts
        if info.get("canceled"):
            print(
                f"[shift] followup: message {message_id} was canceled; skipping."
            )
            return

        when: datetime = info["when"]
        channel = self.bot.get_channel(info["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            print(f"[shift] followup: channel missing for message {message_id}")
            return

        # Wait until time (guard negatives)
        delta = (when - datetime.now(when.tzinfo)).total_seconds()
        if delta > 0:
            print(
                f"[shift] followup: sleeping {int(delta)}s for message {message_id}"
            )
            try:
                await asyncio.sleep(delta)
            except asyncio.CancelledError:
                print(f"[shift] followup: sleeper for {message_id} canceled.")
                return
        else:
            print(
                f"[shift] followup: time already passed by {-int(delta)}s; "
                f"posting now for message {message_id}"
            )

        # Build attendees list from tracked reactions
        reactors = info.get("reactors", set())
        attendees_mentions = " ".join(f"<@{uid}>" for uid in reactors)
        attendee_line = "| |" if not reactors else f"| {attendees_mentions} |"

        # Random big image inside the embed
        images = [
            "https://i.imgur.com/BMIzRKJ.jpeg",
            "https://i.imgur.com/h4KISNW.png",
            "https://i.imgur.com/scoVlB7.png",
            "https://i.imgur.com/rmcIwnq.png",
            "https://i.imgur.com/5cJuCUt.png",
            "https://i.imgur.com/aSJvclP.png",
            "https://i.imgur.com/kztq1gq.jpeg",
            "https://i.imgur.com/wxiIM8C.png",
            "https://i.imgur.com/LgthyeB.png",
            "https://i.imgur.com/XySPomR.png",
        ]
        img_url = random.choice(images)

        join_text = "[THIS](https://www.netransit.net/shift)"
        desc = (
            f"Please use {join_text} link to join.\n"
            "Console use `/joinshift` in the game hub.\n"
            "If you have any problems joining, ping the host."
        )
        embed = discord.Embed(color=discord.Color.blurple(), description=desc)

        host_id = info.get("host_id")
        if host_id:
            embed.add_field(name="Host", value=f"<@{host_id}>", inline=True)
        embed.add_field(name="Attendees", value=attendee_line, inline=False)
        embed.set_image(url=img_url)

        header = "# **Shift Happening**"
        content = (
            f"{header}\n{attendees_mentions}" if attendees_mentions else header
        )

        view = ShiftFollowupView()
        try:
            orig_msg = await channel.fetch_message(message_id)
            await orig_msg.reply(
                content=content,
                embed=embed,
                view=view,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
            print(f"[shift] followup: posted reply under {message_id}")
        except discord.HTTPException as e:
            await channel.send(
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
            print(
                "[shift] followup: posted new msg in channel "
                f"(reply failed): {e}"
            )

    # ---------- events ----------

    @commands.Cog.listener()
    async def on_ready(self):
        # Sync commands and register the persistent view
        guild = discord.Object(id=GUILD_ID)
        try:
            self.bot.tree.clear_commands(guild=None)
            await self.bot.tree.sync(guild=None)
            synced = await self.bot.tree.sync(guild=guild)
            print(
                f"Cleared globals and synced {len(synced)} command(s) "
                f"to guild {GUILD_ID}."
            )
            self.bot.add_view(ShiftFollowupView())
        except Exception as e:
            print("Slash command sync error:", e)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.message_id not in SHIFT_TRACK:
            return

        emoji_ok = False
        if isinstance(payload.emoji, discord.PartialEmoji) and str(NET_EMOJI).startswith("<:"):
            try:
                net_id = int(str(NET_EMOJI).split(":")[-1].strip(">"))
                emoji_ok = payload.emoji.id == net_id
            except ValueError:
                pass
        else:
            emoji_ok = (str(payload.emoji) == NET_EMOJI)

        if not emoji_ok or payload.user_id == self.bot.user.id:
            return

        SHIFT_TRACK[payload.message_id].setdefault("reactors", set()).add(
            payload.user_id
        )

    # ---------- Slash commands ----------

    @app_commands.command(
        name="result",
        description="DMs you your application result.",
    )
    @app_commands.guild_only()
    async def result_cmd(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild or guild.id != GUILD_ID:
            await interaction.response.send_message(
                "This command is not available in this server.",
                ephemeral=True,
            )
            return

        user = interaction.user
        found_key = next(
            (k for k in possible_keys_for_user(user) if k in RESULTS), None
        )
        if not found_key:
            await interaction.response.send_message(
                "❌ Results unavailable (name not found or request window expired).",
                ephemeral=True,
            )
            return

        outcome = RESULTS[found_key]["Result"]
        feedback = RESULTS[found_key]["Feedback"]
        embed = discord.Embed(
            title=f"Your application was {outcome}",
            description=feedback or "No feedback provided.",
            color=color_for_decision(outcome),
        )
        embed.set_footer(text=FOOTER_TEXT)

        try:
            await user.send(embed=embed)
            await interaction.response.send_message(
                "✅ Results sent to your DMs.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I could not DM you. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @app_commands.command(
        name="add",
        description="Add or update a result row (Lead Supervisor only).",
    )
    @app_commands.describe(
        user="Select a server member to add/update",
        decision="Accepted, Denied, or Blacklisted",
        feedback="Feedback to include in the DM",
    )
    @app_commands.guild_only()
    async def add_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        decision: Literal["Accepted", "Denied", "Blacklisted"],
        feedback: str,
    ):
        if not isinstance(interaction.user, discord.Member) or not has_lead_supervisor_role(interaction.user):
            await interaction.response.send_message(
                "❌ You do not have permission to use /add.", ephemeral=True
            )
            return

        username_key = (
            (user.name or user.display_name or str(user)).strip().lower()
        )
        RESULTS[username_key] = {"Result": decision, "Feedback": feedback}
        try:
            save_results_csv(RESULTS)
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to write CSV: {e}", ephemeral=True
            )
            return

        confirm = discord.Embed(
            title="Entry saved",
            description=(
                f"**User:** {user.mention}\n"
                f"**Result:** {decision}\n"
                f"**Feedback:** {feedback}"
            ),
            color=color_for_decision(decision),
        )
        confirm.set_footer(text=FOOTER_TEXT)
        await interaction.response.send_message(
            embed=confirm, ephemeral=True
        )

    @app_commands.command(
        name="reloadcsv",
        description="Reload results from CSV (Lead Supervisor only).",
    )
    @app_commands.guild_only()
    async def reloadcsv_cmd(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not has_lead_supervisor_role(interaction.user):
            await interaction.response.send_message(
                "❌ You do not have permission to use /reloadcsv.",
                ephemeral=True,
            )
            return

        global RESULTS
        RESULTS = load_results_csv()
        await interaction.response.send_message(
            "✅ CSV reloaded.", ephemeral=True
        )

    # ---------- Helper: accept message link or raw ID ----------

    @staticmethod
    def _resolve_message_id(maybe_link_or_id: str) -> int:
        s = maybe_link_or_id.strip()
        if s.isdigit():
            return int(s)
        parts = s.split("/")
        if len(parts) >= 3 and parts[-1].isdigit():
            return int(parts[-1])
        raise ValueError("Please provide a valid message ID or message link.")

    # ---------- /shift (Supervisor) ----------

    @app_commands.command(
        name="shift",
        description=(
            "Create a shift announcement for MBTA/WRTA and track "
            "attendees via :net: reaction."
        ),
    )
    @app_commands.describe(
        game="Select the game (MBTA or WRTA)",
        time_str="Shift date & time (e.g., '4:00 PM', 'today 4:00 PM', or '9/23 16:00')",
        routes="Routes to run (required)",
        buses_on_duty="Buses on duty (required)",
        notes="(Optional) Extra notes to show in the announcement",
    )
    @app_commands.choices(
        game=[
            app_commands.Choice(name="MBTA", value="MBTA"),
            app_commands.Choice(name="WRTA", value="WRTA"),
        ]
    )
    @app_commands.checks.has_role(SUPERVISOR_ROLE_ID)  # Require Supervisor
    @app_commands.guild_only()
    async def shift_cmd(
        self,
        interaction: discord.Interaction,
        game: app_commands.Choice[str],
        time_str: str,
        routes: str,
        buses_on_duty: str,
        notes: str | None = None,
    ):
        try:
            when = parse_time_to_dt(time_str, DEFAULT_TZ)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        game_name = game.value

        loc_value = (
            f"{game_name} <:mbtalogo:1054907034505584747>"
            if game_name == "MBTA"
            else game_name
        )

        embed = discord.Embed(color=discord.Color.brand_green())
        embed.title = "!RUN!"

        if game_name == "MBTA":
            embed.set_thumbnail(url="https://i.imgur.com/uYNgKE3.png")

        embed.add_field(name="Location", value=loc_value, inline=True)
        time_value = f"{_ts(when, 't')} ({_ts(when, 'R')})"
        date_value = _ts(when, "D")
        embed.add_field(name="Time", value=time_value, inline=True)
        embed.add_field(name="Date", value=date_value, inline=False)

        embed.add_field(
            name="Host", value=f"<@{interaction.user.id}>", inline=True
        )
        embed.add_field(name="Routes", value=routes, inline=True)
        embed.add_field(name="Buses On Duty", value=buses_on_duty, inline=True)
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)

        embed.add_field(
            name="\u200b",
            value=f"React {NET_EMOJI} if you plan on attending!",
            inline=False,
        )
        embed.set_footer(
            text=(
                f"{FOOTER_TEXT}\n\n"
                "You must react with the emoji if you want to be notified."
            )
        )

        shifts_channel = self.bot.get_channel(SHIFTS_CHANNEL_ID)
        if not isinstance(shifts_channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "❌ I couldn't find the shifts channel.", ephemeral=True
            )
            return

        content_ping = f"<@&{RUNS_NOTIFIED_ROLE_ID}>"
        await interaction.response.send_message(
            f"✅ Shift posted to {shifts_channel.mention}.", ephemeral=True
        )
        posted_msg = await shifts_channel.send(
            content=content_ping,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

        try:
            await posted_msg.add_reaction(NET_EMOJI)
        except discord.HTTPException:
            await shifts_channel.send(
                "⚠️ I couldn't add the :net: reaction. Check NET_EMOJI config."
            )

        SHIFT_TRACK[posted_msg.id] = {
            "when": when,
            "reactors": set(),
            "channel_id": posted_msg.channel.id,
            "host_id": interaction.user.id,
            "task": None,
        }

        print(
            f"[shift] scheduled followup at {when.isoformat()} "
            f"for message {posted_msg.id}"
        )
        task = asyncio.create_task(self.schedule_run_followup(posted_msg.id))
        SHIFT_TRACK[posted_msg.id]["task"] = task

    # ---------- /cancelshift (Supervisor) ----------

    @app_commands.command(
        name="cancelshift",
        description=(
            "Cancel a posted shift (stops its follow-up and notifies attendees)."
        ),
    )
    @app_commands.describe(
        message_id_or_link="Message ID or link of the original shift announcement.",
        notes="(Optional) Extra notes to include in the cancel message.",
    )
    @app_commands.checks.has_role(SUPERVISOR_ROLE_ID)
    @app_commands.guild_only()
    async def cancelshift_cmd(
        self,
        interaction: discord.Interaction,
        message_id_or_link: str,
        notes: str | None = None,
    ):
        try:
            target_id = self._resolve_message_id(message_id_or_link)
        except ValueError as e:
            await interaction.response.send_message(
                f"❌ {e}", ephemeral=True
            )
            return

        info = SHIFT_TRACK.get(target_id)
        if not info:
            await interaction.response.send_message(
                "❌ I can't find a tracked shift with that message ID.",
                ephemeral=True,
            )
            return

        task: asyncio.Task | None = info.get("task")
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        reactors = info.get("reactors", set())
        attendees_mentions = " ".join(f"<@{uid}>" for uid in reactors)
        attendees_ping_line = attendees_mentions if attendees_mentions else ""

        when: datetime = info["when"]
        when_str = f"{_fmt_date(when)} at {_fmt_time(when)}"
        host_id = info.get("host_id")
        host_mention = f"<@{host_id}>" if host_id else "the host"

        channel = self.bot.get_channel(info["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ I can't access the original channel.", ephemeral=True
            )
            return

        header = "# Shift Canceled"
        desc = (
            f"Unfortunately, the shift that was supposed to happen **{when_str}** "
            f"was canceled by {host_mention}."
        )
        embed = discord.Embed(
            title="Shift Canceled", description=desc, color=discord.Color.red()
        )
        embed.add_field(name="Host", value=host_mention, inline=True)
        embed.add_field(name="Scheduled Time", value=when_str, inline=True)
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)
        embed.set_footer(text=FOOTER_TEXT)

        try:
            orig_msg = await channel.fetch_message(target_id)
            await orig_msg.reply(
                content=f"{header}\n{attendees_ping_line}",
                embed=embed,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
        except discord.HTTPException:
            await channel.send(
                content=f"{header}\n{attendees_ping_line}",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )

        info["canceled"] = True
        SHIFT_TRACK[target_id] = info
        await interaction.response.send_message(
            "✅ Shift canceled and attendees notified.", ephemeral=True
        )

    # ---------- /shiftstop (Supervisor) ----------

    @app_commands.command(
        name="shiftstop",
        description="Announce that a shift is over (no pings).",
    )
    @app_commands.describe(
        message_id_or_link="Message ID or link of the original shift announcement."
    )
    @app_commands.checks.has_role(SUPERVISOR_ROLE_ID)
    @app_commands.guild_only()
    async def shiftstop_cmd(
        self,
        interaction: discord.Interaction,
        message_id_or_link: str,
    ):
        try:
            target_id = self._resolve_message_id(message_id_or_link)
        except ValueError as e:
            await interaction.response.send_message(
                f"❌ {e}", ephemeral=True
            )
            return

        info = SHIFT_TRACK.get(target_id)
        if not info:
            await interaction.response.send_message(
                "❌ I can't find a tracked shift with that message ID.",
                ephemeral=True,
            )
            return

        channel = self.bot.get_channel(info["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ I can't access the original channel.", ephemeral=True
            )
            return

        header = "# Shift Over"
        desc = "The shift has now concluded.\nPlease wait to participate in the next shift."
        embed = discord.Embed(
            title="Shift Over",
            description=desc,
            color=discord.Color.dark_gray(),
        )
        embed.set_footer(text=FOOTER_TEXT)

        try:
            orig_msg = await channel.fetch_message(target_id)
            await orig_msg.reply(
                content=header,
                embed=embed,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions(
                    users=False, roles=False, everyone=False
                ),
            )
        except discord.HTTPException:
            await channel.send(
                content=header,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    users=False, roles=False, everyone=False
                ),
            )

        await interaction.response.send_message(
            "✅ Posted “Shift Over.”", ephemeral=True
        )


async def setup(bot: commands.Bot):
    cog = NetCommands(bot)
    await bot.add_cog(cog)

    # Register slash commands as guild commands
    guild = discord.Object(id=GUILD_ID)
    bot.tree.add_command(cog.result_cmd, guild=guild)
    bot.tree.add_command(cog.add_cmd, guild=guild)
    bot.tree.add_command(cog.reloadcsv_cmd, guild=guild)
    bot.tree.add_command(cog.shift_cmd, guild=guild)
    bot.tree.add_command(cog.cancelshift_cmd, guild=guild)
    bot.tree.add_command(cog.shiftstop_cmd, guild=guild)

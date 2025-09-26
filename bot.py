import csv
import os
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from typing import Literal
from discord import app_commands
from discord.ext import commands

# ==================== CONFIG =====================
CSV_PATH = "results.csv"

# Roles
LEAD_SUPERVISOR_ROLE_ID = 1351333124965142600   # for /add, /reloadcsv
SUPERVISOR_ROLE_ID = 947288094804176957         # minimum role to run /shift

# Guild/Channels/Emojis
GUILD_ID = 882441222487162912
SHIFTS_CHANNEL_ID = 1329659267963420703         # #shifts
RUNS_NOTIFIED_ROLE_ID = 1392329893282578504     # @Runs Notified (or "run notifications")

# If :net: is a custom emoji, set the full tag like "<:net:123456789012345678>"
# If it's a standard Unicode emoji, you can put the emoji itself here.
NET_EMOJI = "<:net:1323882053858492437>"  # <-- REPLACE with your :net: (e.g., "<:net:123456789012345678>")
DEFAULT_TZ = "America/New_York"                 # MBTA/WRTA locale

# ---------- Footer text ----------
FOOTER_TEXT = "More questions or concerns? Please open a ticket inside the New England Transit Discord Server."
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
            w.writerow({"Username": username, "Result": v.get("Result", ""), "Feedback": v.get("Feedback", "")})

RESULTS = load_results_csv()


# ---------- Utilities ----------
def possible_keys_for_user(user: discord.abc.User):
    keys = {str(user).strip().lower()}
    if getattr(user, "name", None): keys.add(user.name.strip().lower())
    if getattr(user, "global_name", None): keys.add(user.global_name.strip().lower())
    disp = getattr(user, "display_name", None)
    if disp: keys.add(disp.strip().lower())
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
    # Example: Thursday September 25, 2025  (Windows safe)
    try:
        return dt.strftime("%A %B %-d, %Y")
    except ValueError:
        return dt.strftime("%A %B %#d, %Y")

def _fmt_time(dt: datetime) -> str:
    # Example: 4:00 PM  (Windows safe)
    try:
        return dt.strftime("%-I:%M %p")
    except ValueError:
        return dt.strftime("%#I:%M %p")

def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())

def _ts(dt: datetime, style: str = "R") -> str:
    return f"<t:{_epoch(dt)}:{style}>"

def parse_time_to_dt(time_str: str, tz_name: str = DEFAULT_TZ) -> datetime:
    """
    Accepts friendly inputs and returns a tz-aware datetime in tz_name.
    Supported examples:
      - "2025-09-23 16:00"
      - "2025/09/23 4:00 PM"
      - "Tue 9/23/2025 16:00"
      - "9/23 4:00 PM"           (assumes current year)
      - "today 4:00 PM"
      - "tomorrow 16:00"
      - "4:00 PM"                (assumes today, else tomorrow if already passed)
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    s = time_str.strip().lower()

    # Keywords today/tomorrow + time
    if s.startswith("today ") or s == "today":
        t = s.replace("today", "").strip()
        if not t:
            raise ValueError("Please include a time, e.g., 'today 4:00 PM'.")
        for p in ["%I:%M %p", "%H:%M"]:
            try:
                t_naive = datetime.strptime(t, p)
                dt = datetime(now.year, now.month, now.day, t_naive.hour, t_naive.minute, tzinfo=tz)
                return dt
            except ValueError:
                pass
        raise ValueError(f"Could not parse time in '{time_str}'. Try 'today 4:00 PM'.")

    if s.startswith("tomorrow ") or s == "tomorrow":
        t = s.replace("tomorrow", "").strip()
        if not t:
            raise ValueError("Please include a time, e.g., 'tomorrow 16:00'.")
        for p in ["%I:%M %p", "%H:%M"]:
            try:
                t_naive = datetime.strptime(t, p)
                next_day = now + timedelta(days=1)
                dt = datetime(next_day.year, next_day.month, next_day.day, t_naive.hour, t_naive.minute, tzinfo=tz)
                return dt
            except ValueError:
                pass
        raise ValueError(f"Could not parse time in '{time_str}'. Try 'tomorrow 4:00 PM'.")

    # Time-only → today or tomorrow if already passed
    for p in ["%I:%M %p", "%H:%M"]:
        try:
            t_naive = datetime.strptime(s, p)
            dt = datetime(now.year, now.month, now.day, t_naive.hour, t_naive.minute, tzinfo=tz)
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
    last_err = None
    for p in patterns:
        try:
            dt_naive = datetime.strptime(time_str.strip(), p)
            if "%Y" not in p:
                dt_naive = dt_naive.replace(year=now.year)
            return dt_naive.replace(tzinfo=tz)
        except ValueError as e:
            last_err = e

    raise ValueError(
        f"Could not parse time '{time_str}'. Try '4:00 PM', 'today 4:00 PM', 'tomorrow 16:00', or '9/23 4:00 PM'. Last error: {last_err}"
    )


# ---------- Bot ----------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# In-memory shift tracker: message_id -> info
SHIFT_TRACK: dict[int, dict] = {}


@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=GUILD_ID)
        tree.clear_commands(guild=None)
        await tree.sync(guild=None)
        synced = await tree.sync(guild=guild)
        print(f"Cleared globals and synced {len(synced)} command(s) to guild {GUILD_ID}. Logged in as {bot.user}.")
    except Exception as e:
        print("Slash command sync error:", e)


# ---------- /result ----------
@tree.command(name="result", description="DMs you your application result.", guild=discord.Object(id=GUILD_ID))
async def result_cmd(interaction: discord.Interaction):
    user = interaction.user
    found_key = next((k for k in possible_keys_for_user(user) if k in RESULTS), None)
    if not found_key:
        await interaction.response.send_message("❌ Results unavailable (name not found or request window expired).")
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
        await interaction.response.send_message("✅ Results sent to your DMs.")
    except discord.Forbidden:
        await interaction.response.send_message("❌ I could not DM you. Please enable DMs from server members and try again.")


# ---------- /add (Lead Supervisor) ----------
@tree.command(name="add", description="Add or update a result row (Lead Supervisor only).", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    user="Select a server member to add/update",
    decision="Accepted, Denied, or Blacklisted",
    feedback="Feedback to include in the DM"
)
@app_commands.guild_only()
async def add_cmd(
    interaction: discord.Interaction,
    user: discord.Member,
    decision: Literal["Accepted", "Denied", "Blacklisted"],
    feedback: str
):
    if not isinstance(interaction.user, discord.Member) or not has_lead_supervisor_role(interaction.user):
        await interaction.response.send_message("❌ You do not have permission to use /add.", ephemeral=True)
        return

    username_key = (user.name or user.display_name or str(user)).strip().lower()
    RESULTS[username_key] = {"Result": decision, "Feedback": feedback}

    try:
        save_results_csv(RESULTS)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to write CSV: {e}", ephemeral=True)
        return

    confirm = discord.Embed(
        title="Entry saved",
        description=f"**User:** {user.mention}\n**Result:** {decision}\n**Feedback:** {feedback}",
        color=color_for_decision(decision),
    )
    confirm.set_footer(text=FOOTER_TEXT)
    await interaction.response.send_message(embed=confirm, ephemeral=True)


# ---------- /reloadcsv (Lead Supervisor) ----------
@tree.command(name="reloadcsv", description="Reload results from CSV (Lead Supervisor only).", guild=discord.Object(id=GUILD_ID))
@app_commands.guild_only()
async def reloadcsv_cmd(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not has_lead_supervisor_role(interaction.user):
        await interaction.response.send_message("❌ You do not have permission to use /reloadcsv.", ephemeral=True)
        return
    global RESULTS
    RESULTS = load_results_csv()
    await interaction.response.send_message("🔄 CSV reloaded.", ephemeral=True)


@tree.command(
    name="shift",
    description="Create a shift announcement for MBTA/WRTA and track attendees via :net: reaction.",
    guild=discord.Object(id=GUILD_ID),
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
    interaction: discord.Interaction,
    game: app_commands.Choice[str],
    time_str: str,
    routes: str,
    buses_on_duty: str,
    notes: str | None = None,
):
    # Parse time
    try:
        when = parse_time_to_dt(time_str, DEFAULT_TZ)
    except ValueError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return

    game_name = game.value
    epoch = _epoch(when)

    # Location field
    if game_name == "MBTA":
        loc_value = f"{game_name} <:mbtalogo:1054907034505584747>"
    else:
        loc_value = game_name

    # Build announcement embed (cleaner + pure timestamps)
    embed = discord.Embed(color=discord.Color.brand_green())
    embed.title = "!RUN!"
    # (Removed the old 'MBTA: Run' line.)
    embed.add_field(name="Location", value=loc_value, inline=True)
    embed.add_field(name="Time", value=f"<t:{epoch}:T> (<t:{epoch}:R>)", inline=True)
    embed.add_field(name="Date", value=f"<t:{epoch}:D>", inline=False)
    embed.add_field(name="Routes", value=routes, inline=True)
    embed.add_field(name="Buses On Duty", value=buses_on_duty, inline=True)
    if notes:
        embed.add_field(name="Notes", value=notes, inline=False)
    embed.add_field(name="\u200b", value=f"React {NET_EMOJI} if you plan on attending!", inline=False)

    # Post to #shifts with role ping OUTSIDE the embed
    shifts_channel = interaction.client.get_channel(SHIFTS_CHANNEL_ID)
    if not isinstance(shifts_channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("❌ I couldn't find the shifts channel.", ephemeral=True)
        return

    content_ping = f"<@&{RUNS_NOTIFIED_ROLE_ID}>"
    await interaction.response.send_message(f"✅ Shift posted to {shifts_channel.mention}.", ephemeral=True)

    posted_msg = await shifts_channel.send(
        content=content_ping,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True)
    )

    # Add :net: reaction
    try:
        await posted_msg.add_reaction(NET_EMOJI)
    except discord.HTTPException:
        await shifts_channel.send("⚠️ I couldn't add the :net: reaction. Check NET_EMOJI config.")

    # Track shift for follow-up
    SHIFT_TRACK[posted_msg.id] = {
        "when": when,
        "reactors": set(),
        "channel_id": posted_msg.channel.id,
        "host_id": interaction.user.id,
    }

    asyncio.create_task(schedule_run_followup(bot, posted_msg.id))

# ---------- Reaction tracker for /shift ----------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.message_id not in SHIFT_TRACK:
        return

    # Check emoji match (supports custom or unicode)
    emoji_ok = False
    if isinstance(payload.emoji, discord.PartialEmoji) and str(NET_EMOJI).startswith("<:"):
        try:
            net_id = int(str(NET_EMOJI).split(":")[-1].strip(">"))
            emoji_ok = payload.emoji.id == net_id
        except ValueError:
            pass
    else:
        emoji_ok = (str(payload.emoji) == NET_EMOJI)

    if not emoji_ok or payload.user_id == bot.user.id:
        return

    SHIFT_TRACK[payload.message_id]["reactors"].add(payload.user_id)


# ---------- Main ----------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable not set.")
    bot.run(token)





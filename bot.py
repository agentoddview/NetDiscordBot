import csv
import os
import discord
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Literal
from discord import app_commands
from discord.ext import commands

# ==================== CONFIG =====================
CSV_PATH = "results.csv"
LEAD_SUPERVISOR_ROLE_ID = 1351333124965142600
GUILD_ID = 882441222487162912               # e.g., 123456789012345678
# =================================================

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

# ---------- Footer text ----------
FOOTER_TEXT = "More questions or concerns? Please open a ticket inside the New England Transit Discord Server."

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=GUILD_ID)
        tree.clear_commands(guild=None)      # clear globals to avoid duplicates
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
        # 👇 no ephemeral here → visible in channel
        await interaction.response.send_message("✅ Results sent to your DMs.")
    except discord.Forbidden:
        await interaction.response.send_message("❌ I could not DM you. Please enable DMs from server members and try again.")

# ---------- Permission helper ----------
def has_lead_supervisor_role(member: discord.Member) -> bool:
    return any(r.id == LEAD_SUPERVISOR_ROLE_ID for r in member.roles) or member.guild_permissions.administrator

# ---------- /add ----------
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

# ---------- /reloadcsv ----------
@tree.command(name="reloadcsv", description="Reload results from CSV (Lead Supervisor only).", guild=discord.Object(id=GUILD_ID))
@app_commands.guild_only()
async def reloadcsv_cmd(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not has_lead_supervisor_role(interaction.user):
        await interaction.response.send_message("❌ You do not have permission to use /reloadcsv.", ephemeral=True)
        return
    global RESULTS
    RESULTS = load_results_csv()
    await interaction.response.send_message("🔄 CSV reloaded.", ephemeral=True)

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN") or "MTQxOTA1MDE1MzYzMjk4OTIxNA.Ges_5j.-ZJvcCMIV54sNxJbrYE4loyOyEPsiWJ2OugS7o"

    # bot_shift_feature.py
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

# ------------------ CONFIG ------------------
GUILD_ID = 0                     # <- (optional) set if you want to sync to a single guild faster
SHIFTS_CHANNEL_ID = 1329659267963420703
RUNS_NOTIFIED_ROLE_ID = 1332862724039774289

# If :net: is a custom emoji, put its full tag here like "<:net:123456789012345678>"
# If it's a standard Unicode emoji, you can just put the emoji itself.
NET_EMOJI = "<:net:123456789012345678>"  # <-- set this to your actual :net: emoji tag

# Default timezone for MBTA/WRTA. Change if you want or add as a command option.
DEFAULT_TZ = "America/New_York"

# --------------------------------------------

intents = discord.Intents.default()
intents.guilds = True
intents.reactions = True
intents.members = True  # to resolve user mentions cleanly
bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory store: shift_message_id -> {"when": datetime, "reactors": set(user_ids), "channel_id": int}
SHIFT_TRACK: dict[int, dict] = {}


class GameChoice(discord.Enum):
    MBTA = "MBTA"
    WRTA = "WRTA"


def parse_time_to_dt(time_str: str, tz_name: str = DEFAULT_TZ) -> datetime:
    """
    Accepts a few friendly formats and returns a timezone-aware datetime.
    Examples it accepts (in DEFAULT_TZ):
      - 2025-09-23 16:00
      - 2025/09/23 4:00 PM
      - Tue 9/23/2025 16:00
      - 9/23 4:00 PM        (assumes current year)
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)

    # Try several patterns from strict to friendly
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
            # If year missing, assume current year
            if "%Y" not in p:
                dt_naive = dt_naive.replace(year=now.year)
            return dt_naive.replace(tzinfo=tz)
        except ValueError as e:
            last_err = e

    raise ValueError(
        f"Could not parse time '{time_str}'. Try formats like '2025-09-23 16:00' or '9/23 4:00 PM'. Last error: {last_err}"
    )


def discord_abs_date(dt: datetime) -> str:
    # Example: Tuesday September 23, 2025
    return dt.strftime("%A %B %-d, %Y") if hasattr(dt, "strftime") else ""


def discord_abs_time(dt: datetime) -> str:
    # Example: 4:00 PM
    return dt.strftime("%-I:%M %p")


def discord_ts(dt: datetime, style: str = "R") -> str:
    # <t:epoch:R> for relative; use :F for full, :D for date, :T for time
    epoch = int(dt.timestamp())
    return f"<t:{epoch}:{style}>"


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID)) if GUILD_ID else await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Command sync failed: {e}")
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.tree.command(name="shift", description="Create a shift announcement for MBTA/WRTA and track attendees via :net: reaction.")
@app_commands.describe(
    game="Select the game (MBTA or WRTA)",
    time_str="Shift date & time (e.g., '2025-09-23 4:00 PM' or '9/23 16:00')",
    routes="Routes to run (required)",
    buses_on_duty="Buses on duty (required)",
)
@app_commands.choices(
    game=[
        app_commands.Choice(name="MBTA", value="MBTA"),
        app_commands.Choice(name="WRTA", value="WRTA"),
    ]
)
async def shift(
    interaction: discord.Interaction,
    game: app_commands.Choice[str],
    time_str: str,
    routes: str,
    buses_on_duty: str,
):
    # Parse time
    try:
        when = parse_time_to_dt(time_str, DEFAULT_TZ)
    except ValueError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return

    # Compose lines to match your sample as closely as Discord allows
    game_name = game.value
    title_line = "!RUN!"
    location_line = f"Location: {game_name} :mbtalogo:" if game_name == "MBTA" else f"Location: {game_name}"
    time_line = f"Time: {discord_abs_time(when)} ({discord_ts(when, 'R')})"
    date_line = f"Date: {discord_abs_date(when)}"
    routes_line = f"Routes: {routes}"
    buses_line = f"Buses On Duty: {buses_on_duty}"
    role_ping = f"<@&{RUNS_NOTIFIED_ROLE_ID}>"

    embed = discord.Embed(color=discord.Color.brand_green(), description="")
    embed.title = title_line
    embed.add_field(name="MBTA: Run" if game_name == "MBTA" else "WRTA: Run", value="\u200b", inline=False)
    embed.add_field(name="Location", value=f"{game_name} :mbtalogo:" if game_name == "MBTA" else game_name, inline=True)
    embed.add_field(name="Time", value=f"{discord_abs_time(when)} ({discord_ts(when, 'R')})", inline=True)
    embed.add_field(name="Date", value=discord_abs_date(when), inline=False)
    embed.add_field(name="Routes", value=routes, inline=True)
    embed.add_field(name="Buses On Duty", value=buses_on_duty, inline=True)
    embed.add_field(name="\u200b", value=f"{role_ping}\nReact {NET_EMOJI} if you plan on attending!", inline=False)

    shifts_channel = interaction.client.get_channel(SHIFTS_CHANNEL_ID)
    if not isinstance(shifts_channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("❌ I couldn't find the shifts channel.", ephemeral=True)
        return

    # Post in #shifts
    await interaction.response.send_message(
        f"✅ Shift posted to {shifts_channel.mention}.",
        ephemeral=True
    )

    posted_msg = await shifts_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))

    # Add :net: reaction
    try:
        await posted_msg.add_reaction(NET_EMOJI)
    except discord.HTTPException:
        # fallback if NET_EMOJI isn't set correctly
        await shifts_channel.send("⚠️ I couldn't add the :net: reaction. Check NET_EMOJI config.")

    # Track this shift
    SHIFT_TRACK[posted_msg.id] = {
        "when": when,
        "reactors": set(),
        "channel_id": posted_msg.channel.id,
        "original_message_id": posted_msg.id,
        "game": game_name,
        "routes": routes,
        "buses": buses_on_duty,
    }

    # Schedule the follow-up
    asyncio.create_task(schedule_run_followup(posted_msg.id))


async def schedule_run_followup(message_id: int):
    info = SHIFT_TRACK.get(message_id)
    if not info:
        return

    when: datetime = info["when"]
    channel = bot.get_channel(info["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return

    # Sleep until the time (clip to a max to protect against negatives)
    delta = (when - datetime.now(when.tzinfo)).total_seconds()
    if delta > 0:
        await asyncio.sleep(delta)

    # Prepare attendee list from reactions we collected
    reactors = info.get("reactors", set())
    if not reactors:
        attendee_line = "| |"
    else:
        mentions = " ".join(f"<@{uid}>" for uid in reactors)
        attendee_line = f"| {mentions} |"

    # Build the follow-up text, reply to the original
    game = info["game"]
    routes = info["routes"]
    buses = info["buses"]

    followup_lines = [
        "**RUN**",
        "Please use THIS (https://www.netransit.net/shift) link to join, Console use /joinshift in the game hub. "
        "If you have any problems joining ping me in ⁠main-chatroom.\n",
        attendee_line,
    ]

    try:
        orig_msg = await channel.fetch_message(message_id)
        await orig_msg.reply("\n".join(followup_lines), mention_author=False, allowed_mentions=discord.AllowedMentions(users=True))
    except discord.HTTPException:
        # If we can't reply, just send a new message
        await channel.send("\n".join(followup_lines), allowed_mentions=discord.AllowedMentions(users=True))

    # (Optional) cleanup: you could del SHIFT_TRACK[message_id] here if you don't need it anymore
    # del SHIFT_TRACK[message_id]


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # We only care about :net: reactions on tracked shift messages
    if payload.message_id not in SHIFT_TRACK:
        return

    # Check emoji match (handles custom or unicode)
    emoji_ok = False
    if isinstance(payload.emoji, discord.PartialEmoji) and NET_EMOJI.startswith("<:"):
        # Custom emoji match by ID if available
        try:
            net_id = int(NET_EMOJI.split(":")[-1].strip(">"))
            emoji_ok = payload.emoji.id == net_id
        except ValueError:
            pass
    else:
        # Fall back to comparing names (works for unicode if NET_EMOJI is unicode)
        emoji_ok = (str(payload.emoji) == NET_EMOJI)

    if not emoji_ok:
        return

    # Ignore bot self-reactions
    if payload.user_id == bot.user.id:
        return

    SHIFT_TRACK[payload.message_id]["reactors"].add(payload.user_id)


# ---------- Startup ----------
# bot.run("YOUR_TOKEN")


    bot.run(token)




import csv
import os

# Read from env with sensible defaults
CSV_PATH = os.getenv("RESULTS_PATH", "results.csv")

# Put your real IDs in Railway’s variables later (keep defaults for local)
LEAD_SUPERVISOR_ROLE_ID = int(os.getenv("LEAD_SUPERVISOR_ROLE_ID", "1351333124965142600"))
GUILD_ID = int(os.getenv("GUILD_ID", "123456789012345678"))

# Footer text you wanted
FOOTER_TEXT = os.getenv(
    "FOOTER_TEXT",
    "More questions or concerns? Please open a ticket inside the New England Transit Discord Server."
)

# At the very bottom when running:
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN") or "MTQxOTA1MDE1MzYzMjk4OTIxNA.Ges_5j.-ZJvcCMIV54sNxJbrYE4loyOyEPsiWJ2OugS7o"
    bot.run(token)

import discord
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

    bot.run(token)


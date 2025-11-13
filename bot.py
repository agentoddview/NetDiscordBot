import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load token from .env or environment variable
load_dotenv(Path(__file__).parent / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set.")

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.reactions = True  # needed for shift reaction tracking

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook():
    """Load cogs before the bot becomes ready."""
    initial_extensions = [
    "cogs.net_commands",     # your slash commands + shift engine
    "cogs.shift_tracking",   # clock system
    "cogs.loa",              # LOA system
    "cogs.modlog",           # mod logs
    "cogs.config",           # /netconfig
    "cogs.moderation",       # /moderate
]

    for ext in initial_extensions:
        try:
            await bot.load_extension(ext)
            print(f"Loaded extension {ext}")
        except Exception as e:
            print(f"Failed to load extension {ext}: {e}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")


if __name__ == "__main__":
    bot.run(TOKEN)


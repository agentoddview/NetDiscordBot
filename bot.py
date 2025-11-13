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
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.event
async def setup_hook():
    """Load all cogs when the bot starts."""
    initial_extensions = [
        "cogs.shift_tracking",
        "cogs.loa",
        "cogs.modlog",
    ]

    for ext in initial_extensions:
        try:
            await bot.load_extension(ext)
            print(f"Loaded extension {ext}")
        except Exception as e:
            print(f"Failed to load extension {ext}: {e}")


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("Pong!")


if __name__ == "__main__":
    bot.run(TOKEN)

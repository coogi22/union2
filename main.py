import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
STATUS = os.getenv("STATUS", "Redeeming Keys")
GUILD_ID = 1345153296360542271

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ✅ Only load what you actually use
EXTENSIONS = [
    "commands.shop",
    "commands.checkorder",
    "commands.tickets",
]

@bot.event
async def setup_hook():
    print("🔄 Loading extensions...")
    for ext in EXTENSIONS:
        try:
            await bot.load_extension(ext)
            print(f"✅ Loaded extension: {ext}")
        except Exception as e:
            print(f"❌ Extension load failed {ext}: {e}")

    # Guild sync (instant)
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🏠 Synced {len(synced)} commands to guild {GUILD_ID}")
    except Exception as e:
        print(f"❌ Guild sync failed: {e}")

    # Global sync (optional)
    try:
        global_synced = await bot.tree.sync()
        print(f"🌍 Synced {len(global_synced)} global commands")
    except Exception as e:
        print(f"❌ Global sync failed: {e}")

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game(STATUS))
    print(f"✅ Bot ready: {bot.user} (ID: {bot.user.id})")

async def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Check your .env file next to main.py")

    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

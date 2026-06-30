import os
import sqlite3
import time
from collections import defaultdict, deque

import discord
from discord.ext import commands
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# --- Config ---
MODEL = "llama-3.1-8b-instant"
CONTEXT_WINDOW = 8          # messages kept per channel
DAILY_LIMIT = 30            # AI replies per user per day
MAX_TOKENS = 400

SYSTEM_PROMPT = (
    "You are Sir Meowington, a regular member of this Discord server. You talk "
    "like a normal person hanging out in the server - witty, dry-humored, a bit "
    "sarcastic, with your own opinions and a casual texting style (lowercase is "
    "fine, no need for formal phrasing). You're not here to act like a customer "
    "service bot - you're just vibing in chat. Keep replies short and casual "
    "(1-4 sentences usually) unless the conversation clearly calls for more. "
)

groq_client = Groq(api_key=GROQ_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory conversation context: channel_id -> deque of {role, content}
conversations = defaultdict(lambda: deque(maxlen=CONTEXT_WINDOW))

# --- SQLite for daily rate limiting ---
db = sqlite3.connect("usage.db")
db.execute("""
CREATE TABLE IF NOT EXISTS usage (
    user_id TEXT,
    day TEXT,
    count INTEGER,
    PRIMARY KEY (user_id, day)
)
""")
db.commit()


def today_str():
    return time.strftime("%Y-%m-%d")


def get_usage(user_id: str) -> int:
    row = db.execute(
        "SELECT count FROM usage WHERE user_id = ? AND day = ?",
        (user_id, today_str()),
    ).fetchone()
    return row[0] if row else 0


def increment_usage(user_id: str):
    day = today_str()
    db.execute(
        """
        INSERT INTO usage (user_id, day, count) VALUES (?, ?, 1)
        ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1
        """,
        (user_id, day),
    )
    db.commit()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    # Respond only when mentioned
    if bot.user not in message.mentions:
        return

    user_id = str(message.author.id)
    usage = get_usage(user_id)

    if usage >= DAILY_LIMIT:
        await message.reply(
            "You're out of AI credits for today. Come back tomorrow - "
            "I need my rest too.",
            mention_author=False,
        )
        return

    # Clean the mention out of the message text
    content = message.content
    for mention in message.mentions:
        content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
    content = content.strip()

    if not content:
        await message.reply("ermm...do you need something?", mention_author=False)
        return

    channel_key = str(message.channel.id)
    history = conversations[channel_key]
    history.append({"role": "user", "content": content})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(history)

    async with message.channel.typing():
        try:
            response = groq_client.chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=MAX_TOKENS,
            )
            reply_text = response.choices[0].message.content
        except Exception as e:
            print(f"Groq API error: {e}")
            await message.reply(
                "Something broke on my end, I think?. Could you try again a little later?",
                mention_author=False,
            )
            return

    history.append({"role": "assistant", "content": reply_text})
    increment_usage(user_id)

    # Discord has a 2000 char limit per message
    if len(reply_text) > 1900:
        reply_text = reply_text[:1900] + "..."

    await message.reply(reply_text, mention_author=False)


@bot.command(name="usage")
async def usage_command(ctx: commands.Context):
    """Check how many AI replies you've used today."""
    user_id = str(ctx.author.id)
    used = get_usage(user_id)
    await ctx.reply(
        f"You've used {used}/{DAILY_LIMIT} AI replies today.",
        mention_author=False,
    )


@bot.command(name="reset_context")
async def reset_context(ctx: commands.Context):
    """Clear the bot's memory of this channel's conversation."""
    channel_key = str(ctx.channel.id)
    conversations[channel_key].clear()
    await ctx.reply("Context cleared. Fresh start.", mention_author=False)


if __name__ == "__main__":
    if not DISCORD_TOKEN or not GROQ_API_KEY:
        raise SystemExit(
            "Missing DISCORD_TOKEN or GROQ_API_KEY. Copy .env.example to .env and fill it in."
        )
    bot.run(DISCORD_TOKEN)

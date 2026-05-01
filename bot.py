import discord
from discord.ext import commands, tasks
from discord import app_commands
from groq import Groq
import asyncio
import time
import traceback
import random
import json
import os
import io
import wave
import struct
import tempfile
from datetime import datetime, timedelta
import aiohttp
from typing import Optional
from dotenv import load_dotenv

# ================= LOAD ENV =================
load_dotenv()

DISCORD_TOKEN  = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")   # X-Master-Key from jsonbin.io
JSONBIN_BIN_ID  = os.getenv("JSONBIN_BIN_ID")    # Bin ID from jsonbin.io
PREFIX         = os.getenv("PREFIX", ".")
RESTART_DELAY  = int(os.getenv("RESTART_DELAY", "30"))
TOPGG_TOKEN    = os.getenv("TOPGG_TOKEN", "")
TOPGG_BOT_ID   = os.getenv("TOPGG_BOT_ID", "")
TOPGG_URL      = "https://top.gg/bot/{bot_id}/vote"
ZONOS_API_KEY  = os.getenv("ZONOS_API_KEY", "")   # Zyphra Zonos TTS API key

JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
JSONBIN_HEADERS: dict[str, str] = {
    "Content-Type":    "application/json",
    "X-Master-Key":    JSONBIN_API_KEY or "",
    "X-Bin-Versioning": "false",
}

# =========================================

assert GROQ_API_KEY   is not None, "GROQ_API_KEY is not set in .env!"
assert JSONBIN_API_KEY is not None, "JSONBIN_API_KEY is not set in .env!"
assert JSONBIN_BIN_ID  is not None, "JSONBIN_BIN_ID is not set in .env!"
groq_client = Groq(api_key=GROQ_API_KEY)

# ============== PERSISTENT STORAGE (JSONBin) ==================
# All data lives in a single JSONBin bin — nothing written to disk.
# Auto-saved every 60 s if dirty; loaded once at startup.

_dirty = False

def _parse_raw(raw: dict) -> tuple[dict, dict, dict, dict, dict, int]:
    user_points = {int(k): v for k, v in raw.get("user_points", {}).items()}
    user_stats: dict = {}
    for k, v in raw.get("user_stats", {}).items():
        user_stats[int(k)] = {
            "commands_used": v["commands_used"],
            "last_seen": datetime.fromisoformat(v["last_seen"])
        }
    user_personas  = {int(k): v for k, v in raw.get("user_personas",  {}).items()}
    daily_claimed  = {int(k): v for k, v in raw.get("daily_claimed",  {}).items()}
    voted_users    = {int(k): v for k, v in raw.get("voted_users",    {}).items()}
    bot_msg_count  = int(raw.get("bot_message_count", 0))
    return user_points, user_stats, user_personas, daily_claimed, voted_users, bot_msg_count

async def load_data_async() -> tuple[dict, dict, dict, dict, dict, int]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                JSONBIN_URL, headers=JSONBIN_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    result = _parse_raw(payload.get("record", {}))
                    print(f"✅ Loaded data from JSONBin ({len(result[0])} point records)")
                    return result
                print(f"⚠️  JSONBin load HTTP {resp.status} — starting fresh.")
    except Exception as e:
        print(f"⚠️  JSONBin load error: {e} — starting fresh.")
    return {}, {}, {}, {}, {}, 0

async def save_data_async() -> bool:
    global _dirty
    try:
        payload = {
            "user_points":       {str(k): v for k, v in user_points.items()},
            "user_stats":        {str(k): {"commands_used": v["commands_used"],
                                            "last_seen": v["last_seen"].isoformat()}
                                  for k, v in user_stats.items()},
            "user_personas":     {str(k): v for k, v in user_personas.items()},
            "daily_claimed":     {str(k): v for k, v in daily_claimed.items()},
            "voted_users":       {str(k): v for k, v in voted_users.items()},
            "bot_message_count": bot_message_count,
        }
        async with aiohttp.ClientSession() as session:
            async with session.put(
                JSONBIN_URL, headers=JSONBIN_HEADERS,
                json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    _dirty = False
                    return True
                print(f"⚠️  JSONBin save HTTP {resp.status}")
    except Exception as e:
        print(f"⚠️  JSONBin save error: {e}")
    return False

def mark_dirty():
    global _dirty
    _dirty = True

# In-memory store — populated in on_ready via load_data_async()
user_points:      dict[int, int]  = {}
user_stats:       dict[int, dict] = {}
user_personas:    dict[int, str]  = {}
daily_claimed:    dict[int, str]  = {}
voted_users:      dict[int, str]  = {}
bot_message_count: int            = 0

active_trivia = {}
reminders     = []

# ============== PERSONAS =================

PERSONAS = {
    "default": (
        "You are Rune, a helpful and friendly Discord bot. "
        "Reply with ONE short, friendly message. "
        "Do NOT create dialogue or invent user messages. "
        "Stop immediately after your reply. "
        "Always reply in the same language the user is writing in. "
        "Be helpful, witty, and engaging."
    ),
    "sarcastic": (
        "You are Rune, a sarcastic Discord bot who always replies with dry humor and wit. "
        "Keep it short, one reply only. Never break character. "
        "Always reply in the same language the user is writing in."
    ),
    "pirate": (
        "You are Rune, a pirate Discord bot. Speak like a pirate at all times! "
        "Keep replies short and swashbuckling. One reply only. "
        "Always reply in the same language the user is writing in."
    ),
    "shakespeare": (
        "You are Rune, a Discord bot who speaks in the style of Shakespeare. "
        "Use old English, be poetic but brief. One reply only. "
        "Always reply in the same language the user is writing in."
    ),
    "robot": (
        "You are Rune, a robot Discord bot. Speak in a very robotic, logical, and emotionless manner. "
        "Use technical language. One reply only. "
        "Always reply in the same language the user is writing in."
    ),
    "cheerful": (
        "You are Rune, an extremely cheerful and enthusiastic Discord bot! "
        "Use lots of energy and positivity! One reply only. "
        "Always reply in the same language the user is writing in."
    ),
}

def get_system_prompt(user_id: int) -> str:
    persona_key = user_personas.get(user_id, "default")
    return PERSONAS.get(persona_key, PERSONAS["default"])

# ============== FILTERS ==================

BAD_WORDS = ["fuck", "shit", "idiot", "bitch", "hurensohn", "arschloch"]
INAPPROPRIATE_PHRASES = ["sex", "naked", "fetish"]

ROASTS = [
    "{target}, you just might be why the middle finger was invented.",
    "{target}, if I were on a deserted island with you and a tin of corned beef, I'd rather eat you and talk to the corned beef.",
    "I'd smack {target}, but I'm against animal abuse.",
    "When I see {target} coming, I get pre-annoyed.",
    "If I had a dollar every time {target} shut up, I would give it back as a thank you.",
    "{target} is like a software update. Every time I see them, I think, 'Not now.'",
    "A glowstick has a brighter future than {target}.",
    "{target}'s so dense, light bends around them.",
    "I've seen more life in a cemetery than in {target}'s personality.",
    "{target}, you're the reason the gene pool needs a lifeguard."
]

COMPLIMENTS = [
    "{target}, you're like a ray of sunshine on a cloudy day! ☀️",
    "{target}, you're breathtaking! Keep being awesome! 🌟",
    "{target}, you light up every room you enter! ✨",
    "{target}, you're one in a million! 💎",
    "{target}, your smile is contagious! 😊",
    "{target}, you make the world a better place! 🌍",
    "{target}, you're absolutely amazing! 🎉",
    "{target}, you're proof that good people exist! 💙"
]

# ========== HELPER FUNCTIONS ==============

def is_toxic(text):
    return any(w in text.lower() for w in BAD_WORDS)

def is_inappropriate(text):
    return any(p in text.lower() for p in INAPPROPRIATE_PHRASES)

def clean_output(text: str) -> str:
    if not text:
        return ""
    text = text.split("\n")[0]
    for forbidden in ["user:", "assistant:", "bot:"]:
        if forbidden in text.lower():
            text = text.lower().split(forbidden)[0]
    return text.strip()

def add_points(user_id: int, points: int = 1):
    user_points[user_id] = user_points.get(user_id, 0) + points
    mark_dirty()

def get_points(user_id: int) -> int:
    return user_points.get(user_id, 0)

def track_user_activity(user_id: int):
    if user_id not in user_stats:
        user_stats[user_id] = {"commands_used": 0, "last_seen": datetime.now()}
    user_stats[user_id]["commands_used"] += 1
    user_stats[user_id]["last_seen"] = datetime.now()
    mark_dirty()

# ========== API FUNCTIONS =================

async def get_joke_async():
    try:
        url = "https://v2.jokeapi.dev/joke/Programming,Misc,Pun?blacklistFlags=explicit"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if data["type"] == "single":
                    return data["joke"]
                else:
                    return f'{data["setup"]} — {data["delivery"]}'
    except Exception:
        return "😄 Joke generator is taking a break."

FALLBACK_TRIVIA = [
    {"question": "What is the capital of Australia?", "correct_answer": "Canberra", "incorrect_answers": ["Sydney", "Melbourne", "Brisbane"], "category": "Geography", "difficulty": "medium"},
    {"question": "How many sides does a hexagon have?", "correct_answer": "6", "incorrect_answers": ["5", "7", "8"], "category": "Mathematics", "difficulty": "easy"},
    {"question": "What planet is known as the Red Planet?", "correct_answer": "Mars", "incorrect_answers": ["Venus", "Jupiter", "Saturn"], "category": "Science", "difficulty": "easy"},
    {"question": "Who wrote Romeo and Juliet?", "correct_answer": "William Shakespeare", "incorrect_answers": ["Charles Dickens", "Leo Tolstoy", "Mark Twain"], "category": "Literature", "difficulty": "easy"},
    {"question": "What is the chemical symbol for gold?", "correct_answer": "Au", "incorrect_answers": ["Ag", "Fe", "Pb"], "category": "Science", "difficulty": "easy"},
    {"question": "In which year did the Berlin Wall fall?", "correct_answer": "1989", "incorrect_answers": ["1991", "1985", "1993"], "category": "History", "difficulty": "medium"},
    {"question": "What is the longest river in the world?", "correct_answer": "Nile", "incorrect_answers": ["Amazon", "Yangtze", "Mississippi"], "category": "Geography", "difficulty": "medium"},
    {"question": "How many bones are in the adult human body?", "correct_answer": "206", "incorrect_answers": ["198", "215", "224"], "category": "Science", "difficulty": "medium"},
    {"question": "What is the smallest country in the world?", "correct_answer": "Vatican City", "incorrect_answers": ["Monaco", "San Marino", "Liechtenstein"], "category": "Geography", "difficulty": "medium"},
    {"question": "What programming language was created by Guido van Rossum?", "correct_answer": "Python", "incorrect_answers": ["Ruby", "Perl", "Java"], "category": "Technology", "difficulty": "easy"},
    {"question": "How many planets are in our solar system?", "correct_answer": "8", "incorrect_answers": ["7", "9", "10"], "category": "Science", "difficulty": "easy"},
    {"question": "What is the fastest land animal?", "correct_answer": "Cheetah", "incorrect_answers": ["Lion", "Greyhound", "Pronghorn"], "category": "Animals", "difficulty": "easy"},
    {"question": "What year did World War II end?", "correct_answer": "1945", "incorrect_answers": ["1943", "1944", "1946"], "category": "History", "difficulty": "easy"},
    {"question": "What is the square root of 144?", "correct_answer": "12", "incorrect_answers": ["11", "13", "14"], "category": "Mathematics", "difficulty": "easy"},
    {"question": "Which ocean is the largest?", "correct_answer": "Pacific", "incorrect_answers": ["Atlantic", "Indian", "Arctic"], "category": "Geography", "difficulty": "easy"},
    {"question": "Who painted the Mona Lisa?", "correct_answer": "Leonardo da Vinci", "incorrect_answers": ["Michelangelo", "Raphael", "Botticelli"], "category": "Art", "difficulty": "easy"},
    {"question": "What is the hardest natural substance on Earth?", "correct_answer": "Diamond", "incorrect_answers": ["Quartz", "Corundum", "Topaz"], "category": "Science", "difficulty": "easy"},
    {"question": "What does HTTP stand for?", "correct_answer": "HyperText Transfer Protocol", "incorrect_answers": ["High Transfer Text Protocol", "HyperText Transmission Protocol", "Hyper Transfer Text Procedure"], "category": "Technology", "difficulty": "medium"},
    {"question": "What element has atomic number 1?", "correct_answer": "Hydrogen", "incorrect_answers": ["Helium", "Lithium", "Carbon"], "category": "Science", "difficulty": "easy"},
    {"question": "In what country was cricket invented?", "correct_answer": "England", "incorrect_answers": ["India", "Australia", "Pakistan"], "category": "Sports", "difficulty": "medium"},
]

ACTIVITY_SUGGESTIONS = [
    "Go for a 20-minute walk outside 🚶", "Learn 3 new facts about a country you've never visited 🌍",
    "Write down 5 things you're grateful for today 📝", "Try cooking a recipe you've never made before 🍳",
    "Call or message a friend you haven't spoken to in a while 📞", "Do a 10-minute meditation 🧘",
    "Sketch something without worrying about the result 🎨", "Read a chapter of a book 📖",
    "Organize one drawer or shelf 🗂️", "Watch a documentary on a topic you know nothing about 🎬",
    "Learn 10 words in a new language 🗣️", "Do 20 push-ups, squats, or jumping jacks 💪",
    "Write a short poem about your day ✍️", "Try a new music genre and make a playlist 🎵",
    "Play a puzzle or brain teaser 🧩", "Bake something sweet and share it 🍪",
    "Plan your ideal dream trip ✈️", "Do a random act of kindness 💙",
    "Stargaze or watch the sunset 🌅", "Try a new sport for 30 minutes 🏸",
]

async def get_trivia_question():
    try:
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status != 429:
                    data = await resp.json(content_type=None)
                    if data["response_code"] == 0:
                        q = data["results"][0]
                        return {
                            "question": q["question"],
                            "correct_answer": q["correct_answer"],
                            "all_answers": q["incorrect_answers"] + [q["correct_answer"]],
                            "category": q["category"],
                            "difficulty": q["difficulty"]
                        }
    except Exception:
        pass
    # Fallback to local question bank
    q = random.choice(FALLBACK_TRIVIA)
    return {
        "question": q["question"],
        "correct_answer": q["correct_answer"],
        "all_answers": q["incorrect_answers"] + [q["correct_answer"]],
        "category": q["category"],
        "difficulty": q["difficulty"]
    }

async def get_cat_fact():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://catfact.ninja/fact", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data.get("fact", "Cats are amazing! 🐱")
    except Exception:
        return "Cats are amazing! 🐱"

async def get_dog_image():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://dog.ceo/api/breeds/image/random", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data.get("message")
    except Exception:
        return None

async def get_advice():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.adviceslip.com/advice", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data["slip"]["advice"]
    except Exception:
        return "Be kind to yourself and others. 💙"

async def get_quote():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://zenquotes.io/api/random", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return f'"{data[0]["q"]}" — {data[0]["a"]}'
    except Exception:
        return '"Believe you can and you\'re halfway there." — Theodore Roosevelt'

async def get_meme():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://meme-api.com/gimme", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return {"title": data.get("title"), "url": data.get("url"), "author": data.get("author")}
    except Exception:
        return None

async def get_activity_suggestion():
    return random.choice(ACTIVITY_SUGGESTIONS)

# ========== AI REPLY =================

def generate_reply(user_message: str, system_prompt: str) -> str:
    # Truncate user message to 300 chars to stay within free-tier TPM limits
    user_message = user_message[:300]
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",   # fast, low-token free-tier model
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message}
            ],
            temperature=0.8,
            max_tokens=200,   # keep replies short; Discord messages don't need novels
            stream=False,
        )
        reply = completion.choices[0].message.content or ""
        reply = clean_output(reply)
        return reply if reply else "🤔 I'm not sure how to answer that."
    except Exception as e:
        err = str(e)
        if "413" in err or "rate_limit" in err.lower():
            return "⏳ I'm a bit overloaded right now — try again in a moment!"
        raise

# ========== TOP.GG VOTE NUDGE ===================

VOTE_MESSAGES = [
    "💙 Enjoying Rune? A quick vote on **Top.gg** helps more people find me — and voters get **+50 bonus points**!",
    "🚀 Want to help Rune grow? Vote on **Top.gg** — it only takes 2 seconds and you earn **+50 bonus points**!",
    "⭐ Every vote on **Top.gg** really helps! Voted users get a sweet **+50 point bonus** as a thank-you!",
    "🎉 Fun fact: you can vote for Rune on **Top.gg** every 12 hours and earn **+50 points** each time!",
]

def build_vote_embed(bot_id: str) -> discord.Embed:
    url = f"https://top.gg/bot/{bot_id}/vote" if bot_id else "https://top.gg"
    desc = (
        f"**[Click here to vote!]({url})**\n\n"
        "Voting is free, takes 2 seconds, and you can do it every **12 hours**.\n\n"
        "🎁 **Reward:** Use `/checkvote` after voting to claim **+50 bonus points**!"
    )
    embed = discord.Embed(
        title="⭐ Vote for Rune on Top.gg!",
        description=desc,
        color=discord.Color.from_rgb(255, 0, 119)
    )
    embed.set_footer(text="Your votes help Rune reach more servers 💙")
    return embed

async def check_topgg_vote(user_id: int) -> bool:
    """Returns True if the user has voted on Top.gg in the last 12 hours."""
    if not TOPGG_TOKEN or not TOPGG_BOT_ID:
        return False
    try:
        url = f"https://top.gg/api/bots/{TOPGG_BOT_ID}/check?userId={user_id}"
        headers = {"Authorization": TOPGG_TOKEN}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return bool(data.get("voted", 0))
    except Exception as e:
        print(f"Top.gg API error: {e}")
    return False

# ========== BOT FACTORY ===================

def create_bot():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(command_prefix=PREFIX, intents=intents)

    @bot.event
    async def on_ready():
        global user_points, user_stats, user_personas, daily_claimed, voted_users, bot_message_count
        # Load data from JSONBin
        user_points, user_stats, user_personas, daily_claimed, voted_users, bot_message_count = await load_data_async()

        for g in bot.guilds:
            bot.tree.clear_commands(guild=g)
            await bot.tree.sync(guild=g)
        await bot.tree.sync()
        print("✅ Slash commands synced globally")

        check_reminders.start()
        auto_save.start()
        vc_watchdog.start()
        print(f"✅ Bot online as {bot.user}")
        print(f"📊 Serving {len(bot.guilds)} servers")
        print(f"☁️  JSONBin storage active ({len(user_points)} point records loaded)")

    @bot.event
    async def on_message(message):
        if message.author.bot:
            return

        triggers = ['.joke', '.roast', '.trivia', '.meme']
        if any(word in message.content.lower() for word in triggers):
            await message.add_reaction('😎')

        if not message.content.startswith(PREFIX):
            return

        user_input = message.content[len(PREFIX):].strip()
        if not user_input:
            return

        track_user_activity(message.author.id)

        if is_toxic(user_input):
            await message.channel.send("Hey 🙂 let's keep it respectful.")
            return

        if is_inappropriate(user_input):
            joke = await get_joke_async()
            await message.channel.send(f"Let's keep it clean! Here's a joke instead:\n{joke}")
            return

        system_prompt = get_system_prompt(message.author.id)
        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(generate_reply, user_input, system_prompt)
            except Exception:
                traceback.print_exc()
                reply = "⚠️ AI crashed. Please try again."

        global bot_message_count
        bot_message_count += 1
        await message.channel.send(reply)

        # Every 25 bot messages, drop a vote nudge
        if bot_message_count % 25 == 0:
            await asyncio.sleep(1.5)  # small delay so it doesn't feel instant
            vote_embed = build_vote_embed(TOPGG_BOT_ID)
            await message.channel.send(
                random.choice(VOTE_MESSAGES),
                embed=vote_embed
            )
            mark_dirty()

    # ========== SLASH COMMANDS =================

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            try:
                await interaction.response.send_message(
                    f"⏳ Slow down! Try again in **{error.retry_after:.1f}s**.", ephemeral=True)
            except Exception:
                pass
            return
        inner = getattr(error, "original", error)
        if isinstance(inner, discord.HTTPException) and inner.status == 429:
            print("⚠️  Discord 429 — Cloudflare rate-limiting this IP")
            return
        traceback.print_exc()
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ Something went wrong. Please try again!", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ Something went wrong. Please try again!", ephemeral=True)
        except Exception:
            pass

    @bot.tree.command(name="joke", description="Get a random joke 😂")
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def joke(interaction: discord.Interaction):
        await interaction.response.defer()
        joke_text = await get_joke_async()
        await interaction.followup.send(f"😂 {joke_text}")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="roast", description="Roast someone with a savage burn 🔥")
    @app_commands.describe(user="The user to roast (optional)")
    async def roast(interaction: discord.Interaction, user: Optional[discord.User] = None):
        target = user.mention if user else interaction.user.mention
        roast_text = random.choice(ROASTS).format(target=target)
        await interaction.response.send_message(f"🔥 {roast_text}")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="compliment", description="Give someone a wholesome compliment 💙")
    @app_commands.describe(user="The user to compliment (optional)")
    async def compliment(interaction: discord.Interaction, user: Optional[discord.User] = None):
        target = user.mention if user else interaction.user.mention
        compliment_text = random.choice(COMPLIMENTS).format(target=target)
        await interaction.response.send_message(compliment_text)
        track_user_activity(interaction.user.id)

    # ========== TRIVIA VIEW (buttons, lockout, 5-min timer) =================

    class TriviaView(discord.ui.View):
        def __init__(self, guild_id: int, correct: str, answers: list[str], question_data: dict):
            super().__init__(timeout=300)  # 5 minutes
            self.guild_id   = guild_id
            self.correct    = correct
            self.answered   = False
            self.wrong_ids: set[int] = set()
            self.message: discord.Message | None = None  # set after send

            letters = ["A", "B", "C", "D"]
            for i, ans in enumerate(answers):
                btn = discord.ui.Button(
                    label=f"{letters[i]}. {ans[:80]}",   # truncate very long answers
                    style=discord.ButtonStyle.primary,
                    custom_id=f"trivia_{i}",
                    row=i // 2
                )
                btn.callback = self._make_callback(ans)
                self.add_item(btn)

        def _make_callback(self, answer: str):
            async def callback(interaction: discord.Interaction):
                if self.answered:
                    await interaction.response.send_message(
                        "⏹️ This trivia round is already over!", ephemeral=True
                    )
                    return
                if interaction.user.id in self.wrong_ids:
                    await interaction.response.send_message(
                        "🚫 You already guessed wrong — you're locked out of this question!", ephemeral=True
                    )
                    return

                if answer.strip().lower() == self.correct.strip().lower():
                    self.answered = True
                    active_trivia.pop(self.guild_id, None)
                    add_points(interaction.user.id, 10)
                    # Disable all buttons and mark correct one green
                    for item in self.children:
                        btn = item  # type: ignore[assignment]
                        btn.disabled = True
                        if btn.label and btn.label.split(". ", 1)[-1] == answer[:80]:
                            btn.style = discord.ButtonStyle.success
                        else:
                            btn.style = discord.ButtonStyle.secondary
                    self.stop()
                    embed = self.message.embeds[0]
                    embed.color = discord.Color.green()
                    embed.set_footer(text=f"✅ {interaction.user.display_name} got it right! +10 points")
                    await interaction.response.edit_message(embed=embed, view=self)
                    await interaction.followup.send(
                        f"🎉 **{interaction.user.mention}** answered correctly and earned **10 points**!\n"
                        f"Total: **{get_points(interaction.user.id)}** points"
                    )
                else:
                    # Wrong — lock this user out silently (only they see it)
                    self.wrong_ids.add(interaction.user.id)
                    await interaction.response.send_message(
                        "❌ **Wrong answer!** You're locked out of this question.", ephemeral=True
                    )
            return callback

        async def on_timeout(self):
            self.answered = True
            active_trivia.pop(self.guild_id, None)
            for item in self.children:
                btn = item  # type: ignore[assignment]
                btn.disabled = True
                if btn.label and btn.label.split(". ", 1)[-1] == self.correct[:80]:
                    btn.style = discord.ButtonStyle.success
                else:
                    btn.style = discord.ButtonStyle.secondary
            msg = getattr(self, "message", None)
            if msg is not None:
                embed = msg.embeds[0]
                embed.color = discord.Color.red()
                embed.set_footer(text=f"⏰ Time's up! The answer was: {self.correct}")
                try:
                    await msg.edit(embed=embed, view=self)
                    await msg.channel.send(
                        f"⏰ **Nobody got it!** The correct answer was: **{self.correct}**"
                    )
                except Exception:
                    pass

    @bot.tree.command(name="trivia", description="Start a trivia question! 🧠")
    @app_commands.checks.cooldown(1, 10, key=lambda i: i.guild_id)
    async def trivia(interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        if guild.id in active_trivia:
            try:
                await interaction.followup.send("❌ A trivia question is already active! Finish it first.")
            except discord.HTTPException:
                pass
            return

        question_data = await get_trivia_question()
        if not question_data:
            await interaction.followup.send("⚠️ Couldn't fetch a trivia question. Try again!")
            return

        correct = question_data["correct_answer"]
        answers = question_data["all_answers"][:]
        random.shuffle(answers)

        active_trivia[guild.id] = {"answer": correct, "category": question_data["category"]}

        diff_colors = {"easy": discord.Color.green(), "medium": discord.Color.orange(), "hard": discord.Color.red()}
        color = diff_colors.get(question_data["difficulty"], discord.Color.blue())

        embed = discord.Embed(
            title="🧠 Trivia Time!",
            description=f"**{question_data['question']}**",
            color=color
        )
        embed.add_field(name="📚 Category",   value=question_data["category"],                inline=True)
        embed.add_field(name="⚡ Difficulty", value=question_data["difficulty"].capitalize(),  inline=True)
        embed.add_field(name="⏳ Time Limit", value="5 minutes",                              inline=True)
        embed.set_footer(text="Press a button to answer! Wrong answers lock you out.")

        view = TriviaView(guild.id, correct, answers, question_data)
        try:
            msg = await interaction.followup.send(embed=embed, view=view)
            view.message = msg  # type: ignore[assignment]
        except discord.HTTPException as e:
            active_trivia.pop(guild.id, None)
            print(f"⚠️  Could not send trivia message: {e}")
            return
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="resettrivia", description="Force-reset a stuck trivia question 🔄 (Mod only)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def resettrivia(interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        if guild.id in active_trivia:
            active_trivia.pop(guild.id)
            await interaction.response.send_message("✅ Trivia reset! Start a new one.", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ No active trivia to reset.", ephemeral=True)

    @bot.tree.command(name="points", description="Check your points or someone else's 🏆")
    @app_commands.describe(user="User to check points for (optional)")
    async def points(interaction: discord.Interaction, user: Optional[discord.User] = None):
        target = user or interaction.user
        pts = get_points(target.id)
        embed = discord.Embed(title="🏆 Points", description=f"{target.mention} has **{pts}** points!", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="leaderboard", description="View the top 10 users by points 📊")
    async def leaderboard(interaction: discord.Interaction):
        if not user_points:
            await interaction.response.send_message("No one has points yet! Play trivia to earn some!")
            return
        sorted_users = sorted(user_points.items(), key=lambda x: x[1], reverse=True)[:10]
        embed = discord.Embed(title="🏆 Top 10 Leaderboard", color=discord.Color.gold())
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, pts) in enumerate(sorted_users):
            try:
                u = await bot.fetch_user(uid)
                name = u.name
            except Exception:
                name = f"Unknown ({uid})"
            medal = medals[i] if i < 3 else f"#{i+1}"
            embed.add_field(name=f"{medal} {name}", value=f"{pts} points", inline=False)
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="give", description="Give some of your points to another user 🎁")
    @app_commands.describe(user="Who to give points to", amount="How many points to give")
    async def give(interaction: discord.Interaction, user: discord.User, amount: int):
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't give points to yourself!", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("❌ You can't give points to a bot!", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("❌ Amount must be positive!", ephemeral=True)
            return
        sender_pts = get_points(interaction.user.id)
        if sender_pts < amount:
            await interaction.response.send_message(
                f"❌ You only have **{sender_pts}** points — not enough to give **{amount}**!", ephemeral=True
            )
            return
        user_points[interaction.user.id] = sender_pts - amount
        add_points(user.id, amount)
        embed = discord.Embed(
            title="🎁 Points Gifted!",
            description=f"{interaction.user.mention} gave **{amount}** points to {user.mention}!",
            color=discord.Color.green()
        )
        embed.add_field(name="Your new balance", value=f"{get_points(interaction.user.id)} pts", inline=True)
        embed.add_field(name=f"{user.name}'s new balance", value=f"{get_points(user.id)} pts", inline=True)
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="daily", description="Claim your daily bonus points! 🌅")
    async def daily(interaction: discord.Interaction):
        uid = interaction.user.id
        now_str = datetime.now().strftime("%Y-%m-%d")
        last_claimed = daily_claimed.get(uid)
        if last_claimed == now_str:
            await interaction.response.send_message(
                "⏳ You've already claimed your daily points today! Come back tomorrow.", ephemeral=True
            )
            return
        bonus = random.randint(15, 50)
        daily_claimed[uid] = now_str
        add_points(uid, bonus)
        mark_dirty()
        embed = discord.Embed(
            title="🌅 Daily Bonus!",
            description=f"You claimed **{bonus}** bonus points!\nTotal: **{get_points(uid)}** points",
            color=discord.Color.yellow()
        )
        embed.set_footer(text="Come back tomorrow for more!")
        await interaction.response.send_message(embed=embed)
        track_user_activity(uid)

    @bot.tree.command(name="duel", description="Challenge someone to a coin flip duel for points! 🪙")
    @app_commands.describe(user="Who to duel", wager="How many points to wager")
    async def duel(interaction: discord.Interaction, user: discord.User, wager: int):
        challenger = interaction.user
        if user.id == challenger.id:
            await interaction.response.send_message("❌ You can't duel yourself!", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("❌ You can't duel a bot!", ephemeral=True)
            return
        if wager <= 0:
            await interaction.response.send_message("❌ Wager must be positive!", ephemeral=True)
            return
        if get_points(challenger.id) < wager:
            await interaction.response.send_message(
                f"❌ You don't have enough points! You have **{get_points(challenger.id)}**.", ephemeral=True
            )
            return
        if get_points(user.id) < wager:
            await interaction.response.send_message(
                f"❌ {user.name} doesn't have enough points to accept this duel!", ephemeral=True
            )
            return
        winner = random.choice([challenger, user])
        loser = user if winner == challenger else challenger
        user_points[loser.id] = get_points(loser.id) - wager
        add_points(winner.id, wager)
        embed = discord.Embed(
            title="🪙 Coin Flip Duel!",
            description=(
                f"{challenger.mention} vs {user.mention}\n"
                f"**Wager:** {wager} points\n\n"
                f"🎉 **{winner.mention} wins!**"
            ),
            color=discord.Color.gold()
        )
        embed.add_field(name=f"{winner.name}", value=f"{get_points(winner.id)} pts (+{wager})", inline=True)
        embed.add_field(name=f"{loser.name}", value=f"{get_points(loser.id)} pts (-{wager})", inline=True)
        await interaction.response.send_message(embed=embed)
        track_user_activity(challenger.id)

    # ========== POLL VIEW (buttons, live counts, close button) =================

    class PollView(discord.ui.View):
        message: discord.Message

        def __init__(self, options: list[str], creator_id: int):
            super().__init__(timeout=86400)  # polls live for 24h max
            self.options     = options
            self.creator_id  = creator_id
            self.votes: dict[int, int] = {}   # user_id -> option index
            self.counts = [0] * len(options)
            self.closed  = False
            self.message = None  # type: ignore[assignment]

            letters = ["🇦", "🇧", "🇨", "🇩"]
            for i, opt in enumerate(options):
                btn = discord.ui.Button(
                    label=f"{letters[i]} {opt}",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"poll_opt_{i}",
                    row=0
                )
                btn.callback = self._make_vote_callback(i)
                self.add_item(btn)

            close_btn = discord.ui.Button(
                label="🔒 Close Poll",
                style=discord.ButtonStyle.danger,
                custom_id="poll_close",
                row=1
            )
            close_btn.callback = self.close_poll
            self.add_item(close_btn)

        def _make_vote_callback(self, idx: int):
            async def callback(interaction: discord.Interaction):
                if self.closed:
                    await interaction.response.send_message("🔒 This poll is closed.", ephemeral=True)
                    return
                uid = interaction.user.id
                if uid in self.votes:
                    old = self.votes[uid]
                    if old == idx:
                        # Unvote
                        self.counts[old] -= 1
                        del self.votes[uid]
                        await interaction.response.send_message(
                            f"↩️ Removed your vote for **{self.options[idx]}**.", ephemeral=True
                        )
                    else:
                        # Change vote
                        self.counts[old] -= 1
                        self.counts[idx] += 1
                        self.votes[uid] = idx
                        await interaction.response.send_message(
                            f"🔄 Changed your vote to **{self.options[idx]}**.", ephemeral=True
                        )
                else:
                    self.counts[idx] += 1
                    self.votes[uid] = idx
                    await interaction.response.send_message(
                        f"✅ Voted for **{self.options[idx]}**!", ephemeral=True
                    )
                await self._refresh_embed(interaction)
            return callback

        async def close_poll(self, interaction: discord.Interaction):
            if interaction.user.id != self.creator_id:
                await interaction.response.send_message(
                    "❌ Only the poll creator can close it.", ephemeral=True
                )
                return
            self.closed = True
            self.stop()
            for item in self.children:
                b = item  # type: ignore[assignment]
                b.disabled = True
            await self._refresh_embed(interaction, closed=True)

        def _build_embed(self, closed: bool = False) -> discord.Embed:
            total = sum(self.counts)
            letters = ["🇦", "🇧", "🇨", "🇩"]
            desc_lines = []
            for i, opt in enumerate(self.options):
                count = self.counts[i]
                pct   = (count / total * 100) if total > 0 else 0
                bar   = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
                desc_lines.append(f"{letters[i]} **{opt}**\n`{bar}` {count} vote{'s' if count != 1 else ''} ({pct:.1f}%)\n")
            status = "🔒 Poll Closed" if closed else "📊 Poll Active"
            msg = getattr(self, "message", None)
            embed = discord.Embed(
                title=msg.embeds[0].title if msg else "📊 Poll",
                description="\n".join(desc_lines),
                color=discord.Color.greyple() if closed else discord.Color.blurple()
            )
            embed.set_footer(text=f"{status} • {total} total vote{'s' if total != 1 else ''}")
            return embed

        async def _refresh_embed(self, interaction: discord.Interaction, closed: bool = False):
            embed = self._build_embed(closed=closed)
            try:
                if closed:
                    await interaction.response.edit_message(embed=embed, view=self)
                else:
                    msg = getattr(self, "message", None)
                    if msg:
                        await msg.edit(embed=embed, view=self)
            except Exception:
                pass

    @bot.tree.command(name="poll", description="Create a professional poll with live vote counts 📊")
    @app_commands.describe(
        question="The poll question",
        option1="First option",
        option2="Second option",
        option3="Third option (optional)",
        option4="Fourth option (optional)"
    )
    async def poll(
        interaction: discord.Interaction,
        question: str,
        option1: str,
        option2: str,
        option3: Optional[str] = None,
        option4: Optional[str] = None
    ):
        options = [opt for opt in [option1, option2, option3, option4] if opt]
        letters = ["🇦", "🇧", "🇨", "🇩"]
        desc_lines = []
        for i, opt in enumerate(options):
            desc_lines.append(f"{letters[i]} **{opt}**\n`░░░░░░░░░░` 0 votes (0.0%)\n")
        embed = discord.Embed(
            title=f"📊 {question}",
            description="\n".join(desc_lines),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Poll by {interaction.user.display_name} • 0 total votes • Click a button to vote!")

        view = PollView(options, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="serverinfo", description="View info about this server 🏠")
    async def serverinfo(interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        embed = discord.Embed(title=f"🏠 {guild.name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.add_field(name="👑 Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="👥 Members", value=guild.member_count, inline=True)
        embed.add_field(name="📅 Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
        embed.add_field(name="💬 Channels", value=len(guild.channels), inline=True)
        embed.add_field(name="🎭 Roles", value=len(guild.roles), inline=True)
        embed.add_field(name="😀 Emojis", value=len(guild.emojis), inline=True)
        embed.set_footer(text=f"Server ID: {guild.id}")
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="catfact", description="Get a random cat fact 🐱")
    @app_commands.checks.cooldown(1, 8, key=lambda i: i.user.id)
    async def catfact(interaction: discord.Interaction):
        await interaction.response.defer()
        fact = await get_cat_fact()
        embed = discord.Embed(title="🐱 Cat Fact", description=fact, color=discord.Color.orange())
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="dog", description="Get a random dog picture 🐕")
    @app_commands.checks.cooldown(1, 8, key=lambda i: i.user.id)
    async def dog(interaction: discord.Interaction):
        await interaction.response.defer()
        image_url = await get_dog_image()
        if image_url:
            embed = discord.Embed(title="🐕 Random Dog", color=discord.Color.blue())
            embed.set_image(url=image_url)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("⚠️ Couldn't fetch a dog image right now!")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="advice", description="Get random life advice 💡")
    async def advice(interaction: discord.Interaction):
        await interaction.response.defer()
        advice_text = await get_advice()
        embed = discord.Embed(title="💡 Random Advice", description=advice_text, color=discord.Color.green())
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="quote", description="Get an inspirational quote ✨")
    async def quote(interaction: discord.Interaction):
        await interaction.response.defer()
        quote_text = await get_quote()
        embed = discord.Embed(title="✨ Inspirational Quote", description=quote_text, color=discord.Color.purple())
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="meme", description="Get a random meme 😂")
    async def meme(interaction: discord.Interaction):
        await interaction.response.defer()
        meme_data = await get_meme()
        if meme_data:
            embed = discord.Embed(title=meme_data["title"], color=discord.Color.red())
            embed.set_image(url=meme_data["url"])
            embed.set_footer(text=f"Posted by u/{meme_data['author']}")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("⚠️ Couldn't fetch a meme right now!")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="activity", description="Get a random activity suggestion 🎯")
    async def activity(interaction: discord.Interaction):
        await interaction.response.defer()
        activity_text = await get_activity_suggestion()
        embed = discord.Embed(title="🎯 Activity Suggestion", description=activity_text, color=discord.Color.teal())
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="8ball", description="Ask the magic 8-ball a yes/no question 🎱")
    @app_commands.describe(question="Your yes/no question")
    async def eightball(interaction: discord.Interaction, question: str):
        responses = [
            "It is certain.", "It is decidedly so.", "Without a doubt.",
            "Yes definitely.", "You may rely on it.", "As I see it, yes.",
            "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.",
            "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
            "Cannot predict now.", "Concentrate and ask again.",
            "Don't count on it.", "My reply is no.", "My sources say no.",
            "Outlook not so good.", "Very doubtful."
        ]
        answer = random.choice(responses)
        embed = discord.Embed(
            title="🎱 Magic 8-Ball",
            description=f"**Question:** {question}\n**Answer:** {answer}",
            color=discord.Color.dark_blue()
        )
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="flip", description="Flip a coin 🪙")
    async def flip(interaction: discord.Interaction):
        result = random.choice(["Heads", "Tails"])
        await interaction.response.send_message(f"🪙 The coin landed on: **{result}**!")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="roll", description="Roll a dice 🎲")
    @app_commands.describe(sides="Number of sides (default: 6)")
    async def roll(interaction: discord.Interaction, sides: int = 6):
        if sides < 2:
            await interaction.response.send_message("❌ Dice must have at least 2 sides!")
            return
        result = random.randint(1, sides)
        await interaction.response.send_message(f"🎲 You rolled a **{result}** on a {sides}-sided dice!")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="remind", description="Set a reminder ⏰")
    @app_commands.describe(minutes="Minutes from now", message="What to remind you about")
    async def remind(interaction: discord.Interaction, minutes: int, message: str):
        if minutes < 1 or minutes > 1440:
            await interaction.response.send_message("❌ Please set a reminder between 1 and 1440 minutes!")
            return
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.VoiceChannel)):
            await interaction.response.send_message("❌ Can't set reminders in this channel type!", ephemeral=True)
            return
        remind_time = datetime.now() + timedelta(minutes=minutes)
        reminders.append({
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,
            "message": message,
            "time": remind_time
        })
        await interaction.response.send_message(
            f"⏰ Reminder set! I'll remind you in **{minutes} minute(s)** about: {message}"
        )
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="stats", description="View your bot usage statistics 📊")
    async def stats(interaction: discord.Interaction):
        if interaction.user.id not in user_stats:
            await interaction.response.send_message("You haven't used any commands yet!")
            return
        s = user_stats[interaction.user.id]
        persona = user_personas.get(interaction.user.id, "default")
        embed = discord.Embed(title="📊 Your Statistics", color=discord.Color.blue())
        embed.add_field(name="Commands Used", value=s["commands_used"], inline=True)
        embed.add_field(name="Points", value=get_points(interaction.user.id), inline=True)
        embed.add_field(name="AI Persona", value=persona.capitalize(), inline=True)
        embed.add_field(name="Last Seen", value=s["last_seen"].strftime("%Y-%m-%d %H:%M:%S"), inline=False)
        await interaction.response.send_message(embed=embed)

    # ========== PERSONA COMMAND =================

    @bot.tree.command(name="persona", description="Change Rune's AI personality just for you 🎭")
    @app_commands.describe(style="Choose a personality style")
    @app_commands.choices(style=[
        app_commands.Choice(name="Default (helpful & friendly)", value="default"),
        app_commands.Choice(name="Sarcastic", value="sarcastic"),
        app_commands.Choice(name="Pirate ☠️", value="pirate"),
        app_commands.Choice(name="Shakespeare 📜", value="shakespeare"),
        app_commands.Choice(name="Robot 🤖", value="robot"),
        app_commands.Choice(name="Cheerful 🎉", value="cheerful"),
    ])
    async def persona(interaction: discord.Interaction, style: str):
        user_personas[interaction.user.id] = style
        mark_dirty()
        descriptions = {
            "default": "Back to normal — helpful and friendly! 😊",
            "sarcastic": "Oh great, you picked sarcastic. Wonderful choice. 🙄",
            "pirate": "Arrr! I'll be speakin' like a pirate now, matey! ☠️",
            "shakespeare": "Henceforth, I shall speaketh in the tongue of the Bard! 📜",
            "robot": "ACKNOWLEDGED. SWITCHING TO ROBOT MODE. BEEP BOOP. 🤖",
            "cheerful": "YAY! I'm SO excited to be super cheerful for you! 🎉✨",
        }
        embed = discord.Embed(
            title="🎭 Persona Changed!",
            description=descriptions.get(style, "Persona updated!"),
            color=discord.Color.magenta()
        )
        embed.set_footer(text=f"Your persona is now: {style.capitalize()} — only you see this change!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        track_user_activity(interaction.user.id)

    # ========== MODERATION COMMANDS =================

    @bot.tree.command(name="kick", description="Kick a member from the server 👢")
    @app_commands.describe(user="The user to kick", reason="Reason for kick (optional)")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(interaction: discord.Interaction, user: discord.Member, reason: Optional[str] = "No reason provided"):
        if interaction.guild and interaction.guild.me and user.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I can't kick someone with a higher or equal role than me!", ephemeral=True)
            return
        if user == interaction.user:
            await interaction.response.send_message("❌ You can't kick yourself!", ephemeral=True)
            return
        try:
            await user.kick(reason=f"{reason} (by {interaction.user})")
            embed = discord.Embed(title="👢 Member Kicked", description=f"**{user.mention}** has been kicked.", color=discord.Color.orange())
            embed.add_field(name="Reason", value=reason, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to kick that user.", ephemeral=True)

    @kick.error
    async def kick_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need the **Kick Members** permission to use this!", ephemeral=True)

    @bot.tree.command(name="ban", description="Ban a member from the server 🔨")
    @app_commands.describe(user="The user to ban", reason="Reason for ban (optional)", delete_days="Days of messages to delete (0-7)")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(interaction: discord.Interaction, user: discord.Member, reason: Optional[str] = "No reason provided", delete_days: int = 0):
        if interaction.guild and interaction.guild.me and user.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I can't ban someone with a higher or equal role than me!", ephemeral=True)
            return
        if user == interaction.user:
            await interaction.response.send_message("❌ You can't ban yourself!", ephemeral=True)
            return
        delete_days = max(0, min(7, delete_days))
        try:
            await user.ban(reason=f"{reason} (by {interaction.user})", delete_message_days=delete_days)
            embed = discord.Embed(title="🔨 Member Banned", description=f"**{user.mention}** has been banned.", color=discord.Color.red())
            embed.add_field(name="Reason", value=reason, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to ban that user.", ephemeral=True)

    @ban.error
    async def ban_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need the **Ban Members** permission to use this!", ephemeral=True)

    @bot.tree.command(name="mute", description="Timeout (mute) a member ⏱️")
    @app_commands.describe(user="The user to mute", minutes="Duration in minutes (max 40320 = 28 days)", reason="Reason (optional)")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(interaction: discord.Interaction, user: discord.Member, minutes: int = 10, reason: Optional[str] = "No reason provided"):
        if interaction.guild and interaction.guild.me and user.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I can't mute someone with a higher or equal role than me!", ephemeral=True)
            return
        if user == interaction.user:
            await interaction.response.send_message("❌ You can't mute yourself!", ephemeral=True)
            return
        minutes = max(1, min(40320, minutes))
        try:
            await user.timeout(timedelta(minutes=minutes), reason=f"{reason} (by {interaction.user})")
            embed = discord.Embed(title="⏱️ Member Muted", description=f"**{user.mention}** has been muted for **{minutes} minute(s)**.", color=discord.Color.yellow())
            embed.add_field(name="Reason", value=reason, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to mute that user.", ephemeral=True)

    @mute.error
    async def mute_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need the **Moderate Members** permission to use this!", ephemeral=True)

    @bot.tree.command(name="unmute", description="Remove a timeout from a member 🔊")
    @app_commands.describe(user="The user to unmute")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(interaction: discord.Interaction, user: discord.Member):
        try:
            await user.timeout(None)
            embed = discord.Embed(title="🔊 Member Unmuted", description=f"**{user.mention}** has been unmuted.", color=discord.Color.green())
            embed.add_field(name="Moderator", value=interaction.user.mention)
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to unmute that user.", ephemeral=True)

    @unmute.error
    async def unmute_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need the **Moderate Members** permission to use this!", ephemeral=True)

    # ========== TOP.GG VOTE COMMANDS =================

    @bot.tree.command(name="checkvote", description="Claim your Top.gg vote reward! 🗳️")
    async def checkvote(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        vote_url = f"https://top.gg/bot/{TOPGG_BOT_ID}/vote" if TOPGG_BOT_ID else "https://top.gg"

        # Check cooldown — Top.gg votes are valid for 12 hours
        now = datetime.now()
        last_vote_claim = voted_users.get(uid)
        if last_vote_claim:
            last_dt = datetime.fromisoformat(last_vote_claim)
            if (now - last_dt).total_seconds() < 43200:  # 12 hours
                remaining = timedelta(seconds=43200) - (now - last_dt)
                h, m = divmod(int(remaining.total_seconds()), 3600)
                m = m // 60
                await interaction.followup.send(
                    f"⏳ You already claimed your vote reward! Come back in **{h}h {m}m**.",
                    ephemeral=True
                )
                return

        # Check Top.gg API
        has_voted = await check_topgg_vote(uid)

        if not has_voted and TOPGG_TOKEN:
            no_vote_desc = (
                "Looks like you haven't voted yet!\n\n"
                f"**[Vote here → Top.gg]({vote_url})**\n\n"
                "After voting, come back and run `/checkvote` again to claim your reward!"
            )
            embed = discord.Embed(
                title="❌ No vote found",
                description=no_vote_desc,
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # No API token configured — trust user (honor system) or inform
        if not TOPGG_TOKEN:
            not_cfg_desc = (
                "The bot owner hasn't set up automatic vote verification yet.\n\n"
                f"**[Vote here → Top.gg]({vote_url})**\n\n"
                "Ask the bot owner to add `TOPGG_TOKEN` and `TOPGG_BOT_ID` to the `.env` file!"
            )
            embed = discord.Embed(
                title="⚙️ Vote verification not configured",
                description=not_cfg_desc,
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Award the reward
        bonus = 50
        voted_users[uid] = now.isoformat()
        add_points(uid, bonus)
        mark_dirty()

        reward_desc = (
            "Thank you for voting for Rune on Top.gg! 💙\n\n"
            f"You received **+{bonus} bonus points**!\n"
            f"Total points: **{get_points(uid)}**\n\n"
            "You can vote again in **12 hours**."
        )
        embed = discord.Embed(
            title="🎉 Vote reward claimed!",
            description=reward_desc,
            color=discord.Color.green()
        )
        embed.set_footer(text="Voting helps Rune reach more servers!")
        await interaction.followup.send(embed=embed, ephemeral=True)
        track_user_activity(uid)

    @bot.tree.command(name="vote", description="Vote for Rune on Top.gg and earn bonus points! 🗳️")
    async def vote(interaction: discord.Interaction):
        vote_url = f"https://top.gg/bot/{TOPGG_BOT_ID}/vote" if TOPGG_BOT_ID else "https://top.gg"
        embed = build_vote_embed(TOPGG_BOT_ID)
        await interaction.response.send_message(
            f"Thanks for supporting Rune! 💙 After voting, use `/checkvote` to claim your **+50 points**!",
            embed=embed
        )
        track_user_activity(interaction.user.id)


    # ========== PUSH-TO-TALK / TTS STATE ==========
    # Records raw PCM via a background asyncio task, wraps it in WAV,
    # sends to Groq Whisper for STT, then Zonos for TTS reply.
    # No dependency on discord.sinks — works with any discord.py fork.
    # {guild_id: {"recording": bool, "user_id": int, "text_channel_id": int,
    #              "frames": list[bytes], "stop_event": asyncio.Event}}
    ptt_sessions: dict[int, dict] = {}

    # ── TTS helpers ─────────────────────────────────────────────────────────

    async def transcribe_audio(audio_bytes: bytes) -> str:
        """Send WAV bytes to Groq Whisper and return transcribed text."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                result = groq_client.audio.transcriptions.create(
                    model="whisper-large-v3",
                    file=("audio.wav", f, "audio/wav"),
                    response_format="text",
                    language="en",
                )
            os.unlink(tmp_path)
            return str(result).strip()
        except Exception as e:
            print(f"⚠️  Whisper STT error: {e}")
            return ""

    async def zonos_tts(text: str) -> bytes | None:
        """Convert text → speech via Zyphra Zonos API. Returns MP3 bytes or None."""
        if not ZONOS_API_KEY:
            return None
        try:
            payload = {
                "model": "zonos-v0.1-hybrid",
                "text": text[:500],
                "speaking_rate": 15,
                "language_iso_code": "en-us",
            }
            headers = {
                "Authorization": f"Bearer {ZONOS_API_KEY}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.zyphra.com/v1/audio/text-to-speech",
                    json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    body = await resp.text()
                    print(f"⚠️  Zonos TTS error {resp.status}: {body[:200]}")
        except Exception as e:
            print(f"⚠️  Zonos TTS exception: {e}")
        return None

    async def play_tts_response(vc: discord.VoiceClient, text: str,
                                 text_channel: discord.abc.Messageable | None = None):
        """Generate TTS via Zonos and play in VC. Falls back to text if unavailable."""
        audio_bytes = await zonos_tts(text)
        if not audio_bytes:
            if text_channel:
                await text_channel.send(f"🔊 *(TTS unavailable — no Zonos key)* {text}")
            return
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            source = discord.FFmpegPCMAudio(tmp_path)
            if vc.is_playing():
                vc.stop()
            vc.play(source, after=lambda e: os.unlink(tmp_path) if os.path.exists(tmp_path) else None)
        except Exception as e:
            print(f"⚠️  FFmpeg playback error: {e}")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def capture_and_process_ptt(guild_id: int, user_id: int,
                                       text_channel_id: int, seconds: int,
                                       vc: discord.VoiceClient):
        """
        Records raw PCM from the VC for `seconds` seconds by reading from
        vc.recv_audio (available in discord.py 2.7.x with davey), wraps it
        in a WAV container, transcribes via Whisper, replies via LLaMA + Zonos.
        """
        frames: list[bytes] = []
        stop_event = asyncio.Event()
        ptt_sessions[guild_id]["stop_event"] = stop_event

        text_channel = bot.get_channel(text_channel_id)
        messageable = text_channel if isinstance(text_channel, discord.TextChannel) else None

        # ── Record ──────────────────────────────────────────────────────────
        # discord.py 2.7.x (davey) exposes vc.recv_audio as an async generator
        # that yields (user_id, PCMFrame) tuples. We filter to our target user.
        FRAME_DURATION = 0.02          # 20 ms per Discord frame
        MAX_FRAMES = int(seconds / FRAME_DURATION)
        frame_count = 0

        try:
            async for packet in vc.recv_audio():   # type: ignore[attr-defined]
                if stop_event.is_set() or frame_count >= MAX_FRAMES:
                    break
                uid, pcm_frame = packet
                if uid == user_id:
                    frames.append(bytes(pcm_frame))
                frame_count += 1
        except Exception as e:
            print(f"⚠️  PTT capture error: {e}")

        ptt_sessions[guild_id]["recording"] = False

        if not frames:
            if messageable:
                await messageable.send("⚠️ No audio captured — make sure you're unmuted and speaking!")
            return

        # ── Wrap PCM → WAV ──────────────────────────────────────────────────
        pcm_bytes = b"".join(frames)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(2)       # Discord sends stereo
            wf.setsampwidth(2)       # 16-bit PCM
            wf.setframerate(48000)   # Discord audio rate
            wf.writeframes(pcm_bytes)
        wav_bytes = wav_buffer.getvalue()

        # ── STT ─────────────────────────────────────────────────────────────
        if messageable:
            await messageable.send("🔄 Processing your audio...")
        transcript = await transcribe_audio(wav_bytes)
        if not transcript:
            if messageable:
                await messageable.send("⚠️ Couldn't understand that — try again with `/ptt`.")
            return

        if messageable:
            await messageable.send(f"🎙️ **You said:** {transcript}")

        # ── LLaMA reply ─────────────────────────────────────────────────────
        persona = user_personas.get(
            user_id,
            "You are Rune, a friendly Discord bot. Keep replies short and conversational (1-3 sentences max)."
        )
        reply = await asyncio.to_thread(generate_reply, transcript, persona)
        if messageable:
            await messageable.send(f"🔊 **Rune:** {reply}")

        # ── TTS playback ────────────────────────────────────────────────────
        vc_session = vc_sessions.get(guild_id)
        if vc_session and vc_session["vc"].is_connected():
            await play_tts_response(vc_session["vc"], reply, messageable)

    # ========== 24/7 VOICE CHAT =================
    # Tracks which channel the bot is holding per guild
    # {guild_id: {"channel_id": int, "joined_at": datetime, "vc": VoiceClient}}
    vc_sessions: dict[int, dict] = {}

    @bot.tree.command(name="247", description="Make Rune join a voice channel 24/7 🔊 (Mod only)")
    @app_commands.describe(channel="The voice channel to join (defaults to your current channel)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def vc_247(interaction: discord.Interaction, channel: Optional[discord.VoiceChannel] = None):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return

        # Determine target channel
        target = channel
        if target is None:
            # Try to use the invoker's current VC
            if isinstance(interaction.user, discord.Member) and interaction.user.voice and interaction.user.voice.channel:
                vc_channel = interaction.user.voice.channel
                if isinstance(vc_channel, discord.VoiceChannel):
                    target = vc_channel
        if target is None:
            await interaction.response.send_message(
                "❌ Please join a voice channel first, or specify one with the `channel` option.",
                ephemeral=True
            )
            return

        # If already in a VC in this guild, move instead of rejoin
        existing = vc_sessions.get(guild.id)
        if existing:
            vc: discord.VoiceClient = existing["vc"]
            if vc.is_connected():
                await vc.move_to(target)
                existing["channel_id"] = target.id
                existing["joined_at"]  = datetime.now()
                embed = discord.Embed(
                    title="🔊 Moved to new channel",
                    description=f"Now holding **{target.name}** 24/7.",
                    color=discord.Color.green()
                )
                embed.set_footer(text="Use /leave247 to disconnect.")
                await interaction.response.send_message(embed=embed)
                return

        # Join fresh
        try:
            vc = await target.connect(reconnect=True, self_deaf=True)
        except discord.ClientException:
            await interaction.response.send_message("❌ Already connected somewhere — use `/leave247` first.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to join that channel.", ephemeral=True)
            return

        vc_sessions[guild.id] = {
            "channel_id": target.id,
            "joined_at":  datetime.now(),
            "vc":         vc,
        }

        embed = discord.Embed(
            title="🔊 24/7 Voice Active",
            description=f"Now holding **{target.name}** open 24/7!\nI'll stay even if everyone leaves.",
            color=discord.Color.green()
        )
        embed.add_field(name="Channel", value=target.mention, inline=True)
        embed.add_field(name="Started", value=f"<t:{int(datetime.now().timestamp())}:R>", inline=True)
        embed.set_footer(text="Use /leave247 to disconnect | /vcstatus to check uptime")
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="leave247", description="Make Rune leave the 24/7 voice channel 🔇 (Mod only)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def leave_247(interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return

        session = vc_sessions.pop(guild.id, None)
        if session is None:
            await interaction.response.send_message("ℹ️ I'm not in a 24/7 voice session right now.", ephemeral=True)
            return

        vc = session["vc"]
        channel_id = session["channel_id"]
        joined_at  = session["joined_at"]

        if vc.is_connected():
            await vc.disconnect(force=True)

        duration = datetime.now() - joined_at
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes = remainder // 60

        channel = guild.get_channel(channel_id)
        ch_name = channel.name if isinstance(channel, discord.VoiceChannel) else f"<#{channel_id}>"

        embed = discord.Embed(
            title="🔇 Left voice channel",
            description=f"Disconnected from **{ch_name}**.",
            color=discord.Color.orange()
        )
        embed.add_field(name="⏱️ Total uptime", value=f"{hours}h {minutes}m", inline=True)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="vcstatus", description="Check Rune's 24/7 voice session status 📊")
    async def vc_status(interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return

        session = vc_sessions.get(guild.id)
        if not session:
            await interaction.response.send_message("ℹ️ No active 24/7 voice session in this server.", ephemeral=True)
            return

        joined_at  = session["joined_at"]
        channel_id = session["channel_id"]
        vc: discord.VoiceClient = session["vc"]

        duration = datetime.now() - joined_at
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes = remainder // 60

        channel = guild.get_channel(channel_id)
        ch_name = channel.name if isinstance(channel, discord.VoiceChannel) else f"#{channel_id}"
        members_in_vc = len([m for m in (channel.members if isinstance(channel, discord.VoiceChannel) else []) if not m.bot])

        embed = discord.Embed(
            title="📊 24/7 Voice Status",
            color=discord.Color.green() if vc.is_connected() else discord.Color.red()
        )
        embed.add_field(name="🔊 Channel",      value=f"<#{channel_id}>",       inline=True)
        embed.add_field(name="⏱️ Uptime",       value=f"{hours}h {minutes}m",   inline=True)
        embed.add_field(name="👥 Users in VC",  value=str(members_in_vc),       inline=True)
        embed.add_field(name="📡 Connected",    value="✅ Yes" if vc.is_connected() else "❌ No", inline=True)
        embed.add_field(name="🕐 Joined at",    value=f"<t:{int(joined_at.timestamp())}:F>", inline=False)
        embed.set_footer(text="Use /leave247 to disconnect")
        await interaction.response.send_message(embed=embed)

    # Auto-reconnect task: if the bot gets disconnected from a 24/7 VC, rejoin
    @tasks.loop(seconds=30)
    async def vc_watchdog():
        for guild_id, session in list(vc_sessions.items()):
            vc: discord.VoiceClient = session["vc"]
            if not vc.is_connected():
                guild = bot.get_guild(guild_id)
                if guild is None:
                    vc_sessions.pop(guild_id, None)
                    continue
                channel = guild.get_channel(session["channel_id"])
                if not isinstance(channel, discord.VoiceChannel):
                    vc_sessions.pop(guild_id, None)
                    continue
                try:
                    new_vc = await channel.connect(reconnect=True, self_deaf=True)
                    session["vc"] = new_vc
                    print(f"🔊 Auto-reconnected to {channel.name} in {guild.name}")
                except Exception as e:
                    print(f"⚠️  VC watchdog reconnect failed for {guild_id}: {e}")


    @bot.tree.command(name="ptt", description="Start push-to-talk — Rune listens to you for a few seconds 🎙️")
    @app_commands.describe(seconds="How many seconds to record (3–15, default 5)")
    async def ptt(interaction: discord.Interaction, seconds: int = 5):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return

        # Must be in a VC
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel to use PTT!", ephemeral=True)
            return

        # Bot must also be in a VC in this guild
        vc_session = vc_sessions.get(guild.id)
        if not vc_session or not vc_session["vc"].is_connected():
            await interaction.response.send_message("❌ Rune isn't in a voice channel yet — use `/247` first!", ephemeral=True)
            return

        vc: discord.VoiceClient = vc_session["vc"]

        # Clamp seconds
        seconds = max(3, min(15, seconds))

        # Already recording?
        if ptt_sessions.get(guild.id, {}).get("recording"):
            await interaction.response.send_message("⏺️ Already recording! Wait for the current PTT to finish.", ephemeral=True)
            return

        ptt_sessions[guild.id] = {
            "recording": True,
            "user_id": interaction.user.id,
            "text_channel_id": interaction.channel.id if isinstance(interaction.channel, discord.TextChannel) else 0,
        }

        await interaction.response.send_message(
            f"🎙️ **Recording for {seconds}s...** Speak now! *(Results will appear in this channel)*",
            ephemeral=False
        )

        user_id = interaction.user.id
        text_channel_id = ptt_sessions[guild.id]["text_channel_id"]

        # Kick off background recording + processing task
        asyncio.ensure_future(
            capture_and_process_ptt(guild.id, user_id, text_channel_id, seconds, vc)
        )

    @bot.tree.command(name="stopttt", description="Stop an ongoing PTT recording early ⏹️")
    async def stop_ptt(interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return

        vc_session = vc_sessions.get(guild.id)
        if not vc_session:
            await interaction.response.send_message("ℹ️ Not in a voice channel.", ephemeral=True)
            return

        vc: discord.VoiceClient = vc_session["vc"]
        session = ptt_sessions.get(guild.id, {})

        if not session.get("recording"):
            await interaction.response.send_message("ℹ️ No active recording to stop.", ephemeral=True)
            return

        stop_event = session.get("stop_event")
        if stop_event:
            stop_event.set()
        ptt_sessions[guild.id]["recording"] = False
        await interaction.response.send_message("⏹️ Recording stopped — processing now...")

    # ========== HELP COMMAND =================

    @bot.tree.command(name="help", description="View all available commands 📖")
    async def help_command(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 Rune Bot Commands",
            description="Here are all the commands you can use!",
            color=discord.Color.blue()
        )
        commands_list = [
            ("🎮 **Fun**", "`/joke`, `/roast`, `/compliment`, `/8ball`, `/flip`, `/roll`, `/meme`"),
            ("🧠 **Trivia & Points**", "`/trivia` *(button-based, wrong = locked out, 5min timer)*, `/points`, `/leaderboard`, `/daily`, `/give`, `/duel`"),
            ("📊 **Polls**", "`/poll` — Button poll with live vote counts, change/remove vote, creator can close"),
            ("🐾 **Animals**", "`/catfact`, `/dog`"),
            ("💡 **Inspiration**", "`/advice`, `/quote`, `/activity`"),
            ("🎭 **AI Persona**", "`/persona` — Change how Rune talks to you (your choice is private!)"),
            ("🛡️ **Moderation**", "`/kick`, `/ban`, `/mute`, `/unmute`, `/resettrivia` *(requires permissions)*"),
            ("⏰ **Utility**", "`/remind`, `/stats`, `/serverinfo`, `/help`"),
            ("🔊 **Voice 24/7**", "`/247` — Join a VC 24/7 | `/leave247` — Disconnect | `/vcstatus` — Uptime & status *(Mod only for join/leave)*"),
            ("🎙️ **Push-to-Talk**", "`/ptt [seconds]` — Speak & Rune replies with TTS | `/stopttt` — Stop recording early"),
            ("🗳️ **Vote**", "`/vote` — Vote for Rune on Top.gg | `/checkvote` — Claim your **+50 point** vote reward!"),
            ("💬 **AI Chat**", f"Use `{PREFIX}` prefix to chat with AI (e.g., `{PREFIX}hello`)"),
        ]
        for category, cmds in commands_list:
            embed.add_field(name=category, value=cmds, inline=False)
        embed.set_footer(text="Have fun! 🎉")
        await interaction.response.send_message(embed=embed)

    # ========== BACKGROUND TASKS =================

    @tasks.loop(seconds=30)
    async def check_reminders():
        now = datetime.now()
        for reminder in reminders[:]:
            if now >= reminder["time"]:
                try:
                    channel = bot.get_channel(reminder["channel_id"])
                    user = await bot.fetch_user(reminder["user_id"])
                    if isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.VoiceChannel)):
                        await channel.send(f"⏰ {user.mention} Reminder: **{reminder['message']}**")
                    reminders.remove(reminder)
                except Exception as e:
                    print(f"Error sending reminder: {e}")
                    reminders.remove(reminder)

    @tasks.loop(seconds=60)
    async def auto_save():
        if _dirty:
            ok = await save_data_async()
            if ok:
                print("☁️  Auto-saved to JSONBin.")

    return bot

# ========== AUTO-RESTART LOOP ==============

def run_forever():
    while True:
        try:
            bot = create_bot()
            assert DISCORD_TOKEN is not None, "DISCORD_TOKEN not set!"
            bot.run(DISCORD_TOKEN)
        except Exception:
            print("🔴 BOT CRASHED:")
            traceback.print_exc()
            print(f"♻️ Restarting in {RESTART_DELAY} seconds...\n")
            time.sleep(RESTART_DELAY)

# ============== START =====================

if __name__ == "__main__":
    print("🚀 Starting Rune Bot...")
    run_forever()
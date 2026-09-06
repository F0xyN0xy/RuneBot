import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import time
import traceback
import random
import json
import os
import io
import wave
import tempfile
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web
from typing import Optional
from dotenv import load_dotenv

# NEW: OpenAI client for OmniRoute compatibility
try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

# Groq client for fallback
try:
    from groq import Groq
except ImportError:
    Groq = None

# ================= LOAD ENV =================
load_dotenv()

DISCORD_TOKEN  = os.getenv("DISCORD_TOKEN")
OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY", "")
OMNIROUTE_BASE_URL = os.getenv("OMNIROUTE_BASE_URL", "")
OMNIROUTE_MODEL = os.getenv("OMNIROUTE_MODEL", "auto")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY", "")
JSONBIN_BIN_ID  = os.getenv("JSONBIN_BIN_ID", "")
PREFIX         = os.getenv("PREFIX", ".")
RESTART_DELAY  = int(os.getenv("RESTART_DELAY", "30"))
TOPGG_TOKEN    = os.getenv("TOPGG_TOKEN", "")
TOPGG_BOT_ID   = os.getenv("TOPGG_BOT_ID", "")
TOPGG_WEBHOOK_AUTH = os.getenv("TOPGG_WEBHOOK_AUTH", "")
WEBHOOK_PORT   = int(os.getenv("WEBHOOK_PORT", "8080"))
ZONOS_API_KEY  = os.getenv("ZONOS_API_KEY", "")

# NEW: Gateway logging channel ID (set in .env as GATEWAY_LOG_CHANNEL_ID)
GATEWAY_LOG_CHANNEL_ID: Optional[int] = None
raw_gateway_id = os.getenv("GATEWAY_LOG_CHANNEL_ID", "")
if raw_gateway_id:
    try:
        GATEWAY_LOG_CHANNEL_ID = int(raw_gateway_id)
    except ValueError:
        GATEWAY_LOG_CHANNEL_ID = None

# NEW: Local fallback storage path
LOCAL_STORAGE_PATH = os.getenv("LOCAL_STORAGE_PATH", "bot_data.json")

JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
JSONBIN_HEADERS: dict[str, str] = {
    "Content-Type":    "application/json",
    "X-Master-Key":    JSONBIN_API_KEY or "",
    "X-Bin-Versioning": "false",
}

# =========================================

assert DISCORD_TOKEN is not None, "DISCORD_TOKEN is not set in .env!"

# OmniRoute / OpenAI client setup
if AsyncOpenAI:
    ai_client = AsyncOpenAI(
        base_url=OMNIROUTE_BASE_URL,
        api_key=OMNIROUTE_API_KEY or "omniroute-local-key",
    )
else:
    ai_client = None
    print("WARNING: openai package not installed. Run: pip install openai")

# Groq client setup (synchronous fallback)
if Groq and GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
else:
    groq_client = None
    if not GROQ_API_KEY:
        print("INFO: GROQ_API_KEY not set. Groq fallback disabled.")
    elif not Groq:
        print("WARNING: groq package not installed. Run: pip install groq")

# ============== PERSISTENT STORAGE (JSONBin + Local Fallback) ==================
_dirty = False
_jsonbin_working = False  # Track if JSONBin is functional

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

def _to_payload(user_points, user_stats, user_personas, daily_claimed, voted_users, bot_message_count) -> dict:
    return {
        "user_points":       {str(k): v for k, v in user_points.items()},
        "user_stats":        {str(k): {"commands_used": v["commands_used"],
                                        "last_seen": v["last_seen"].isoformat()}
                              for k, v in user_stats.items()},
        "user_personas":     {str(k): v for k, v in user_personas.items()},
        "daily_claimed":     {str(k): v for k, v in daily_claimed.items()},
        "voted_users":       {str(k): v for k, v in voted_users.items()},
        "bot_message_count": bot_message_count,
    }

async def load_data_async() -> tuple[dict, dict, dict, dict, dict, int]:
    global _jsonbin_working

    # Try JSONBin first if configured
    if JSONBIN_API_KEY and JSONBIN_BIN_ID:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    JSONBIN_URL, headers=JSONBIN_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        result = _parse_raw(payload.get("record", {}))
                        _jsonbin_working = True
                        print(f"Loaded data from JSONBin ({len(result[0])} point records)")
                        return result
                    elif resp.status == 403:
                        print(f"JSONBin HTTP 403 — API key invalid or bin not accessible. Check your JSONBIN_API_KEY and JSONBIN_BIN_ID.")
                        print(f"   Bin URL: {JSONBIN_URL}")
                    elif resp.status == 404:
                        print(f"JSONBin HTTP 404 — Bin not found. Create one at jsonbin.io")
                    else:
                        print(f"JSONBin load HTTP {resp.status} — starting fresh.")
        except Exception as e:
            print(f"JSONBin load error: {e} — starting fresh.")
    else:
        print("JSONBin not configured (JSONBIN_API_KEY or JSONBIN_BIN_ID missing).")

    # Fallback to local file
    if os.path.exists(LOCAL_STORAGE_PATH):
        try:
            with open(LOCAL_STORAGE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            result = _parse_raw(raw)
            print(f"Loaded data from local file ({len(result[0])} point records)")
            return result
        except Exception as e:
            print(f"Local file load error: {e}")

    print("Starting with fresh empty data.")
    return {}, {}, {}, {}, {}, 0

async def save_data_async() -> bool:
    global _dirty, _jsonbin_working

    payload = _to_payload(user_points, user_stats, user_personas, daily_claimed, voted_users, bot_message_count)

    # Try JSONBin if it was working or configured
    if JSONBIN_API_KEY and JSONBIN_BIN_ID:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    JSONBIN_URL, headers=JSONBIN_HEADERS,
                    json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        _dirty = False
                        _jsonbin_working = True
                        return True
                    elif resp.status == 403:
                        print(f"JSONBin save HTTP 403 — API key invalid or bin private.")
                        _jsonbin_working = False
                    else:
                        print(f"JSONBin save HTTP {resp.status}")
                        _jsonbin_working = False
        except Exception as e:
            print(f"JSONBin save error: {e}")
            _jsonbin_working = False

    # Always save to local file as fallback/primary
    try:
        with open(LOCAL_STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        _dirty = False
        if not (JSONBIN_API_KEY and JSONBIN_BIN_ID):
            print("Auto-saved to local file.")
        else:
            print(f"Saved to local file (JSONBin unavailable).")
        return True
    except Exception as e:
        print(f"Local save error: {e}")
    return False

def mark_dirty():
    global _dirty
    _dirty = True

# In-memory store
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

# ========== AI REPLY (OmniRoute / OpenAI-compatible) =================

async def generate_reply(user_message: str, system_prompt: str) -> str:
    """Async AI reply — tries OmniRoute first, falls back to Groq direct."""
    user_message = user_message[:300]

    # ── Try OmniRoute first ──────────────────────────────────────────────
    if ai_client is not None:
        try:
            completion = await ai_client.chat.completions.create(
                model=OMNIROUTE_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message}
                ],
                temperature=0.8,
                max_tokens=200,
            )
            reply = completion.choices[0].message.content or ""
            reply = clean_output(reply)
            return reply if reply else "🤔 I'm not sure how to answer that."
        except Exception as e:
            err = str(e)
            # Broader detection: OmniRoute / OpenAI-style errors often contain
            # "overloaded", "busy", "unavailable", or HTTP error codes.
            busy_signals = ["503", "429", "500", "502", "504", "overloaded", "busy",
                            "unavailable", "timeout", "rate_limit", "too many requests",
                            "service unavailable", "temporarily unavailable", "down"]
            is_busy = any(sig in err.lower() for sig in busy_signals)
            if is_busy:
                print(f"[OmniRoute fallback] {err[:120]}")
            elif "413" in err or "rate_limit" in err.lower():
                print(f"[OmniRate limit] {err[:120]}")
            else:
                print(f"[OmniRoute error] {err[:120]}")
            # Always fall through to Groq on any OmniRoute exception.

    # ── Fallback: Groq direct ────────────────────────────────────────────
    if groq_client is not None:
        try:
            completion = groq_client.chat.completions.create(
                model="groq/compound",  # Updated to use Groq's Compound model (mixture of agents)
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message}
                ],
                temperature=0.8,
                max_tokens=200,
                stream=False,
            )
            reply = completion.choices[0].message.content or ""
            reply = clean_output(reply)
            return reply if reply else "🤔 I'm not sure how to answer that."
        except Exception as e:
            err = str(e)
            print(f"[Groq fallback error] {err[:120]}")
            if "413" in err or "rate_limit" in err.lower():
                return "⏳ All AI providers are busy right now — try again in a moment!"

    return "⚠️ AI is temporarily unavailable. Please try again later."

# ========== TOP.GG VOTE NUDGE ===================

VOTE_MESSAGES = [
    "💙 Enjoying Rune? A quick vote on **Top.gg** helps more people find me — and voters get **+50 bonus points**!",
    "🚀 Want to help Rune grow? Vote on **Top.gg** — it only takes 2 seconds and you earn **+50 bonus points**!",
    "⭐ Every vote on **Top.gg** really helps! Voted users get a sweet **+50 point bonus** as a thank-you!",
    "🎉 Fun fact: you can vote for Rune on **Top.gg** every 12 hours and earn **+50 points** each time!",
]

def build_vote_embed(bot_id: str) -> discord.Embed:
    url = f"https://top.gg/bot/{bot_id}/vote" if bot_id else "https://top.gg"
    desc = (f"**[Click here to vote!]({url})**\n\n\n ""Voting is free, takes 2 seconds, and you can do it every **12 hours**.\n\n ""🎁 **Reward:** Use `/checkvote` after voting to claim **+50 bonus points**!"
            )
    embed = discord.Embed(
        title="⭐ Vote for Rune on Top.gg!",
        description=desc,
        color=discord.Color.from_rgb(255, 0, 119)
    )
    embed.set_footer(text="Your votes help Rune reach more servers 💙")
    return embed

async def check_topgg_vote(user_id: int) -> bool:
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


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║           TOP.GG WEBHOOK — Auto-detect votes & DM thank-you                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

VOTE_THANK_YOU_MESSAGES = [
    "💙 Thank you so much for voting for **Rune** on Top.gg! You're amazing!",
    "🎉 Woohoo! Thanks for voting! Your support means the world to me! 💙",
    "⭐ You just made my day! Thanks for voting for Rune! Here's **+50 bonus points** as a thank-you!",
    "🚀 Big thanks for the vote! You're helping Rune grow! 💙 Enjoy your **+50 points**!",
    "✨ A wild voter appears! Thanks for supporting Rune! 💙 Have **+50 bonus points**!",
]

# Reference to the bot instance, set when the bot is created
_bot_instance: Optional[commands.Bot] = None


async def _handle_topgg_webhook(request: web.Request) -> web.Response:
    """Handle incoming Top.gg vote webhooks."""
    global _bot_instance

    # Validate authorization header
    auth_header = request.headers.get("Authorization", "")
    if TOPGG_WEBHOOK_AUTH and auth_header != TOPGG_WEBHOOK_AUTH:
        print(f"[Top.gg Webhook] Unauthorized request from {request.remote}")
        return web.Response(status=403, text="Forbidden")

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Bad request")

    user_id = data.get("user")
    vote_type = data.get("type", "upvote")
    is_test = data.get("test", False)

    if not user_id:
        return web.Response(status=400, text="Missing user field")

    user_id = int(user_id)
    print(f"[Top.gg Webhook] Vote received! User: {user_id}, type: {vote_type}, test: {is_test}")

    if is_test:
        print("[Top.gg Webhook] Test vote — skipping reward & DM")
        return web.Response(status=200, text="OK (test)")

    # Check if this vote was already auto-claimed (avoid double-reward)
    now = datetime.now()
    last_claim = voted_users.get(user_id)
    if last_claim:
        last_dt = datetime.fromisoformat(last_claim)
        if (now - last_dt).total_seconds() < 43200:  # 12 hours
            print(f"[Top.gg Webhook] User {user_id} already claimed recently — skipping")
            return web.Response(status=200, text="OK (already claimed)")

    # Award bonus points
    bonus = 50
    voted_users[user_id] = now.isoformat()
    add_points(user_id, bonus)
    track_user_activity(user_id)

    # Send a thank-you DM
    if _bot_instance:
        try:
            user = _bot_instance.get_user(user_id) or await _bot_instance.fetch_user(user_id)
            dm_channel = user.dm_channel or await user.create_dm()

            name = user.display_name or user.name
            msg = random.choice(VOTE_THANK_YOU_MESSAGES)
            total = get_points(user_id)
            embed = discord.Embed(
                title="🗳️ Thanks for voting!",
                description=f"Hey **{name}**! 💙\n\n{msg}\n\n🎁 **+{bonus} bonus points** awarded!\n💰 Total points: **{total}**\n\nYou can vote again in **12 hours** — every vote helps Rune grow! 🚀",
                color=discord.Color.from_rgb(255, 0, 119),
            )
            embed.set_footer(text="Your votes help Rune reach more servers! 💙")
            await dm_channel.send(embed=embed)
            print(f"[Top.gg Webhook] Thank-you DM sent to {user_id}")
        except discord.Forbidden:
            print(f"[Top.gg Webhook] Could not DM user {user_id} (DMs disabled)")
        except Exception as e:
            print(f"[Top.gg Webhook] Error sending DM to {user_id}: {e}")

    return web.Response(status=200, text="OK")


async def _start_webhook_server(bot: commands.Bot) -> None:
    """Start the aiohttp web server to receive Top.gg webhooks."""
    global _bot_instance
    _bot_instance = bot

    if not TOPGG_WEBHOOK_AUTH:
        print(f"[Top.gg Webhook] ⚠️  TOPGG_WEBHOOK_AUTH not set — webhook server not started")
        print(f"[Top.gg Webhook]    Set it in .env to enable automatic vote detection!")
        return

    app = web.Application()
    app.router.add_post("/api/topgg", _handle_topgg_webhook)
    async def _health_check(request: web.Request) -> web.Response:
        return web.Response(text="Rune Top.gg webhook is running! ✅")
    app.router.add_get("/api/topgg", _health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    try:
        await site.start()
        print(f"[Top.gg Webhook] ✅ Webhook server running on port {WEBHOOK_PORT}")
        print(f"[Top.gg Webhook]    Endpoint: POST /api/topgg")
    except OSError as e:
        print(f"[Top.gg Webhook] ❌ Failed to start webhook server on port {WEBHOOK_PORT}: {e}")
        print(f"[Top.gg Webhook]    Set WEBHOOK_PORT in .env to use a different port")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║           GATEWAY LOGGER — Rich, Colorful Discord Event Logging              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class GatewayLogger:
    """
    Rich, colorful gateway event logger that sends beautifully formatted
    embeds to a designated Discord channel.
    """

    COLORS = {
        "PRESENCE_UPDATE":    discord.Color.from_rgb(88, 101, 242),
        "VOICE_STATE_UPDATE": discord.Color.from_rgb(88, 196, 221),
        "MESSAGE_CREATE":     discord.Color.from_rgb(88, 214, 141),
        "GUILD_MEMBER_ADD":   discord.Color.from_rgb(241, 196, 15),
        "GUILD_MEMBER_REMOVE":discord.Color.from_rgb(231, 76, 60),
        "GUILD_MEMBER_UPDATE":discord.Color.from_rgb(155, 89, 182),
        "TYPING_START":       discord.Color.from_rgb(149, 165, 166),
        "INVITE_CREATE":      discord.Color.from_rgb(26, 188, 156),
        "INVITE_DELETE":      discord.Color.from_rgb(192, 57, 43),
        "GUILD_BAN_ADD":      discord.Color.from_rgb(211, 84, 0),
        "GUILD_BAN_REMOVE":   discord.Color.from_rgb(46, 204, 113),
        "CHANNEL_CREATE":     discord.Color.from_rgb(52, 152, 219),
        "CHANNEL_DELETE":     discord.Color.from_rgb(231, 76, 60),
        "GUILD_ROLE_CREATE":  discord.Color.from_rgb(155, 89, 182),
        "GUILD_ROLE_DELETE":  discord.Color.from_rgb(231, 76, 60),
        "GUILD_UPDATE":       discord.Color.from_rgb(52, 73, 94),
        "MESSAGE_REACTION_ADD":    discord.Color.from_rgb(255, 105, 180),
        "MESSAGE_REACTION_REMOVE": discord.Color.from_rgb(255, 182, 193),
        "THREAD_CREATE":      discord.Color.from_rgb(26, 188, 156),
        "THREAD_DELETE":      discord.Color.from_rgb(192, 57, 43),
        "STAGE_INSTANCE_CREATE": discord.Color.from_rgb(155, 89, 182),
        "STAGE_INSTANCE_DELETE": discord.Color.from_rgb(231, 76, 60),
        "GUILD_SCHEDULED_EVENT_CREATE": discord.Color.from_rgb(46, 204, 113),
        "GUILD_SCHEDULED_EVENT_DELETE": discord.Color.from_rgb(231, 76, 60),
        "default":            discord.Color.from_rgb(149, 165, 166),
    }

    EMOJIS = {
        "PRESENCE_UPDATE":    "🌐",
        "VOICE_STATE_UPDATE": "🔊",
        "MESSAGE_CREATE":     "💬",
        "GUILD_MEMBER_ADD":   "👋",
        "GUILD_MEMBER_REMOVE": "👋",
        "GUILD_MEMBER_UPDATE": "✏️",
        "TYPING_START":       "⌨️",
        "INVITE_CREATE":      "📨",
        "INVITE_DELETE":      "🗑️",
        "GUILD_BAN_ADD":      "🔨",
        "GUILD_BAN_REMOVE":   "🔓",
        "CHANNEL_CREATE":     "📁",
        "CHANNEL_DELETE":     "🗑️",
        "GUILD_ROLE_CREATE":  "🏷️",
        "GUILD_ROLE_DELETE":  "🗑️",
        "GUILD_UPDATE":       "🏠",
        "MESSAGE_REACTION_ADD":    "💖",
        "MESSAGE_REACTION_REMOVE": "💔",
        "THREAD_CREATE":      "🧵",
        "THREAD_DELETE":      "🗑️",
        "STAGE_INSTANCE_CREATE": "🎤",
        "STAGE_INSTANCE_DELETE": "🚫",
        "GUILD_SCHEDULED_EVENT_CREATE": "📅",
        "GUILD_SCHEDULED_EVENT_DELETE": "🗑️",
        "default":            "📋",
    }

    STATUS_COLORS = {
        "online":    discord.Color.green(),
        "idle":      discord.Color.gold(),
        "dnd":       discord.Color.red(),
        "offline":   discord.Color.light_grey(),
        "invisible": discord.Color.light_grey(),
    }

    STATUS_EMOJIS = {
        "online":    "🟢",
        "idle":      "🌙",
        "dnd":       "⛔",
        "offline":   "⚫",
        "invisible": "⚫",
    }

    def __init__(self, bot: commands.Bot, channel_id: Optional[int]):
        self.bot = bot
        self.channel_id = channel_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._cache: dict = {"users": {}, "channels": {}, "guilds": {}}

    async def start(self):
        if self._task is None or self._task.done():
            self._task = self.bot.loop.create_task(self._processor())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _processor(self):
        while True:
            try:
                embed = await self._queue.get()
                channel = self.bot.get_channel(self.channel_id) if self.channel_id else None
                if channel and isinstance(channel, discord.TextChannel):
                    try:
                        await channel.send(embed=embed)
                    except discord.HTTPException as e:
                        print(f"Gateway log send failed: {e}")
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Gateway logger error: {e}")
                await asyncio.sleep(1)

    def _get_color(self, event_type: str) -> discord.Color:
        return self.COLORS.get(event_type, self.COLORS["default"])

    def _get_emoji(self, event_type: str) -> str:
        return self.EMOJIS.get(event_type, self.EMOJIS["default"])

    def _timestamp(self) -> str:
        return f"<t:{int(datetime.now().timestamp())}:F>"

    def _relative_time(self) -> str:
        return f"<t:{int(datetime.now().timestamp())}:R>"

    async def log(self, event_type: str, data: dict):
        if not self.channel_id:
            return
        try:
            embed = await self._build_embed(event_type, data)
            if embed:
                await self._queue.put(embed)
        except Exception as e:
            print(f"Failed to build gateway log embed: {e}")

    async def _build_embed(self, event_type: str, data: dict) -> Optional[discord.Embed]:
        color = self._get_color(event_type)
        emoji = self._get_emoji(event_type)

        if event_type == "PRESENCE_UPDATE":
            user = data.get("user", {})
            user_id = int(user.get("id", 0))
            status = data.get("status", "unknown")
            activities = data.get("activities", [])
            client_status = data.get("client_status", {})
            guild_id = data.get("guild_id")
            member = None
            if guild_id:
                guild = self.bot.get_guild(int(guild_id))
                if guild:
                    member = guild.get_member(user_id)
            username = member.display_name if member else (user.get("global_name") or user.get("username", "Unknown"))
            avatar_url = member.display_avatar.url if member and member.display_avatar else (
                user.get("avatar") and f"https://cdn.discordapp.com/avatars/{user_id}/{user['avatar']}.png"
            )
            status_emoji = self.STATUS_EMOJIS.get(status, "⚪")
            status_color = self.STATUS_COLORS.get(status, discord.Color.light_grey())
            activity_lines = []
            for act in activities:
                act_type = act.get("type", 0)
                act_name = act.get("name", "Unknown")
                act_details = act.get("details", "")
                act_state = act.get("state", "")
                type_names = {0: "Playing", 1: "Streaming", 2: "Listening to", 3: "Watching", 4: "Custom", 5: "Competing in"}
                type_emoji = {0: "🎮", 1: "🔴", 2: "🎵", 3: "📺", 4: "✨", 5: "🏆"}
                line = f"{type_emoji.get(act_type, '❓')} **{type_names.get(act_type, 'Doing')}** {act_name}"
                if act_details:
                    line += f"   └ *{act_details}*"
                if act_state:
                    line += f"   └ *{act_state}*"
                activity_lines.append(line)
            client_lines = []
            for platform, plat_status in client_status.items():
                if plat_status:
                    plat_emoji = self.STATUS_EMOJIS.get(plat_status, "⚪")
                    client_lines.append(f"{plat_emoji} {platform.capitalize()}")
            embed = discord.Embed(title=f"{emoji} Presence Update", description=(f"**{username}** is now **{status_emoji} {status.upper()}** {self._relative_time()}"),
                color=status_color, timestamp=datetime.now()
            )
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            if activity_lines:
                embed.add_field(name="🎯 Activities", value="".join(activity_lines), inline=False)
            if client_lines:
                embed.add_field(name="📱 Platforms", value=" | ".join(client_lines), inline=True)
            if guild_id and member:
                embed.add_field(name="🏠 Server", value=member.guild.name, inline=True)
            embed.set_footer(text=f"User ID: {user_id}  •  Event: PRESENCE_UPDATE")
            return embed

        elif event_type == "VOICE_STATE_UPDATE":
            user_id = int(data.get("user_id", 0))
            guild_id = data.get("guild_id")
            channel_id = data.get("channel_id")
            session_id = data.get("session_id", "?")
            self_deaf = data.get("self_deaf", False)
            self_mute = data.get("self_mute", False)
            self_stream = data.get("self_stream", False)
            self_video = data.get("self_video", False)
            suppress = data.get("suppress", False)
            request_to_speak = data.get("request_to_speak_timestamp")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            member = guild.get_member(user_id) if guild else None
            username = member.display_name if member else f"User {user_id}"
            avatar_url = member.display_avatar.url if member and member.display_avatar else None
            if channel_id:
                vc = guild.get_channel(int(channel_id)) if guild else None
                channel_name = vc.name if vc else f"Channel {channel_id}"
                desc = f"**{username}** 🔊 **joined** #{channel_name}"
            else:
                desc = f"**{username}** 🔇 **left** voice chat"
            embed = discord.Embed(title=f"{emoji} Voice State Update", description=f"{desc} {self._relative_time()}", color=color, timestamp=datetime.now())
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            state_details = []
            if self_mute: state_details.append("🔇 Self-muted")
            if self_deaf: state_details.append("🎧 Self-deafened")
            if self_stream: state_details.append("🔴 Streaming")
            if self_video: state_details.append("📹 Camera on")
            if suppress: state_details.append("🚫 Suppressed")
            if request_to_speak: state_details.append("🎤 Requested to speak")
            if state_details:
                embed.add_field(name="🎛️ State", value="".join(state_details), inline=True)
            if guild:
                embed.add_field(name="🏠 Server", value=guild.name, inline=True)
                if channel_id and vc and isinstance(vc, discord.VoiceChannel):
                    human_count = len([m for m in vc.members if not m.bot])
                    bot_count = len([m for m in vc.members if m.bot])
                    embed.add_field(name="👥 Channel Population", value=f"{human_count} humans  •  {bot_count} bots", inline=True)
            embed.set_footer(text=f"User ID: {user_id}  •  Session: {session_id[:8]}...")
            return embed

        elif event_type == "MESSAGE_CREATE":
            author = data.get("author", {})
            user_id = int(author.get("id", 0))
            username = author.get("global_name") or author.get("username", "Unknown")
            content = data.get("content", "")
            msg_id = data.get("id", "?")
            channel_id = data.get("channel_id")
            guild_id = data.get("guild_id")
            attachments = data.get("attachments", [])
            embeds_count = len(data.get("embeds", []))
            mentions = len(data.get("mentions", []))
            mention_roles = len(data.get("mention_roles", []))
            mention_everyone = data.get("mention_everyone", False)
            is_bot = author.get("bot", False)
            if is_bot:
                return None
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            member = guild.get_member(user_id) if guild else None
            display_name = member.display_name if member else username
            avatar_url = member.display_avatar.url if member and member.display_avatar else (
                author.get("avatar") and f"https://cdn.discordapp.com/avatars/{user_id}/{author['avatar']}.png"
            )
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            channel_name = channel.name if isinstance(channel, discord.TextChannel) else f"DM/{channel_id}"
            display_content = content[:400] + "..." if len(content) > 400 else content
            if not display_content:
                display_content = "*(empty message / embed only)*"
            embed = discord.Embed(
                title=f"{emoji} New Message", description=(f"**{display_name}** in **#{channel_name}** {self._relative_time()}"),
                color=color, timestamp=datetime.now()
            )
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            embed.add_field(name="💬 Content", value=display_content or "*(no text)*", inline=False)
            if attachments:
                attch_info = []
                for i, att in enumerate(attachments[:3]):
                    fname = att.get("filename", "unknown")
                    fsize = att.get("size", 0)
                    fsize_str = f"{fsize/1024:.1f} KB" if fsize < 1024*1024 else f"{fsize/(1024*1024):.1f} MB"
                    attch_info.append(f"📎 `{fname}` ({fsize_str})")
                if len(attachments) > 3:
                    attch_info.append(f"*...and {len(attachments)-3} more*")
                embed.add_field(name="📎 Attachments", value="".join(attch_info), inline=False)
            mention_info = []
            if mention_everyone: mention_info.append("📢 @everyone/@here")
            if mentions: mention_info.append(f"👤 {mentions} user mention{'s' if mentions != 1 else ''}")
            if mention_roles: mention_info.append(f"🏷️ {mention_roles} role mention{'s' if mention_roles != 1 else ''}")
            if embeds_count: mention_info.append(f"🖼️ {embeds_count} embed{'s' if embeds_count != 1 else ''}")
            if mention_info:
                embed.add_field(name="📊 Details", value=" | ".join(mention_info), inline=False)
            if guild:
                embed.add_field(name="🏠 Server", value=guild.name, inline=True)
            embed.set_footer(text=f"User ID: {user_id}  •  Msg ID: {msg_id}")
            return embed

        elif event_type == "GUILD_MEMBER_ADD":
            user = data.get("user", {})
            user_id = int(user.get("id", 0))
            username = user.get("global_name") or user.get("username", "Unknown")
            avatar = user.get("avatar")
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else None
            guild_id = data.get("guild_id")
            joined_at = data.get("joined_at")
            is_pending = data.get("pending", False)
            premium_since = data.get("premium_since")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            member_count = guild.member_count if guild else "?"
            embed = discord.Embed(title=f"{emoji} Member Joined", description=(f"**{username}** just joined **{guild_name}**! {self._relative_time()}"),
                color=color, timestamp=datetime.now()
            )
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            embed.add_field(name="👥 Server Population", value=f"{member_count} members", inline=True)
            if is_pending:
                embed.add_field(name="⏳ Status", value="Pending membership screening", inline=True)
            if premium_since:
                embed.add_field(name="⭐ Nitro Booster", value="Yes! 💎", inline=True)
            if joined_at:
                embed.add_field(name="📅 Account Joined", value=f"<t:{int(datetime.fromisoformat(joined_at.replace('Z', '+00:00')).timestamp())}:F>", inline=False)
            embed.set_footer(text=f"User ID: {user_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "GUILD_MEMBER_REMOVE":
            user = data.get("user", {})
            user_id = int(user.get("id", 0))
            username = user.get("global_name") or user.get("username", "Unknown")
            avatar = user.get("avatar")
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else None
            guild_id = data.get("guild_id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            member_count = guild.member_count if guild else "?"
            embed = discord.Embed(
                title=f"{emoji} Member Left", description=(f"**{username}** left **{guild_name}** {self._relative_time()}"),
                color=color, timestamp=datetime.now()
            )
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            embed.add_field(name="👥 Server Population", value=f"{member_count} members", inline=True)
            embed.set_footer(text=f"User ID: {user_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "GUILD_MEMBER_UPDATE":
            user = data.get("user", {})
            user_id = int(user.get("id", 0))
            username = user.get("global_name") or user.get("username", "Unknown")
            avatar = user.get("avatar")
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else None
            guild_id = data.get("guild_id")
            nick = data.get("nick")
            roles = data.get("roles", [])
            premium_since = data.get("premium_since")
            pending = data.get("pending", False)
            communication_disabled_until = data.get("communication_disabled_until")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            changes = []
            if nick is not None: changes.append(f"📝 Nickname set to: **{nick}**")
            if premium_since: changes.append("⭐ Started Nitro boosting!")
            if communication_disabled_until:
                until = datetime.fromisoformat(communication_disabled_until.replace("Z", "+00:00"))
                changes.append(f"⏱️ Timed out until <t:{int(until.timestamp())}:F>")
            if pending: changes.append("⏳ Passed membership screening")
            role_objs = []
            if guild and roles:
                for rid in roles[-5:]:
                    role = guild.get_role(int(rid))
                    if role: role_objs.append(role.mention)
            embed = discord.Embed(
                title=f"{emoji} Member Updated",
                description=(f"**{username}** was updated in **{guild_name}** {self._relative_time()}"),
                color=color, timestamp=datetime.now()
            )
            if avatar_url: embed.set_thumbnail(url=avatar_url)
            if changes: embed.add_field(name="🔄 Changes", value="".join(changes), inline=False)
            if role_objs: embed.add_field(name="🏷️ Roles", value=" ".join(role_objs), inline=False)
            embed.set_footer(text=f"User ID: {user_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "TYPING_START":
            user_id = int(data.get("user_id", 0))
            channel_id = data.get("channel_id")
            guild_id = data.get("guild_id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            member = guild.get_member(user_id) if guild else None
            username = member.display_name if member else f"User {user_id}"
            avatar_url = member.display_avatar.url if member and member.display_avatar else None
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            channel_name = channel.name if isinstance(channel, discord.TextChannel) else f"Channel {channel_id}"
            embed = discord.Embed(title=f"{emoji} Typing", description=f"**{username}** is typing in **#{channel_name}**...", color=color, timestamp=datetime.now())
            if avatar_url: embed.set_thumbnail(url=avatar_url)
            if guild: embed.add_field(name="🏠 Server", value=guild.name, inline=True)
            embed.set_footer(text=f"User ID: {user_id}")
            return embed

        elif event_type == "GUILD_BAN_ADD":
            user = data.get("user", {})
            user_id = int(user.get("id", 0))
            username = user.get("global_name") or user.get("username", "Unknown")
            avatar = user.get("avatar")
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else None
            guild_id = data.get("guild_id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            embed = discord.Embed(title=f"{emoji} User Banned", description=f"**{username}** was banned from **{guild_name}** 🔨", color=color, timestamp=datetime.now())
            if avatar_url: embed.set_thumbnail(url=avatar_url)
            embed.set_footer(text=f"User ID: {user_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "GUILD_BAN_REMOVE":
            user = data.get("user", {})
            user_id = int(user.get("id", 0))
            username = user.get("global_name") or user.get("username", "Unknown")
            avatar = user.get("avatar")
            avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else None
            guild_id = data.get("guild_id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            embed = discord.Embed(title=f"{emoji} User Unbanned", description=f"**{username}** was unbanned from **{guild_name}** 🔓", color=color, timestamp=datetime.now())
            if avatar_url: embed.set_thumbnail(url=avatar_url)
            embed.set_footer(text=f"User ID: {user_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "CHANNEL_CREATE":
            channel_type = data.get("type", 0)
            channel_id = data.get("id")
            name = data.get("name", "unknown")
            guild_id = data.get("guild_id")
            parent_id = data.get("parent_id")
            nsfw = data.get("nsfw", False)
            type_names = {0: "Text", 2: "Voice", 4: "Category", 5: "News", 10: "News Thread", 11: "Public Thread", 12: "Private Thread", 13: "Stage", 15: "Forum"}
            type_emojis = {0: "💬", 2: "🔊", 4: "📁", 5: "📰", 10: "🧵", 11: "🧵", 12: "🔒", 13: "🎤", 15: "📋"}
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            embed = discord.Embed(title=f"{emoji} Channel Created", description=(f"**#{name}** was created in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.add_field(name="📋 Type", value=f"{type_emojis.get(channel_type, '❓')} {type_names.get(channel_type, 'Unknown')}", inline=True)
            embed.add_field(name="🔞 NSFW", value="Yes" if nsfw else "No", inline=True)
            if parent_id:
                parent = guild.get_channel(int(parent_id)) if guild else None
                parent_name = parent.name if parent else f"Category {parent_id}"
                embed.add_field(name="📁 Parent", value=parent_name, inline=True)
            embed.set_footer(text=f"Channel ID: {channel_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "CHANNEL_DELETE":
            channel_type = data.get("type", 0)
            channel_id = data.get("id")
            name = data.get("name", "unknown")
            guild_id = data.get("guild_id")
            type_names = {0: "Text", 2: "Voice", 4: "Category", 5: "News", 10: "News Thread", 11: "Public Thread", 12: "Private Thread", 13: "Stage", 15: "Forum"}
            type_emojis = {0: "💬", 2: "🔊", 4: "📁", 5: "📰", 10: "🧵", 11: "🧵", 12: "🔒", 13: "🎤", 15: "📋"}
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            embed = discord.Embed(title=f"{emoji} Channel Deleted", description=(f"**#{name}** was deleted from **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.add_field(name="📋 Type", value=f"{type_emojis.get(channel_type, '❓')} {type_names.get(channel_type, 'Unknown')}", inline=True)
            embed.set_footer(text=f"Channel ID: {channel_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "GUILD_ROLE_CREATE":
            role_data = data.get("role", {})
            role_id = role_data.get("id")
            name = role_data.get("name", "unknown")
            color_val = role_data.get("color", 0)
            hoist = role_data.get("hoist", False)
            mentionable = role_data.get("mentionable", False)
            guild_id = data.get("guild_id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            hex_color = f"#{color_val:06x}" if color_val else "Default"
            role_color = discord.Color(color_val) if color_val else discord.Color.default()
            embed = discord.Embed(title=f"{emoji} Role Created", description=(f"**{name}** was created in **{guild_name}** {self._relative_time()}"), color=role_color, timestamp=datetime.now())
            embed.add_field(name="🎨 Color", value=hex_color, inline=True)
            embed.add_field(name="📌 Hoisted", value="Yes" if hoist else "No", inline=True)
            embed.add_field(name="📢 Mentionable", value="Yes" if mentionable else "No", inline=True)
            embed.set_footer(text=f"Role ID: {role_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "GUILD_ROLE_DELETE":
            role_id = data.get("role_id")
            guild_id = data.get("guild_id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            embed = discord.Embed(title=f"{emoji} Role Deleted", description=(f"A role was deleted from **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.add_field(name="🏷️ Role ID", value=role_id, inline=True)
            embed.set_footer(text=f"Guild ID: {guild_id}")
            return embed

        elif event_type == "MESSAGE_REACTION_ADD":
            user_id = int(data.get("user_id", 0))
            msg_id = data.get("message_id")
            channel_id = data.get("channel_id")
            guild_id = data.get("guild_id")
            emoji_data = data.get("emoji", {})
            emoji_name = emoji_data.get("name", "❓")
            emoji_id = emoji_data.get("id")
            emoji_animated = emoji_data.get("animated", False)
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            member = guild.get_member(user_id) if guild else None
            username = member.display_name if member else f"User {user_id}"
            avatar_url = member.display_avatar.url if member and member.display_avatar else None
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            channel_name = channel.name if isinstance(channel, discord.TextChannel) else f"Channel {channel_id}"
            if emoji_id:
                ext = "gif" if emoji_animated else "png"
                emoji_display = f"<:{emoji_name}:{emoji_id}>"
                emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
            else:
                emoji_display = emoji_name
                emoji_url = None
            embed = discord.Embed(title=f"{emoji} Reaction Added", description=(f"**{username}** reacted with {emoji_display} in **#{channel_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            if avatar_url: embed.set_thumbnail(url=avatar_url)
            if emoji_url: embed.set_image(url=emoji_url)
            embed.set_footer(text=f"User ID: {user_id}  •  Msg ID: {msg_id}")
            return embed

        elif event_type == "MESSAGE_REACTION_REMOVE":
            user_id = int(data.get("user_id", 0))
            msg_id = data.get("message_id")
            channel_id = data.get("channel_id")
            guild_id = data.get("guild_id")
            emoji_data = data.get("emoji", {})
            emoji_name = emoji_data.get("name", "❓")
            emoji_id = emoji_data.get("id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            member = guild.get_member(user_id) if guild else None
            username = member.display_name if member else f"User {user_id}"
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            channel_name = channel.name if isinstance(channel, discord.TextChannel) else f"Channel {channel_id}"
            if emoji_id: emoji_display = f"<:{emoji_name}:{emoji_id}>"
            else: emoji_display = emoji_name
            embed = discord.Embed(title=f"{emoji} Reaction Removed", description=(f"**{username}** removed their {emoji_display} reaction from **#{channel_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.set_footer(text=f"User ID: {user_id}  •  Msg ID: {msg_id}")
            return embed

        elif event_type == "THREAD_CREATE":
            thread_id = data.get("id")
            name = data.get("name", "unknown")
            guild_id = data.get("guild_id")
            parent_id = data.get("parent_id")
            owner_id = int(data.get("owner_id", 0))
            message_count = data.get("message_count", 0)
            member_count = data.get("member_count", 0)
            thread_type = data.get("type", 11)
            type_names = {10: "News Thread", 11: "Public Thread", 12: "Private Thread"}
            type_emojis = {10: "📰", 11: "🧵", 12: "🔒"}
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            owner = guild.get_member(owner_id) if guild else None
            owner_name = owner.display_name if owner else f"User {owner_id}"
            parent = guild.get_channel(int(parent_id)) if guild and parent_id else None
            parent_name = parent.name if parent else f"Channel {parent_id}"
            embed = discord.Embed(title=f"{emoji} Thread Created", description=(f"**{name}** was created in **#{parent_name}** in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.add_field(name="📋 Type", value=f"{type_emojis.get(thread_type, '🧵')} {type_names.get(thread_type, 'Thread')}", inline=True)
            embed.add_field(name="👤 Creator", value=owner_name, inline=True)
            embed.add_field(name="💬 Messages", value=str(message_count), inline=True)
            embed.add_field(name="👥 Members", value=str(member_count), inline=True)
            embed.set_footer(text=f"Thread ID: {thread_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "THREAD_DELETE":
            thread_id = data.get("id")
            guild_id = data.get("guild_id")
            parent_id = data.get("parent_id")
            thread_type = data.get("type", 11)
            type_names = {10: "News Thread", 11: "Public Thread", 12: "Private Thread"}
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            parent = guild.get_channel(int(parent_id)) if guild and parent_id else None
            parent_name = parent.name if parent else f"Channel {parent_id}"
            embed = discord.Embed(title=f"{emoji} Thread Deleted", description=(f"A {type_names.get(thread_type, 'thread')} was deleted from **#{parent_name}** in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.set_footer(text=f"Thread ID: {thread_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "GUILD_UPDATE":
            guild_id = data.get("id")
            name = data.get("name", "Unknown")
            icon = data.get("icon")
            description = data.get("description", "")
            owner_id = int(data.get("owner_id", 0))
            member_count = data.get("member_count", "?")
            premium_tier = data.get("premium_tier", 0)
            verification_level = data.get("verification_level", 0)
            verif_names = ["None", "Low", "Medium", "High", "Very High"]
            boost_names = ["None", "Tier 1", "Tier 2", "Tier 3"]
            boost_emojis = ["", "🥉", "🥈", "🥇"]
            icon_url = f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.png" if icon else None
            embed = discord.Embed(title=f"{emoji} Server Updated", description=(f"**{name}** server settings were updated {self._relative_time()}"), color=color, timestamp=datetime.now())
            if icon_url: embed.set_thumbnail(url=icon_url)
            embed.add_field(name="👥 Members", value=str(member_count), inline=True)
            embed.add_field(name="🛡️ Verification", value=verif_names[min(verification_level, 4)], inline=True)
            embed.add_field(name="💎 Boost Tier", value=f"{boost_emojis[premium_tier]} {boost_names[premium_tier]}", inline=True)
            if description: embed.add_field(name="📝 Description", value=description[:200], inline=False)
            embed.set_footer(text=f"Guild ID: {guild_id}  •  Owner ID: {owner_id}")
            return embed

        elif event_type == "INVITE_CREATE":
            code = data.get("code", "?")
            guild_id = data.get("guild_id")
            channel_id = data.get("channel_id")
            inviter = data.get("inviter", {})
            inviter_id = int(inviter.get("id", 0)) if inviter else 0
            inviter_name = inviter.get("global_name") or inviter.get("username", "Unknown") if inviter else "Unknown"
            max_uses = data.get("max_uses", 0)
            max_age = data.get("max_age", 0)
            temporary = data.get("temporary", False)
            created_at = data.get("created_at")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            channel_name = channel.name if isinstance(channel, discord.TextChannel) else f"Channel {channel_id}"
            age_str = f"{max_age // 3600}h" if max_age > 0 else "Never expires"
            uses_str = str(max_uses) if max_uses > 0 else "Unlimited"
            embed = discord.Embed(title=f"{emoji} Invite Created", description=(f"An invite was created in **#{channel_name}** in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.add_field(name="🔗 Code", value=f"`{code}`", inline=True)
            embed.add_field(name="👤 Creator", value=inviter_name, inline=True)
            embed.add_field(name="⏳ Expires", value=age_str, inline=True)
            embed.add_field(name="🔢 Max Uses", value=uses_str, inline=True)
            embed.add_field(name="🕒 Temporary", value="Yes" if temporary else "No", inline=True)
            embed.set_footer(text=f"Guild ID: {guild_id}  •  Channel ID: {channel_id}")
            return embed

        elif event_type == "INVITE_DELETE":
            code = data.get("code", "?")
            guild_id = data.get("guild_id")
            channel_id = data.get("channel_id")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            channel_name = channel.name if isinstance(channel, discord.TextChannel) else f"Channel {channel_id}"
            embed = discord.Embed(title=f"{emoji} Invite Deleted", description=(f"Invite `{code}` was deleted from **#{channel_name}** in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.set_footer(text=f"Guild ID: {guild_id}  •  Channel ID: {channel_id}")
            return embed

        elif event_type == "STAGE_INSTANCE_CREATE":
            stage_id = data.get("id")
            guild_id = data.get("guild_id")
            channel_id = data.get("channel_id")
            topic = data.get("topic", "Unknown")
            privacy_level = data.get("privacy_level", 2)
            discoverable = data.get("discoverable_disabled", False)
            privacy_names = {1: "Public", 2: "Guild-only"}
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            channel = guild.get_channel(int(channel_id)) if (guild and channel_id) else None
            channel_name = channel.name if channel else f"Channel {channel_id}"
            embed = discord.Embed(title=f"{emoji} Stage Started", description=(f"**{topic}** started in **#{channel_name}** in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.add_field(name="🔒 Privacy", value=privacy_names.get(privacy_level, "Unknown"), inline=True)
            embed.add_field(name="🔍 Discoverable", value="No" if discoverable else "Yes", inline=True)
            embed.set_footer(text=f"Stage ID: {stage_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "STAGE_INSTANCE_DELETE":
            stage_id = data.get("id")
            guild_id = data.get("guild_id")
            channel_id = data.get("channel_id")
            topic = data.get("topic", "Unknown")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            channel = guild.get_channel(int(channel_id)) if (guild and channel_id) else None
            channel_name = channel.name if channel else f"Channel {channel_id}"
            embed = discord.Embed(title=f"{emoji} Stage Ended", description=(f"**{topic}** ended in **#{channel_name}** in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.set_footer(text=f"Stage ID: {stage_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "GUILD_SCHEDULED_EVENT_CREATE":
            event_id = data.get("id")
            guild_id = data.get("guild_id")
            name = data.get("name", "Unknown")
            description = data.get("description", "")
            scheduled_start = data.get("scheduled_start_time")
            scheduled_end = data.get("scheduled_end_time")
            privacy_level = data.get("privacy_level", 2)
            status = data.get("status", 1)
            entity_type = data.get("entity_type", 1)
            channel_id = data.get("channel_id")
            creator = data.get("creator", {})
            creator_name = creator.get("global_name") or creator.get("username", "Unknown") if creator else "Unknown"
            privacy_names = {1: "Public", 2: "Guild-only"}
            status_names = {1: "Scheduled", 2: "Active", 3: "Completed", 4: "Cancelled"}
            entity_names = {1: "Stage", 2: "Voice", 3: "External"}
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            embed = discord.Embed(title=f"{emoji} Event Created", description=(f"**{name}** scheduled in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.add_field(name="👤 Creator", value=creator_name, inline=True)
            embed.add_field(name="📋 Type", value=entity_names.get(entity_type, "Unknown"), inline=True)
            embed.add_field(name="🔒 Privacy", value=privacy_names.get(privacy_level, "Unknown"), inline=True)
            embed.add_field(name="📊 Status", value=status_names.get(status, "Unknown"), inline=True)
            if scheduled_start:
                start_dt = datetime.fromisoformat(scheduled_start.replace("Z", "+00:00"))
                embed.add_field(name="📅 Starts", value=f"<t:{int(start_dt.timestamp())}:F>", inline=True)
            if scheduled_end:
                end_dt = datetime.fromisoformat(scheduled_end.replace("Z", "+00:00"))
                embed.add_field(name="🏁 Ends", value=f"<t:{int(end_dt.timestamp())}:F>", inline=True)
            if description:
                embed.add_field(name="📝 Description", value=description[:200], inline=False)
            embed.set_footer(text=f"Event ID: {event_id}  •  Guild ID: {guild_id}")
            return embed

        elif event_type == "GUILD_SCHEDULED_EVENT_DELETE":
            event_id = data.get("id")
            guild_id = data.get("guild_id")
            name = data.get("name", "Unknown")
            guild = self.bot.get_guild(int(guild_id)) if guild_id else None
            guild_name = guild.name if guild else f"Guild {guild_id}"
            embed = discord.Embed(title=f"{emoji} Event Deleted", description=(f"**{name}** was cancelled in **{guild_name}** {self._relative_time()}"), color=color, timestamp=datetime.now())
            embed.set_footer(text=f"Event ID: {event_id}  •  Guild ID: {guild_id}")
            return embed

        else:
            embed = discord.Embed(title=f"{emoji} {event_type}", description=f"Gateway event received {self._relative_time()}", color=color, timestamp=datetime.now())
            raw_json = json.dumps(data, indent=2, default=str)[:1000]
            embed.add_field(name="📄 Raw Data", value=f"```json {raw_json} ```", inline=False)
            embed.set_footer(text=f"Event: {event_type}")
            return embed


# ========== BOT FACTORY ===================

def create_bot():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.presences = True
    intents.guilds = True
    intents.voice_states = True
    intents.bans = True
    intents.invites = True
    intents.reactions = True
    intents.typing = True
    intents.message_content = True

    bot = commands.Bot(command_prefix=PREFIX, intents=intents)
    gateway_logger = GatewayLogger(bot, GATEWAY_LOG_CHANNEL_ID)

    @bot.event
    async def on_ready():
        global user_points, user_stats, user_personas, daily_claimed, voted_users, bot_message_count
        user_points, user_stats, user_personas, daily_claimed, voted_users, bot_message_count = await load_data_async()
        await gateway_logger.start()
        for g in bot.guilds:
            bot.tree.clear_commands(guild=g)
            await bot.tree.sync(guild=g)
        await bot.tree.sync()
        print("Slash commands synced globally")
        check_reminders.start()
        auto_save.start()
        vc_watchdog.start()
        print(f"Bot online as {bot.user}")
        print(f"Serving {len(bot.guilds)} servers")
        print(f"Storage: JSONBin={'active' if _jsonbin_working else 'disabled/failed'}, Local file={'enabled'}")
        if GATEWAY_LOG_CHANNEL_ID:
            print(f"Gateway logging active -> Channel ID: {GATEWAY_LOG_CHANNEL_ID}")
        print(f"OmniRoute endpoint: {OMNIROUTE_BASE_URL}")
        print(f"Top.gg webhook auth: {'configured ✅' if TOPGG_WEBHOOK_AUTH else 'not set ⚠️'}")
        await _start_webhook_server(bot)

    # ═══════════════════════════════════════════════════════════════════════
    # GATEWAY EVENT HANDLERS
    # ═══════════════════════════════════════════════════════════════════════

    @bot.event
    async def on_presence_update(before, after):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "user": {"id": str(after.id), "username": after.name, "global_name": after.global_name, "avatar": after.avatar.key if after.avatar else None},
            "status": after.status.value if after.status else "unknown",
            "activities": [{"type": a.type.value, "name": a.name, "details": getattr(a, "details", None), "state": getattr(a, "state", None)} for a in after.activities],
            "client_status": {"desktop": after.client_status.desktop if after.client_status.desktop else None, "mobile": after.client_status.mobile if after.client_status.mobile else None, "web": after.client_status.web if after.client_status.web else None},
            "guild_id": str(getattr(after.guild, "id", None)) if hasattr(after, "guild") else None,
        }
        await gateway_logger.log("PRESENCE_UPDATE", data)

    @bot.event
    async def on_voice_state_update(member, before, after):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "user_id": str(member.id),
            "guild_id": str(member.guild.id),
            "channel_id": str(after.channel.id) if after.channel else None,
            "session_id": after.session_id or "?",
            "self_deaf": after.self_deaf,
            "self_mute": after.self_mute,
            "self_stream": after.self_stream or False,
            "self_video": after.self_video or False,
            "suppress": after.suppress,
            "request_to_speak_timestamp": after.request_to_speak_timestamp.isoformat() if hasattr(after, "request_to_speak_timestamp") and after.request_to_speak_timestamp else None,
        }
        await gateway_logger.log("VOICE_STATE_UPDATE", data)

    @bot.event
    async def on_message(message):
        if message.author.bot:
            return
        if GATEWAY_LOG_CHANNEL_ID and message.guild:
            data = {
                "author": {
                    "id": str(message.author.id),
                    "username": message.author.name,
                    "global_name": getattr(message.author, "global_name", None),
                    "avatar": message.author.avatar.key if message.author.avatar else None,
                    "bot": message.author.bot,
                },
                "content": message.content,
                "id": str(message.id),
                "channel_id": str(message.channel.id),
                "guild_id": str(message.guild.id),
                "attachments": [{"filename": a.filename, "size": a.size, "url": a.url} for a in message.attachments],
                "embeds": [{"type": e.type} for e in message.embeds],
                "mentions": [{"id": str(m.id), "username": m.name} for m in message.mentions],
                "mention_roles": [str(r.id) for r in message.role_mentions],
                "mention_everyone": message.mention_everyone,
            }
            await gateway_logger.log("MESSAGE_CREATE", data)

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
            await message.channel.send(f"Let's keep it clean! Here's a joke instead: {joke}")
            return

        system_prompt = get_system_prompt(message.author.id)
        async with message.channel.typing():
            try:
                reply = await generate_reply(user_input, system_prompt)
            except Exception:
                traceback.print_exc()
                reply = "⚠️ AI crashed. Please try again."

        global bot_message_count
        bot_message_count += 1
        await message.channel.send(reply)

        if bot_message_count % 25 == 0:
            await asyncio.sleep(1.5)
            vote_embed = build_vote_embed(TOPGG_BOT_ID)
            await message.channel.send(random.choice(VOTE_MESSAGES), embed=vote_embed)
            mark_dirty()

    @bot.event
    async def on_member_join(member):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "user": {"id": str(member.id), "username": member.name, "global_name": getattr(member, "global_name", None), "avatar": member.avatar.key if member.avatar else None},
            "guild_id": str(member.guild.id),
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "pending": member.pending,
            "premium_since": member.premium_since.isoformat() if member.premium_since else None,
        }
        await gateway_logger.log("GUILD_MEMBER_ADD", data)

    @bot.event
    async def on_member_remove(member):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "user": {"id": str(member.id), "username": member.name, "global_name": getattr(member, "global_name", None), "avatar": member.avatar.key if member.avatar else None},
            "guild_id": str(member.guild.id),
        }
        await gateway_logger.log("GUILD_MEMBER_REMOVE", data)

    @bot.event
    async def on_member_update(before, after):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "user": {"id": str(after.id), "username": after.name, "global_name": getattr(after, "global_name", None), "avatar": after.avatar.key if after.avatar else None},
            "guild_id": str(after.guild.id),
            "nick": after.nick,
            "roles": [str(r.id) for r in after.roles[1:]],
            "premium_since": after.premium_since.isoformat() if after.premium_since else None,
            "pending": after.pending,
            "communication_disabled_until": after.timed_out_until.isoformat() if after.timed_out_until else None,
        }
        await gateway_logger.log("GUILD_MEMBER_UPDATE", data)

    @bot.event
    async def on_typing(channel, user, when):
        if not GATEWAY_LOG_CHANNEL_ID or not isinstance(channel, discord.TextChannel):
            return
        data = {"user_id": str(user.id), "channel_id": str(channel.id), "guild_id": str(channel.guild.id) if channel.guild else None, "timestamp": when.isoformat()}
        await gateway_logger.log("TYPING_START", data)

    @bot.event
    async def on_raw_reaction_add(payload):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "user_id": str(payload.user_id), "message_id": str(payload.message_id),
            "channel_id": str(payload.channel_id),
            "guild_id": str(payload.guild_id) if payload.guild_id else None,
            "emoji": {"name": payload.emoji.name, "id": str(payload.emoji.id) if payload.emoji.id else None, "animated": payload.emoji.animated},
        }
        await gateway_logger.log("MESSAGE_REACTION_ADD", data)

    @bot.event
    async def on_raw_reaction_remove(payload):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "user_id": str(payload.user_id), "message_id": str(payload.message_id),
            "channel_id": str(payload.channel_id),
            "guild_id": str(payload.guild_id) if payload.guild_id else None,
            "emoji": {"name": payload.emoji.name, "id": str(payload.emoji.id) if payload.emoji.id else None},
        }
        await gateway_logger.log("MESSAGE_REACTION_REMOVE", data)

    @bot.event
    async def on_guild_channel_create(channel):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"type": channel.type.value, "id": str(channel.id), "name": channel.name, "guild_id": str(channel.guild.id), "parent_id": str(channel.category_id) if channel.category_id else None, "nsfw": getattr(channel, "nsfw", False)}
        await gateway_logger.log("CHANNEL_CREATE", data)

    @bot.event
    async def on_guild_channel_delete(channel):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"type": channel.type.value, "id": str(channel.id), "name": channel.name, "guild_id": str(channel.guild.id)}
        await gateway_logger.log("CHANNEL_DELETE", data)

    @bot.event
    async def on_guild_role_create(role):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"role": {"id": str(role.id), "name": role.name, "color": role.color.value, "hoist": role.hoist, "mentionable": role.mentionable}, "guild_id": str(role.guild.id)}
        await gateway_logger.log("GUILD_ROLE_CREATE", data)

    @bot.event
    async def on_guild_role_delete(role):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"role_id": str(role.id), "guild_id": str(role.guild.id)}
        await gateway_logger.log("GUILD_ROLE_DELETE", data)

    @bot.event
    async def on_guild_update(before, after):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"id": str(after.id), "name": after.name, "icon": after.icon.key if after.icon else None, "description": after.description, "owner_id": str(after.owner_id), "member_count": after.member_count, "premium_tier": after.premium_tier, "verification_level": after.verification_level.value}
        await gateway_logger.log("GUILD_UPDATE", data)

    @bot.event
    async def on_invite_create(invite):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "code": invite.code, "guild_id": str(invite.guild.id) if invite.guild else None,
            "channel_id": str(invite.channel.id) if invite.channel else None,
            "inviter": {"id": str(invite.inviter.id), "username": invite.inviter.name, "global_name": getattr(invite.inviter, "global_name", None)} if invite.inviter else None,
            "max_uses": invite.max_uses, "max_age": invite.max_age, "temporary": invite.temporary,
            "created_at": invite.created_at.isoformat() if invite.created_at else None,
        }
        await gateway_logger.log("INVITE_CREATE", data)

    @bot.event
    async def on_invite_delete(invite):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"code": invite.code, "guild_id": str(invite.guild.id) if invite.guild else None, "channel_id": str(invite.channel.id) if invite.channel else None}
        await gateway_logger.log("INVITE_DELETE", data)

    @bot.event
    async def on_thread_create(thread):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"id": str(thread.id), "name": thread.name, "guild_id": str(thread.guild.id), "parent_id": str(thread.parent_id) if thread.parent_id else None, "owner_id": str(thread.owner_id), "message_count": thread.message_count, "member_count": thread.member_count, "type": thread.type.value}
        await gateway_logger.log("THREAD_CREATE", data)

    @bot.event
    async def on_thread_delete(thread):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"id": str(thread.id), "guild_id": str(thread.guild.id), "parent_id": str(thread.parent_id) if thread.parent_id else None, "type": thread.type.value}
        await gateway_logger.log("THREAD_DELETE", data)

    @bot.event
    async def on_guild_ban(guild, user):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"user": {"id": str(user.id), "username": user.name, "global_name": getattr(user, "global_name", None), "avatar": user.avatar.key if user.avatar else None}, "guild_id": str(guild.id)}
        await gateway_logger.log("GUILD_BAN_ADD", data)

    @bot.event
    async def on_guild_unban(guild, user):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"user": {"id": str(user.id), "username": user.name, "global_name": getattr(user, "global_name", None), "avatar": user.avatar.key if user.avatar else None}, "guild_id": str(guild.id)}
        await gateway_logger.log("GUILD_BAN_REMOVE", data)

    @bot.event
    async def on_stage_instance_create(stage):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"id": str(stage.id), "guild_id": str(stage.guild.id), "channel_id": str(stage.channel_id), "topic": stage.topic, "privacy_level": stage.privacy_level.value, "discoverable_disabled": stage.discoverable_disabled}
        await gateway_logger.log("STAGE_INSTANCE_CREATE", data)

    @bot.event
    async def on_stage_instance_delete(stage):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"id": str(stage.id), "guild_id": str(stage.guild.id), "channel_id": str(stage.channel_id), "topic": stage.topic}
        await gateway_logger.log("STAGE_INSTANCE_DELETE", data)

    @bot.event
    async def on_scheduled_event_create(event):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {
            "id": str(event.id), "guild_id": str(event.guild_id), "name": event.name, "description": event.description,
            "scheduled_start_time": event.start_time.isoformat() if event.start_time else None,
            "scheduled_end_time": event.end_time.isoformat() if event.end_time else None,
            "privacy_level": event.privacy_level.value, "status": event.status.value, "entity_type": event.entity_type.value,
            "channel_id": str(event.channel_id) if event.channel_id else None,
            "creator": {"id": str(event.creator_id), "username": event.creator.name if event.creator else "Unknown", "global_name": getattr(event.creator, "global_name", None) if event.creator else None} if event.creator else None,
        }
        await gateway_logger.log("GUILD_SCHEDULED_EVENT_CREATE", data)

    @bot.event
    async def on_scheduled_event_delete(event):
        if not GATEWAY_LOG_CHANNEL_ID:
            return
        data = {"id": str(event.id), "guild_id": str(event.guild_id), "name": event.name}
        await gateway_logger.log("GUILD_SCHEDULED_EVENT_DELETE", data)


    # ========== SLASH COMMANDS =================

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            try:
                await interaction.response.send_message(f"⏳ Slow down! Try again in **{error.retry_after:.1f}s**.", ephemeral=True)
            except Exception:
                pass
            return
        inner = getattr(error, "original", error)
        if isinstance(inner, discord.HTTPException) and inner.status == 429:
            print("Discord 429 — Cloudflare rate-limiting this IP")
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

    # ========== TRIVIA VIEW =================

    class TriviaView(discord.ui.View):
        def __init__(self, guild_id: int, correct: str, answers: list[str], question_data: dict):
            super().__init__(timeout=300)
            self.guild_id   = guild_id
            self.correct    = correct
            self.answered   = False
            self.wrong_ids: set[int] = set()
            self.message: discord.Message | None = None
            letters = ["A", "B", "C", "D"]
            for i, ans in enumerate(answers):
                btn = discord.ui.Button(label=f"{letters[i]}. {ans[:80]}", style=discord.ButtonStyle.primary, custom_id=f"trivia_{i}", row=i // 2)
                btn.callback = self._make_callback(ans)
                self.add_item(btn)

        def _make_callback(self, answer: str):
            async def callback(interaction: discord.Interaction):
                if self.answered:
                    await interaction.response.send_message("⏹️ This trivia round is already over!", ephemeral=True)
                    return
                if interaction.user.id in self.wrong_ids:
                    await interaction.response.send_message("🚫 You already guessed wrong — you're locked out of this question!", ephemeral=True)
                    return
                if answer.strip().lower() == self.correct.strip().lower():
                    self.answered = True
                    active_trivia.pop(self.guild_id, None)
                    add_points(interaction.user.id, 10)
                    for item in self.children:
                        if isinstance(item, discord.ui.Button):
                            item.disabled = True
                            if item.label and item.label.split(". ", 1)[-1] == answer[:80]:
                                item.style = discord.ButtonStyle.success
                            else:
                                item.style = discord.ButtonStyle.secondary
                    self.stop()
                    if self.message and self.message.embeds:
                        embed = self.message.embeds[0]
                    else:
                        embed = discord.Embed(title="🧠 Trivia Time!", color=discord.Color.green())
                    embed.color = discord.Color.green()
                    embed.set_footer(text=f"✅ {interaction.user.display_name} got it right! +10 points")
                    await interaction.response.edit_message(embed=embed, view=self)
                    await interaction.followup.send(f"🎉 **{interaction.user.mention}** answered correctly and earned **10 points**! Total: **{get_points(interaction.user.id)}** points")
                else:
                    self.wrong_ids.add(interaction.user.id)
                    await interaction.response.send_message("❌ **Wrong answer!** You're locked out of this question.", ephemeral=True)
            return callback

        async def on_timeout(self):
            self.answered = True
            active_trivia.pop(self.guild_id, None)
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
                    if item.label and item.label.split(". ", 1)[-1] == self.correct[:80]:
                        item.style = discord.ButtonStyle.success
                    else:
                        item.style = discord.ButtonStyle.secondary
            msg = getattr(self, "message", None)
            if msg is not None and msg.embeds:
                embed = msg.embeds[0]
                embed.color = discord.Color.red()
                embed.set_footer(text=f"⏰ Time's up! The answer was: {self.correct}")
                try:
                    await msg.edit(embed=embed, view=self)
                    await msg.channel.send(f"⏰ **Nobody got it!** The correct answer was: **{self.correct}**")
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
        embed = discord.Embed(title="🧠 Trivia Time!", description=f"**{question_data['question']}**", color=color)
        embed.add_field(name="📚 Category", value=question_data["category"], inline=True)
        embed.add_field(name="⚡ Difficulty", value=question_data["difficulty"].capitalize(), inline=True)
        embed.add_field(name="⏳ Time Limit", value="5 minutes", inline=True)
        embed.set_footer(text="Press a button to answer! Wrong answers lock you out.")
        view = TriviaView(guild.id, correct, answers, question_data)
        try:
            msg = await interaction.followup.send(embed=embed, view=view)
            view.message = msg
        except discord.HTTPException as e:
            active_trivia.pop(guild.id, None)
            print(f"Could not send trivia message: {e}")
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
            await interaction.response.send_message(f"❌ You only have **{sender_pts}** points — not enough to give **{amount}**!", ephemeral=True)
            return
        user_points[interaction.user.id] = sender_pts - amount
        add_points(user.id, amount)
        embed = discord.Embed(title="🎁 Points Gifted!", description=f"{interaction.user.mention} gave **{amount}** points to {user.mention}!", color=discord.Color.green())
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
            await interaction.response.send_message("⏳ You've already claimed your daily points today! Come back tomorrow.", ephemeral=True)
            return
        bonus = random.randint(15, 50)
        daily_claimed[uid] = now_str
        add_points(uid, bonus)
        mark_dirty()
        embed = discord.Embed(title="🌅 Daily Bonus!", description=f"You claimed **{bonus}** bonus points! Total: **{get_points(uid)}** points", color=discord.Color.yellow())
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
            await interaction.response.send_message(f"❌ You don't have enough points! You have **{get_points(challenger.id)}**.", ephemeral=True)
            return
        if get_points(user.id) < wager:
            await interaction.response.send_message(f"❌ {user.name} doesn't have enough points to accept this duel!", ephemeral=True)
            return
        winner = random.choice([challenger, user])
        loser = user if winner == challenger else challenger
        user_points[loser.id] = get_points(loser.id) - wager
        add_points(winner.id, wager)
        embed = discord.Embed(title="🪙 Coin Flip Duel!", description=(f"{challenger.mention} vs {user.mention} **Wager:** {wager} points 🎉 **{winner.mention} wins!**"), color=discord.Color.gold())
        embed.add_field(name=f"{winner.name}", value=f"{get_points(winner.id)} pts (+{wager})", inline=True)
        embed.add_field(name=f"{loser.name}", value=f"{get_points(loser.id)} pts (-{wager})", inline=True)
        await interaction.response.send_message(embed=embed)
        track_user_activity(challenger.id)


    # ========== POLL VIEW =================

    class PollView(discord.ui.View):
        message: discord.Message | None = None
        def __init__(self, options: list[str], creator_id: int):
            super().__init__(timeout=86400)
            self.options = options
            self.creator_id = creator_id
            self.votes: dict[int, int] = {}
            self.counts = [0] * len(options)
            self.closed = False
            self.message = None
            letters = ["🇦", "🇧", "🇨", "🇩"]
            for i, opt in enumerate(options):
                btn = discord.ui.Button(label=f"{letters[i]} {opt}", style=discord.ButtonStyle.primary, custom_id=f"poll_opt_{i}", row=0)
                btn.callback = self._make_vote_callback(i)
                self.add_item(btn)
            close_btn = discord.ui.Button(label="🔒 Close Poll", style=discord.ButtonStyle.danger, custom_id="poll_close", row=1)
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
                        self.counts[old] -= 1
                        del self.votes[uid]
                        await interaction.response.send_message(f"↩️ Removed your vote for **{self.options[idx]}**.", ephemeral=True)
                    else:
                        self.counts[old] -= 1
                        self.counts[idx] += 1
                        self.votes[uid] = idx
                        await interaction.response.send_message(f"🔄 Changed your vote to **{self.options[idx]}**.", ephemeral=True)
                else:
                    self.counts[idx] += 1
                    self.votes[uid] = idx
                    await interaction.response.send_message(f"✅ Voted for **{self.options[idx]}**!", ephemeral=True)
                await self._refresh_embed(interaction)
            return callback

        async def close_poll(self, interaction: discord.Interaction):
            if interaction.user.id != self.creator_id:
                await interaction.response.send_message("❌ Only the poll creator can close it.", ephemeral=True)
                return
            self.closed = True
            self.stop()
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await self._refresh_embed(interaction, closed=True)

        def _build_embed(self, closed: bool = False) -> discord.Embed:
            total = sum(self.counts)
            letters = ["🇦", "🇧", "🇨", "🇩"]
            desc_lines = []
            for i, opt in enumerate(self.options):
                count = self.counts[i]
                pct = (count / total * 100) if total > 0 else 0
                bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
                desc_lines.append(f"{letters[i]} **{opt}**\n`{bar}` {count} vote{'s' if count != 1 else ''} ({pct:.1f}%)\n")
            status = "🔒 Poll Closed" if closed else "📊 Poll Active"
            msg = getattr(self, "message", None)
            embed = discord.Embed(title=msg.embeds[0].title if msg and msg.embeds else "📊 Poll", description="\n".join(desc_lines), color=discord.Color.greyple() if closed else discord.Color.blurple())
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
    @app_commands.describe(question="The poll question", option1="First option", option2="Second option", option3="Third option (optional)", option4="Fourth option (optional)")
    async def poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: Optional[str] = None, option4: Optional[str] = None):
        options = [opt for opt in [option1, option2, option3, option4] if opt]
        letters = ["🇦", "🇧", "🇨", "🇩"]
        desc_lines = []
        for i, opt in enumerate(options):
            desc_lines.append(f"{letters[i]} **{opt}**\n`░░░░░░░░░░` 0 votes (0.0%)\n")
        embed = discord.Embed(title=f"📊 {question}", description="\n".join(desc_lines), color=discord.Color.blurple())
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
        responses = ["It is certain.", "It is decidedly so.", "Without a doubt.", "Yes definitely.", "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.", "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.", "Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."]
        answer = random.choice(responses)
        embed = discord.Embed(title="🎱 Magic 8-Ball", description=f"**Question:** {question}\n**Answer:** {answer}", color=discord.Color.dark_blue())
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
        reminders.append({"user_id": interaction.user.id, "channel_id": interaction.channel.id, "message": message, "time": remind_time})
        await interaction.response.send_message(f"⏰ Reminder set! I'll remind you in **{minutes} minute(s)** about: {message}")
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
        embed = discord.Embed(title="🎭 Persona Changed!", description=descriptions.get(style, "Persona updated!"), color=discord.Color.magenta())
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
        now = datetime.now()
        last_vote_claim = voted_users.get(uid)
        if last_vote_claim:
            last_dt = datetime.fromisoformat(last_vote_claim)
            if (now - last_dt).total_seconds() < 43200:
                remaining = timedelta(seconds=43200) - (now - last_dt)
                h, m = divmod(int(remaining.total_seconds()), 3600)
                m = m // 60
                await interaction.followup.send(f"⏳ You already claimed your vote reward! Come back in **{h}h {m}m**.", ephemeral=True)
                return
        has_voted = await check_topgg_vote(uid)
        if not has_voted and TOPGG_TOKEN:
            no_vote_desc = f"Looks like you haven't voted yet!\n\n**[Vote here → Top.gg]({vote_url})**\n\nAfter voting, come back and run `/checkvote` again to claim your reward!"
            embed = discord.Embed(title="❌ No vote found", description=no_vote_desc, color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        if not TOPGG_TOKEN:
            not_cfg_desc = f"The bot owner hasn't set up automatic vote verification yet.\n\n**[Vote here → Top.gg]({vote_url})**\n\nAsk the bot owner to add `TOPGG_TOKEN` and `TOPGG_BOT_ID` to the `.env` file!"
            embed = discord.Embed(title="⚙️ Vote verification not configured", description=not_cfg_desc, color=discord.Color.orange())
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        bonus = 50
        voted_users[uid] = now.isoformat()
        add_points(uid, bonus)
        mark_dirty()
        reward_desc = f"Thank you for voting for Rune on Top.gg! 💙\n\nYou received **+{bonus} bonus points**!\nTotal points: **{get_points(uid)}**\n\nYou can vote again in **12 hours**."
        embed = discord.Embed(title="🎉 Vote reward claimed!", description=reward_desc, color=discord.Color.green())
        embed.set_footer(text="Voting helps Rune reach more servers!")
        await interaction.followup.send(embed=embed, ephemeral=True)
        track_user_activity(uid)

    @bot.tree.command(name="vote", description="Vote for Rune on Top.gg and earn bonus points! 🗳️")
    async def vote(interaction: discord.Interaction):
        vote_url = f"https://top.gg/bot/{TOPGG_BOT_ID}/vote" if TOPGG_BOT_ID else "https://top.gg"
        embed = build_vote_embed(TOPGG_BOT_ID)
        await interaction.response.send_message(f"Thanks for supporting Rune! 💙 After voting, use `/checkvote` to claim your **+50 points**!", embed=embed)
        track_user_activity(interaction.user.id)


    # ========== PUSH-TO-TALK / TTS STATE ==========
    ptt_sessions: dict[int, dict] = {}

    async def transcribe_audio(audio_bytes: bytes) -> str:
        """Send WAV bytes to OmniRoute Whisper-compatible endpoint and return transcribed text."""
        if ai_client is None:
            return ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                result = await ai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=("audio.wav", f, "audio/wav"),
                    response_format="text",
                )
            os.unlink(tmp_path)
            return str(result).strip()
        except Exception as e:
            print(f"Whisper STT error: {e}")
            return ""

    async def zonos_tts(text: str) -> bytes | None:
        """Convert text → speech via Zyphra Zonos API. Returns MP3 bytes or None."""
        if not ZONOS_API_KEY:
            return None
        try:
            payload = {"model": "zonos-v0.1-hybrid", "text": text[:500], "speaking_rate": 15, "language_iso_code": "en-us"}
            headers = {"Authorization": f"Bearer {ZONOS_API_KEY}", "Content-Type": "application/json"}
            async with aiohttp.ClientSession() as session:
                async with session.post("https://api.zyphra.com/v1/audio/text-to-speech", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    body = await resp.text()
                    print(f"Zonos TTS error {resp.status}: {body[:200]}")
        except Exception as e:
            print(f"Zonos TTS exception: {e}")
        return None

    async def play_tts_response(vc: discord.VoiceClient, text: str, text_channel: discord.abc.Messageable | None = None):
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
            print(f"FFmpeg playback error: {e}")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def capture_and_process_ptt(guild_id: int, user_id: int, text_channel_id: int, seconds: int, vc: discord.VoiceClient):
        """Records raw PCM from the VC for `seconds` seconds, transcribes via Whisper, replies via LLaMA + Zonos."""
        frames: list[bytes] = []
        stop_event = asyncio.Event()
        ptt_sessions[guild_id]["stop_event"] = stop_event
        text_channel = bot.get_channel(text_channel_id)
        messageable = text_channel if isinstance(text_channel, discord.TextChannel) else None
        FRAME_DURATION = 0.02
        MAX_FRAMES = int(seconds / FRAME_DURATION)
        frame_count = 0
        # NOTE: discord.py does not support receiving audio by default.
        # This requires discord-ext-voice-recv or similar extension.
        # PTT is currently disabled until audio receiving is set up.
        if messageable:
            await messageable.send("⚠️ PTT requires `discord-ext-voice-recv` to be installed. Run: `pip install discord-ext-voice-recv`")
        ptt_sessions[guild_id]["recording"] = False
        if not frames:
            if messageable:
                await messageable.send("⚠️ No audio captured — make sure you're unmuted and speaking!")
            return
        pcm_bytes = b"".join(frames)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(pcm_bytes)
        wav_bytes = wav_buffer.getvalue()
        if messageable:
            await messageable.send("🔄 Processing your audio...")
        transcript = await transcribe_audio(wav_bytes)
        if not transcript:
            if messageable:
                await messageable.send("⚠️ Couldn't understand that — try again with `/ptt`.")
            return
        if messageable:
            await messageable.send(f"🎙️ **You said:** {transcript}")
        persona = user_personas.get(user_id, "You are Rune, a friendly Discord bot. Keep replies short and conversational (1-3 sentences max).")
        reply = await generate_reply(transcript, persona)
        if messageable:
            await messageable.send(f"🔊 **Rune:** {reply}")
        vc_session = vc_sessions.get(guild_id)
        if vc_session and vc_session["vc"].is_connected():
            await play_tts_response(vc_session["vc"], reply, messageable)

    # ========== 24/7 VOICE CHAT =================
    vc_sessions: dict[int, dict] = {}

    @bot.tree.command(name="247", description="Make Rune join a voice channel 24/7 🔊 (Mod only)")
    @app_commands.describe(channel="The voice channel to join (defaults to your current channel)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def vc_247(interaction: discord.Interaction, channel: Optional[discord.VoiceChannel] = None):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        target = channel
        if target is None:
            if isinstance(interaction.user, discord.Member) and interaction.user.voice and interaction.user.voice.channel:
                vc_channel = interaction.user.voice.channel
                if isinstance(vc_channel, discord.VoiceChannel):
                    target = vc_channel
        if target is None:
            await interaction.response.send_message("❌ Please join a voice channel first, or specify one with the `channel` option.", ephemeral=True)
            return
        existing = vc_sessions.get(guild.id)
        if existing:
            vc: discord.VoiceClient = existing["vc"]
            if vc.is_connected():
                await vc.move_to(target)
                existing["channel_id"] = target.id
                existing["joined_at"] = datetime.now()
                embed = discord.Embed(title="🔊 Moved to new channel", description=f"Now holding **{target.name}** 24/7.", color=discord.Color.green())
                embed.set_footer(text="Use /leave247 to disconnect.")
                await interaction.response.send_message(embed=embed)
                return
        try:
            vc = await target.connect(reconnect=True, self_deaf=True)
        except discord.ClientException:
            await interaction.response.send_message("❌ Already connected somewhere — use `/leave247` first.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to join that channel.", ephemeral=True)
            return
        vc_sessions[guild.id] = {"channel_id": target.id, "joined_at": datetime.now(), "vc": vc}
        embed = discord.Embed(title="🔊 24/7 Voice Active", description=f"Now holding **{target.name}** open 24/7! I'll stay even if everyone leaves.", color=discord.Color.green())
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
        joined_at = session["joined_at"]
        if vc.is_connected():
            await vc.disconnect(force=True)
        duration = datetime.now() - joined_at
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes = remainder // 60
        channel = guild.get_channel(channel_id)
        ch_name = channel.name if isinstance(channel, discord.VoiceChannel) else f"<#{channel_id}>"
        embed = discord.Embed(title="🔇 Left voice channel", description=f"Disconnected from **{ch_name}**.", color=discord.Color.orange())
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
        joined_at = session["joined_at"]
        channel_id = session["channel_id"]
        vc: discord.VoiceClient = session["vc"]
        duration = datetime.now() - joined_at
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes = remainder // 60
        channel = guild.get_channel(channel_id)
        ch_name = channel.name if isinstance(channel, discord.VoiceChannel) else f"#{channel_id}"
        members_in_vc = len([m for m in (channel.members if isinstance(channel, discord.VoiceChannel) else []) if not m.bot])
        embed = discord.Embed(title="📊 24/7 Voice Status", color=discord.Color.green() if vc.is_connected() else discord.Color.red())
        embed.add_field(name="🔊 Channel", value=f"<#{channel_id}>", inline=True)
        embed.add_field(name="⏱️ Uptime", value=f"{hours}h {minutes}m", inline=True)
        embed.add_field(name="👥 Users in VC", value=str(members_in_vc), inline=True)
        embed.add_field(name="📡 Connected", value="✅ Yes" if vc.is_connected() else "❌ No", inline=True)
        embed.add_field(name="🕐 Joined at", value=f"<t:{int(joined_at.timestamp())}:F>", inline=False)
        embed.set_footer(text="Use /leave247 to disconnect")
        await interaction.response.send_message(embed=embed)

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
                    # Wait a moment for any in-flight disconnect to settle,
                    # then attempt the reconnect with a fresh voice client.
                    await asyncio.sleep(1)
                    new_vc = await channel.connect(reconnect=True, self_deaf=True)
                    session["vc"] = new_vc
                    print(f"Auto-reconnected to {channel.name} in {guild.name}")
                except Exception as e:
                    err = str(e)
                    # "Cannot write to closing transport" means the gateway socket
                    # was already closing — retry on the next loop tick instead
                    # of spamming the same failure.
                    if "closing transport" in err or "ConnectionReset" in err:
                        print(f"VC watchdog: gateway socket closing for {guild_id}, will retry next tick")
                        continue
                    print(f"VC watchdog reconnect failed for {guild_id}: {e}")

    @bot.tree.command(name="ptt", description="Start push-to-talk — Rune listens to you for a few seconds 🎙️")
    @app_commands.describe(seconds="How many seconds to record (3–15, default 5)")
    async def ptt(interaction: discord.Interaction, seconds: int = 5):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel to use PTT!", ephemeral=True)
            return
        vc_session = vc_sessions.get(guild.id)
        if not vc_session or not vc_session["vc"].is_connected():
            await interaction.response.send_message("❌ Rune isn't in a voice channel yet — use `/247` first!", ephemeral=True)
            return
        vc: discord.VoiceClient = vc_session["vc"]
        seconds = max(3, min(15, seconds))
        if ptt_sessions.get(guild.id, {}).get("recording"):
            await interaction.response.send_message("⏺️ Already recording! Wait for the current PTT to finish.", ephemeral=True)
            return
        ptt_sessions[guild.id] = {"recording": True, "user_id": interaction.user.id, "text_channel_id": interaction.channel.id if isinstance(interaction.channel, discord.TextChannel) else 0}
        await interaction.response.send_message(f"🎙️ **Recording for {seconds}s...** Speak now! *(Results will appear in this channel)*", ephemeral=False)
        user_id = interaction.user.id
        text_channel_id = ptt_sessions[guild.id]["text_channel_id"]
        asyncio.ensure_future(capture_and_process_ptt(guild.id, user_id, text_channel_id, seconds, vc))

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
        session = ptt_sessions.get(guild.id, {})
        if not session.get("recording"):
            await interaction.response.send_message("ℹ️ No active recording to stop.", ephemeral=True)
            return
        stop_event = session.get("stop_event")
        if stop_event:
            stop_event.set()
        ptt_sessions[guild.id]["recording"] = False
        await interaction.response.send_message("⏹️ Recording stopped — processing now...")


    # ========== TEST PROVIDERS COMMAND =================
    # Tests multiple OmniRoute free providers with increasing message lengths

    @bot.tree.command(name="testproviders", description="Test OmniRoute free providers with different message lengths 🧪")
    @app_commands.describe(length="Message length: short (50), medium (500), long (2000), max (4000)", model="Specific model to test, or 'auto' for all")
    @app_commands.choices(length=[
        app_commands.Choice(name="Short (~50 chars)", value="short"),
        app_commands.Choice(name="Medium (~500 chars)", value="medium"),
        app_commands.Choice(name="Long (~2000 chars)", value="long"),
        app_commands.Choice(name="Max (~4000 chars)", value="max"),
    ])
    async def testproviders(interaction: discord.Interaction, length: str = "short", model: str = "auto"):
        if ai_client is None:
            await interaction.response.send_message("⚠️ AI client not available. Install openai: `pip install openai`", ephemeral=True)
            return

        await interaction.response.defer()

        test_messages = {
            "short": "Hello! What's 2+2? Reply with just the number.",
            "medium": "Explain the concept of recursion in programming. Keep it under 3 sentences. " + "A" * 400,
            "long": "Write a short story about a robot learning to paint. " + "B" * 1800,
            "max": "Describe the entire history of computing from 1940 to 2026 in extreme detail. " + "C" * 3800,
        }

        test_msg = test_messages.get(length, test_messages["short"])

        # Models to test — these are common free-tier models available through OmniRoute
        models_to_test = [
            "auto",
            "gpt-3.5-turbo",
            "groq/compound",
            "gemini-1.5-flash",
            "claude-3-haiku",
        ] if model == "auto" else [model]

        results = []

        for m in models_to_test:
            start = time.time()
            try:
                completion = await ai_client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": "You are a test bot. Reply very briefly."},
                        {"role": "user", "content": test_msg[:4000]}
                    ],
                    temperature=0.5,
                    max_tokens=100,
                )
                elapsed = time.time() - start
                reply = completion.choices[0].message.content or "(empty)"
                provider = getattr(completion, 'model', m)
                results.append({
                    "model": m,
                    "provider": provider,
                    "status": "✅ SUCCESS",
                    "time": f"{elapsed:.2f}s",
                    "reply_preview": reply[:100].replace(chr(10), " "),
                    "tokens_in": len(test_msg),
                    "tokens_out": len(reply),
                })
            except Exception as e:
                elapsed = time.time() - start
                err = str(e)
                results.append({
                    "model": m,
                    "provider": "N/A",
                    "status": f"❌ FAILED ({err[:80]})",
                    "time": f"{elapsed:.2f}s",
                    "reply_preview": "—",
                    "tokens_in": len(test_msg),
                    "tokens_out": 0,
                })

        embed = discord.Embed(
            title=f"🧪 Provider Test Results — {length.upper()} ({len(test_msg)} chars)",
            description=f"Endpoint: `{OMNIROUTE_BASE_URL}`",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )

        for r in results:
            embed.add_field(
                name=f"{r['status']} {r['model']}",
                value=f"⏱️ {r['time']} | 📝 {r['tokens_in']}→{r['tokens_out']} chars {r['reply_preview']}",
                inline=False
            )

        embed.set_footer(text="OmniRoute auto-fallback may route to different providers than requested")
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    # ========== HELP COMMAND =================

    @bot.tree.command(name="help", description="View all available commands 📖")
    async def help_command(interaction: discord.Interaction):
        embed = discord.Embed(title="🤖 Rune Bot Commands", description="Here are all the commands you can use!", color=discord.Color.blue())
        commands_list = [
            ("🎮 **Fun**", "`/joke`, `/roast`, `/compliment`, `/8ball`, `/flip`, `/roll`, `/meme`"),
            ("🧠 **Trivia & Points**", "`/trivia`, `/points`, `/leaderboard`, `/daily`, `/give`, `/duel`"),
            ("📊 **Polls**", "`/poll` — Button poll with live vote counts"),
            ("🐾 **Animals**", "`/catfact`, `/dog`"),
            ("💡 **Inspiration**", "`/advice`, `/quote`, `/activity`"),
            ("🎭 **AI Persona**", "`/persona` — Change how Rune talks to you"),
            ("🛡️ **Moderation**", "`/kick`, `/ban`, `/mute`, `/unmute`, `/resettrivia` *(requires permissions)*"),
            ("⏰ **Utility**", "`/remind`, `/stats`, `/serverinfo`, `/help`"),
            ("🔊 **Voice 24/7**", "`/247` — Join a VC 24/7 | `/leave247` — Disconnect | `/vcstatus` — Uptime *(Mod only)*"),
            ("🎙️ **Push-to-Talk**", "`/ptt [seconds]` — Speak & Rune replies with TTS | `/stopttt` — Stop recording"),
            ("🗳️ **Vote**", "`/vote` — Vote for Rune | `/checkvote` — Claim **+50 point** reward"),
            ("🧪 **Test Providers**", "`/testproviders` — Test OmniRoute free AI providers with different message lengths"),
            ("💬 **AI Chat**", f"Use `{PREFIX}` prefix to chat with AI (e.g., `{PREFIX}hello`)"),
        ]
        for category, cmds in commands_list:
            embed.add_field(name=category, value=cmds, inline=False)
        embed.set_footer(text="Powered by OmniRoute — auto-fallback across free AI providers 🚀")
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
            if ok and _jsonbin_working:
                print("Auto-saved to JSONBin.")

    return bot

# ========== AUTO-RESTART LOOP ==============

def run_forever():
    while True:
        try:
            bot = create_bot()
            assert DISCORD_TOKEN is not None, "DISCORD_TOKEN not set!"
            bot.run(DISCORD_TOKEN)
        except Exception:
            print("BOT CRASHED:")
            traceback.print_exc()
            print(f"Restarting in {RESTART_DELAY} seconds...\n")
            time.sleep(RESTART_DELAY)

# ============== START =====================

if __name__ == "__main__":
    print("🚀 Starting Rune Bot with OmniRoute...")
    print("📡 Make sure OmniRoute is running: npm install -g omniroute && omniroute")
    run_forever()
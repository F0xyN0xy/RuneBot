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
from datetime import datetime, timedelta
import aiohttp
from typing import Optional
from dotenv import load_dotenv

# ================= LOAD ENV =================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PREFIX = os.getenv("PREFIX", ".")
RESTART_DELAY = int(os.getenv("RESTART_DELAY", "5"))
DATA_FILE = "data.json"

# =========================================

groq_client = Groq(api_key=GROQ_API_KEY)

# ============== PERSISTENT STORAGE ==================
# All data is saved to data.json so it survives restarts.

def load_data():
    """Load persisted data from disk. Returns defaults if file missing."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                raw = json.load(f)
            # JSON keys are always strings — convert user IDs back to int
            user_points   = {int(k): v for k, v in raw.get("user_points", {}).items()}
            user_stats_raw = raw.get("user_stats", {})
            user_stats = {}
            for k, v in user_stats_raw.items():
                user_stats[int(k)] = {
                    "commands_used": v["commands_used"],
                    "last_seen": datetime.fromisoformat(v["last_seen"])
                }
            user_personas = {int(k): v for k, v in raw.get("user_personas", {}).items()}
            daily_claimed = {int(k): v for k, v in raw.get("daily_claimed", {}).items()}
            return user_points, user_stats, user_personas, daily_claimed
        except Exception as e:
            print(f"⚠️  Could not load data.json: {e}. Starting fresh.")
    return {}, {}, {}, {}

def save_data():
    """Persist all in-memory data to disk."""
    try:
        serializable_stats = {
            str(k): {
                "commands_used": v["commands_used"],
                "last_seen": v["last_seen"].isoformat()
            }
            for k, v in user_stats.items()
        }
        with open(DATA_FILE, "w") as f:
            json.dump({
                "user_points":  {str(k): v for k, v in user_points.items()},
                "user_stats":   serializable_stats,
                "user_personas": {str(k): v for k, v in user_personas.items()},
                "daily_claimed": {str(k): v for k, v in daily_claimed.items()},
            }, f, indent=2)
    except Exception as e:
        print(f"⚠️  Could not save data.json: {e}")

# Load at startup
user_points, user_stats, user_personas, daily_claimed = load_data()

active_trivia = {}   # {guild_id: {answer, category}}
reminders     = []   # [{user_id, channel_id, message, time}]

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
    "leader": (
        "You are Rune, a confident and inspiring leader bot. Speak with authority and motivation! "
        "Encourage users to be their best selves. One sentence only. "
        "Always reply in the same language the user is writing in."
    )
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
    save_data()

def get_points(user_id: int) -> int:
    return user_points.get(user_id, 0)

def track_user_activity(user_id: int):
    if user_id not in user_stats:
        user_stats[user_id] = {"commands_used": 0, "last_seen": datetime.now()}
    user_stats[user_id]["commands_used"] += 1
    user_stats[user_id]["last_seen"] = datetime.now()
    save_data()

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

async def get_trivia_question():
    try:
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
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
    return None

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
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.boredapi.com/api/activity", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data.get("activity", "Try something new today!")
    except Exception:
        return "Try something new today!"

# ========== AI REPLY =================

def generate_reply(user_message: str, system_prompt: str) -> str:
    completion = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        temperature=1,
        max_completion_tokens=8192,
        top_p=1,
        reasoning_effort="medium",
        stream=True,
        stop=None
    )
    reply = ""
    for chunk in completion:
        reply += chunk.choices[0].delta.content or ""
    reply = clean_output(reply)
    if not reply:
        return "🤔 I'm not sure how to answer that."
    return reply

# ========== BOT FACTORY ===================

def create_bot():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(command_prefix=PREFIX, intents=intents)

    @bot.event
    async def on_ready():
        # ---------------------------------------------------------------
        # SLASH COMMAND SYNC STRATEGY
        # ---------------------------------------------------------------
        # During development set DEV_GUILD_ID in your .env to your server's
        # ID. Guild-scoped commands update INSTANTLY — no re-invite needed.
        # For production (DEV_GUILD_ID not set) we sync globally; Discord
        # can take up to 1 hour to propagate changes everywhere, but you
        # still NEVER need to re-invite the bot for new commands.
        # ---------------------------------------------------------------
        # Wipe stale guild-specific commands from every server, then global sync
        for g in bot.guilds:
            bot.tree.clear_commands(guild=g)
            await bot.tree.sync(guild=g)
        await bot.tree.sync()
        print("✅ Slash commands synced globally")

        check_reminders.start()
        print(f"✅ Bot online as {bot.user}")
        print(f"📊 Serving {len(bot.guilds)} servers")
        print(f"💾 Loaded {len(user_points)} user point records from disk")

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

        await message.channel.send(reply)

    # ========== SLASH COMMANDS =================

    @bot.tree.command(name="joke", description="Get a random joke 😂")
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
            self.answered   = False          # True once someone is correct
            self.wrong_ids  = set()          # users who already guessed wrong
            self.message    = None           # set after send so we can edit on timeout

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
                        item.disabled = True
                        if item.label.split(". ", 1)[-1] == answer[:80]:
                            item.style = discord.ButtonStyle.success
                        else:
                            item.style = discord.ButtonStyle.secondary
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
                item.disabled = True
                if item.label.split(". ", 1)[-1] == self.correct[:80]:
                    item.style = discord.ButtonStyle.success
                else:
                    item.style = discord.ButtonStyle.secondary
            if self.message:
                embed = self.message.embeds[0]
                embed.color = discord.Color.red()
                embed.set_footer(text=f"⏰ Time's up! The answer was: {self.correct}")
                try:
                    await self.message.edit(embed=embed, view=self)
                    await self.message.channel.send(
                        f"⏰ **Nobody got it!** The correct answer was: **{self.correct}**"
                    )
                except Exception:
                    pass

    @bot.tree.command(name="trivia", description="Start a trivia question! 🧠")
    async def trivia(interaction: discord.Interaction):
        await interaction.response.defer()
        if interaction.guild.id in active_trivia:
            await interaction.followup.send("❌ A trivia question is already active! Finish it first.")
            return
        question_data = await get_trivia_question()
        if not question_data:
            await interaction.followup.send("⚠️ Couldn't fetch a trivia question. Try again!")
            return

        correct  = question_data["correct_answer"]
        answers  = question_data["all_answers"][:]
        random.shuffle(answers)

        active_trivia[interaction.guild.id] = {"answer": correct, "category": question_data["category"]}

        diff_colors = {"easy": discord.Color.green(), "medium": discord.Color.orange(), "hard": discord.Color.red()}
        color = diff_colors.get(question_data["difficulty"], discord.Color.blue())

        embed = discord.Embed(
            title="🧠 Trivia Time!",
            description=f"**{question_data['question']}**",
            color=color
        )
        embed.add_field(name="📚 Category",    value=question_data["category"],               inline=True)
        embed.add_field(name="⚡ Difficulty",  value=question_data["difficulty"].capitalize(), inline=True)
        embed.add_field(name="⏳ Time Limit",  value="5 minutes",                             inline=True)
        embed.set_footer(text="Press a button to answer! Wrong answers lock you out.")

        view = TriviaView(interaction.guild.id, correct, answers, question_data)
        msg  = await interaction.followup.send(embed=embed, view=view)
        view.message = msg
        track_user_activity(interaction.user.id)

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
        add_points(uid, bonus)  # also calls save_data()
        save_data()
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
        def __init__(self, options: list[str], creator_id: int):
            super().__init__(timeout=86400)  # polls live for 24h max
            self.options     = options
            self.creator_id  = creator_id
            self.votes: dict[int, int] = {}   # user_id -> option index
            self.counts = [0] * len(options)
            self.closed  = False
            self.message = None

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
                item.disabled = True
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
            embed = discord.Embed(
                title=self.message.embeds[0].title if self.message else "📊 Poll",
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
                    await self.message.edit(embed=embed, view=self)
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
    async def catfact(interaction: discord.Interaction):
        await interaction.response.defer()
        fact = await get_cat_fact()
        embed = discord.Embed(title="🐱 Cat Fact", description=fact, color=discord.Color.orange())
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="dog", description="Get a random dog picture 🐕")
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
        app_commands.Choice(name="Leader 🏆", value="leader")
    ])
    async def persona(interaction: discord.Interaction, style: str):
        user_personas[interaction.user.id] = style
        save_data()
        descriptions = {
            "default": "Back to normal — helpful and friendly! 😊",
            "sarcastic": "Oh great, you picked sarcastic. Wonderful choice. 🙄",
            "pirate": "Arrr! I'll be speakin' like a pirate now, matey! ☠️",
            "shakespeare": "Henceforth, I shall speaketh in the tongue of the Bard! 📜",
            "robot": "ACKNOWLEDGED. SWITCHING TO ROBOT MODE. BEEP BOOP. 🤖",
            "cheerful": "YAY! I'm SO excited to be super cheerful for you! 🎉✨",
            "leader": "I shall conquer the world!"
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
        if user.top_role >= interaction.guild.me.top_role:
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
        if user.top_role >= interaction.guild.me.top_role:
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
        if user.top_role >= interaction.guild.me.top_role:
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
            ("🛡️ **Moderation**", "`/kick`, `/ban`, `/mute`, `/unmute` *(requires permissions)*"),
            ("⏰ **Utility**", "`/remind`, `/stats`, `/serverinfo`, `/help`"),
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
                    if channel:
                        await channel.send(f"⏰ {user.mention} Reminder: **{reminder['message']}**")
                    reminders.remove(reminder)
                except Exception as e:
                    print(f"Error sending reminder: {e}")
                    reminders.remove(reminder)

    return bot

# ========== AUTO-RESTART LOOP ==============

def run_forever():
    while True:
        try:
            bot = create_bot()
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
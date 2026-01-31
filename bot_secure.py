import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput
from gpt4all import GPT4All
import asyncio
import time
import traceback
import random
import requests
from datetime import datetime, timedelta
import aiohttp
from typing import Optional
import json
import html

# Import configuration from secure config file
try:
    from config import (
        DISCORD_TOKEN, PREFIX, MAX_TOKENS, RESTART_DELAY,
        MODEL_PATH, MODEL_NAME, DEBUG_MODE, BOT_STATUS,
        COMMAND_COOLDOWN, TRIVIA_TIMEOUT
    )
except ImportError:
    print("❌ ERROR: config.py not found or .env file missing!")
    print("Please create a .env file with your Discord token.")
    print("See .env.example for template.")
    exit(1)

# =========================================

# 🔒 GLOBAL MODEL LOCK
model_lock = asyncio.Lock()

# ============== STORAGE ==================

user_points = {}  # {user_id: points}
active_trivia = {}  # {guild_id: {question, answer, answers, participants, message_id, created_at}}
user_stats = {}  # {user_id: {commands_used, last_seen, trivia_correct, trivia_wrong}}
reminders = []  # [{user_id, channel_id, message, time}]
daily_claims = {}  # {user_id: last_claim_date}
achievements = {}  # {user_id: [achievement_ids]}
user_streaks = {}  # {user_id: {streak: int, last_answer_date: date}}

# ============== ACHIEVEMENTS ================

ACHIEVEMENTS_LIST = {
    "first_blood": {"name": "🎯 First Blood", "desc": "Answer your first trivia question correctly", "points": 5},
    "trivia_master": {"name": "🧠 Trivia Master", "desc": "Answer 10 trivia questions correctly", "points": 50},
    "quick_draw": {"name": "⚡ Quick Draw", "desc": "Answer a trivia question in under 5 seconds", "points": 25},
    "perfectionist": {"name": "💯 Perfectionist", "desc": "Get 5 correct answers in a row", "points": 100},
    "early_bird": {"name": "🌅 Early Bird", "desc": "Claim daily reward 7 days in a row", "points": 75},
    "comedian": {"name": "😂 Comedian", "desc": "Use /joke 50 times", "points": 20},
    "social_butterfly": {"name": "🦋 Social Butterfly", "desc": "Use 100 commands total", "points": 50},
    "dedicated": {"name": "🏆 Dedicated", "desc": "Reach 500 points", "points": 100},
}

# ============== PROMPT ===================

SYSTEM_PROMPT = (
    "You are Rune, a helpful Discord bot.\n"
    "Reply with ONE short, friendly message.\n"
    "Do NOT create dialogue or invent user messages.\n"
    "Stop immediately after your reply.\n"
    "Reply in the same language as the user.\n"
    "Be helpful, witty, and engaging.\n"
)

BAD_WORDS = [
    "fuck", "shit", "idiot", "bitch",
    "hurensohn", "arschloch"
]

INAPPROPRIATE_PHRASES = [
    "öl mich ein", "leck mich", "mach mich",
    "sex", "nackt", "fetisch"
]

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
    check_point_achievements(user_id)

def get_points(user_id: int) -> int:
    return user_points.get(user_id, 0)

def track_user_activity(user_id: int):
    if user_id not in user_stats:
        user_stats[user_id] = {
            "commands_used": 0, 
            "last_seen": datetime.now(),
            "trivia_correct": 0,
            "trivia_wrong": 0
        }
    user_stats[user_id]["commands_used"] += 1
    user_stats[user_id]["last_seen"] = datetime.now()
    check_command_achievements(user_id)

def add_achievement(user_id: int, achievement_id: str):
    if user_id not in achievements:
        achievements[user_id] = []
    if achievement_id not in achievements[user_id]:
        achievements[user_id].append(achievement_id)
        return True
    return False

def check_point_achievements(user_id: int):
    points = get_points(user_id)
    if points >= 500:
        add_achievement(user_id, "dedicated")

def check_command_achievements(user_id: int):
    commands = user_stats[user_id]["commands_used"]
    if commands >= 100:
        add_achievement(user_id, "social_butterfly")

def can_claim_daily(user_id: int) -> bool:
    today = datetime.now().date()
    if user_id not in daily_claims:
        return True
    last_claim = daily_claims[user_id]
    return last_claim < today

def claim_daily(user_id: int) -> int:
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    
    # Check streak
    if user_id in daily_claims and daily_claims[user_id] == yesterday:
        # Continue streak
        if user_id not in user_streaks:
            user_streaks[user_id] = {"streak": 1, "last_claim": today}
        user_streaks[user_id]["streak"] += 1
        user_streaks[user_id]["last_claim"] = today
    else:
        # Start new streak
        user_streaks[user_id] = {"streak": 1, "last_claim": today}
    
    daily_claims[user_id] = today
    
    # Check for achievement
    if user_streaks[user_id]["streak"] >= 7:
        add_achievement(user_id, "early_bird")
    
    # Base reward + streak bonus
    base_reward = 50
    streak_bonus = min(user_streaks[user_id]["streak"] * 5, 100)
    return base_reward + streak_bonus

# ========== API FUNCTIONS =================

async def get_joke_async(language="en"):
    """Fetch joke from JokeAPI"""
    try:
        if language == "de":
            url = "https://v2.jokeapi.dev/joke/Misc,Pun?lang=de&blacklistFlags=explicit"
        else:
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
    """Fetch trivia from Open Trivia Database API"""
    try:
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if data["response_code"] == 0:
                    q = data["results"][0]
                    return {
                        "question": html.unescape(q["question"]),
                        "correct_answer": html.unescape(q["correct_answer"]),
                        "all_answers": [html.unescape(ans) for ans in q["incorrect_answers"]] + [html.unescape(q["correct_answer"])],
                        "category": html.unescape(q["category"]),
                        "difficulty": q["difficulty"]
                    }
    except Exception:
        pass
    return None

async def get_cat_fact():
    """Fetch random cat fact"""
    try:
        url = "https://catfact.ninja/fact"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data.get("fact", "Cats are amazing! 🐱")
    except Exception:
        return "Cats are amazing! 🐱"

async def get_dog_image():
    """Fetch random dog image"""
    try:
        url = "https://dog.ceo/api/breeds/image/random"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data.get("message")
    except Exception:
        return None

async def get_advice():
    """Fetch random advice"""
    try:
        url = "https://api.adviceslip.com/advice"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data["slip"]["advice"]
    except Exception:
        return "Be kind to yourself and others. 💙"

async def get_quote():
    """Fetch inspirational quote"""
    try:
        url = "https://zenquotes.io/api/random"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                quote = data[0]["q"]
                author = data[0]["a"]
                return f'"{quote}" — {author}'
    except Exception:
        return '"Believe you can and you\'re halfway there." — Theodore Roosevelt'

async def get_meme():
    """Fetch random meme from Reddit"""
    try:
        url = "https://meme-api.com/gimme"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return {
                    "title": data.get("title"),
                    "url": data.get("url"),
                    "author": data.get("author")
                }
    except Exception:
        return None

async def get_activity_suggestion():
    """Fetch random activity suggestion"""
    try:
        url = "https://www.boredapi.com/api/activity"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data.get("activity", "Try something new today!")
    except Exception:
        return "Try something new today!"

async def get_riddle():
    """Fetch random riddle"""
    try:
        url = "https://riddles-api.vercel.app/random"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return {
                    "riddle": data.get("riddle"),
                    "answer": data.get("answer")
                }
    except Exception:
        return None

# ========== UI COMPONENTS =================

class TriviaAnswerModal(Modal, title="Answer Trivia Question"):
    answer = TextInput(
        label="Your Answer",
        placeholder="Type your answer here...",
        required=True,
        max_length=200
    )
    
    def __init__(self, guild_id: int, question_start_time: float):
        super().__init__()
        self.guild_id = guild_id
        self.question_start_time = question_start_time
    
    async def on_submit(self, interaction: discord.Interaction):
        user_answer = self.answer.value.strip()
        
        if self.guild_id not in active_trivia:
            await interaction.response.send_message(
                "⚠️ This trivia question has already been answered or expired!",
                ephemeral=True
            )
            return
        
        trivia = active_trivia[self.guild_id]
        
        # Check if user already participated
        if interaction.user.id in trivia["participants"]:
            await interaction.response.send_message(
                "❌ You've already submitted an answer for this question!",
                ephemeral=True
            )
            return
        
        # Add to participants
        trivia["participants"].add(interaction.user.id)
        
        # Check answer
        correct_answer = trivia["answer"]
        is_correct = user_answer.lower() == correct_answer.lower()
        
        # Calculate response time
        response_time = time.time() - self.question_start_time
        
        if is_correct:
            # Award points
            points_earned = 10
            add_points(interaction.user.id, points_earned)
            
            # Update stats
            if interaction.user.id not in user_stats:
                user_stats[interaction.user.id] = {
                    "commands_used": 0,
                    "last_seen": datetime.now(),
                    "trivia_correct": 0,
                    "trivia_wrong": 0
                }
            user_stats[interaction.user.id]["trivia_correct"] += 1
            
            # Check achievements
            if user_stats[interaction.user.id]["trivia_correct"] == 1:
                new_achievement = add_achievement(interaction.user.id, "first_blood")
                if new_achievement:
                    points_earned += ACHIEVEMENTS_LIST["first_blood"]["points"]
            
            if user_stats[interaction.user.id]["trivia_correct"] >= 10:
                new_achievement = add_achievement(interaction.user.id, "trivia_master")
                if new_achievement:
                    points_earned += ACHIEVEMENTS_LIST["trivia_master"]["points"]
            
            if response_time < 5:
                new_achievement = add_achievement(interaction.user.id, "quick_draw")
                if new_achievement:
                    points_earned += ACHIEVEMENTS_LIST["quick_draw"]["points"]
            
            await interaction.response.send_message(
                f"✅ **Correct!** 🎉\n"
                f"You earned **{points_earned} points**!\n"
                f"Response time: **{response_time:.2f}s**\n"
                f"Total points: **{get_points(interaction.user.id)}**",
                ephemeral=True
            )
            
            # Remove trivia and announce winner
            del active_trivia[self.guild_id]
            
            # Update original message
            try:
                channel = interaction.channel
                message = await channel.fetch_message(trivia["message_id"])
                embed = message.embeds[0]
                embed.color = discord.Color.green()
                embed.add_field(
                    name="🏆 Winner",
                    value=f"{interaction.user.mention} answered correctly in {response_time:.2f}s!",
                    inline=False
                )
                await message.edit(embed=embed, view=None)
            except:
                pass
            
        else:
            # Wrong answer
            user_stats[interaction.user.id]["trivia_wrong"] += 1
            
            await interaction.response.send_message(
                f"❌ **Incorrect!**\n"
                f"Your answer: `{user_answer}`\n"
                f"You can't answer this question again!",
                ephemeral=True
            )

class TriviaView(View):
    def __init__(self, guild_id: int, question_start_time: float):
        super().__init__(timeout=TRIVIA_TIMEOUT)
        self.guild_id = guild_id
        self.question_start_time = question_start_time
    
    @discord.ui.button(label="Answer Question", style=discord.ButtonStyle.primary, emoji="✍️")
    async def answer_button(self, interaction: discord.Interaction, button: Button):
        modal = TriviaAnswerModal(self.guild_id, self.question_start_time)
        await interaction.response.send_modal(modal)
    
    async def on_timeout(self):
        if self.guild_id in active_trivia:
            trivia = active_trivia[self.guild_id]
            try:
                channel = await self.message.channel.fetch()
                message = await channel.fetch_message(trivia["message_id"])
                embed = message.embeds[0]
                embed.color = discord.Color.red()
                embed.add_field(
                    name="⏰ Time's Up!",
                    value=f"The correct answer was: **{trivia['answer']}**",
                    inline=False
                )
                await message.edit(embed=embed, view=None)
            except:
                pass
            del active_trivia[self.guild_id]

class RiddleView(View):
    def __init__(self, answer: str):
        super().__init__(timeout=120)
        self.answer = answer
        self.revealed = False
    
    @discord.ui.button(label="Reveal Answer", style=discord.ButtonStyle.secondary, emoji="🔍")
    async def reveal_button(self, interaction: discord.Interaction, button: Button):
        if not self.revealed:
            self.revealed = True
            button.disabled = True
            await interaction.response.edit_message(
                content=f"{interaction.message.content}\n\n**Answer:** ||{self.answer}||",
                view=self
            )
        else:
            await interaction.response.send_message("Answer already revealed!", ephemeral=True)

# ========== MODEL LOADING =================

print("Loading AI model...")
llm = GPT4All(
    model_name=MODEL_NAME,
    model_path=MODEL_PATH,
    allow_download=False
)
print("AI model loaded ✅")

def generate_reply(user_message: str) -> str:
    prompt = SYSTEM_PROMPT + "\nUser message:\n" + user_message + "\nAssistant:"
    raw = llm.generate(prompt, max_tokens=MAX_TOKENS, temp=0.7)
    reply = clean_output(raw)
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
        await bot.tree.sync()
        check_reminders.start()
        
        # Set bot status
        await bot.change_presence(
            activity=discord.Game(name=BOT_STATUS)
        )
        
        print(f"✅ Bot online as {bot.user}")
        print(f"📊 Serving {len(bot.guilds)} servers")
        if DEBUG_MODE:
            print(f"🔧 Debug mode: ON")

    @bot.event
    async def on_message(message):
        if message.author.bot:
            return

        # React to certain commands
        triggers = ['.joke', '.roast', '.trivia', '.meme']
        if any(word in message.content.lower() for word in triggers):
            await message.add_reaction('😎')

        if not message.content.startswith(PREFIX):
            return

        user_input = message.content[len(PREFIX):].strip()
        if not user_input:
            return

        track_user_activity(message.author.id)

        # Toxicity filter
        if is_toxic(user_input):
            await message.channel.send("Hey 🙂 let's keep it respectful.")
            return
        
        # Inappropriate content handler
        if is_inappropriate(user_input):
            joke = await get_joke_async()
            await message.channel.send(f"Let's keep it clean! Here's a joke instead:\n{joke}")
            return
        
        # AI response
        async with message.channel.typing():
            async with model_lock:
                try:
                    reply = await asyncio.to_thread(generate_reply, user_input)
                except Exception:
                    traceback.print_exc()
                    reply = "⚠️ AI crashed. Please try again."

        await message.channel.send(reply)

    # ========== SLASH COMMANDS =================

    @bot.tree.command(name="daily", description="Claim your daily reward! 🎁")
    async def daily(interaction: discord.Interaction):
        if not can_claim_daily(interaction.user.id):
            next_claim = datetime.now().date() + timedelta(days=1)
            await interaction.response.send_message(
                f"❌ You've already claimed your daily reward today!\n"
                f"Come back tomorrow ({next_claim})!",
                ephemeral=True
            )
            return
        
        reward = claim_daily(interaction.user.id)
        add_points(interaction.user.id, reward)
        streak = user_streaks.get(interaction.user.id, {}).get("streak", 1)
        
        embed = discord.Embed(
            title="🎁 Daily Reward Claimed!",
            description=f"You received **{reward} points**!",
            color=discord.Color.gold()
        )
        embed.add_field(name="Current Streak", value=f"🔥 {streak} day(s)", inline=True)
        embed.add_field(name="Total Points", value=f"⭐ {get_points(interaction.user.id)}", inline=True)
        embed.set_footer(text="Come back tomorrow for more rewards!")
        
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="achievements", description="View your achievements 🏆")
    async def view_achievements(interaction: discord.Interaction):
        user_achievements = achievements.get(interaction.user.id, [])
        
        embed = discord.Embed(
            title="🏆 Your Achievements",
            color=discord.Color.purple()
        )
        
        if not user_achievements:
            embed.description = "You haven't unlocked any achievements yet!\nKeep playing to unlock them! 🎯"
        else:
            for ach_id in user_achievements:
                ach = ACHIEVEMENTS_LIST.get(ach_id)
                if ach:
                    embed.add_field(
                        name=ach["name"],
                        value=f"{ach['desc']}\n+{ach['points']} points",
                        inline=False
                    )
        
        total_achievements = len(ACHIEVEMENTS_LIST)
        unlocked = len(user_achievements)
        embed.set_footer(text=f"Unlocked: {unlocked}/{total_achievements} achievements")
        
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="profile", description="View your complete profile 👤")
    async def profile(interaction: discord.Interaction, user: Optional[discord.User] = None):
        target = user or interaction.user
        
        embed = discord.Embed(
            title=f"👤 {target.name}'s Profile",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        
        # Points
        pts = get_points(target.id)
        embed.add_field(name="⭐ Points", value=str(pts), inline=True)
        
        # Rank
        sorted_users = sorted(user_points.items(), key=lambda x: x[1], reverse=True)
        rank = next((i+1 for i, (uid, _) in enumerate(sorted_users) if uid == target.id), "Unranked")
        embed.add_field(name="🏆 Rank", value=f"#{rank}", inline=True)
        
        # Streak
        streak = user_streaks.get(target.id, {}).get("streak", 0)
        embed.add_field(name="🔥 Daily Streak", value=f"{streak} day(s)", inline=True)
        
        # Stats
        if target.id in user_stats:
            stats = user_stats[target.id]
            embed.add_field(name="📊 Commands Used", value=str(stats["commands_used"]), inline=True)
            embed.add_field(name="✅ Trivia Correct", value=str(stats.get("trivia_correct", 0)), inline=True)
            embed.add_field(name="❌ Trivia Wrong", value=str(stats.get("trivia_wrong", 0)), inline=True)
            
            if stats.get("trivia_correct", 0) + stats.get("trivia_wrong", 0) > 0:
                accuracy = (stats.get("trivia_correct", 0) / (stats.get("trivia_correct", 0) + stats.get("trivia_wrong", 0))) * 100
                embed.add_field(name="🎯 Accuracy", value=f"{accuracy:.1f}%", inline=True)
        
        # Achievements
        user_ach = achievements.get(target.id, [])
        embed.add_field(name="🏆 Achievements", value=f"{len(user_ach)}/{len(ACHIEVEMENTS_LIST)}", inline=True)
        
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="joke", description="Get a random joke")
    async def joke(interaction: discord.Interaction):
        await interaction.response.defer()
        joke_text = await get_joke_async("en")
        await interaction.followup.send(f"😂 {joke_text}")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="joke_de", description="Bekomme einen deutschen Witz")
    async def joke_de(interaction: discord.Interaction):
        await interaction.response.defer()
        joke_text = await get_joke_async("de")
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

    @bot.tree.command(name="trivia", description="Start a trivia question with button answers! 🧠")
    async def trivia(interaction: discord.Interaction):
        await interaction.response.defer()
        
        if interaction.guild.id in active_trivia:
            await interaction.followup.send("❌ A trivia question is already active! Answer it first.")
            return
        
        question_data = await get_trivia_question()
        if not question_data:
            await interaction.followup.send("⚠️ Couldn't fetch a trivia question. Try again!")
            return
        
        # Shuffle answers
        answers = question_data["all_answers"]
        random.shuffle(answers)
        
        # Create embed
        embed = discord.Embed(
            title="🧠 Trivia Time!",
            description=question_data["question"],
            color=discord.Color.blue()
        )
        embed.add_field(name="Category", value=question_data["category"], inline=True)
        embed.add_field(name="Difficulty", value=question_data["difficulty"].capitalize(), inline=True)
        embed.add_field(name="Options", value="\n".join([f"• {ans}" for ans in answers]), inline=False)
        embed.set_footer(text="Click the button below to submit your answer privately!")
        
        # Create view with button
        question_start_time = time.time()
        view = TriviaView(interaction.guild.id, question_start_time)
        
        message = await interaction.followup.send(embed=embed, view=view)
        
        # Store trivia data
        active_trivia[interaction.guild.id] = {
            "answer": question_data["correct_answer"],
            "answers": answers,
            "participants": set(),
            "message_id": message.id,
            "created_at": datetime.now()
        }
        
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="riddle", description="Get a riddle to solve! 🤔")
    async def riddle(interaction: discord.Interaction):
        await interaction.response.defer()
        riddle_data = await get_riddle()
        
        if not riddle_data:
            await interaction.followup.send("⚠️ Couldn't fetch a riddle. Try again!")
            return
        
        view = RiddleView(riddle_data["answer"])
        
        embed = discord.Embed(
            title="🤔 Riddle Time!",
            description=riddle_data["riddle"],
            color=discord.Color.purple()
        )
        embed.set_footer(text="Click the button below to reveal the answer!")
        
        await interaction.followup.send(embed=embed, view=view)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="points", description="Check your points or someone else's 🏆")
    @app_commands.describe(user="User to check points for (optional)")
    async def points(interaction: discord.Interaction, user: Optional[discord.User] = None):
        target = user or interaction.user
        pts = get_points(target.id)
        
        embed = discord.Embed(
            title="🏆 Points",
            description=f"{target.mention} has **{pts}** points!",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="leaderboard", description="View the top 10 users by points 📊")
    async def leaderboard(interaction: discord.Interaction):
        if not user_points:
            await interaction.response.send_message("No one has points yet! Play trivia to earn some!")
            return
        
        sorted_users = sorted(user_points.items(), key=lambda x: x[1], reverse=True)[:10]
        
        embed = discord.Embed(
            title="🏆 Top 10 Leaderboard",
            color=discord.Color.gold()
        )
        
        medals = ["🥇", "🥈", "🥉"]
        for i, (user_id, pts) in enumerate(sorted_users):
            try:
                user = await bot.fetch_user(user_id)
                medal = medals[i] if i < 3 else f"#{i+1}"
                embed.add_field(
                    name=f"{medal} {user.name}",
                    value=f"{pts} points",
                    inline=False
                )
            except:
                continue
        
        await interaction.response.send_message(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="catfact", description="Get a random cat fact 🐱")
    async def catfact(interaction: discord.Interaction):
        await interaction.response.defer()
        fact = await get_cat_fact()
        embed = discord.Embed(
            title="🐱 Cat Fact",
            description=fact,
            color=discord.Color.orange()
        )
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="dog", description="Get a random dog picture 🐕")
    async def dog(interaction: discord.Interaction):
        await interaction.response.defer()
        image_url = await get_dog_image()
        if image_url:
            embed = discord.Embed(
                title="🐕 Random Dog",
                color=discord.Color.blue()
            )
            embed.set_image(url=image_url)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("⚠️ Couldn't fetch a dog image right now!")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="advice", description="Get random life advice 💡")
    async def advice(interaction: discord.Interaction):
        await interaction.response.defer()
        advice_text = await get_advice()
        embed = discord.Embed(
            title="💡 Random Advice",
            description=advice_text,
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="quote", description="Get an inspirational quote ✨")
    async def quote(interaction: discord.Interaction):
        await interaction.response.defer()
        quote_text = await get_quote()
        embed = discord.Embed(
            title="✨ Inspirational Quote",
            description=quote_text,
            color=discord.Color.purple()
        )
        await interaction.followup.send(embed=embed)
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="meme", description="Get a random meme from Reddit 😂")
    async def meme(interaction: discord.Interaction):
        await interaction.response.defer()
        meme_data = await get_meme()
        if meme_data:
            embed = discord.Embed(
                title=meme_data["title"],
                color=discord.Color.red()
            )
            embed.set_image(url=meme_data["url"])
            embed.set_footer(text=f"Posted by u/{meme_data['author']}")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("⚠️ Couldn't fetch a meme right now!")
        track_user_activity(interaction.user.id)

    @bot.tree.command(name="activity", description="Get a random activity suggestion when you're bored 🎯")
    async def activity(interaction: discord.Interaction):
        await interaction.response.defer()
        activity_text = await get_activity_suggestion()
        embed = discord.Embed(
            title="🎯 Activity Suggestion",
            description=activity_text,
            color=discord.Color.teal()
        )
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
    @app_commands.describe(
        minutes="Minutes from now",
        message="What to remind you about"
    )
    async def remind(interaction: discord.Interaction, minutes: int, message: str):
        if minutes < 1 or minutes > 1440:
            await interaction.response.send_message("❌ Please set a reminder between 1 and 1440 minutes (24 hours)!")
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
        
        stats = user_stats[interaction.user.id]
        embed = discord.Embed(
            title="📊 Your Statistics",
            color=discord.Color.blue()
        )
        embed.add_field(name="Commands Used", value=stats["commands_used"], inline=True)
        embed.add_field(name="Points", value=get_points(interaction.user.id), inline=True)
        embed.add_field(name="Trivia Correct", value=stats.get("trivia_correct", 0), inline=True)
        embed.add_field(name="Trivia Wrong", value=stats.get("trivia_wrong", 0), inline=True)
        embed.add_field(
            name="Last Seen",
            value=stats["last_seen"].strftime("%Y-%m-%d %H:%M:%S"),
            inline=False
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="help", description="View all available commands 📖")
    async def help_command(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 Rune Bot Commands",
            description="Here are all the commands you can use!",
            color=discord.Color.blue()
        )
        
        commands_list = [
            ("🎮 **Fun**", "`/joke`, `/joke_de`, `/roast`, `/compliment`, `/8ball`, `/flip`, `/roll`, `/meme`, `/riddle`"),
            ("🧠 **Trivia & Games**", "`/trivia`, `/points`, `/leaderboard`, `/profile`"),
            ("🎁 **Rewards**", "`/daily`, `/achievements`"),
            ("🐾 **Animals**", "`/catfact`, `/dog`"),
            ("💡 **Inspiration**", "`/advice`, `/quote`, `/activity`"),
            ("⏰ **Utility**", "`/remind`, `/stats`, `/help`"),
            ("💬 **AI Chat**", f"Use `{PREFIX}` prefix to chat with AI (e.g., `{PREFIX}hello`)")
        ]
        
        for category, cmds in commands_list:
            embed.add_field(name=category, value=cmds, inline=False)
        
        embed.set_footer(text="Have fun! 🎉")
        await interaction.response.send_message(embed=embed)

    # ========== BACKGROUND TASKS =================

    @tasks.loop(seconds=30)
    async def check_reminders():
        """Check and send due reminders"""
        now = datetime.now()
        for reminder in reminders[:]:
            if now >= reminder["time"]:
                try:
                    channel = bot.get_channel(reminder["channel_id"])
                    user = await bot.fetch_user(reminder["user_id"])
                    if channel:
                        await channel.send(
                            f"⏰ {user.mention} Reminder: **{reminder['message']}**"
                        )
                    reminders.remove(reminder)
                except Exception as e:
                    if DEBUG_MODE:
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
    print("🚀 Starting Rune Bot Ultimate Edition...")
    print(f"⚙️  Prefix: {PREFIX}")
    print(f"🤖 Model: {MODEL_NAME}")
    run_forever()
# 🤖 Rune Discord Bot - Enhanced Edition

A feature-rich Discord bot powered by local AI (GPT4All) with trivia games, memes, jokes, and much more!

## ✨ New Features

### 🎮 Fun & Entertainment
- **`/joke`** - Get random programming, pun, or misc jokes
- **`/joke_de`** - Get jokes in German
- **`/roast [user]`** - Roast someone with a savage burn 🔥
- **`/compliment [user]`** - Give wholesome compliments 💙
- **`/8ball <question>`** - Ask the magic 8-ball
- **`/flip`** - Flip a coin
- **`/roll [sides]`** - Roll a dice (default 6 sides)
- **`/meme`** - Get random memes from Reddit

### 🧠 Trivia & Points System
- **`/trivia`** - Start a trivia question (first to answer wins 10 points!)
- **`/points [user]`** - Check your or someone's points
- **`/leaderboard`** - View top 10 users by points
- Persistent point tracking across sessions
- Multiple choice questions from Open Trivia Database

### 🐾 Animals & Nature
- **`/catfact`** - Random cat facts
- **`/dog`** - Random dog pictures

### 💡 Inspiration & Advice
- **`/advice`** - Get random life advice
- **`/quote`** - Inspirational quotes
- **`/activity`** - Activity suggestions when bored

### ⏰ Utility
- **`/remind <minutes> <message>`** - Set reminders (1-1440 mins)
- **`/stats`** - View your usage statistics
- **`/help`** - Complete command list

### 💬 AI Chat
- Use `.` prefix to chat with the AI (e.g., `.hello`, `.tell me a story`)
- Intelligent, context-aware responses
- Multi-language support
- Toxicity and inappropriate content filtering

## 🔧 Setup Instructions

### 1. Install Dependencies

```bash
pip install discord.py gpt4all aiohttp requests
```

### 2. Configure the Bot

Edit the configuration section in `discord_bot_enhanced.py`:

```python
DISCORD_TOKEN = "YOUR_DISCORD_BOT_TOKEN_HERE"
MODEL_PATH = r"C:\Users\YourUsername\AppData\Local\nomic.ai\GPT4All"
MODEL_NAME = "Llama-3.2-3B-Instruct-Q4_0.gguf"
```

### 3. Required Bot Permissions

Your bot needs these Discord permissions:
- Read Messages/View Channels
- Send Messages
- Embed Links
- Add Reactions
- Use Slash Commands

Enable these intents in Discord Developer Portal:
- Message Content Intent
- Server Members Intent

### 4. Run the Bot

```bash
python discord_bot_enhanced.py
```

## 🌐 APIs Used

| Feature | API | Documentation |
|---------|-----|---------------|
| Jokes | JokeAPI | https://v2.jokeapi.dev/ |
| Trivia | Open Trivia DB | https://opentdb.com/ |
| Cat Facts | Cat Facts API | https://catfact.ninja/ |
| Dog Images | Dog CEO API | https://dog.ceo/dog-api/ |
| Advice | Advice Slip | https://api.adviceslip.com/ |
| Quotes | ZenQuotes | https://zenquotes.io/ |
| Memes | Meme API | https://github.com/D3vd/Meme_Api |
| Activities | Bored API | https://www.boredapi.com/ |

All APIs are **free** and require **no API keys**!

## 📊 Features Comparison

### Before vs After

| Feature | Original | Enhanced |
|---------|----------|----------|
| Commands | 3 | 20+ |
| APIs | 1 | 8 |
| Point System | ❌ | ✅ |
| Leaderboard | ❌ | ✅ |
| Trivia | Basic | Advanced (Open Trivia DB) |
| Reminders | ❌ | ✅ |
| Stats Tracking | ❌ | ✅ |
| Embeds | ❌ | ✅ |
| Error Handling | Basic | Advanced |
| Async Operations | Partial | Full |

## 🎯 Bot Behavior

### Content Filtering
- **Toxicity Filter**: Blocks offensive language
- **Inappropriate Content**: Redirects to jokes
- **Safe for All Ages**: Family-friendly responses

### AI Features
- Local AI model (GPT4All) - no API costs!
- Smart response generation
- Multi-language support (EN/DE)
- Context-aware conversations
- Model lock prevents concurrent access issues

### User Engagement
- Automatic reactions to commands
- Points and achievements
- Activity tracking
- Personalized statistics
- Reminder system

## 🏆 Point System

Users earn points by:
- Answering trivia correctly: **+10 points**
- Future: More point-earning activities coming!

View points with `/points` or compete on `/leaderboard`

## 🔐 Security Notes

1. **NEVER share your Discord bot token**
2. Keep `DISCORD_TOKEN` secret
3. Add the bot file to `.gitignore`
4. Use environment variables for production

Example `.env` usage:
```python
import os
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
```

## 🐛 Troubleshooting

### Bot doesn't respond to slash commands
- Make sure you've synced commands (happens automatically on startup)
- Check bot has proper permissions
- Verify Message Content intent is enabled

### AI responses are slow
- This is normal with local models
- The bot uses a lock to prevent crashes
- Consider using a smaller model for faster responses

### Trivia questions don't work
- Check your internet connection (API dependent)
- The Open Trivia DB might be temporarily down
- Bot will show error message if API fails

### Reminders don't send
- Make sure bot stays online
- Reminders are stored in memory (lost on restart)
- Maximum reminder time: 24 hours

## 📈 Future Enhancements

Potential additions:
- [ ] Music playback
- [ ] Custom prefix per server
- [ ] Database for persistent storage
- [ ] More mini-games
- [ ] Economy system
- [ ] Moderation commands
- [ ] Custom user profiles
- [ ] Server-specific settings

## 🤝 Contributing

Feel free to:
- Report bugs
- Suggest features
- Submit pull requests
- Improve documentation

## 📝 License

This bot is for personal/educational use. Respect API rate limits and terms of service.

## ❓ Support

For issues or questions:
1. Check this README
2. Review error messages in console
3. Verify all dependencies are installed
4. Ensure bot has proper Discord permissions

## 🎉 Credits

- **GPT4All** for local AI
- **Discord.py** for bot framework
- Various free APIs for content
- Original bot creator

---

**Made with ❤️ for the Discord community**

Enjoy your enhanced Rune bot! 🚀
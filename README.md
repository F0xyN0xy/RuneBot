# 🤖 Rune Discord Bot

A feature-rich Discord bot powered by OmniRoute AI with automatic provider fallback, extensive gamification, moderation tools, 24/7 voice support, and push-to-talk voice interaction.

## ✨ Features Overview

### 🎮 Fun & Entertainment
- **`/joke`** - Random programming, pun, or misc jokes from JokeAPI
- **`/roast [user]`** - Roast someone with a savage burn 🔥
- **`/compliment [user]`** - Give wholesome compliments 💙
- **`/8ball <question>`** - Ask the magic 8-ball
- **`/flip`** - Flip a coin
- **`/roll [sides]`** - Roll a dice (default 6 sides)
- **`/meme`** - Get random memes from Reddit

### 🧠 Trivia & Points System
- **`/trivia`** - Interactive trivia with multiple choice buttons
- **`/points [user]`** - Check your or someone's points
- **`/leaderboard`** - View top 10 users by points
- **`/daily`** - Claim daily bonus points (15-50 points)
- **`/give <user> <amount>`** - Transfer points to another user
- **`/duel <user> <wager>`** - Challenge someone to a coin flip duel
- **`/resettrivia`** - Force-reset stuck trivia (Mod only)
- Points are earned through trivia (+10), daily bonuses, voting (+50), and duels

### 📊 Polls
- **`/poll <question> <option1> <option2> [option3] [option4]`** - Create interactive polls with live vote tracking
- Real-time vote counts with visual progress bars
- Multiple choice support (2-4 options)
- Vote changing and removal allowed
- Only poll creator can close polls

### 🐾 Animals & Nature
- **`/catfact`** - Random cat facts
- **`/dog`** - Random dog pictures

### 💡 Inspiration & Advice
- **`/advice`** - Get random life advice
- **`/quote`** - Inspirational quotes
- **`/activity`** - Activity suggestions when bored

### 🎭 AI Personas
- **`/persona <style>`** - Change Rune's AI personality
  - Default (helpful & friendly)
  - Sarcastic
  - Pirate ☠️
  - Shakespeare 📜
  - Robot 🤖
  - Cheerful 🎉
- Personal to each user - doesn't affect others

### 💬 AI Chat
- Use `.` prefix to chat with AI (e.g., `.hello`, `.tell me a story`)
- Powered by **OmniRoute** with automatic fallback (GPT, Groq, Gemini, Claude)
- Primary: OmniRoute models
- Fallback: Groq Compound (mixture of agents)
- Multi-language support
- Context-aware responses
- Toxicity and inappropriate content filtering

### 🔊 24/7 Voice Support
- **`/247 [channel]`** - Make Rune join a voice channel 24/7 (Mod only)
- **`/leave247`** - Disconnect from 24/7 voice (Mod only)
- **`/vcstatus`** - Check uptime and voice status
- Auto-reconnect on disconnect
- Stays even when channel is empty

### 🎙️ Push-to-Talk Voice Interaction
- **`/ptt [seconds]`** - Record voice, get AI response with TTS
- **`/stopttt`** - Stop recording early
- Whisper-powered speech-to-text
- Zonos TTS for voice responses
- Records 3-15 seconds of audio
- Requires `discord-ext-voice-recv` (currently in development)

### 🛡️ Moderation Commands
- **`/kick <user> [reason]`** - Kick a member
- **`/ban <user> [reason] [delete_days]`** - Ban a member
- **`/mute <user> [minutes] [reason]`** - Timeout a member (max 28 days)
- **`/unmute <user>`** - Remove timeout
- All require appropriate permissions

### ⏰ Utility
- **`/remind <minutes> <message>`** - Set reminders (1-1440 mins)
- **`/stats`** - View your usage statistics
- **`/serverinfo`** - View server information
- **`/help`** - Complete command list

### 🗳️ Top.gg Voting
- **`/vote`** - Get voting link for Rune on Top.gg
- **`/checkvote`** - Claim +50 point reward after voting
- Vote every 12 hours for bonus points
- Automatic vote verification via Top.gg API

### 🧪 Testing & Debugging
- **`/testproviders [length] [model]`** - Test OmniRoute AI providers
- Test different message lengths (short/medium/long/max)
- Test specific models or all available ones
- View response times and success rates

### 📡 Gateway Event Logging
- Rich, colorful Discord event logging to a designated channel
- Tracks presence updates, voice state changes, messages, member joins/leaves, and more
- Beautifully formatted embeds with detailed information
- 30+ event types supported

## 🔧 Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Required packages:
- `discord.py` - Discord bot framework
- `aiohttp` - Async HTTP requests
- `python-dotenv` - Environment variable management
- `openai` - OpenAI-compatible API client (for OmniRoute)
- `groq` - Groq API client (fallback)

Optional:
- `discord-ext-voice-recv` - For push-to-talk audio capture (in development)

### 2. Install OmniRoute

OmniRoute provides automatic AI provider fallback:

```bash
npm install -g omniroute
omniroute
```

Or use a hosted OmniRoute instance.

### 3. Configure Environment Variables

Create a `.env` file in the project root:

```env
# Required
DISCORD_TOKEN=your_discord_bot_token_here

# OmniRoute Configuration
OMNIROUTE_API_KEY=your_omniroute_key
OMNIROUTE_BASE_URL=http://localhost:3000/v1
OMNIROUTE_MODEL=auto

# Groq Fallback
GROQ_API_KEY=your_groq_api_key

# Storage (JSONBin + Local Fallback)
JSONBIN_API_KEY=your_jsonbin_api_key
JSONBIN_BIN_ID=your_bin_id
LOCAL_STORAGE_PATH=bot_data.json

# Top.gg Integration (Optional)
TOPGG_TOKEN=your_topgg_token
TOPGG_BOT_ID=your_bot_id

# Voice Features (Optional)
ZONOS_API_KEY=your_zonos_api_key

# Gateway Logging (Optional)
GATEWAY_LOG_CHANNEL_ID=1234567890123456789

# Bot Settings
PREFIX=.
RESTART_DELAY=30
```

### 4. Required Bot Permissions

Your bot needs these Discord permissions:
- Read Messages/View Channels
- Send Messages
- Embed Links
- Add Reactions
- Use Slash Commands
- Manage Messages (for moderation)
- Kick Members (for moderation)
- Ban Members (for moderation)
- Moderate Members (for timeout/mute)
- Connect (for voice features)
- Speak (for TTS)

Enable these intents in Discord Developer Portal:
- Message Content Intent
- Server Members Intent
- Presence Intent (for gateway logging)

### 5. Run the Bot

```bash
python bot.py
```

The bot features auto-restart on crash with configurable delay.

## 🌐 APIs Used

| Feature | API | Documentation |
|---------|-----|---------------|
| AI Chat | OmniRoute | Auto-routes to best available provider |
| AI Fallback | Groq Compound | https://groq.com/ |
| Jokes | JokeAPI | https://v2.jokeapi.dev/ |
| Trivia | Open Trivia DB | https://opentdb.com/ |
| Cat Facts | Cat Facts API | https://catfact.ninja/ |
| Dog Images | Dog CEO API | https://dog.ceo/dog-api/ |
| Advice | Advice Slip | https://api.adviceslip.com/ |
| Quotes | ZenQuotes | https://zenquotes.io/ |
| Memes | Meme API | https://github.com/D3vd/Meme_Api |
| Voting | Top.gg API | https://top.gg/api/docs |
| Speech-to-Text | Whisper (via OmniRoute) | OpenAI-compatible endpoint |
| Text-to-Speech | Zonos | https://www.zyphra.com/ |

Most APIs are **free** and require **no API keys** (except OmniRoute, Groq, Top.gg, and Zonos for optional features).

## 💾 Data Storage

### Dual Storage System
- **Primary**: JSONBin.io (cloud storage with version control)
- **Fallback**: Local JSON file (`bot_data.json`)
- Auto-saves every 60 seconds when data changes
- Stores: user points, stats, personas, daily claims, vote records

### What's Stored
- User points and leaderboard data
- User statistics (commands used, last seen)
- AI persona preferences (per-user)
- Daily claim timestamps
- Vote claim timestamps
- Total bot message count

## 🎯 Bot Behavior

### Content Filtering
- **Toxicity Filter**: Blocks offensive language
- **Inappropriate Content Filter**: Redirects to jokes
- **Safe for All Ages**: Family-friendly responses

### AI Features
- **OmniRoute Integration**: Automatic provider fallback for maximum uptime
- **Groq Compound Fallback**: Uses mixture of agents for reliability
- **Smart Response Generation**: Context-aware, conversational
- **Multi-language Support**: Responds in user's language
- **Rate Limit Handling**: Graceful fallback on provider overload

### User Engagement
- Automatic reactions to fun commands (😎)
- Vote nudges every 25 bot messages
- Points and achievements system
- Activity tracking
- Personalized statistics
- Per-user AI personas

### Gateway Event Logging
- Optional rich event logging to designated channel
- Color-coded embeds for different event types
- Real-time presence, voice, message, and moderation tracking
- Detailed user information and context

## 🏆 Point System

### Earning Points
- Answer trivia correctly: **+10 points**
- Daily bonus claim: **+15-50 points** (random)
- Vote on Top.gg: **+50 points** (every 12 hours)
- Win duels: **+wager amount**
- Receive gifts from other users

### Using Points
- Transfer to other users with `/give`
- Wager in duels with `/duel`
- Compete on leaderboard
- More features coming soon!

## 🔐 Security & Privacy

1. **Never share your Discord bot token**
2. Keep `.env` file secure and in `.gitignore`
3. Store sensitive data in environment variables
4. JSONBin provides automatic encryption
5. Local fallback ensures data safety

Example `.gitignore`:
```
.env
bot_data.json
__pycache__/
*.pyc
```

## 🐛 Troubleshooting

### Bot doesn't respond to slash commands
- Slash commands sync automatically on startup
- Check bot has proper permissions
- Verify Message Content intent is enabled

### AI responses are slow or failing
- Check OmniRoute is running (`omniroute` command)
- Verify `OMNIROUTE_BASE_URL` is correct
- Groq fallback activates automatically on OmniRoute failure
- Check API keys are valid

### Voice features don't work
- Requires FFmpeg installed on system
- Push-to-talk requires `discord-ext-voice-recv` (in development)
- TTS requires `ZONOS_API_KEY` in `.env`
- Check bot has Connect and Speak permissions

### Data not saving
- Check JSONBin credentials in `.env`
- Local fallback saves to `bot_data.json` automatically
- Check file permissions in bot directory

### Trivia questions timeout
- Questions have 5-minute timeout
- Use `/resettrivia` to force-reset (Mod only)
- Check internet connection for API access

## 📈 Advanced Features

### Auto-Restart
- Bot automatically restarts on crash
- Configurable delay via `RESTART_DELAY` env var
- Preserves data through JSONBin and local storage

### 24/7 Voice Reliability
- Watchdog task checks connection every 30 seconds
- Auto-reconnects on disconnect
- Survives network interruptions

### Scalability
- Async/await throughout for high performance
- Efficient caching and data management
- Handles multiple servers simultaneously
- Gateway event logging with queuing system

## 📝 Recent Updates

### Latest Version (2026-08-30)
- ✅ Migrated to **OmniRoute** for AI with automatic provider fallback
- ✅ Added **Groq Compound** as secondary fallback
- ✅ Dual storage system (JSONBin + local fallback)
- ✅ Gateway event logging with rich embeds
- ✅ Push-to-talk voice interaction with TTS responses
- ✅ Provider testing command (`/testproviders`)
- ✅ Poll system with live vote tracking
- ✅ 24/7 voice support with auto-reconnect
- ✅ Top.gg voting integration
- ✅ Point gifting and duels
- ✅ Daily bonus system
- ✅ Per-user AI personas

## 🤝 Contributing

Contributions welcome! Areas for improvement:
- Additional API integrations
- More mini-games
- Enhanced moderation features
- Music playback
- Custom emoji reactions
- Leveling system

## 📄 License

This bot is for personal/educational use. Respect API rate limits and terms of service.

## ❓ Support

For issues:
1. Check this README
2. Review error messages in console
3. Verify all dependencies are installed
4. Ensure bot has proper Discord permissions
5. Check `.env` configuration

## 🎉 Credits

- **OmniRoute** - AI provider auto-routing
- **Groq** - Fast AI inference
- **Discord.py** - Bot framework
- Various free APIs for content
- **Zonos** - Text-to-speech
- **Whisper** - Speech-to-text

---

**Made with ❤️ for the Discord community**

Enjoy your Rune bot! 🚀

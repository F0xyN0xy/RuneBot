"""
Quick announcement sender — run once to push an embed to all servers.
Usage: python send_announcement.py
"""
import discord
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# ── Announcement content ─────────────────────────────────────────────
TITLE = "🚀 Rune v1.3.0 — New AI Providers + Duel Fix"

DESCRIPTION = (
    "Big update! Here's what's new ⬇️"
)

FIELDS = [
    (
        "🤖 AI Provider Overhaul",
        (
            "Switched from OmniRoute to **Groq + OpenRouter** for more "
            "reliable, faster responses.\n"
            "• **Groq** handles fast, everyday replies\n"
            "• **OpenRouter** kicks in as fallback for deeper reasoning\n"
            "• No more downtime from unstable routing!"
        ),
    ),
    (
        "⚔️ Duel System Fixed",
        (
            "Duels now require the other person to **accept or decline** — "
            "no more forced coin flips!\n"
            "• Target gets ✅ Accept / ❌ Decline buttons\n"
            "• Challenges expire after 60 seconds if ignored"
        ),
    ),
    (
        "🧪 Provider Testing",
        "/testproviders now tests **Groq** and **OpenRouter** directly — "
        "run it to check your bot's AI health.",
    ),
]

COLOR = discord.Color.from_rgb(255, 0, 119)  # Rune pink
FOOTER = "Rune v1.3.0 • Powered by Groq + OpenRouter"
# ─────────────────────────────────────────────────────────────────────


async def main():
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"✅ Logged in as {client.user} ({client.user.id})")
        print(f"📡 Serving {len(client.guilds)} servers\n")

        embed = discord.Embed(
            title=TITLE,
            description=DESCRIPTION,
            color=COLOR,
        )
        for name, value in FIELDS:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text=FOOTER)

        success = 0
        failed = 0

        for guild in client.guilds:
            channel = None
            # Priority: system channel > first writable channel
            if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                channel = guild.system_channel
            if not channel:
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        channel = ch
                        break

            if channel:
                try:
                    await channel.send(embed=embed)
                    print(f"  ✅ {guild.name} → #{channel.name}")
                    success += 1
                except Exception as e:
                    print(f"  ❌ {guild.name} → {e}")
                    failed += 1
            else:
                print(f"  ⚠️  {guild.name} → no writable channel")
                failed += 1

        print(f"\n📊 Done — ✅ {success} sent, ❌ {failed} failed, {len(client.guilds)} total")
        await client.close()

    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

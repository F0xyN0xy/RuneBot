"""
Rune Bot - Update Announcement System
Sends update messages to all servers with versioning based on git history
"""
import discord
from discord.ext import commands
import json
import os
from datetime import datetime
from typing import Optional
import subprocess

ANNOUNCEMENTS_FILE = "announcements_config.json"

def get_current_version() -> str:
    """Get current version from git describe or commit hash."""
    try:
        # Try git describe first (if tags exist)
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()

        # Fallback to short commit hash
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        print(f"[Announcements] Could not get git version: {e}")

    return "unknown"


def get_commit_message() -> str:
    """Get the latest commit message."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def load_announcements_config() -> dict:
    """Load the announcements config (which updates were sent)."""
    if os.path.exists(ANNOUNCEMENTS_FILE):
        try:
            with open(ANNOUNCEMENTS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"sent_versions": [], "last_sent": None}


def save_announcements_config(config: dict):
    """Save the announcements config."""
    try:
        with open(ANNOUNCEMENTS_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"[Announcements] Error saving config: {e}")


async def send_update_announcement(
    bot: commands.Bot,
    title: str,
    description: str,
    fields: Optional[list[tuple[str, str]]] = None,
    color: Optional[discord.Color] = None,
    thumbnail_url: Optional[str] = None,
    footer_text: Optional[str] = None
) -> dict:
    """
    Send an update announcement to all servers.

    Returns a dict with success/failure counts.
    """
    version = get_current_version()
    config = load_announcements_config()

    if color is None:
        color = discord.Color.from_rgb(255, 0, 119)

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now()
    )

    if fields:
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=False)

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    footer = footer_text or f"Rune v{version}"
    embed.set_footer(text=footer)

    success = 0
    failed = 0

    for guild in bot.guilds:
        # Try to find an appropriate channel to send to
        target_channel = None

        # Priority 1: system channel (where welcome messages go)
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            target_channel = guild.system_channel

        # Priority 2: first text channel bot can send to
        if not target_channel:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break

        if target_channel:
            try:
                await target_channel.send(embed=embed)
                success += 1
                print(f"[Announcements] Sent to {guild.name} (#{target_channel.name})")
            except Exception as e:
                failed += 1
                print(f"[Announcements] Failed to send to {guild.name}: {e}")
        else:
            failed += 1
            print(f"[Announcements] No valid channel in {guild.name}")

    # Record this version as sent
    config["sent_versions"].append({
        "version": version,
        "title": title,
        "timestamp": datetime.now().isoformat(),
        "success": success,
        "failed": failed
    })
    config["last_sent"] = datetime.now().isoformat()
    save_announcements_config(config)

    return {"version": version, "success": success, "failed": failed, "total": len(bot.guilds)}


# ========== SLASH COMMAND ==========

def register_announcement_commands(bot: commands.Bot):
    """Register announcement slash commands (owner only)."""

    @bot.tree.command(name="announce", description="[OWNER ONLY] Send an update announcement to all servers")
    @commands.is_owner()
    async def announce(interaction: discord.Interaction, title: str, message: str):
        """Send an announcement to all servers."""
        await interaction.response.defer(ephemeral=True)

        version = get_current_version()
        commit_msg = get_commit_message()

        fields = []
        if commit_msg:
            fields.append(("🔄 Latest Changes", commit_msg[:1024]))

        result = await send_update_announcement(
            bot,
            title=title,
            description=message,
            fields=fields
        )

        summary = (
            f"✅ **Announcement sent!**\n\n"
            f"📦 Version: `{result['version']}`\n"
            f"✅ Successful: **{result['success']}** servers\n"
            f"❌ Failed: **{result['failed']}** servers\n"
            f"📊 Total: **{result['total']}** servers"
        )

        await interaction.followup.send(summary, ephemeral=True)


    @bot.tree.command(name="version", description="Show Rune's current version")
    async def version_cmd(interaction: discord.Interaction):
        """Show current bot version."""
        version = get_current_version()
        commit_msg = get_commit_message()

        embed = discord.Embed(
            title="🤖 Rune Bot Version",
            description=f"**Version:** `{version}`",
            color=discord.Color.from_rgb(255, 0, 119)
        )

        if commit_msg:
            embed.add_field(name="📝 Latest Commit", value=commit_msg[:1024], inline=False)

        config = load_announcements_config()
        if config["last_sent"]:
            last_sent_dt = datetime.fromisoformat(config["last_sent"])
            embed.add_field(
                name="📢 Last Announcement",
                value=f"<t:{int(last_sent_dt.timestamp())}:R>",
                inline=False
            )

        await interaction.response.send_message(embed=embed)


    @bot.tree.command(name="announcement-history", description="[OWNER ONLY] View announcement history")
    @commands.is_owner()
    async def announcement_history(interaction: discord.Interaction):
        """View recent announcements."""
        await interaction.response.defer(ephemeral=True)

        config = load_announcements_config()
        history = config.get("sent_versions", [])

        if not history:
            await interaction.followup.send("No announcements sent yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📢 Announcement History",
            description=f"Total announcements sent: **{len(history)}**",
            color=discord.Color.blue()
        )

        # Show last 5
        for entry in reversed(history[-5:]):
            dt = datetime.fromisoformat(entry["timestamp"])
            embed.add_field(
                name=f"{entry['title']} (v{entry['version']})",
                value=f"<t:{int(dt.timestamp())}:R> • ✅ {entry['success']} / ❌ {entry['failed']}",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

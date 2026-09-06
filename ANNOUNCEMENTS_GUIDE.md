# 📢 Rune Bot - Update Announcement System

## Overview
This system lets you easily send update announcements to all servers where Rune is running. It automatically tracks versions based on your git commits and keeps a history of what was sent.

## Commands

### `/announce` (Owner Only)
Send an update announcement to all servers.

**Usage:**
```
/announce title:"Big Update!" message:"New features: vote webhooks, auto-DMs, and more!"
```

**What it does:**
- Sends a beautiful embed to all servers
- Automatically includes the current version (from git)
- Includes the latest commit message as "Latest Changes"
- Tracks success/failure for each server
- Saves to history

**Example output in servers:**
```
🎉 Big Update!

New features: vote webhooks, auto-DMs, and more!

🔄 Latest Changes
Fixed Wispbyte errors: voice state AttributeError, VC watchdog reconnect crashes

Rune v5248eb7
```

### `/version`
Anyone can use this to see Rune's current version and latest changes.

### `/announcement-history` (Owner Only)
View the last 5 announcements you sent, with timestamps and success rates.

## How Versioning Works

The bot uses **git** to determine the current version:

1. If you have git tags: `v1.2.3` → shows as `v1.2.3`
2. Otherwise: shows the short commit hash like `5248eb7`

**To create a version tag:**
```bash
git tag v1.0.0
git push --tags
```

Then future announcements will show as "Rune v1.0.0" instead of a commit hash.

## Files

- `announcements.py` — The announcement system code
- `announcements_config.json` — History of sent announcements (hidden from git)

## Tips

- The bot will try to send to the server's system channel first (where welcome messages go)
- If that's not available, it picks the first text channel it has permissions for
- Failed sends are logged but don't stop the announcement
- The history is saved locally — useful for tracking what you've communicated to users

## Example Use Cases

**After deploying a big feature:**
```
/announce title:"New Vote System!" message:"Vote for Rune and get an instant DM with +50 points! No need to run /checkvote anymore!"
```

**Bug fix announcement:**
```
/announce title:"Quick Fix" message:"Fixed the voice channel reconnect issue. Everything should be stable now!"
```

**Scheduled maintenance:**
```
/announce title:"Maintenance Notice" message:"Rune will restart in 10 minutes for updates. Downtime: ~30 seconds."
```

---

**Note:** Only the bot owner (set via Discord app settings) can use `/announce` and `/announcement-history`. Regular users can only use `/version`.

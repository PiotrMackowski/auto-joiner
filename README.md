# zoom-autojoiner

Automatically joins your Zoom meetings on macOS. Reads your local macOS Calendar (works with Google Calendar synced via System Settings > Internet Accounts), watches for upcoming meetings with Zoom links, and opens them at the right time.

No Google Cloud API keys needed. No browser extensions. Just a Python script and a launchd daemon.

## Install

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/PiotrMackowski/auto-joiner/main/setup.sh)"
```

On first run, macOS will ask you to grant Calendar access — say yes.

## What it does

- Polls your macOS Calendar every 5 minutes
- When a meeting with a Zoom link is about to start, opens it via `zoommtg://` deep link
- Skips meetings matching configurable keywords (e.g. "focus time", "lunch")
- Won't interrupt an ongoing meeting — if you're already in a call, it waits
- Tracks which meetings it already joined today so it doesn't rejoin
- Sends a macOS notification before joining
- Runs as a launchd daemon — starts on login, restarts on crash

## Usage

```bash
# See upcoming meetings
~/zoom-autojoiner/venv/bin/python3 ~/zoom-autojoiner/autojoiner.py check

# Join the next Zoom meeting right now
~/zoom-autojoiner/venv/bin/python3 ~/zoom-autojoiner/autojoiner.py join-next

# View logs
tail -f ~/.zoom-autojoiner/autojoiner.log
```

## Config

Edit `~/zoom-autojoiner/config.yaml`:

```yaml
join_early_minutes: 0        # 0 = join right on time
poll_interval_minutes: 5     # how often to check calendar
lookahead_minutes: 30        # how far ahead to look
skip_keywords:               # skip meetings with these words
  - "focus time"
  - "lunch"
  - "blocked"
  - "OOO"
notify_before_join: true     # macOS notification before joining
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.autojoiner.zoom.plist
rm ~/Library/LaunchAgents/com.autojoiner.zoom.plist
rm -rf ~/zoom-autojoiner ~/.zoom-autojoiner
```

## Requirements

- macOS (uses EventKit for calendar access)
- Python 3.9+
- Zoom desktop app installed
- Calendar synced via System Settings > Internet Accounts

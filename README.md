# zoom-autojoiner

Automatically joins your Zoom meetings on macOS. Reads your local macOS Calendar (works with Google Calendar synced via System Settings > Internet Accounts), watches for upcoming meetings with Zoom links, and opens them at the right time.

No Google Cloud API keys needed. No browser extensions. Just a Python script and a launchd daemon. Optional menu bar app for at-a-glance meeting status.

## Install

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/PiotrMackowski/auto-joiner/main/setup.sh)"
```

On first run, macOS will ask you to grant Calendar access — say yes.

## What it does

- Polls your macOS Calendar every minute
- When a meeting with a Zoom link is about to start, opens it via `zoommtg://` deep link
- Joins 1 minute early so you're always on time
- Looks back 30 minutes for late invites — if someone sends an invite after the meeting started, it still joins
- Skips meetings matching configurable keywords (e.g. "focus time", "lunch")
- Won't interrupt an ongoing meeting — if you're already in a call, it waits
- Tracks which meetings it already joined today so it doesn't rejoin
- Sends a macOS notification before joining
- Runs as a launchd daemon — starts on login, restarts on crash

## Menu Bar App

Run the menu bar app instead of (or alongside) the daemon for a visual interface:

```bash
~/zoom-autojoiner/venv/bin/python3 ~/zoom-autojoiner/menubar.py
```

Features:
- **Live countdown** in the menu bar (⏳ 12m 30s → ⚡ 2m 15s → 🔴 when live)
- **Toggle auto-join** on/off with a checkbox
- **Skip meetings** individually from the dropdown
- **Upcoming meetings list** with times and Zoom links
- **Refresh** to re-poll your calendar on demand
- **Open Logs** to view the log file

The menu bar app uses the same config and state as the daemon — they share `config.yaml`, joined/skipped tracking, and logs.

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
join_early_minutes: 1        # join 1 minute before start
poll_interval_minutes: 1     # how often to check calendar
lookahead_minutes: 30        # how far ahead to look
lookback_minutes: 30         # how far back to look (late invites)
skip_keywords:               # skip meetings with these words
  - "focus time"
  - "lunch"
  - "blocked"
  - "OOO"
notify_before_join: true     # macOS notification before joining
zoom_only: true              # only auto-join meetings with Zoom links
log_file: "~/.zoom-autojoiner/autojoiner.log"  # log file path
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

## Limitations

- macOS syncs calendars every 5–15 minutes (hardcoded by Apple, not configurable). If someone sends a last-minute invite, it may take up to 15 minutes before macOS pulls it down. The 30-minute lookback window compensates for this. You can force a sync in Calendar.app with `Cmd+Shift+R`.

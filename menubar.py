#!/usr/bin/env python3
"""Zoom Auto-Joiner — macOS menu bar app using rumps."""

import datetime
import subprocess
import threading
import time

import rumps

from autojoiner import (
    CONFIG_PATH,
    STATE_DIR,
    fetch_events,
    find_zoom_url,
    get_joined_today,
    get_store,
    load_config,
    mark_joined,
    notify,
    to_zoommtg,
    _setup_logging,
    log,
)

SKIP_FILE = STATE_DIR / "skipped_today.txt"


def _get_skipped_today() -> set[str]:
    if not SKIP_FILE.exists():
        return set()
    lines = SKIP_FILE.read_text().strip().split("\n")
    if not lines or lines[0] != datetime.date.today().isoformat():
        return set()
    return set(lines[1:])


def _mark_skipped(event_id: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    if not SKIP_FILE.exists() or SKIP_FILE.read_text().split("\n")[0] != today:
        SKIP_FILE.write_text(f"{today}\n{event_id}\n")
    else:
        with open(SKIP_FILE, "a") as f:
            f.write(f"{event_id}\n")


def _format_countdown(seconds: float) -> str:
    if seconds <= 0:
        return "now"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h {mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


class ZoomAutoJoiner(rumps.App):
    def __init__(self):
        super().__init__(
            name="Zoom Auto-Joiner",
            title="⏳",
            quit_button=None,
        )

        self.config = load_config()
        self.store = None
        self.meetings = []  # upcoming zoom meetings
        self.autojoin_enabled = True
        self._store_age = 0
        self._lock = threading.Lock()

        # Menu items
        self.next_item = rumps.MenuItem("Loading...")
        self.next_item.set_callback(None)

        self.toggle_item = rumps.MenuItem("Auto-Join Enabled", callback=self.toggle_autojoin)
        self.toggle_item.state = 1

        self.upcoming_separator = rumps.MenuItem("─── Upcoming ───")
        self.upcoming_separator.set_callback(None)

        self.menu = [
            self.next_item,
            None,
            self.toggle_item,
            None,
            self.upcoming_separator,
            # Dynamic meeting items inserted here
            None,
            rumps.MenuItem("Refresh", callback=self.on_refresh),
            rumps.MenuItem("Edit Config", callback=self.on_edit_config),
            rumps.MenuItem("Open Logs", callback=self.on_open_logs),
            None,
            rumps.MenuItem("Quit", callback=self.on_quit),
        ]

        # Background: init EventKit store + first fetch
        self._init_thread = threading.Thread(target=self._init_store, daemon=True)
        self._init_thread.start()

        # Timer: update countdown every second
        self._countdown_timer = rumps.Timer(self._tick, 1)
        self._countdown_timer.start()

        # Timer: poll calendar every poll_interval
        interval = self.config.get("poll_interval_minutes", 1) * 60
        self._poll_timer = rumps.Timer(self._poll, interval)
        self._poll_timer.start()

    # --- Init ---

    def _init_store(self):
        try:
            self.store = get_store()
            self._refresh_meetings()
        except PermissionError:
            rumps.notification(
                "Zoom Auto-Joiner",
                "Calendar Access Required",
                "Grant access in System Settings > Privacy & Security > Calendars",
            )
        except Exception as e:
            log.exception("Failed to init EventKit store")

    def _refresh_store_if_stale(self):
        """Recreate EventKit store every hour to avoid stale handles."""
        self._store_age += 1
        if self._store_age >= 3600:
            try:
                self.store = get_store()
                self._store_age = 0
            except Exception:
                log.exception("Failed to refresh store")

    # --- Data ---

    def _refresh_meetings(self):
        if not self.store:
            return
        try:
            lookahead = self.config.get("lookahead_minutes", 30)
            lookback = self.config.get("lookback_minutes", 30)
            skip_kw = [k.lower() for k in self.config.get("skip_keywords", [])]

            events = fetch_events(self.store, lookahead_min=max(lookahead, 60), lookback_min=lookback)
            now = datetime.datetime.now(datetime.timezone.utc)
            joined = get_joined_today()
            skipped = _get_skipped_today()

            zoom_meetings = []
            for ev in events:
                if ev["id"] in joined or ev["id"] in skipped:
                    continue
                if ev["end"] <= now:
                    continue
                if any(kw in ev["title"].lower() for kw in skip_kw):
                    continue
                zoom_url = find_zoom_url(ev)
                if not zoom_url:
                    continue
                ev["zoom_url"] = zoom_url
                zoom_meetings.append(ev)

            with self._lock:
                self.meetings = zoom_meetings

            self._rebuild_menu()
            log.info("Refreshed: %d zoom meetings", len(zoom_meetings))
        except Exception:
            log.exception("Error refreshing meetings")

    def _rebuild_menu(self):
        """Rebuild the dynamic meeting items in the menu."""
        # Remove old dynamic items (between separator and the next None)
        keys_to_remove = []
        for key in self.menu.keys():
            if key.startswith("  ") or key.startswith("→ Skip:"):
                keys_to_remove.append(key)
        for key in keys_to_remove:
            if key in self.menu:
                del self.menu[key]

        with self._lock:
            meetings = list(self.meetings)

        if not meetings:
            item = rumps.MenuItem("  No Zoom meetings")
            item.set_callback(None)
            self.menu.insert_after(self.upcoming_separator.title, item)
        else:
            # Insert in reverse so they appear in correct order after separator
            for ev in reversed(meetings[:8]):
                t = ev["start"].astimezone().strftime("%H:%M")
                title = f"  {t}  {ev['title']}"
                # Truncate long titles
                if len(title) > 50:
                    title = title[:47] + "..."

                skip_title = f"→ Skip: {ev['title'][:30]}"
                skip_item = rumps.MenuItem(skip_title, callback=self._make_skip_cb(ev))

                meeting_item = rumps.MenuItem(title)
                meeting_item.set_callback(None)

                self.menu.insert_after(self.upcoming_separator.title, skip_item)
                self.menu.insert_after(self.upcoming_separator.title, meeting_item)

    def _make_skip_cb(self, ev):
        """Create a callback to skip a specific meeting."""
        def cb(_):
            _mark_skipped(ev["id"])
            with self._lock:
                self.meetings = [m for m in self.meetings if m["id"] != ev["id"]]
            self._rebuild_menu()
            rumps.notification("Zoom Auto-Joiner", "Skipped", ev["title"])
        return cb

    # --- Timers ---

    def _tick(self, timer):
        """Update countdown in menu bar title every second."""
        with self._lock:
            meetings = list(self.meetings)

        now = datetime.datetime.now(datetime.timezone.utc)

        if not meetings:
            self.title = "☁️"
            self.next_item.title = "No upcoming meetings"
            return

        next_ev = meetings[0]
        join_early = self.config.get("join_early_minutes", 0)
        join_at = next_ev["start"] - datetime.timedelta(minutes=join_early)
        seconds_until = (join_at - now).total_seconds()

        if seconds_until <= 0:
            self.title = "🔴"
            self.next_item.title = f"NOW: {next_ev['title']}"
            if self.autojoin_enabled:
                self._join_meeting(next_ev)
        elif seconds_until <= 300:  # 5 min
            self.title = f"⚡ {_format_countdown(seconds_until)}"
            self.next_item.title = f"Next: {next_ev['title']} in {_format_countdown(seconds_until)}"
        else:
            self.title = f"⏳ {_format_countdown(seconds_until)}"
            self.next_item.title = f"Next: {next_ev['title']} in {_format_countdown(seconds_until)}"

    def _poll(self, timer):
        """Poll calendar in background thread."""
        self._refresh_store_if_stale()
        thread = threading.Thread(target=self._refresh_meetings, daemon=True)
        thread.start()

    # --- Actions ---

    def _join_meeting(self, ev):
        """Join a meeting and mark it as joined."""
        zoom_url = ev.get("zoom_url")
        if not zoom_url:
            return

        # Already joined?
        joined = get_joined_today()
        if ev["id"] in joined:
            return

        join_link = to_zoommtg(zoom_url)
        log.info("Joining: %s -> %s", ev["title"], join_link)
        rumps.notification("Zoom Auto-Joiner", "Joining", ev["title"])
        time.sleep(0.5)
        subprocess.run(["open", join_link], capture_output=True)
        mark_joined(ev["id"])

        # Remove from local list
        with self._lock:
            self.meetings = [m for m in self.meetings if m["id"] != ev["id"]]
        self._rebuild_menu()

    def toggle_autojoin(self, sender):
        sender.state = not sender.state
        self.autojoin_enabled = bool(sender.state)
        status = "enabled" if self.autojoin_enabled else "paused"
        rumps.notification("Zoom Auto-Joiner", "", f"Auto-join {status}")
        log.info("Auto-join %s", status)

    def on_refresh(self, _):
        self.next_item.title = "Refreshing..."
        self.config = load_config()
        thread = threading.Thread(target=self._refresh_meetings, daemon=True)
        thread.start()

    def on_edit_config(self, _):
        subprocess.run(["open", "-t", str(CONFIG_PATH)], capture_output=True)

    def on_open_logs(self, _):
        log_file = self.config.get("log_file", "~/.zoom-autojoiner/autojoiner.log")
        from pathlib import Path
        log_path = Path(log_file).expanduser()
        subprocess.run(["open", str(log_path)], capture_output=True)

    def on_quit(self, _):
        log.info("Quit from menu bar")
        rumps.quit_application()


if __name__ == "__main__":
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    _setup_logging(config)
    log.info("Starting menu bar app")
    ZoomAutoJoiner().run()

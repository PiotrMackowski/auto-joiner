#!/usr/bin/env python3
"""Zoom Auto-Joiner — reads macOS Calendar, opens Zoom at meeting time."""

import datetime
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import EventKit
import Foundation
import yaml

# --- Config ---

CONFIG_PATH = Path(__file__).parent / "config.yaml"
STATE_DIR = Path.home() / ".zoom-autojoiner"
JOINED_FILE = STATE_DIR / "joined_today.txt"


def _setup_logging(config: dict):
    log_file = config.get("log_file", "~/.zoom-autojoiner/autojoiner.log")
    log_path = Path(log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path),
        ],
    )


log = logging.getLogger("autojoiner")

ZOOM_URL_RE = re.compile(
    r"https?://[\w.-]*zoom\.us/[jw]/(\d+)(?:\?([^\s\"'<>]+))?", re.IGNORECASE
)

# EventKit participant statuses
EK_STATUS_TENTATIVE = 4  # EKParticipantStatusTentative


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {}


# --- Calendar ---


def get_store() -> EventKit.EKEventStore:
    store = EventKit.EKEventStore.alloc().init()
    status = EventKit.EKEventStore.authorizationStatusForEntityType_(
        EventKit.EKEntityTypeEvent
    )
    if status != EventKit.EKAuthorizationStatusFullAccess:
        event = threading.Event()
        result = [False]

        def cb(granted, err):
            result[0] = granted
            event.set()

        store.requestFullAccessToEventsWithCompletion_(cb)
        event.wait(30)
        if not result[0]:
            raise PermissionError(
                "Calendar access denied. "
                "Grant in System Settings > Privacy & Security > Calendars"
            )
    return store


def fetch_events(store, lookahead_min: int = 30, lookback_min: int = 0) -> list[dict]:
    now = Foundation.NSDate.date()
    start = Foundation.NSDate.dateWithTimeIntervalSinceNow_(-lookback_min * 60)
    end = Foundation.NSDate.dateWithTimeIntervalSinceNow_(lookahead_min * 60)
    calendars = store.calendarsForEntityType_(EventKit.EKEntityTypeEvent)
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(
        start, end, calendars
    )
    raw = store.eventsMatchingPredicate_(pred) or []

    events = []
    for e in raw:
        if e.isAllDay():
            continue
        # Skip cancelled events (EKEventStatusCanceled = 3)
        if e.status() == 3:
            continue
        # Skip declined meetings
        declined = False
        attendee_status = None
        for a in (e.attendees() or []):
            if a.isCurrentUser():
                if a.participantStatus() == EventKit.EKParticipantStatusDeclined:
                    declined = True
                attendee_status = a.participantStatus()
                break
        if declined:
            continue
        events.append(
            {
                "id": str(e.eventIdentifier() or ""),
                "title": str(e.title() or "(No title)"),
                "start": datetime.datetime.fromtimestamp(
                    e.startDate().timeIntervalSince1970(), tz=datetime.timezone.utc
                ),
                "end": datetime.datetime.fromtimestamp(
                    e.endDate().timeIntervalSince1970(), tz=datetime.timezone.utc
                ),
                "location": str(e.location() or ""),
                "notes": str(e.notes() or ""),
                "attendee_status": attendee_status,
            }
        )
    events.sort(key=lambda x: x["start"])
    return events


# --- Zoom URL ---


def find_zoom_url(event: dict) -> str | None:
    for field in (event["location"], event["notes"]):
        m = ZOOM_URL_RE.search(field)
        if m:
            return m.group(0)
    return None


def to_zoommtg(url: str) -> str:
    """Convert https://...zoom.us/j/123?pwd=x or /w/123 to zoommtg:// deep link."""
    m = ZOOM_URL_RE.search(url)
    if not m:
        return url
    host = urlparse(url).hostname or "zoom.us"
    meeting_id = m.group(1)
    params = parse_qs(m.group(2) or "")
    pwd = params.get("pwd", [None])[0]
    link = f"zoommtg://{host}/join?action=join&confno={meeting_id}"
    if pwd:
        link += f"&pwd={pwd}"
    return link


# --- State tracking ---


def get_joined_today() -> set[str]:
    if not JOINED_FILE.exists():
        return set()
    lines = JOINED_FILE.read_text().strip().split("\n")
    if not lines or lines[0] != datetime.date.today().isoformat():
        return set()
    return set(lines[1:])


def mark_joined(event_id: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    joined = get_joined_today()
    if not JOINED_FILE.exists() or JOINED_FILE.read_text().split("\n")[0] != today:
        JOINED_FILE.write_text(f"{today}\n{event_id}\n")
    else:
        with open(JOINED_FILE, "a") as f:
            f.write(f"{event_id}\n")


def is_in_meeting(events: list[dict], now: datetime.datetime) -> bool:
    """Check if we're currently inside a meeting we already joined."""
    joined = get_joined_today()
    for ev in events:
        if ev["id"] in joined and ev["start"] <= now < ev["end"]:
            zoom = find_zoom_url(ev)
            if zoom:
                return True
    return False


# --- Main logic ---


def notify(msg: str):
    try:
        safe = msg.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e", f'display notification "{safe}" with title "Zoom Auto-Joiner"'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def check_and_join(store, config: dict):
    now = datetime.datetime.now(datetime.timezone.utc)
    lookahead = config.get("lookahead_minutes", 30)
    lookback = config.get("lookback_minutes", 30)
    join_early = config.get("join_early_minutes", 0)
    skip_kw = [k.lower() for k in config.get("skip_keywords", [])]
    do_notify = config.get("notify_before_join", True)
    skip_tentative = config.get("skip_tentative", False)

    # Fetch with lookback (late invites) and extra lookahead (ongoing detection)
    events = fetch_events(store, lookahead_min=max(lookahead, 60), lookback_min=lookback)
    joined = get_joined_today()
    zoom_events = [e for e in events if find_zoom_url(e)]
    log.info("Poll: %d events, %d with Zoom links", len(events), len(zoom_events))

    # Don't interrupt an ongoing meeting
    if is_in_meeting(events, now):
        log.debug("Currently in a meeting, skipping check")
        return

    # Only look at events within the join window
    for ev in events:
        if ev["id"] in joined:
            continue
        if ev["end"] <= now:
            continue  # already ended
        if any(kw in ev["title"].lower() for kw in skip_kw):
            continue
        if skip_tentative and ev.get("attendee_status") == EK_STATUS_TENTATIVE:
            continue

        zoom_url = find_zoom_url(ev)
        if not zoom_url and config.get("zoom_only", True):
            continue

        join_at = ev["start"] - datetime.timedelta(minutes=join_early)
        if now >= join_at:
            if zoom_url:
                join_link = to_zoommtg(zoom_url)
                log.info("Joining: %s -> %s", ev["title"], join_link)
                if do_notify:
                    notify(f"Joining: {ev['title']}")
                    time.sleep(1)
                subprocess.run(["open", join_link], capture_output=True)
            else:
                log.info("Meeting time: %s (no Zoom link)", ev["title"])
                if do_notify:
                    notify(f"Meeting starting: {ev['title']}")
            mark_joined(ev["id"])
            return  # only join one meeting at a time
        else:
            wait = (join_at - now).total_seconds()
            log.info("Waiting: %s (in %.0f min)", ev["title"], wait / 60)


def run(config: dict):
    store = get_store()
    interval = config.get("poll_interval_minutes", 5) * 60
    store_refresh = 0
    log.info("Started (poll %ds, early %dm)", interval, config.get("join_early_minutes", 0))
    while True:
        try:
            # Refresh EventKit store every hour to avoid stale handles
            store_refresh += interval
            if store_refresh >= 3600:
                store = get_store()
                store_refresh = 0
            check_and_join(store, config)
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Error in poll cycle")
        time.sleep(interval)


# --- CLI ---

if __name__ == "__main__":
    import sys

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    _setup_logging(config)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"

    if cmd == "check":
        store = get_store()
        events = fetch_events(store, config.get("lookahead_minutes", 30), config.get("lookback_minutes", 30))
        if not events:
            print("No upcoming events.")
        for ev in events:
            t = ev["start"].astimezone().strftime("%H:%M")
            zoom = find_zoom_url(ev)
            tag = "ZOOM" if zoom else "    "
            print(f"  {t}  [{tag}]  {ev['title']}")
            if zoom:
                print(f"         -> {to_zoommtg(zoom)}")

    elif cmd == "join-next":
        store = get_store()
        events = fetch_events(store, config.get("lookahead_minutes", 30), config.get("lookback_minutes", 30))
        skip_kw = [k.lower() for k in config.get("skip_keywords", [])]
        for ev in events:
            if any(kw in ev["title"].lower() for kw in skip_kw):
                continue
            zoom = find_zoom_url(ev)
            if zoom:
                link = to_zoommtg(zoom)
                print(f"Joining: {ev['title']}\n  {link}")
                subprocess.run(["open", link])
                break
        else:
            print("No Zoom meetings found.")

    elif cmd == "run":
        run(config)

    else:
        print("Usage: autojoiner.py [check|join-next|run]")

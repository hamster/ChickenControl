#!/usr/bin/env python3
"""
chickengate — hardware daemon: GPIO control, solar scheduling, and socket IPC.

Runs as user 'chickenhw' (gpio group).  Never touches the network.
Accepts commands from chickenctl (the HTTPS proxy) and chickendoor (CLI)
via a Unix domain socket.

Socket: /run/chickengate.sock  (group chicken-ipc, mode 660)

Protocol: one JSON request line → one JSON response line → close
  {"cmd": "open",   "door": "coop"}  → {"state": "opening"}
  {"cmd": "close",  "door": "coop"}  → {"state": "closing"}
  {"cmd": "stop",   "door": "coop"}  → {"state": "<current>"}
  {"cmd": "status", "door": "coop"}  → {"state": "open|closed|opening|closing|unknown"}
  {"cmd": "doors"}                   → {"doors": ["coop", "run"]}
"""

import argparse
import grp
import json
import logging
import os
import signal
import socket
import sys
import threading
from datetime import datetime, timedelta

import door_control
from version import VERSION

log = logging.getLogger(__name__)

SOCKET_PATH = "/run/chickengate.sock"
SOCKET_GROUP = "chicken-ipc"

# Populated at startup
_doors = {}
_shutdown_event = threading.Event()

# Per-door operation state (all access serialised by _op_lock)
_op_lock = threading.Lock()
_door_stop_events: dict[str, threading.Event] = {}
_door_direction: dict[str, str] = {}    # door_name → "open"/"close" while moving
_door_threads: dict[str, threading.Thread] = {}


# ---------------------------------------------------------------------------
# Door command logic
# ---------------------------------------------------------------------------

def _command_internal(name, command):
    """Execute open/close on a door.  Returns a response dict."""
    if name not in _doors:
        return {"error": "unknown door", "code": 404}

    with _op_lock:
        if door_control.is_moving(name):
            current_dir = _door_direction.get(name)
            if command == "open" and current_dir == "close":
                log.info("Overriding close with open for '%s'", name)
                _door_stop_events[name].set()
                t = _door_threads.get(name)
                if t and t.is_alive():
                    t.join(timeout=2.0)
                _door_stop_events[name].clear()
            else:
                return {"error": "door is moving", "state": "moving", "code": 409}

        _door_direction[name] = command
        t = threading.Thread(
            target=_run_operate,
            args=(_doors[name], command),
            daemon=True,
            name=f"{name}-{command}",
        )
        _door_threads[name] = t
        t.start()

    return {"state": "opening" if command == "open" else "closing"}


def _run_operate(door, command):
    try:
        door_control.operate(door, command, stop_event=_door_stop_events.get(door.name))
    except RuntimeError as e:
        log.warning("operate failed for %s: %s", door.name, e)
    except Exception:
        log.exception("unexpected error operating %s", door.name)
    finally:
        if _door_direction.get(door.name) == command:
            _door_direction.pop(door.name, None)


def _get_status(name):
    if name not in _doors:
        return {"error": "unknown door", "code": 404}
    state = door_control.get_status(name)
    if state == "moving":
        direction = _door_direction.get(name, "open")
        state = "opening" if direction == "open" else "closing"
    return {"state": state}


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

_MAX_REQUEST_BYTES = 4096  # largest valid request is well under 200 bytes


def _handle_client(conn):
    try:
        conn.settimeout(10.0)  # prevent slow/incomplete senders from blocking forever
        with conn.makefile("r") as f:
            line = f.readline(_MAX_REQUEST_BYTES)
        if not line.strip():
            return
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            conn.sendall(json.dumps({"error": "invalid JSON"}).encode() + b"\n")
            return

        cmd = req.get("cmd", "")
        door_name = req.get("door", "")
        # JSON allows any type for these fields; coerce to str so dict lookups
        # below behave predictably rather than relying on type-mismatch misses.
        if not isinstance(cmd, str):
            cmd = ""
        if not isinstance(door_name, str):
            door_name = ""

        if cmd == "doors":
            resp = {"doors": list(_doors.keys())}
        elif cmd in ("open", "close"):
            resp = _command_internal(door_name, cmd)
        elif cmd == "status":
            resp = _get_status(door_name)
        elif cmd == "stop":
            # Stop while closing → reverse to open; stop while opening → no-op
            current = _get_status(door_name).get("state")
            if current == "closing":
                resp = _command_internal(door_name, "open")
            else:
                resp = _get_status(door_name)
        else:
            resp = {"error": "unknown command"}

        conn.sendall(json.dumps(resp).encode() + b"\n")
    except Exception:
        log.exception("error handling client connection")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _socket_server():
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(SOCKET_PATH)
    except OSError as e:
        log.error("Cannot bind socket %s: %s", SOCKET_PATH, e)
        sys.exit(1)

    try:
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
        os.chown(SOCKET_PATH, -1, gid)
        os.chmod(SOCKET_PATH, 0o660)
    except KeyError:
        log.warning("Group %r not found — socket will be owner-only", SOCKET_GROUP)
        os.chmod(SOCKET_PATH, 0o600)
    except OSError as e:
        log.warning("Could not set socket permissions: %s — socket may be accessible to all", e)

    srv.listen(8)
    srv.settimeout(1.0)
    log.info("chickengate listening on %s", SOCKET_PATH)

    while not _shutdown_event.is_set():
        try:
            conn, _ = srv.accept()
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
        except socket.timeout:
            continue
        except OSError:
            break

    srv.close()
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Solar scheduler
# ---------------------------------------------------------------------------

def _scheduler_loop(observer, tz):
    """Background thread: fires door commands at solar-relative times."""
    try:
        from astral.sun import sun as astral_sun
    except ImportError:
        log.error("astral library not installed — scheduler disabled.  pip install astral")
        return

    log.info("Scheduler started (timezone: %s)", tz)

    while not _shutdown_event.is_set():
        now = datetime.now(tz=tz)
        tomorrow_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        try:
            s = astral_sun(observer, date=now.date(), tzinfo=tz)
        except Exception:
            log.exception("Failed to compute solar events — retrying in 1 hour")
            _shutdown_event.wait(timeout=3600)
            continue

        solar = {"sunrise": s["sunrise"], "sunset": s["sunset"]}
        log.info(
            "Schedule for %s — sunrise %s, sunset %s",
            now.strftime("%Y-%m-%d"),
            solar["sunrise"].strftime("%H:%M %Z"),
            solar["sunset"].strftime("%H:%M %Z"),
        )

        events = []
        for door_name, door in _doors.items():
            for schedule, cmd in ((door.open_at, "open"), (door.close_at, "close")):
                if schedule is None:
                    continue
                event_name, offset_min = schedule
                event_time = solar[event_name] + timedelta(minutes=offset_min)
                if event_time > now:
                    events.append((event_time, door_name, cmd))
                else:
                    log.info(
                        "Skipping past event: %s %s (was %s)",
                        cmd, door_name, event_time.strftime("%H:%M"),
                    )

        events.sort()

        for event_time, door_name, cmd in events:
            wait_secs = (event_time - datetime.now(tz=tz)).total_seconds()
            if wait_secs > 0:
                log.info(
                    "Next: %s %s at %s (%.0f s)",
                    cmd, door_name, event_time.strftime("%H:%M %Z"), wait_secs,
                )
                if _shutdown_event.wait(timeout=wait_secs):
                    return
            if _shutdown_event.is_set():
                return
            log.info("Scheduler firing: %s %s", cmd, door_name)
            try:
                _command_internal(door_name, cmd)
            except Exception:
                log.exception("Scheduler failed to command %s %s", cmd, door_name)

        # Sleep until midnight then recalculate for the new day
        wait_secs = (tomorrow_midnight - datetime.now(tz=tz)).total_seconds()
        _shutdown_event.wait(timeout=max(wait_secs, 1))


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

def _shutdown_handler(signum, frame):
    log.info("Signal %d received — shutting down", signum)
    _shutdown_event.set()
    for event in _door_stop_events.values():
        event.set()
    door_control.deenergize_all(_doors)
    door_control.cleanup_gpio()
    sys.exit(0)


def main():
    global _doors

    parser = argparse.ArgumentParser(description="Chicken door hardware daemon")
    parser.add_argument(
        "-c", "--config",
        default=door_control.DEFAULT_CONFIG,
        metavar="FILE",
        help="Path to doors.conf (default: %(default)s)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        cfg, _doors = door_control.load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        sys.exit(1)

    if not _doors:
        log.error("No doors configured in %s", args.config)
        sys.exit(1)

    for name in _doors:
        _door_stop_events[name] = threading.Event()

    door_control.clean_stale_locks(_doors)
    try:
        door_control.setup_gpio(_doors)
    except RuntimeError as e:
        log.error("%s", e)
        sys.exit(1)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    log.info("chickengate %s starting with doors: %s", VERSION, ", ".join(_doors))

    # Start solar scheduler if location is configured
    if cfg.has_section("location"):
        try:
            from astral import LocationInfo
            lat = cfg.getfloat("location", "latitude")
            lon = cfg.getfloat("location", "longitude")
            tz_name = cfg.get("location", "timezone", fallback="UTC")
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_name)
            loc = LocationInfo(latitude=lat, longitude=lon, timezone=tz_name)
            threading.Thread(
                target=_scheduler_loop,
                args=(loc.observer, tz),
                daemon=True,
                name="scheduler",
            ).start()
        except Exception:
            log.exception("Failed to start scheduler — check [location] config")
    else:
        log.info("No [location] section in config — solar scheduler disabled")

    _socket_server()


if __name__ == "__main__":
    main()

"""
Shared GPIO and lockfile logic for chickendoor CLI and chickenctl daemon.

Both tools call operate() to drive a door.  A per-door lockfile in /run/
serializes concurrent callers (cron vs manual) and doubles as the "moving"
state signal for the status endpoint.
"""

import configparser
import logging
import os
import signal
import threading
import time

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except (ImportError, RuntimeError):
    _HAS_GPIO = False
    logging.getLogger(__name__).warning(
        "RPi.GPIO not available — GPIO operations will be logged only"
    )

log = logging.getLogger(__name__)

DEFAULT_CONFIG = "/etc/chickendoor/doors.conf"
_LOCK_DIR = "/run"
_STATE_DIR = "/var/lib/chickendoor"

_STATE_AFTER = {"open": "open", "close": "closed"}


class DoorConfig:
    __slots__ = ("name", "relay_a", "relay_b", "open_ms", "close_ms")

    def __init__(self, name, relay_a, relay_b, open_ms, close_ms):
        self.name = name
        self.relay_a = relay_a
        self.relay_b = relay_b
        self.open_ms = open_ms
        self.close_ms = close_ms


def load_config(path=DEFAULT_CONFIG):
    """Return (RawConfigParser, {name: DoorConfig}).  Raises on missing file."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=('#',))
    if not cfg.read(path):
        raise FileNotFoundError(f"Config not found: {path}")
    doors = {}
    for section in cfg.sections():
        if section == "server":
            continue
        doors[section] = DoorConfig(
            name=section,
            relay_a=cfg.getint(section, "relay_a"),
            relay_b=cfg.getint(section, "relay_b"),
            open_ms=cfg.getint(section, "open_ms"),
            close_ms=cfg.getint(section, "close_ms"),
        )
    return cfg, doors


def setup_gpio(doors):
    """Set BCM mode and configure all door pins as OUTPUT LOW."""
    if not _HAS_GPIO:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for door in doors.values():
        GPIO.setup(door.relay_a, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(door.relay_b, GPIO.OUT, initial=GPIO.LOW)


def deenergize_all(doors):
    """Drive all relay pins LOW.  Safe to call from a signal handler."""
    if not _HAS_GPIO:
        return
    for door in doors.values():
        try:
            GPIO.output(door.relay_a, GPIO.LOW)
            GPIO.output(door.relay_b, GPIO.LOW)
        except Exception:
            pass


def cleanup_gpio():
    """Release all GPIO resources.  Call once on clean shutdown."""
    if not _HAS_GPIO:
        return
    try:
        GPIO.cleanup()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lockfile helpers
# ---------------------------------------------------------------------------

def _lock_path(name):
    return os.path.join(_LOCK_DIR, f"chickendoor-{name}.lock")


def _state_path(name):
    return os.path.join(_STATE_DIR, f"{name}.state")


def is_moving(name):
    """Return True if a live process holds the lock for this door."""
    lp = _lock_path(name)
    if not os.path.exists(lp):
        return False
    try:
        with open(lp) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # raises OSError if PID is gone
        return True
    except (ValueError, OSError):
        # Stale lockfile — clean it up
        try:
            os.unlink(lp)
        except OSError:
            pass
        return False


def get_status(name):
    """Return 'moving', 'open', 'closed', or 'unknown'."""
    if is_moving(name):
        return "moving"
    try:
        with open(_state_path(name)) as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def clean_stale_locks(doors):
    """Called at daemon startup to remove locks left by a previous crash."""
    for name in doors:
        is_moving(name)  # is_moving() removes stale locks as a side-effect


# ---------------------------------------------------------------------------
# Core operation
# ---------------------------------------------------------------------------

def operate(door, command, stop_event=None):
    """
    Drive door to 'open' or 'close'.  Blocks for the full actuator travel
    time (or until stop_event is set, whichever comes first).

    stop_event: optional threading.Event.  When set, the relay is
                de-energized immediately and the state file is NOT updated
                (door position is unknown after an interrupted move).

    Raises RuntimeError if the door is already moving.
    Signal handlers are only installed when called from the main thread
    (i.e. the CLI); the daemon uses stop_event instead.
    """
    if command not in ("open", "close"):
        raise ValueError(f"Unknown command: {command!r}")
    if is_moving(door.name):
        raise RuntimeError(f"Door '{door.name}' is already moving")

    os.makedirs(_STATE_DIR, exist_ok=True)
    lp = _lock_path(door.name)

    with open(lp, "w") as f:
        f.write(str(os.getpid()))

    duration = door.open_ms / 1000 if command == "open" else door.close_ms / 1000

    def _deenergize():
        if _HAS_GPIO:
            try:
                GPIO.output(door.relay_a, GPIO.LOW)
                GPIO.output(door.relay_b, GPIO.LOW)
            except Exception:
                pass

    def _signal_handler(signum, frame):
        log.warning("Signal %d received — de-energizing %s", signum, door.name)
        _deenergize()
        try:
            os.unlink(lp)
        except OSError:
            pass
        raise SystemExit(1)

    in_main = threading.current_thread() is threading.main_thread()
    if in_main:
        prev_int = signal.signal(signal.SIGINT, _signal_handler)
        prev_term = signal.signal(signal.SIGTERM, _signal_handler)

    try:
        if command == "open":
            log.info("Opening %s (relay_a=%d HIGH, relay_b=%d LOW, %.1fs)",
                     door.name, door.relay_a, door.relay_b, duration)
            if _HAS_GPIO:
                GPIO.output(door.relay_b, GPIO.LOW)   # de-energize close relay first
                time.sleep(0.05)
                GPIO.output(door.relay_a, GPIO.HIGH)
        else:
            log.info("Closing %s (relay_a=%d LOW, relay_b=%d HIGH, %.1fs)",
                     door.name, door.relay_a, door.relay_b, duration)
            if _HAS_GPIO:
                GPIO.output(door.relay_a, GPIO.LOW)   # de-energize open relay first
                time.sleep(0.05)
                GPIO.output(door.relay_b, GPIO.HIGH)

        interrupted = False
        if stop_event is not None:
            interrupted = stop_event.wait(timeout=duration)
        else:
            time.sleep(duration)

        if not interrupted:
            log.info("%s %s complete", door.name, command)
            with open(_state_path(door.name), "w") as f:
                f.write(_STATE_AFTER[command])

    finally:
        _deenergize()
        try:
            os.unlink(lp)
        except OSError:
            pass
        if in_main:
            signal.signal(signal.SIGINT, prev_int)
            signal.signal(signal.SIGTERM, prev_term)

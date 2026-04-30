#!/usr/bin/env python3
"""
chickenctl — HTTPS daemon for manual door control via Home Assistant.

Exposes a small REST API protected by a Bearer token.  All door operations
run in background threads so the HTTP response returns immediately (202).
The cron CLI (chickendoor) and this daemon share the same lockfiles, so
concurrent operations on the same door are serialized automatically.

Endpoints:
    POST /door/<name>/open      → 202 Accepted  |  409 Busy  |  404
    POST /door/<name>/close     → 202 Accepted  |  409 Busy  |  404
    GET  /door/<name>/status    → 200 {"state": "open"|"closed"|"moving"|"unknown"}

All endpoints require:
    Authorization: Bearer <token>
"""

import argparse
import logging
import os
import signal
import sys
import threading

from flask import Flask, jsonify, request

import door_control

log = logging.getLogger(__name__)
app = Flask(__name__)

# Populated at startup
_doors = {}
_token = ""
_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.before_request
def _check_token():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != _token:
        return jsonify({"error": "unauthorized"}), 401


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/doors", methods=["GET"])
def list_doors():
    return jsonify({"doors": list(_doors.keys())})


@app.route("/door/<name>/status", methods=["GET"])
def door_status(name):
    if name not in _doors:
        return jsonify({"error": "unknown door"}), 404
    return jsonify({"state": door_control.get_status(name)})


@app.route("/door/<name>/open", methods=["POST"])
def door_open(name):
    return _command(name, "open")


@app.route("/door/<name>/close", methods=["POST"])
def door_close(name):
    return _command(name, "close")


def _command(name, command):
    if name not in _doors:
        return jsonify({"error": "unknown door"}), 404
    if door_control.is_moving(name):
        return jsonify({"error": "door is moving", "state": "moving"}), 409
    door = _doors[name]
    t = threading.Thread(
        target=_run_operate, args=(door, command), daemon=True, name=f"{name}-{command}"
    )
    t.start()
    return jsonify({"state": "moving"}), 202


def _run_operate(door, command):
    try:
        door_control.operate(door, command, stop_event=_stop_event)
    except RuntimeError as e:
        log.warning("operate failed for %s: %s", door.name, e)
    except Exception:
        log.exception("unexpected error operating %s", door.name)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

def _shutdown_handler(signum, frame):
    log.info("Signal %d received — shutting down", signum)
    _stop_event.set()
    door_control.deenergize_all(_doors)
    door_control.cleanup_gpio()
    sys.exit(0)


def main():
    global _doors, _token

    parser = argparse.ArgumentParser(description="Chicken door HTTPS control daemon")
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
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    if not cfg.has_section("server"):
        log.error("doors.conf is missing [server] section")
        sys.exit(1)

    host = cfg.get("server", "host", fallback="0.0.0.0")
    port = cfg.getint("server", "port", fallback=8443)
    cert = cfg.get("server", "cert")
    key = cfg.get("server", "key")
    _token = cfg.get("server", "token")

    for path, label in ((cert, "cert"), (key, "key")):
        if not os.path.exists(path):
            log.error("TLS %s not found: %s", label, path)
            sys.exit(1)

    door_control.clean_stale_locks(_doors)
    door_control.setup_gpio(_doors)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    log.info("chickenctl starting on %s:%d with doors: %s",
             host, port, ", ".join(_doors))

    # threaded=True lets Flask handle concurrent requests (one per door is typical).
    # use_reloader=False is required — the reloader forks the process which breaks GPIO.
    app.run(
        host=host,
        port=port,
        ssl_context=(cert, key),
        threaded=True,
        use_reloader=False,
        debug=False,
    )


if __name__ == "__main__":
    main()

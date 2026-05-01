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
import socket
import ssl
import sys
import threading

from flask import Flask, jsonify, request
from waitress import create_server

import door_control

log = logging.getLogger(__name__)
app = Flask(__name__)

# Populated at startup
_doors = {}
_token = ""
_door_stop_events: dict[str, threading.Event] = {}
_door_direction: dict[str, str] = {}   # door_name → "open"/"close" while moving
_door_threads: dict[str, threading.Thread] = {}


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
    state = door_control.get_status(name)
    if state == "moving":
        direction = _door_direction.get(name, "open")
        state = "opening" if direction == "open" else "closing"
    return jsonify({"state": state})


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
        current_dir = _door_direction.get(name)
        if command == "open" and current_dir == "close":
            log.info("Overriding close with open for '%s'", name)
            _door_stop_events[name].set()
            t = _door_threads.get(name)
            if t and t.is_alive():
                t.join(timeout=2.0)
            _door_stop_events[name].clear()
        else:
            return jsonify({"error": "door is moving", "state": "moving"}), 409
    door = _doors[name]
    _door_direction[name] = command
    t = threading.Thread(
        target=_run_operate, args=(door, command), daemon=True, name=f"{name}-{command}"
    )
    _door_threads[name] = t
    t.start()
    return jsonify({"state": "moving"}), 202


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


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

def _shutdown_handler(signum, frame):
    log.info("Signal %d received — shutting down", signum)
    for event in _door_stop_events.values():
        event.set()
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

    # Clients that are Ctrl-C'd mid-handshake cause a harmless SSLEOFError in
    # waitress's accept() path.  Filter it out to keep the logs clean.
    class _SuppressSSLEOF(logging.Filter):
        def filter(self, record):
            return not (
                record.exc_info
                and record.exc_info[0] is not None
                and issubclass(record.exc_info[0], ssl.SSLEOFError)
            )
    logging.getLogger("waitress").addFilter(_SuppressSSLEOF())

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

    for name in _doors:
        _door_stop_events[name] = threading.Event()

    door_control.clean_stale_locks(_doors)
    door_control.setup_gpio(_doors)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    log.info("chickenctl starting on %s:%d with doors: %s",
             host, port, ", ".join(_doors))

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(cert, key)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
    sock.bind((host, port))
    ssl_sock = ssl_ctx.wrap_socket(sock, server_side=True)

    server = create_server(app, sockets=[ssl_sock])
    server.run()


if __name__ == "__main__":
    main()

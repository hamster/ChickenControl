#!/usr/bin/env python3
"""
chickenctl — HTTPS proxy daemon for Home Assistant integration.

Runs as user 'chickennet' (chicken-ipc group, no GPIO access).
Receives authenticated HTTPS requests and forwards them to the chickengate
hardware daemon via Unix socket at /run/chickengate.sock.

The REST API is unchanged — the Home Assistant integration requires no
modifications.

Endpoints:
    GET  /doors                → 200 {"doors": [...]}
    POST /door/<name>/open     → 202 | 409 Busy | 404 | 503
    POST /door/<name>/close    → 202 | 409 Busy | 404 | 503
    GET  /door/<name>/status   → 200 {"state": "..."} | 404 | 503

All endpoints require:
    Authorization: Bearer <token>
"""

import argparse
import hmac
import json
import logging
import os
import signal
import socket
import ssl
import sys

from flask import Flask, jsonify, request
from waitress import create_server

import door_control

log = logging.getLogger(__name__)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024  # POST bodies are empty; reject anything large

SOCKET_PATH = "/run/chickengate.sock"
_DOOR_NAME_MAX = 64   # rejects absurdly long names before touching the socket

_token = ""
_known_doors: set[str] = set()  # populated at startup from chickengate


# ---------------------------------------------------------------------------
# Gate communication
# ---------------------------------------------------------------------------

def _gate_request(req_dict):
    """Send one JSON request to chickengate and return the parsed response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10.0)
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps(req_dict).encode() + b"\n")
        with sock.makefile("r") as f:
            resp = json.loads(f.readline(4096))
        return resp
    except (OSError, json.JSONDecodeError) as e:
        log.error("chickengate socket error: %s", e)
        return {"error": "hardware daemon unavailable"}
    finally:
        try:
            sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.before_request
def _check_token():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], _token):
        return jsonify({"error": "unauthorized"}), 401


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _reject_bad_door(name):
    """Return a 404 response if name fails local validation, else None.
    Prevents socket round-trips for obviously invalid door names."""
    if len(name) > _DOOR_NAME_MAX or name not in _known_doors:
        return jsonify({"error": "unknown door"}), 404
    return None


@app.route("/doors", methods=["GET"])
def list_doors():
    resp = _gate_request({"cmd": "doors"})
    if "error" in resp:
        return jsonify(resp), 503
    return jsonify(resp)


@app.route("/door/<name>/status", methods=["GET"])
def door_status(name):
    err = _reject_bad_door(name)
    if err:
        return err
    resp = _gate_request({"cmd": "status", "door": name})
    if "error" in resp:
        return jsonify(resp), 503
    return jsonify(resp)


@app.route("/door/<name>/open", methods=["POST"])
def door_open(name):
    return _proxy_command(name, "open")


@app.route("/door/<name>/close", methods=["POST"])
def door_close(name):
    return _proxy_command(name, "close")


def _proxy_command(name, cmd):
    err = _reject_bad_door(name)
    if err:
        return err
    resp = _gate_request({"cmd": cmd, "door": name})
    code = resp.pop("code", None)
    if code == 409 or "moving" in resp.get("error", ""):
        return jsonify(resp), 409
    if "error" in resp:
        return jsonify(resp), 503
    return jsonify(resp), 202


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

def _shutdown_handler(signum, frame):
    log.info("Signal %d received — shutting down", signum)
    sys.exit(0)


def main():
    global _token, _known_doors

    parser = argparse.ArgumentParser(description="Chicken door HTTPS proxy daemon")
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

    # Clients that are Ctrl-C'd mid-handshake cause a harmless SSLEOFError.
    class _SuppressSSLEOF(logging.Filter):
        def filter(self, record):
            return not (
                record.exc_info
                and record.exc_info[0] is not None
                and issubclass(record.exc_info[0], ssl.SSLEOFError)
            )
    logging.getLogger("waitress").addFilter(_SuppressSSLEOF())

    try:
        cfg, doors = door_control.load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        sys.exit(1)

    _known_doors = set(doors)

    if not cfg.has_section("server"):
        log.error("doors.conf is missing [server] section")
        sys.exit(1)

    host = cfg.get("server", "host", fallback="0.0.0.0")
    port = cfg.getint("server", "port", fallback=8443)
    cert = cfg.get("server", "cert")
    key = cfg.get("server", "key")
    _token = cfg.get("server", "token")
    threads = cfg.getint("server", "threads", fallback=8)

    if not _token or _token == "REPLACE_WITH_GENERATED_TOKEN":
        log.error(
            "No API token set in doors.conf.  Generate one with:\n"
            "  python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
            "then set: token = <generated value>  in the [server] section."
        )
        sys.exit(1)

    for path, label in ((cert, "cert"), (key, "key")):
        if not os.path.exists(path):
            log.error("TLS %s not found: %s", label, path)
            sys.exit(1)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    log.info("chickenctl starting on %s:%d", host, port)

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(cert, key)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
    sock.bind((host, port))
    ssl_sock = ssl_ctx.wrap_socket(sock, server_side=True)

    server = create_server(app, sockets=[ssl_sock], threads=threads)
    server.run()


if __name__ == "__main__":
    main()

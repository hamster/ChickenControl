#!/usr/bin/env python3
"""
chickendoor — CLI tool for operating chicken coop doors.

Called directly by cron (via sunwait) for scheduled open/close events.

Usage:
    chickendoor open  <door>
    chickendoor close <door>

Examples:
    chickendoor open  coop
    chickendoor close coop
    chickendoor open  run
"""

import argparse
import json
import logging
import ssl
import sys
import time
import urllib.error
import urllib.request

import door_control


def _try_daemon(cfg, command, door_name):
    """POST command to chickenctl daemon and block until done. Returns True on success,
    False if the daemon isn't reachable (caller should fall back to direct GPIO)."""
    if not cfg.has_section("server"):
        return False

    host = cfg.get("server", "host", fallback="127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = cfg.getint("server", "port", fallback=8443)
    token = cfg.get("server", "token")
    base = f"https://{host}:{port}"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"Authorization": f"Bearer {token}"}

    req = urllib.request.Request(
        f"{base}/door/{door_name}/{command}", method="POST",
        headers=headers, data=b"",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
            if resp.status == 409:
                raise RuntimeError(f"door '{door_name}' is already moving")
    except urllib.error.URLError:
        return False  # daemon not running — fall back to GPIO

    # Block until the daemon reports the door is no longer moving
    status_req = urllib.request.Request(
        f"{base}/door/{door_name}/status", headers=headers,
    )
    try:
        while True:
            with urllib.request.urlopen(status_req, context=ctx, timeout=5) as resp:
                state = json.loads(resp.read()).get("state")
            if state not in ("moving", "opening", "closing"):
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n{door_name}: interrupted — door may still be moving", file=sys.stderr)

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Open or close a chicken coop door.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("command", choices=["open", "close"])
    parser.add_argument("door", help="Door name (must match a section in doors.conf)")
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
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        cfg, doors = door_control.load_config(args.config)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.door not in doors:
        known = ", ".join(sorted(doors))
        print(f"error: unknown door '{args.door}' (known: {known})", file=sys.stderr)
        sys.exit(1)

    door = doors[args.door]

    try:
        if _try_daemon(cfg, args.command, args.door):
            print(f"{args.door}: {args.command} complete")
            return
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    # Daemon not running — drive GPIO directly
    door_control.setup_gpio(doors)
    try:
        door_control.operate(door, args.command)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        door_control.cleanup_gpio()
        sys.exit(1)

    door_control.cleanup_gpio()
    print(f"{args.door}: {args.command} complete")


if __name__ == "__main__":
    main()

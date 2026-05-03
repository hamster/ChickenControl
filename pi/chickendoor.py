#!/usr/bin/env python3
"""
chickendoor — CLI tool for operating chicken coop doors.

Connects to the chickengate hardware daemon via Unix socket.
Requires membership in the chicken-ipc group (or root).

Usage:
    chickendoor open  <door>
    chickendoor close <door>

Examples:
    sudo chickendoor open  coop
    sudo chickendoor close coop
    sudo chickendoor open  run
"""

import argparse
import json
import logging
import socket
import sys
import time

SOCKET_PATH = "/run/chickengate.sock"

log = logging.getLogger(__name__)


def _gate_request(req_dict, timeout=5.0):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps(req_dict).encode() + b"\n")
        with sock.makefile("r") as f:
            line = f.readline()
        if not line:
            raise OSError("chickengate closed connection without responding")
        return json.loads(line)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Open or close a chicken coop door.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("command", choices=["open", "close"])
    parser.add_argument("door", help="Door name (must match a section in doors.conf)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        resp = _gate_request({"cmd": args.command, "door": args.door})
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot communicate with chickengate: {e}", file=sys.stderr)
        print("  Is chickengate running?  sudo systemctl status chickengate", file=sys.stderr)
        sys.exit(1)

    if "error" in resp:
        print(f"error: {resp['error']}", file=sys.stderr)
        sys.exit(1 if resp.get("code", 0) != 409 else 2)

    # Poll until the door stops moving
    try:
        while True:
            resp = _gate_request({"cmd": "status", "door": args.door})
            state = resp.get("state", "unknown")
            if state not in ("moving", "opening", "closing"):
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n{args.door}: interrupted — door may still be moving", file=sys.stderr)
        sys.exit(0)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: lost contact with chickengate while waiting: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"{args.door}: {state}")


if __name__ == "__main__":
    main()

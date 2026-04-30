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
import logging
import sys

import door_control


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

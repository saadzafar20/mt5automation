#!/usr/bin/env python3
"""PlatAlgo Relay compatibility entry point.

The legacy Python GUI launchers were removed. Use the Electron app in `relay-ui/`.
"""
import sys


def main():
    print("The legacy Python GUI launch path has been removed.", file=sys.stderr)
    print("Use the Electron app instead:", file=sys.stderr)
    print("  1) cd relay-ui", file=sys.stderr)
    print("  2) npm install", file=sys.stderr)
    print("  3) npm run electron:dev", file=sys.stderr)
    print("For packaged builds: npm run electron:build:win or npm run electron:build:mac", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()

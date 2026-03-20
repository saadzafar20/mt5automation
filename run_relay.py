#!/usr/bin/env python3
"""PlatAlgo Relay — entry point.
Launches the React UI. Falls back to legacy PyQt6 GUI if React build is missing.
"""
import sys


def main():
    try:
        from relay_webview import main as webview_main
        webview_main()
    except Exception as e:
        print(f"React UI failed: {e}", file=sys.stderr)
        print("Falling back to legacy GUI...", file=sys.stderr)
        try:
            from relay_gui import main_legacy
            main_legacy()
        except Exception as e2:
            print(f"Legacy GUI also failed: {e2}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()

#!/bin/bash
# PlatAlgo Relay — macOS compatibility launcher
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  PlatAlgo Relay — macOS"
echo "========================================"
echo "Legacy Python GUI launch has been removed."
echo "Use the Electron app in relay-ui/ instead:"
echo ""
echo "  cd relay-ui"
echo "  npm install"
echo "  npm run electron:dev"
echo ""
echo "For packaged builds use:"
echo "  npm run electron:build:mac"
exit 1

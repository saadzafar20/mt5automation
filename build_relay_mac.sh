#!/bin/bash
# PlatAlgo Relay — Mac .app + .dmg Build Script
# Run from the project root on a Mac with Python 3.11+
set -e

APP_NAME="PlatAlgoRelay"
APP_VERSION="${VERSION:-1.0.0}"
BUNDLE_ID="com.platalgo.relay"

echo "================================================"
echo "  PlatAlgo Relay - Mac Build"
echo "================================================"
echo ""

# Ensure Python 3.11+
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        VER=$($cmd -c "import sys; print(sys.version_info >= (3, 11))" 2>/dev/null)
        if [ "$VER" = "True" ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "Error: Python 3.11 or newer is required."
    echo "Install via Homebrew: brew install python@3.11"
    exit 1
fi

echo "Using: $($PYTHON_CMD --version)"

# Create/activate venv
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv venv
fi
source venv/bin/activate

echo "Installing/updating dependencies..."
pip install --quiet --upgrade pip
pip install --quiet pyinstaller pillow keyring requests flask flask-cors

# Build React UI if dist doesn't exist
if [ ! -f "relay-ui/dist/index.html" ]; then
    echo "Building React UI..."
    if command -v npm &>/dev/null; then
        cd relay-ui && npm ci && npm run build && cd ..
    else
        echo "Error: npm not found. Install Node.js to build the UI."
        exit 1
    fi
fi

# Ensure config.json exists
[ -f config.json ] || echo '{}' > config.json

[ -f _version.py ] || echo "APP_VERSION = \"$APP_VERSION\"" > _version.py

echo "Building $APP_NAME.app..."
pyinstaller --noconfirm --windowed \
  --name "$APP_NAME" \
  --icon icon.png \
  --add-data "config.json:." \
  --add-data "_version.py:." \
  --add-data "relay-ui/dist:relay-ui/dist" \
  --hidden-import relay_webview \
  --hidden-import relay \
  --hidden-import flask \
  --hidden-import flask.json \
  --hidden-import flask_cors \
  --hidden-import keyring.backends.macOS \
  --hidden-import keyring.backends.fail \
  --collect-all flask \
  --collect-all flask_cors \
  --osx-bundle-identifier "$BUNDLE_ID" \
  run_relay.py

if [ ! -d "dist/$APP_NAME.app" ]; then
    echo ""
    echo "BUILD FAILED — check output above for errors"
    exit 1
fi

echo "Creating $APP_NAME.dmg..."
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "dist/$APP_NAME.app" \
  -ov -format UDZO \
  "dist/$APP_NAME.dmg"

echo ""
echo "================================================"
echo "  Build complete!"
echo "  App bundle : dist/$APP_NAME.app"
echo "  Disk image : dist/$APP_NAME.dmg"
echo "================================================"
echo ""
echo "To distribute: share dist/$APP_NAME.dmg"
echo "Users: open the DMG, drag $APP_NAME to Applications, launch it."

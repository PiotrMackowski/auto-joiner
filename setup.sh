#!/bin/bash
set -e

INSTALL_DIR="$HOME/zoom-autojoiner"
PLIST_NAME="com.autojoiner.zoom.plist"
REPO="https://github.com/PiotrMackowski/auto-joiner.git"

echo "Installing zoom-autojoiner..."

# Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Updating existing install..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    if [ -d "$INSTALL_DIR" ]; then
        echo "Error: $INSTALL_DIR exists but is not a git repo. Remove it first."
        exit 1
    fi
    git clone "$REPO" "$INSTALL_DIR"
fi

# Venv + deps
echo "Setting up Python environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

# State dir
mkdir -p "$HOME/.zoom-autojoiner"

# Fix paths in plist to match this machine
sed "s|/Users/piotr.mackowski|$HOME|g" "$INSTALL_DIR/$PLIST_NAME" > "$HOME/Library/LaunchAgents/$PLIST_NAME"

# Load (unload first if already running)
launchctl unload "$HOME/Library/LaunchAgents/$PLIST_NAME" 2>/dev/null || true
launchctl load "$HOME/Library/LaunchAgents/$PLIST_NAME"

echo ""
echo "Done. The auto-joiner is running."
echo ""
echo "On first run macOS will ask for Calendar access — grant it."
echo "Edit $INSTALL_DIR/config.yaml to change settings."
echo ""
echo "Commands:"
echo "  $INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/autojoiner.py check      # see upcoming meetings"
echo "  $INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/autojoiner.py join-next   # join next meeting now"
echo ""
echo "Logs: tail -f ~/.zoom-autojoiner/autojoiner.log"
echo ""
echo "To uninstall:"
echo "  launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  rm ~/Library/LaunchAgents/$PLIST_NAME"
echo "  rm -rf $INSTALL_DIR ~/.zoom-autojoiner"

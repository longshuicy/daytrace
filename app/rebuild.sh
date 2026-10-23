#!/usr/bin/env bash
# Build the desktop app, replace the copy in /Applications and launch it.
set -euo pipefail

cd "$(dirname "$0")"
npx tauri build

osascript -e 'quit app "daytrace"' 2>/dev/null || true
while pgrep -xq daytrace-app; do sleep 0.2; done

rm -rf /Applications/daytrace.app
cp -R src-tauri/target/release/bundle/macos/daytrace.app /Applications/
open /Applications/daytrace.app

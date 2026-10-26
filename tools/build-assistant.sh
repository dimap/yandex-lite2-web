#!/bin/sh
# Build the autonomous Linux ARM64 daemon, including when the panel runs on macOS.
set -eu
cd "$(dirname "$0")/.."
OUT="${1:-device/assistant/station-assistant-linux-arm64}"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT";; esac
mkdir -p "$(dirname "$OUT")"
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 "${GO:-go}" -C device/assistant build -trimpath -ldflags='-s -w' -o "$OUT" .

#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Deploying to Firebase..."
firebase deploy "$@"

echo "Done."

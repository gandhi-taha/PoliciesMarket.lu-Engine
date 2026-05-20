#!/usr/bin/env bash
# One-command data bootstrap for macOS / Linux.
#
# Thin wrapper around `python -m app.workers.bootstrap`. Run from the
# engine repo root. Sequences:
#   1. Install Playwright + Chromium (one-time).
#   2. Run `--learn` per insurer (one-time per insurer).
#   3. Walk the profile grid via the assisted collector (per insurer).
#   4. Upload CSVs to the engine.
#   5. Hit /v1/recommend to verify offers come back.
#
# A human still has to sit at the browser through the assisted step.
# The script removes the chore of remembering and sequencing commands.
#
# Usage:
#   ./bootstrap.sh                       # all enabled insurers
#   ./bootstrap.sh --providers foyer     # just one
#   ./bootstrap.sh --skip-install        # skip Playwright reinstall
#   ./bootstrap.sh --engine-url http://localhost:8000

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
exec python -m app.workers.bootstrap "$@"

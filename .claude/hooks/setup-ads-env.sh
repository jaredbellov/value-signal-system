#!/usr/bin/env bash
# SessionStart hook: prepare the claude-ads Python toolchain.
#
# The web execution environment is ephemeral, so Python deps and the
# Playwright browser must be (re)installed at the start of each session for
# the ads scripts (PDF reports, landing-page screenshots) to work.
#
# Notes:
# - PyPI installs work under the standard network policy.
# - The Playwright browser download (capture_screenshot.py / ads-dna) requires
#   a wider network policy; failures here are non-fatal and logged only.
set -u

REQ="$CLAUDE_PROJECT_DIR/.claude/skills/ads/requirements.txt"

if [ ! -f "$REQ" ]; then
  exit 0
fi

# Skip if already satisfied this session (cheap import probe).
if python3 -c "import reportlab, matplotlib, PIL, requests, playwright" >/dev/null 2>&1; then
  exit 0
fi

echo "[ads] Installing Python dependencies..." >&2
pip3 install --break-system-packages -q -r "$REQ" >&2 2>&1 \
  || pip3 install -q -r "$REQ" >&2 2>&1 \
  || echo "[ads] WARN: pip install failed" >&2

# Best-effort browser install for screenshot scripts (may be blocked by
# the network policy — non-fatal).
python3 -m playwright install chromium >/dev/null 2>&1 \
  || echo "[ads] NOTE: Playwright browser not installed (network policy?). PDF reports still work; screenshots need a wider network policy." >&2

exit 0

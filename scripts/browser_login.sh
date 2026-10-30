#!/usr/bin/env sh
# One-time logins in the persistent browser profile of the worker (ChatGPT, Google for Gemini,
# Perplexity). Starts a virtual display with the Playwright Chromium the worker uses and serves it as
# noVNC on port 6080; docker-compose.yml publishes that port on 127.0.0.1 only, so it is reachable
# through an SSH tunnel and nowhere else. The container ends when the browser window is closed.
# Run by the compose service "login" while the worker is stopped (both use the same profile).
set -eu

DISPLAY_NUMBER=99
SCREEN_SIZE=1366x768x24
VNC_PORT=5900
WEB_PORT=6080
START_PAGES="https://chatgpt.com/ https://gemini.google.com/ https://www.perplexity.ai/"

chromium=$(find "${PLAYWRIGHT_BROWSERS_PATH:?}" -path '*/chromium-*/chrome-linux*/chrome' -type f | head -n 1)
if [ -z "$chromium" ]; then
    echo "error: no Playwright Chromium under $PLAYWRIGHT_BROWSERS_PATH" >&2
    exit 1
fi

export DISPLAY=":$DISPLAY_NUMBER"
Xvfb "$DISPLAY" -screen 0 "$SCREEN_SIZE" -nolisten tcp &
xvfb_pid=$!
sleep 1
x11vnc -display "$DISPLAY" -rfbport "$VNC_PORT" -localhost -forever -shared -nopw -quiet >/dev/null 2>&1 &
vnc_pid=$!
websockify --web /usr/share/novnc "$WEB_PORT" "localhost:$VNC_PORT" >/dev/null 2>&1 &
web_pid=$!
trap 'kill "$web_pid" "$vnc_pid" "$xvfb_pid" 2>/dev/null || true' EXIT INT TERM

echo "Browser ready: open http://localhost:$WEB_PORT/vnc.html through the SSH tunnel."
echo "Log in on every tab, then close the browser window to finish."

# --password-store=basic matches Playwright, so the worker can read the cookies stored here.
# shellcheck disable=SC2086
"$chromium" \
    --user-data-dir="$BROWSER_PROFILE_DIR" \
    --password-store=basic \
    --no-sandbox \
    --no-first-run \
    --no-default-browser-check \
    --lang=de-DE \
    --window-position=0,0 \
    --window-size=1366,768 \
    $START_PAGES 2>/tmp/chromium.log || {
    tail -n 20 /tmp/chromium.log >&2
    exit 1
}

echo "Browser closed, profile saved in $BROWSER_PROFILE_DIR."

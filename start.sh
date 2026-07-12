#!/usr/bin/env bash
# Aura one-click launcher (Linux/macOS): server + HTTPS tunnel.
set -e
cd "$(dirname "$0")/server"

if [ -n "$OPENROUTER_API_KEY" ]; then
  export AURA_PROVIDER=${AURA_PROVIDER:-openrouter}
elif [ -n "$GEMINI_API_KEY" ]; then
  export AURA_PROVIDER=${AURA_PROVIDER:-gemini}
elif [ -z "$OPENAI_API_KEY" ]; then
  echo "!! Set an API key first, e.g.:  export OPENROUTER_API_KEY=sk-or-..."
  echo "   (or OPENAI_API_KEY / GEMINI_API_KEY)"
  exit 1
fi
echo ">>> AI provider: ${AURA_PROVIDER:-openai}"

pip install -q -r requirements.txt

# HTTPS tunnel (phones require https for mic/camera)
if command -v ngrok >/dev/null 2>&1; then
  (ngrok http 8000 --log=stdout > /tmp/aura-ngrok.log &)
  sleep 3
  URL=$(curl -s localhost:4040/api/tunnels | python3 -c "import sys,json;print(json.load(sys.stdin)['tunnels'][0]['public_url'])" 2>/dev/null || true)
  [ -n "$URL" ] && echo "" && echo ">>> Open this on your phone:  $URL" && echo ""
elif command -v cloudflared >/dev/null 2>&1; then
  (cloudflared tunnel --url http://localhost:8000 &)
  echo ">>> Watch above for the https://....trycloudflare.com URL — open it on your phone."
else
  echo "!! No tunnel tool found. Install ngrok (ngrok.com) or cloudflared for phone access."
fi

exec uvicorn main:app --host 0.0.0.0 --port 8000

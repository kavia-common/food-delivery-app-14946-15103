```bash
#!/usr/bin/env bash
set -euo pipefail

# Detect privilege level
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

# Set workspace path
WORKSPACE="/home/kavia/workspace/code-generation/food-delivery-app-14946-15103/NotificationService"
cd "$WORKSPACE"

# === COMMAND: INSTALL ===
# Set NODE_ENV for all shell sessions (idempotent)
echo 'export NODE_ENV=development' | $SUDO tee /etc/profile.d/react_env.sh > /dev/null

# === COMMAND: SCAFFOLD ===
# Scaffold React app only if not already initialized
if [ ! -f "$WORKSPACE/package.json" ]; then
    npx --yes create-react-app . --use-npm --template cra-template --quiet
fi

# === COMMAND: DEPS ===
# Install essential React dependencies (idempotent, minimal output)
npm i --legacy-peer-deps --silent --no-progress
npm ls react react-dom --silent >/dev/null 2>&1 || npm i react react-dom --silent --no-progress

# === COMMAND: BUILD ===
npm run build --silent

# === COMMAND: TEST ===
# Use CI=true for full non-interactive runs in React
CI=true npm test --watchAll=false --silent

# === COMMAND: START ===
# Robustly start the React dev server in the background on port 3000 and store PID
PIDFILE=".react_pid"
[ -f "$PIDFILE" ] && (kill $(cat "$PIDFILE") 2>/dev/null || true; rm -f "$PIDFILE")
nohup npm start -- --port=3000 >/dev/null 2>&1 &
echo $! > "$PIDFILE"

# === COMMAND: VALIDATE ===
# Full lifecycle: build, start, verify, stop
VALIDATE_PIDFILE=".react_validate_pid"
npm run build --silent
[ -f "$VALIDATE_PIDFILE" ] && (kill $(cat "$VALIDATE_PIDFILE") 2>/dev/null || true; rm -f "$VALIDATE_PIDFILE")
nohup npm start -- --port=3000 >/dev/null 2>&1 &
echo $! > "$VALIDATE_PIDFILE"
sleep 7
if ! lsof -i :3000 >/dev/null 2>&1; then
    echo "Error: React dev server not running on port 3000" >&2; kill $(cat "$VALIDATE_PIDFILE") 2>/dev/null || true; rm -f "$VALIDATE_PIDFILE"; exit 1
fi
if ! curl -sf http://localhost:3000 > /dev/null; then
    echo "Error: React app not healthy on http://localhost:3000" >&2; kill $(cat "$VALIDATE_PIDFILE") 2>/dev/null || true; rm -f "$VALIDATE_PIDFILE"; exit 2
fi
kill $(cat "$VALIDATE_PIDFILE") 2>/dev/null || true; rm -f "$VALIDATE_PIDFILE"

# === COMMAND: STOP ===
# Cleanly stop the React dev server started by START
PIDFILE=".react_pid"
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        kill "$PID" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
fi
```
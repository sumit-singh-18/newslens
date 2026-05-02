#!/usr/bin/env bash
# Serves frontend/ over HTTP. Prints the URL (may differ if default port is busy).
# Uses Python for port checks (works without `lsof`; portable Linux/macOS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"

port_can_bind() {
  python3 - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
else:
    sys.exit(0)
finally:
    s.close()
PY
}

try_ports="${NEWSLENS_FRONTEND_PORT:-5173} 8080 5174 3000"
for P in $try_ports; do
  if ! port_can_bind "$P"; then
    echo "NewsLens: port ${P} is already in use — skipping." >&2
    continue
  fi
  echo "" >&2
  echo "  NewsLens dashboard →  http://127.0.0.1:${P}" >&2
  echo "  (Ctrl+C to stop)" >&2
  echo "" >&2
  exec python3 -m http.server "${P}" --bind 127.0.0.1
done

echo "" >&2
echo "NewsLens: no free port in: ${try_ports}" >&2
echo "Free one with:  kill \$(lsof -t -i :5173)   # macOS/Linux (optional)" >&2
exit 1

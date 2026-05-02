#!/usr/bin/env bash
# Smoke-test API + static frontend (defaults: API 8000, tries 5173 then 8080 for HTML).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${NEWSLENS_API_URL:-http://127.0.0.1:8000}"
echo "Checking API: ${API}/health"
code=$(curl -sf -o /tmp/nl-health.json -w "%{http_code}" "${API}/health" || echo "000")
if [[ "$code" == "200" ]] && grep -q '"success":true' /tmp/nl-health.json 2>/dev/null; then
  echo "  OK (HTTP ${code})"
else
  echo "  FAIL (HTTP ${code}) — start: ./scripts/serve-backend.sh" >&2
  exit 1
fi

found=""
for P in 5173 8080 5174 3000; do
  code=$(curl -sf -o /tmp/nl-fe.html -w "%{http_code}" "http://127.0.0.1:${P}/" || echo "000")
  if [[ "$code" == "200" ]] && grep -q "NewsLens" /tmp/nl-fe.html 2>/dev/null; then
    echo "Checking frontend: http://127.0.0.1:${P}/"
    echo "  OK (HTTP ${code}, title matches)"
    bc=$(curl -sf -o /tmp/nl-bundle.js -w "%{http_code}" "http://127.0.0.1:${P}/bundle.js" || echo "000")
    if [[ "$bc" == "200" ]]; then
      echo "  bundle.js OK (HTTP ${bc})"
    else
      echo "  bundle.js FAIL (HTTP ${bc})" >&2
      exit 1
    fi
    found=1
    break
  fi
done

if [[ -z "$found" ]]; then
  echo "No NewsLens static server found on 5173/8080/5174/3000." >&2
  echo "Start: ./scripts/serve-frontend.sh" >&2
  exit 1
fi

echo "All checks passed."

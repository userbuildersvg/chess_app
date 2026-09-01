#!/bin/sh
# Langflow startup for the Render deployment.
#
# This is the inline python block from docker-compose.yml's langflow service,
# lifted into a real script: Render builds from a Dockerfile alone, with no
# compose file and no bind mounts, so the flow JSON is baked into the image
# (see Dockerfile.langflow) and imported from disk at boot instead.
#
# The one behavioural difference from the compose version is auth. Production
# runs with LANGFLOW_AUTO_LOGIN=false, so every /api/v1/* call needs an
# x-api-key header - including the ones this script makes to import the flow.

set -e

langflow run --host 0.0.0.0 --port "${PORT:-7860}" &
LANGFLOW_PID=$!

python3 - <<'PYEOF'
import json, time, os, gzip, urllib.request, urllib.error

base = f"http://localhost:{os.environ.get('PORT', '7860')}"
api_key = os.environ.get("LANGFLOW_API_KEY", "")

# Once auto-login is off, unauthenticated calls get 401 - so the import script
# has to authenticate to Langflow exactly like the backend does.
AUTH = {"x-api-key": api_key} if api_key else {}


def request(url, data=None, method="GET", timeout=10):
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in AUTH.items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        status = resp.status
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return status, raw


def fetch_json(url, timeout=10):
    _, raw = request(url, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


if not api_key:
    print("WARNING: LANGFLOW_API_KEY is unset - flow import will fail "
          "while LANGFLOW_AUTO_LOGIN=false")

for _ in range(60):
    try:
        request(f"{base}/api/v1/flows/", timeout=3)
        break
    except Exception:
        time.sleep(2)
else:
    print("Langflow did not become ready in time")
    raise SystemExit(0)

try:
    flows = fetch_json(f"{base}/api/v1/flows/")
except urllib.error.HTTPError as e:
    print("Could not list flows:", e.code, e.read().decode()[:200])
    raise SystemExit(0)

if any(f.get("name") == "Chess" for f in flows):
    print("Chess flow already exists, skipping import")
    raise SystemExit(0)

with open("/app/flows-template/Chess.json") as f:
    raw = f.read().replace("__GEMINI_API_KEY__", os.environ.get("GEMINI_API_KEY", ""))
flow_data = json.loads(raw)
for field in ("id", "folder_id", "user_id", "created_at", "updated_at"):
    flow_data.pop(field, None)

try:
    status, _ = request(
        f"{base}/api/v1/flows/",
        data=json.dumps(flow_data).encode(),
        method="POST",
    )
    print("Chess flow imported:", status)
except urllib.error.HTTPError as e:
    print("Chess flow import FAILED:", e.code, e.read().decode()[:300])
PYEOF

wait $LANGFLOW_PID

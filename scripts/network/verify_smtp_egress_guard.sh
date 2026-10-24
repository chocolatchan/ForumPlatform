#!/bin/sh
# Reproducible, non-destructive verification of the per-container SMTP guard.
#
# Creates temporary Docker networks/containers and a controlled TCP listener.
# It never sends email and does not restart or inspect application containers
# beyond using the existing Mailpit service for the allowed-route probe.
set -eu

IMAGE="${SMTP_GUARD_TEST_IMAGE:-forumplatform-user-service:latest}"
MAILPIT_NETWORK="${SMTP_GUARD_MAILPIT_NETWORK:-forumplatform_forum_network}"
NET="smtp_guard_verify_net_$$"
LISTENER="smtp_guard_verify_listener_$$"
GUARDED="smtp_guard_verify_probe_$$"
TMP_DIR="$(mktemp -d)"

cleanup() {
    docker rm -f "$GUARDED" >/dev/null 2>&1 || true
    docker rm -f "$LISTENER" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

cat >"$TMP_DIR/listener.py" <<'PY'
import socketserver
import threading

PORTS = [25, 465, 587, 1025, 2525, 8080]


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.sendall(b"220 controlled-listener ESMTP\r\n")
        except OSError:
            pass


for port in PORTS:
    server = socketserver.ThreadingTCPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

print("controlled listener ready", flush=True)
threading.Event().wait()
PY

cat >"$TMP_DIR/probe.py" <<'PY'
import os
import socket
import sys
import urllib.request

LISTENER_IP = os.environ["LISTENER_IP"]
SMTP_PORTS = [25, 465, 587, 1025, 2525]
failures = []


def can_connect(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check(label: str, actual: bool, expected: bool) -> None:
    status = "PASS" if actual == expected else "FAIL"
    print(f"{status}: {label} (actual={actual}, expected={expected})")
    if actual != expected:
        failures.append(label)


check("Mailpit SMTP mailpit:1025 reachable", can_connect("mailpit", 1025), True)
check("DNS resolution for mailpit", bool(socket.getaddrinfo("mailpit", 1025)), True)

try:
    with urllib.request.urlopen(
        "http://mailpit:8025/api/v1/messages?limit=1", timeout=3
    ) as response:
        ui_ok = response.status == 200
except Exception as exc:
    print(f"Mailpit UI/API probe error: {type(exc).__name__}: {exc}")
    ui_ok = False
check("Mailpit UI/API mailpit:8025 reachable", ui_ok, True)

for port in SMTP_PORTS:
    check(
        f"Non-Mailpit SMTP {LISTENER_IP}:{port} blocked",
        can_connect(LISTENER_IP, port),
        False,
    )

check(
    "Non-SMTP control port 8080 remains reachable",
    can_connect(LISTENER_IP, 8080),
    True,
)
check("Mailpit port 25 not allowed", can_connect("mailpit", 25), False)

if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("All guard probes passed.")
PY

docker image inspect "$IMAGE" >/dev/null
docker network inspect "$MAILPIT_NETWORK" >/dev/null

docker network create "$NET" >/dev/null
docker run -d --name "$LISTENER" --network "$NET" \
    -v "$TMP_DIR/listener.py:/listener.py:ro" \
    python:3.14-slim python /listener.py >/dev/null

LISTENER_IP="$(docker inspect "$LISTENER" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"

docker run -d --name "$GUARDED" --cap-add NET_ADMIN \
    --network "$MAILPIT_NETWORK" \
    -e ENVIRONMENT=development \
    -e SMTP_GUARD_ALLOWED_HOST=mailpit \
    -e SMTP_GUARD_ALLOWED_PORT=1025 \
    -v "$TMP_DIR/probe.py:/probe.py:ro" \
    --entrypoint /app/scripts/network/smtp_egress_guard.sh \
    "$IMAGE" sleep 300 >/dev/null

docker network connect "$NET" "$GUARDED"
sleep 1

echo "controlled_listener_ip=$LISTENER_IP"
echo "--- guard startup ---"
docker logs "$GUARDED" 2>&1 | sed -n '1,20p'
echo "--- OUTPUT chain ---"
docker exec "$GUARDED" iptables -S OUTPUT
echo "--- probe results ---"
docker exec -e LISTENER_IP="$LISTENER_IP" "$GUARDED" python /probe.py

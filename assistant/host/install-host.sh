#!/usr/bin/env bash
# One-time setup on the Docker host for the dashboard assistant. Run as root from the project folder:
#
#   sudo DATA_DIR=/path/to/dashboard-data ASSISTANT_DIR=/path/to/assistant-home ./assistant/host/install-host.sh
#
# It makes the assistant's folder, a shared key the dashboard and the assistant use to talk to each other,
# and a systemd path unit that runs the build helper when the dashboard drops a build request.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
: "${DATA_DIR:?Set DATA_DIR to the dashboard data folder}"
: "${ASSISTANT_DIR:?Set ASSISTANT_DIR to the folder the assistant keeps its home in}"
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

mkdir -p "$ASSISTANT_DIR" "$DATA_DIR/assistant-build"
if [[ ! -s "$DATA_DIR/assistant.key" ]]; then
  umask 077
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$DATA_DIR/assistant.key"
fi
cp "$DATA_DIR/assistant.key" "$ASSISTANT_DIR/assistant.key"
chmod 600 "$DATA_DIR/assistant.key" "$ASSISTANT_DIR/assistant.key"
chown 1000:1000 "$DATA_DIR/assistant.key" "$ASSISTANT_DIR" "$ASSISTANT_DIR/assistant.key" "$DATA_DIR/assistant-build"

install -m 755 "$SRC/fd-assistant-build.sh" /usr/local/bin/fd-assistant-build
cat > /etc/systemd/system/fd-assistant-build.service <<EOF
[Unit]
Description=Family Dashboard: build the image the assistant's Apply asked for

[Service]
Type=oneshot
Environment=FD_REQUESTS=$DATA_DIR/assistant-build
Environment=FD_WORK=$ASSISTANT_DIR/work
ExecStart=/usr/local/bin/fd-assistant-build
TimeoutStartSec=30min
EOF
cat > /etc/systemd/system/fd-assistant-build.path <<EOF
[Unit]
Description=Family Dashboard: watch for build requests from the assistant

[Path]
PathExists=$DATA_DIR/assistant-build/request

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now fd-assistant-build.path >/dev/null
echo "Host part of the assistant is set up."

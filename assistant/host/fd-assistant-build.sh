#!/usr/bin/env bash
# Runs on the Docker host, as root, when the dashboard drops a build request (after someone pressed Apply in
# Settings → Assistant). It builds the dashboard image from exactly the commit named in the request, taken from
# the assistant's code copy, and writes how it went where the dashboard can read it. It never deploys: the
# running dashboard changes only when someone updates the stack, as with any other update.
#
#   REQUESTS  the dashboard's data folder, subfolder assistant-build (request in, status out)
#   WORK      the assistant's code copy (a git repository)
set -u
REQUESTS="${FD_REQUESTS:?set FD_REQUESTS}"
WORK="${FD_WORK:?set FD_WORK}"
IMAGE="${FD_IMAGE:-family-dashboard:latest}"
REQ="$REQUESTS/request"
STATUS="$REQUESTS/status.json"

[ -f "$REQ" ] || exit 0
commit="$(head -c 200 "$REQ" | tr -dc '0-9a-f')"
rm -f "$REQ"
status() { printf '{"state": "%s", "commit": "%s", "at": %s, "message": "%s"}\n' "$1" "$commit" "$(date +%s)" "$2" > "$STATUS.tmp" && mv "$STATUS.tmp" "$STATUS"; chown 1000:1000 "$STATUS" 2>/dev/null || true; }

if [ "${#commit}" -ne 40 ]; then status failed "The build request didn't name a commit."; exit 0; fi
if ! git -c safe.directory='*' -C "$WORK" cat-file -e "$commit^{commit}" 2>/dev/null; then
  status failed "The assistant's copy doesn't have that commit."; exit 0
fi
status building "Building the new version."
docker tag "$IMAGE" "${IMAGE%:*}:previous" 2>/dev/null || true
if git -c safe.directory='*' -C "$WORK" archive --format=tar "$commit" | docker build -q -t "$IMAGE" - > "$REQUESTS/build.log" 2>&1; then
  status built "Built. Update the stack in Portainer (Re-pull image off) to put it on the TV."
else
  status failed "The build failed. The previous version is untouched; see build.log in the data folder."
fi

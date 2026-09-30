#!/usr/bin/env bash
# Update PostersPlus Studio to the newest image, or roll back to the previous one.
#
#   bash ~/docker/postersplus/update.sh              # update
#   bash ~/docker/postersplus/update.sh --rollback   # back to the image before the last update
#
# Server copy lives at /home/serverino/docker/postersplus/update.sh (repo: deploy/update.sh).
set -euo pipefail
cd "$(dirname "$0")"

IMAGE=ghcr.io/pietrocanovai/postersplus
CONTAINER=postersplus

if [ "${1:-}" = "--rollback" ]; then
  if ! docker image inspect "$IMAGE:previous" >/dev/null 2>&1; then
    echo "No previous image to roll back to." >&2
    exit 1
  fi
  echo "Rolling back to the previous image..."
  docker tag "$IMAGE:previous" "$IMAGE:latest"
  docker compose up -d
else
  # Keep the running image under a tag, so pruning can't delete it and
  # --rollback can bring it back.
  current=$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)
  echo "Pulling $IMAGE:latest..."
  docker compose pull
  new=$(docker image inspect --format '{{.Id}}' "$IMAGE:latest")
  if [ -n "$current" ] && [ "$current" != "$new" ]; then
    docker tag "$current" "$IMAGE:previous"
  elif [ "$current" = "$new" ]; then
    echo "Already up to date."
  fi
  docker compose up -d
fi

echo -n "Waiting for the server to be healthy"
for _ in $(seq 1 60); do
  status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || echo missing)
  if [ "$status" = "healthy" ]; then break; fi
  echo -n "."
  sleep 3
done
echo
if [ "$status" != "healthy" ]; then
  echo "Not healthy after 3 minutes (status: $status). Recent log:" >&2
  docker logs --tail 30 "$CONTAINER" >&2
  echo "Roll back with: bash $(pwd)/update.sh --rollback" >&2
  exit 1
fi

version=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" | sed -n 's/^STUDIO_GIT_COMMIT=//p' | cut -c1-7)
built=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" | sed -n 's/^STUDIO_BUILD_DATE=//p')
echo "PostersPlus is healthy. Version ${version:-unknown} (built ${built:-unknown})."

docker image prune -f >/dev/null

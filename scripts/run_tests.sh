#!/usr/bin/env bash
# Run the pytest suite inside the built image (offline, no network needed).
set -euo pipefail
PROJ=/opt/pixoo-local
IMAGE="${PIXOO_IMAGE:-pixoo-local:latest}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[tests] image $IMAGE missing — building"
    ( cd "$PROJ" && docker compose build )
fi

echo "[tests] running pytest in $IMAGE"
exec docker run --rm --network none \
    -e PYTHONPATH=/opt/pixoo-local \
    -e PIXOO_STATUS_DIR=/tmp/pixoo-status \
    -v "$PROJ:/opt/pixoo-local" \
    -w /opt/pixoo-local \
    --entrypoint pytest \
    "$IMAGE" -q

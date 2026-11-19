#!/usr/bin/env bash
# Golden round trip of the 3D Basic Conversion image (see tests/test_3dbasic_golden_round_trip.py).
#
# Builds the image (or uses one), starts an S3 stand-in (moto) and the image under the Lambda runtime
# interface emulator, runs the golden suite against them, and removes both. Run from anywhere:
#
#   tests/golden/run_golden.sh                       # build the image from this directory and test it
#   GOLDEN_IMAGE=my-tag GOLDEN_SKIP_BUILD=1 tests/golden/run_golden.sh   # test an image already built
#   GOLDEN_DUMP_DIR=/tmp/golden-a tests/golden/run_golden.sh             # also write every output file
#   GOLDEN_STEP=1 tests/golden/run_golden.sh         # build and test the image with STEP input
#
# Needs: docker, and a Python with pytest, moto[server], boto3, trimesh and Pillow (the CI job installs
# them next to the image's own hashed requirements).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAMBDA_DIR="$(cd "$HERE/../.." && pwd)"
IMAGE="${GOLDEN_IMAGE:-vams-3dbasic-golden}"
PYTHON="${PYTHON:-python3}"
RIE_PORT="${GOLDEN_RIE_PORT:-9000}"
S3_PORT="${GOLDEN_S3_PORT:-5099}"
NAME="vams-3dbasic-golden-$$"
LOG_DIR="$(mktemp -d)"

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    if [[ -n "${MOTO_PID:-}" ]]; then kill "$MOTO_PID" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

STEP_ARG="false"
if [[ "${GOLDEN_STEP:-}" == "1" ]]; then STEP_ARG="true"; fi
if [[ -z "${GOLDEN_SKIP_BUILD:-}" ]]; then
    docker build --platform linux/amd64 --build-arg "ENABLE_STEP_INPUT=${STEP_ARG}" -t "$IMAGE" "$LAMBDA_DIR"
fi

# What the image holds: the Assimp tool runs, and Open CASCADE (cascadio and its bundled libraries) is
# present only in the image built with STEP input.
docker run --rm --platform linux/amd64 --entrypoint /opt/assimp/bin/assimp "$IMAGE" version >/dev/null
OCCT_FILES="$(docker run --rm --platform linux/amd64 --entrypoint python "$IMAGE" -c \
    "import glob, site; print(len([p for d in site.getsitepackages() for p in glob.glob(d + '/cascadio*') + glob.glob(d + '/**/libTK*', recursive=True)]))")"
if [[ "$STEP_ARG" == "true" && "$OCCT_FILES" == "0" ]]; then
    echo "The STEP image holds no cascadio" >&2; exit 1
fi
if [[ "$STEP_ARG" != "true" && "$OCCT_FILES" != "0" ]]; then
    echo "The default image holds Open CASCADE files ($OCCT_FILES)" >&2; exit 1
fi

# The stand-in listens on the docker bridge gateway only (not on every interface) and the emulator port
# is published on the loopback only, so neither is reachable from the network. The container reaches the
# stand-in through the gateway, an IP address, so the S3 client
# addresses buckets by path rather than by a host name that does not resolve.
GATEWAY="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')"
"$PYTHON" -m moto.server -H "$GATEWAY" -p "$S3_PORT" >"$LOG_DIR/moto.log" 2>&1 &
MOTO_PID=$!
for _ in $(seq 1 60); do
    if curl -s -o /dev/null "http://${GATEWAY}:${S3_PORT}/"; then break; fi
    sleep 0.5
done

docker run -d --name "$NAME" --platform linux/amd64 -p "127.0.0.1:${RIE_PORT}:8080" \
    -e AWS_REGION=us-east-1 -e AWS_DEFAULT_REGION=us-east-1 \
    -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test \
    -e AWS_LAMBDA_FUNCTION_MEMORY_SIZE=5308 -e "ENABLE_STEP_INPUT=${STEP_ARG}" \
    -e "AWS_ENDPOINT_URL_S3=http://${GATEWAY}:${S3_PORT}" \
    "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
    if curl -s -o /dev/null "http://127.0.0.1:${RIE_PORT}/"; then break; fi
    sleep 0.5
done

status=0
GOLDEN_STEP="${GOLDEN_STEP:-}" \
GOLDEN_RIE_URL="http://127.0.0.1:${RIE_PORT}/2015-03-31/functions/function/invocations" \
GOLDEN_S3_ENDPOINT="http://${GATEWAY}:${S3_PORT}" \
    "$PYTHON" -m pytest -q -m golden "$LAMBDA_DIR/tests/test_3dbasic_golden_round_trip.py" "$@" || status=$?

if [[ $status -ne 0 ]]; then
    echo "--- image log (last 60 lines) ---"
    docker logs "$NAME" 2>&1 | tail -n 60
fi
exit $status

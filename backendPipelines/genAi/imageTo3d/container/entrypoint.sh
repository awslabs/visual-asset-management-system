#!/bin/bash
set -e

# HF transport hardening: fall back to the standard HF transport rather than the xet client.
export HF_HUB_DISABLE_XET=1
export HF_XET_DISABLE=1

# Sweep stale download artifacts from prior crashed runs. The HF cache is on a shared EFS mount, so
# a lock left behind by one instance blocks every later run on every instance.
HF_CACHE=${HF_HOME:-/mnt/efs/imageto3d-models/hf_cache}
if [ -d "$HF_CACHE" ]; then
    find "$HF_CACHE" -name "*.incomplete" -delete 2>/dev/null || true
    find "$HF_CACHE" -name "*.lock"       -delete 2>/dev/null || true
    rm -rf "$HF_CACHE/xet" 2>/dev/null || true
fi

cd /opt/ml/code

# Some model repos rasterize their UV atlas through a GL context that expects a display server, and
# a headless Batch host has none: the context request fails with "XOpenDisplay: cannot open display"
# and the texture is lost after the mesh is already generated. A virtual framebuffer backs that X11
# path. Xvfb runs as a background process with an explicit display number, and the entrypoint waits
# for the socket before handing over -- `xvfb-run --auto-servernum` blocks indefinitely under Batch's
# process supervision, which stalls the job rather than failing it.
if command -v Xvfb >/dev/null 2>&1 && [ -z "${DISPLAY:-}" ]; then
    Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
    for _ in $(seq 1 20); do
        [ -e /tmp/.X11-unix/X99 ] && break
        sleep 0.5
    done
    if [ -e /tmp/.X11-unix/X99 ]; then
        export DISPLAY=:99
        echo "Virtual framebuffer ready on DISPLAY=$DISPLAY"
    else
        echo "Virtual framebuffer did not start; continuing without one" >&2
    fi
fi

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"

#!/usr/bin/env bash
#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
#
# Container-level format check for the Potree preview pipeline (preview/pcPotreeViewer), kept outside
# the pipeline's own folder so it never changes the deployed image or Lambda assets.
#
# It runs public sample files through the PDAL image with the exact `pdal translate` command the PDAL
# stage runs (pipelines/pdal/pipeline.py), then through the Potree image with the PotreeConverter flags
# the POTREE stage runs (pipelines/potree/pipeline.py), and writes one TSV row per file: PDAL exit
# status, intermediate LAZ size, LAS version / point format, point count, colour and intensity ranges,
# PotreeConverter exit status and octree point count. Run it once per PDAL image and diff the TSVs.
#
# LAS/LAZ inputs skip the PDAL stage in a real run; they are also translated here so the two PDAL
# versions can be compared on them, and the POTREE column for them is the direct (production) path.
#
# Usage:
#   pcPotreeViewer-formats.sh fetch <samples-dir>
#   pcPotreeViewer-formats.sh run <pdal-image> <potree-image> <samples-dir> <out-dir>
#   PDAL_LAS_OPTIONS="" pcPotreeViewer-formats.sh run ...   # an image built before #1203 (no options)
#
# Build the images from backendPipelines/preview/pcPotreeViewer/container:
#   docker build -f Dockerfile_PDAL -t vams-pdal:local .
#   docker build -f Dockerfile_Potree -t vams-potree:local .
#
# Samples (fetched at pinned commits):
#   E57 - asmaloney/libE57Format-test-data (CC0-1.0). self/ColouredCube*.e57 carry the CompressedVector
#         index packet current libE57Format writers emit, which PDAL 2.7.0 rejected (#1203).
#   LAS/LAZ/PLY - PDAL/PDAL test/data at tag 2.10.2 (BSD-3-Clause). autzen_trim is real airborne LiDAR
#         with RGB and intensity; las_with_several_extra_byte_bloc.laz carries several Extra Bytes VLRs
#         in point format 8, which PotreeConverter does not read.
#   potree/lion_takanawa_2-1-1-1.laz - potree/potree at the commit web/customInstalls/potree pins
#         (BSD-2-Clause): the #783 file, whose OriginId Extra Bytes description fills all 32 bytes.
#   autzen_trim.ply - written from autzen_trim.las by the PDAL image under test's writers.ply, as a
#         larger real-world PLY than the PDAL test PLYs.
set -euo pipefail

E57_REPO="https://raw.githubusercontent.com/asmaloney/libE57Format-test-data/24201531715945ba7d49af6dfcb8e07b6b9b6b7f"
PDAL_REPO="https://raw.githubusercontent.com/PDAL/PDAL/27008f6241be44585c866a29dfc13bb16d678dab/test/data"

E57_FILES=(
  self/ColouredCubeDouble.e57
  self/ColouredCubeFloat.e57
  reference/bunnyDouble.e57
  reference/bunnyInt32.e57
  3rdParty/las2e57/ColourRepresentation.e57
  self/PinholeImageWithDistortionParameters.e57
)
POTREE_783_URL="https://raw.githubusercontent.com/potree/potree/f6ac2d3b3786aed9f3bd7b240d9f8644e80f43fd/pointclouds/lion_takanawa_ept_laz/ept-data/2-1-1-1.laz"
POTREE_783_FILE="potree/lion_takanawa_2-1-1-1.laz"
PDAL_FILES=(
  las/autzen_trim.las
  laz/autzen_trim.laz
  laz/las_with_several_extra_byte_bloc.laz
  ply/simple_binary.ply
  ply/simple_text.ply
)

fetch() {
  local dir="$1" f
  mkdir -p "$dir"
  for f in "${E57_FILES[@]}"; do
    mkdir -p "$dir/$(dirname "$f")"
    curl -sSfL -o "$dir/$f" "$E57_REPO/$f"
  done
  for f in "${PDAL_FILES[@]}"; do
    mkdir -p "$dir/$(dirname "$f")"
    curl -sSfL -o "$dir/$f" "$PDAL_REPO/$f"
  done
  mkdir -p "$dir/$(dirname "$POTREE_783_FILE")"
  curl -sSfL -o "$dir/$POTREE_783_FILE" "$POTREE_783_URL"
  echo "fetched into $dir"
}

# pdal_in <image> <args...>: run pdal inside the PDAL image, as the pipeline's runtime user would.
pdal_in() {
  local image="$1"; shift
  docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$SAMPLES:/in:ro" -v "$OUT:/out" \
    --entrypoint conda "$image" run --no-capture-output -n pdal pdal "$@"
}

# summarize <info.json> <laz>: LAS version/format from the LAZ header, then the count and the colour and
# intensity ranges from pdal info --stats.
summarize() {
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    print("-\t-\t-\t-"); sys.exit(0)
# LAS header: version major/minor at bytes 24-25, point data record format at byte 104 (the top bits
# flag LAZ compression).
try:
    with open(sys.argv[2], "rb") as handle:
        header = handle.read(105)
    fmt = f"{header[24]}.{header[25]}/pdrf{header[104] & 0x3F}"
except Exception:
    fmt = "?"
stats = {s["name"]: s for s in doc.get("stats", {}).get("statistic", [])}
count = stats.get("X", {}).get("count", "-")
def rng(name):
    s = stats.get(name)
    return f"{s['minimum']:g}-{s['maximum']:g}" if s else "none"
print(f"{fmt}\t{count}\tR{rng('Red')} G{rng('Green')} B{rng('Blue')}\t{rng('Intensity')}")
PY
}

run() {
  local pdal_image="$1" potree_image="$2"
  SAMPLES="$(cd "$3" && pwd)"; mkdir -p "$4"; OUT="$(cd "$4" && pwd)"
  local tsv="$OUT/results.tsv" f base laz rc bytes octree prc points source
  mkdir -p "$OUT/laz" "$OUT/potree" "$OUT/info" "$OUT/logs"

  if [ -f "$SAMPLES/las/autzen_trim.las" ] && [ ! -f "$OUT/autzen_trim.ply" ]; then
    pdal_in "$pdal_image" translate -i /in/las/autzen_trim.las -o /out/autzen_trim.ply > "$OUT/logs/make-ply.log" 2>&1
  fi

  printf 'file\tpdal_exit\tlaz_bytes\tlas_format\tpoints\tcolour\tintensity\tpotree_exit\toctree_points\tpdal_last_line\n' > "$tsv"
  while IFS= read -r f; do
    base="$(echo "$f" | tr '/.' '__')"
    laz="$OUT/laz/$base.laz"
    rm -f "$laz"
    if [ "$f" = "autzen_trim.ply" ]; then source="/out/autzen_trim.ply"; else source="/in/$f"; fi
    # The PDAL stage's command (pipelines/pdal/pipeline.py: pdal translate -i <in> -o <out>.laz plus
    # its LAS writer options). For an image built before #1203, run with PDAL_LAS_OPTIONS="" to get
    # the command that image's stage ran.
    set +e
    # shellcheck disable=SC2086 # the options are a word list
    pdal_in "$pdal_image" translate -i "$source" -o "/out/laz/$base.laz" \
      ${PDAL_LAS_OPTIONS---writers.las.minor_version=2 --writers.las.dataformat_id=3} > "$OUT/logs/$base.pdal.log" 2>&1
    rc=$?
    set -e
    bytes="$(stat -c %s "$laz" 2>/dev/null || echo none)"
    if [ "$rc" = 0 ] && [ "$bytes" != none ] && [ "$bytes" != 0 ]; then
      pdal_in "$pdal_image" info --stats "/out/laz/$base.laz" > "$OUT/info/$base.json" 2>/dev/null || true
    else
      : > "$OUT/info/$base.json"
    fi
    # What PotreeConverter is given: the PDAL output for E57/PLY, the file itself for LAS/LAZ.
    case "$f" in
      *.las|*.laz) octree_src="$SAMPLES/$f"; mount_src="/src/$f"; mount_dir="$SAMPLES" ;;
      *) octree_src="$laz"; mount_src="/src/laz/$base.laz"; mount_dir="$OUT" ;;
    esac
    octree="$OUT/potree/$base"
    rm -rf "$octree"; mkdir -p "$octree"
    if [ -s "$octree_src" ]; then
      set +e
      docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$mount_dir:/src:ro" -v "$octree:/octree" -w /PotreeConverterBuild \
        --entrypoint ./PotreeConverter "$potree_image" \
        --source "$mount_src" --outdir /octree --encoding UNCOMPRESSED --method poisson \
        > "$OUT/logs/$base.potree.log" 2>&1
      prc=$?
      set -e
      points="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['points'])" "$octree/metadata.json" 2>/dev/null || echo -)"
    else
      prc="skipped"; points="-"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$f" "$rc" "$bytes" "$(summarize "$OUT/info/$base.json" "$laz")" \
      "$prc" "$points" "$(grep -v '^\s*$' "$OUT/logs/$base.pdal.log" | grep -v 'conda.cli.main_run' | tail -1)" >> "$tsv"
  done < <(printf '%s\n' "${E57_FILES[@]}" "${PDAL_FILES[@]}" "$POTREE_783_FILE" autzen_trim.ply)
  column -t -s $'\t' "$tsv"
}

case "${1:-}" in
  fetch) fetch "${2:?samples dir}" ;;
  run) run "${2:?pdal image}" "${3:?potree image}" "${4:?samples dir}" "${5:?out dir}" ;;
  *) sed -n '2,/^set -euo/p' "$0" | grep '^#'; exit 2 ;;
esac

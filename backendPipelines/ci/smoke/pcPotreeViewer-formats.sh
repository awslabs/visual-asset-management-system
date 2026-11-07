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
# the route PotreeConverter's input took, its exit status, the octree point count, its attribute names
# and its RGB range. Run it once per PDAL image (or per routing) and diff the TSVs.
#
# Every input is translated by PDAL here so the PDAL versions can be compared on it. What PotreeConverter
# is given follows constructPipeline: the PDAL output for E57/PLY, and for LAS/LAZ the file itself when
# its header's point data record format is 0-7 (the formats PotreeConverter reads) or the PDAL output
# when it is not (8-10, #1215). LAS_ROUTING=extension gives every LAS/LAZ to PotreeConverter directly,
# the routing before #1215.
#
# Usage:
#   pcPotreeViewer-formats.sh fetch <samples-dir>
#   pcPotreeViewer-formats.sh run <pdal-image> <potree-image> <samples-dir> <out-dir>
#   PDAL_LAS_OPTIONS="" pcPotreeViewer-formats.sh run ...   # an image built before #1203 (no options)
#   LAS_ROUTING=extension pcPotreeViewer-formats.sh run ... # the LAS/LAZ routing before #1215
#
# Build the images from backendPipelines/preview/pcPotreeViewer/container:
#   docker build -f Dockerfile_PDAL -t vams-pdal:local .
#   docker build -f Dockerfile_Potree -t vams-potree:local .
#
# Samples (fetched at pinned commits):
#   E57 - asmaloney/libE57Format-test-data (CC0-1.0). self/ColouredCube*.e57 carry the CompressedVector
#         index packet current libE57Format writers emit, which PDAL 2.7.0 rejected (#1203).
#   LAS/LAZ/PLY - PDAL/PDAL test/data at tag 2.10.2 (BSD-3-Clause). autzen_trim is real airborne LiDAR
#         with RGB and intensity (format 3; autzen_trim_7 is the same in LAS 1.4 format 7); 4_6.las is
#         LAS 1.4 format 6; las_with_several_extra_byte_bloc.laz carries several Extra Bytes VLRs in
#         point format 8, which PotreeConverter does not read (#1215).
#   autzen_trim_{0,1,2}.las, autzen_trim_8.las - written from autzen_trim.las by the PDAL image under
#         test as LAS 1.2 formats 0, 1 and 2 and as LAS 1.4 format 8 (RGB + NIR), so every format has a
#         real-world input. (PDAL's permutations/1.2_*.las hold one point, a zero-size bounding box
#         PotreeConverter refuses whatever the format.)
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
  las/autzen_trim_7.las
  las/4_6.las
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

# las_point_format <file>: the point data record format in a LAS/LAZ header (byte 104, LAZ compression
# bits cleared), as openPipeline reads it (lambda/lasHeader.py); "-" for another file type, "?" when the
# file has no LAS header.
las_point_format() {
  case "$1" in
    *.las|*.laz|*.LAS|*.LAZ) ;;
    *) echo "-"; return ;;
  esac
  python3 - "$1" <<'PY'
import sys
with open(sys.argv[1], "rb") as handle:
    prefix = handle.read(105)
print(prefix[104] & 0x3F if len(prefix) == 105 and prefix[:4] == b"LASF" else "?")
PY
}

# octree_summary <metadata.json>: octree point count, attribute names and RGB range, tab-separated.
octree_summary() {
  python3 - "$1" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    print("-\t-\t-"); sys.exit(0)
attributes = doc.get("attributes", [])
names = ",".join(a["name"].replace(" ", "_") for a in attributes)
rgb = next((a for a in attributes if a["name"] == "rgb"), None)
colour = f"{min(rgb['min']):g}-{max(rgb['max']):g}" if rgb else "none"
print(f"{doc.get('points', '-')}\t{names}\t{colour}")
PY
}

run() {
  local pdal_image="$1" potree_image="$2"
  SAMPLES="$(cd "$3" && pwd)"; mkdir -p "$4"; OUT="$(cd "$4" && pwd)"
  local tsv="$OUT/results.tsv" f base laz rc bytes octree prc points source route input_dir input_fmt
  mkdir -p "$OUT/laz" "$OUT/potree" "$OUT/info" "$OUT/logs"

  if [ -f "$SAMPLES/las/autzen_trim.las" ] && [ ! -f "$OUT/autzen_trim.ply" ]; then
    pdal_in "$pdal_image" translate -i /in/las/autzen_trim.las -o /out/autzen_trim.ply > "$OUT/logs/make-ply.log" 2>&1
  fi
  local pdrf minor
  for pdrf in 0 1 2 8; do
    if [ "$pdrf" = 8 ]; then minor=4; else minor=2; fi
    if [ -f "$SAMPLES/las/autzen_trim.las" ] && [ ! -f "$OUT/autzen_trim_$pdrf.las" ]; then
      pdal_in "$pdal_image" translate -i /in/las/autzen_trim.las -o "/out/autzen_trim_$pdrf.las" \
        "--writers.las.minor_version=$minor" "--writers.las.dataformat_id=$pdrf" > "$OUT/logs/make-las$pdrf.log" 2>&1
    fi
  done

  printf 'file\tinput_pdrf\tpdal_exit\tlaz_bytes\tlas_format\tpoints\tcolour\tintensity\troute\tpotree_exit\toctree_points\toctree_attributes\toctree_rgb\tpdal_last_line\n' > "$tsv"
  while IFS= read -r f; do
    base="$(echo "$f" | tr '/.' '__')"
    laz="$OUT/laz/$base.laz"
    rm -f "$laz"
    # Files this script writes (autzen_trim.ply, autzen_trim_<format>.las) are in the output folder.
    case "$f" in
      autzen_trim.ply|autzen_trim_?.las) source="/out/$f"; input_dir="$OUT" ;;
      *) source="/in/$f"; input_dir="$SAMPLES" ;;
    esac
    input_fmt="$(las_point_format "$input_dir/$f")"
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
    # What PotreeConverter is given, as constructPipeline routes it: the PDAL output for E57/PLY; for
    # LAS/LAZ the file itself when its header's point format is one PotreeConverter reads (0-7) or
    # cannot be read, the PDAL output otherwise. LAS_ROUTING=extension sends every LAS/LAZ directly.
    route="pdal"
    case "$f" in
      *.las|*.laz)
        if [ "${LAS_ROUTING:-header}" = extension ] || [ "$input_fmt" = "?" ] || [ "$input_fmt" -le 7 ]; then
          route="direct"
        fi ;;
    esac
    if [ "$route" = direct ]; then
      octree_src="$input_dir/$f"; mount_src="/src/$f"; mount_dir="$input_dir"
    else
      octree_src="$laz"; mount_src="/src/laz/$base.laz"; mount_dir="$OUT"
    fi
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
      points="$(octree_summary "$octree/metadata.json")"
    else
      prc="skipped"; points=$'-\t-\t-'
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$f" "$input_fmt" "$rc" "$bytes" "$(summarize "$OUT/info/$base.json" "$laz")" \
      "$route" "$prc" "$points" "$(grep -v '^\s*$' "$OUT/logs/$base.pdal.log" | grep -v 'conda.cli.main_run' | tail -1)" >> "$tsv"
  done < <(printf '%s\n' "${E57_FILES[@]}" "${PDAL_FILES[@]}" "$POTREE_783_FILE" autzen_trim.ply \
    autzen_trim_0.las autzen_trim_1.las autzen_trim_2.las autzen_trim_8.las)
  column -t -s $'\t' "$tsv"
}

case "${1:-}" in
  fetch) fetch "${2:?samples dir}" ;;
  run) run "${2:?pdal image}" "${3:?potree image}" "${4:?samples dir}" "${5:?out dir}" ;;
  *) sed -n '2,/^set -euo/p' "$0" | grep '^#'; exit 2 ;;
esac

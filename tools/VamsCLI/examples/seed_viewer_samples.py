#!/usr/bin/env python3
"""
Seed a VAMS deployment with the public sample files the viewer render cases need.

Reads the sample manifest `web/e2e/fixtures/viewer-samples.json`, downloads every listed sample,
refuses any whose size or SHA-256 differs from the manifest, writes the generated and derived
samples, and uploads them all to ONE distributable asset. It then writes a JSON fixture that
`web/e2e/viewers.spec.ts` reads through the `E2E_VIEWER_SAMPLES` environment variable. No sample is
checked in; they are fetched at seed time from the sources the manifest names.

Run steps (from the repo root):

    # 1. Configure + log in once (never put credentials in this script or in the repo)
    vamscli --profile <name> auth login

    # 2. Seed (safe to repeat; files already in the asset are not uploaded again)
    python3 tools/VamsCLI/examples/seed_viewer_samples.py --profile <name>

    # 3. Point the e2e suite at the fixture it printed
    export E2E_VIEWER_SAMPLES=/abs/path/to/web/e2e/fixtures/viewer-samples-seed.json
    cd web && npx playwright test e2e/viewers.spec.ts

The `vamscli` group-level `--profile` option is PREPENDED before every subcommand. Only stdlib is
used; every CLI call is list-form argv, never a shell string.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import math
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed_compare_smoke import (  # noqa: E402  (sibling example script, same CLI plumbing)
    Cli,
    SeedError,
    ensure_asset,
    ensure_authenticated,
    ensure_database,
    version_count,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "web" / "e2e" / "fixtures" / "viewer-samples.json"
DEFAULT_OUT = REPO_ROOT / "web" / "e2e" / "fixtures" / "viewer-samples-seed.json"
DOWNLOAD_TIMEOUT_SECONDS = 120
UTF8_BOM = b"\xef\xbb\xbf"


def off_cube() -> bytes:
    """A unit cube in the OFF format with one colour per face, so a render shows several colours."""
    vertices = [
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
    ]
    faces = [
        ((0, 3, 2, 1), (220, 40, 40)),
        ((4, 5, 6, 7), (40, 180, 40)),
        ((0, 1, 5, 4), (40, 80, 220)),
        ((2, 3, 7, 6), (230, 200, 40)),
        ((1, 2, 6, 5), (200, 40, 200)),
        ((0, 4, 7, 3), (40, 200, 200)),
    ]
    lines = ["OFF", f"{len(vertices)} {len(faces)} 0"]
    lines += [f"{x} {y} {z}" for x, y, z in vertices]
    lines += [f"4 {a} {b} {c} {d} {r} {g} {bl}" for (a, b, c, d), (r, g, bl) in faces]
    return ("\n".join(lines) + "\n").encode("ascii")


def text_sample(sample: Dict[str, Any]) -> bytes:
    """The sample's own `content`, or three numbered lines the Text Viewer case looks for."""
    if "content" in sample:
        return str(sample["content"]).encode("utf-8")
    return (
        "VAMS viewer sample: plain text line 1\n"
        "VAMS viewer sample: plain text line 2\n"
        "VAMS viewer sample: plain text line 3\n"
    ).encode("utf-8")


GENERATORS: Dict[str, Callable[[Dict[str, Any]], bytes]] = {
    "off-cube": lambda sample: off_cube(),
    "text": text_sample,
}


def zip_sources(sample: Dict[str, Any], sources: List[Tuple[Dict[str, Any], bytes]]) -> bytes:
    """A zip of the source samples, each stored under its file name (or `zipEntries[i]`)."""
    names = sample.get("zipEntries") or [src["path"].rsplit("/", 1)[-1] for src, _ in sources]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, (_, data) in zip(names, sources):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # reproducible bytes
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def splat_transform(sample: Dict[str, Any], sources: List[Tuple[Dict[str, Any], bytes]]) -> bytes:
    """The source splat converted by @playcanvas/splat-transform at the pinned `version` to the
    format of the sample's own extension, installed under the 7-day release-age floor."""
    ((source, data),) = sources
    with tempfile.TemporaryDirectory(prefix="vams-splat-") as tmp:
        src = Path(tmp) / source["path"].rsplit("/", 1)[-1]
        out = Path(tmp) / ("out-" + sample["path"].rsplit("/", 1)[-1])
        src.write_bytes(data)
        argv = [
            "npm",
            "exec",
            "--yes",
            "--min-release-age=7",
            f"--package=@playcanvas/splat-transform@{sample['version']}",
            "--",
            "splat-transform",
            *[str(arg) for arg in sample.get("args", [])],
            str(src),
            str(out),
        ]
        print(f"  $ {' '.join(argv)}")
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            raise SeedError(
                f"{sample['id']}: splat-transform failed ({proc.returncode})\n"
                f"stdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}"
            )
        return out.read_bytes()


# Byte offset of red/green/blue (uint16 each) within a point record, per LAS point data format that
# carries colour (ASPRS LAS 1.4 R15, section 2.6). X, Y, Z are int32 at offsets 0, 4 and 8 in all.
LAS_RGB_OFFSET = {2: 20, 3: 28, 5: 28, 7: 30, 8: 30, 10: 30}


def las_to_ply(sample: Dict[str, Any], sources: List[Tuple[Dict[str, Any], bytes]]) -> bytes:
    """An uncompressed LAS point cloud with colour, written as binary little-endian PLY vertices
    (x, y, z as double; red, green, blue as uchar). Point clouds only: no faces. A `.laz` source is
    refused, since decompressing LAZ needs a native library."""
    ((source, data),) = sources
    if data[:4] != b"LASF":
        raise SeedError(f"{sample['id']}: {source['id']} is not an uncompressed LAS file")
    major, minor = data[24], data[25]
    point_offset = struct.unpack_from("<I", data, 96)[0]
    point_format = data[104] & 0x3F  # bits 6-7 flag LAZ compression
    if data[104] & 0xC0:
        raise SeedError(
            f"{sample['id']}: {source['id']} is LAZ-compressed; use an uncompressed LAS"
        )
    record_length = struct.unpack_from("<H", data, 105)[0]
    count = struct.unpack_from("<I", data, 107)[0]
    if (major, minor) >= (1, 4) and count == 0:
        count = struct.unpack_from("<Q", data, 247)[0]
    scale = struct.unpack_from("<3d", data, 131)
    offset = struct.unpack_from("<3d", data, 155)
    rgb_offset = LAS_RGB_OFFSET.get(point_format)
    if rgb_offset is None:
        raise SeedError(f"{sample['id']}: LAS point format {point_format} carries no colour")
    if point_offset + count * record_length > len(data):
        raise SeedError(f"{sample['id']}: {source['id']} is shorter than its header says")

    rows = []
    colours = []
    for i in range(count):
        base = point_offset + i * record_length
        x, y, z = struct.unpack_from("<3i", data, base)
        rows.append((x * scale[0] + offset[0], y * scale[1] + offset[1], z * scale[2] + offset[2]))
        colours.append(struct.unpack_from("<3H", data, base + rgb_offset))
    # LAS colour is 16-bit by convention, but some writers store 8-bit values; scale only then.
    shift = 8 if any(channel > 255 for colour in colours for channel in colour) else 0
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"comment derived from {source['path'].rsplit('/', 1)[-1]} by seed_viewer_samples.py\n"
        f"element vertex {count}\n"
        "property double x\nproperty double y\nproperty double z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    ).encode("ascii")
    body = io.BytesIO()
    for (x, y, z), (r, g, b) in zip(rows, colours):
        body.write(struct.pack("<3d3B", x, y, z, r >> shift, g >> shift, b >> shift))
    return header + body.getvalue()


# The steps of util/create-ksplat.js in mkkellogg/GaussianSplats3D (MIT), against the npm build:
# argv = source .ply, output .ksplat, compression level.
KSPLAT_SCRIPT = """\
import * as GaussianSplats3D from "@mkkellogg/gaussian-splats-3d";
import * as fs from "fs";
const [input, output, level] = process.argv.slice(2);
const data = fs.readFileSync(input);
const buffer = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
const splats = GaussianSplats3D.PlyParser.parseToUncompressedSplatArray(buffer, 0);
const generator = GaussianSplats3D.SplatBufferGenerator.getStandardGenerator(1, Number(level), 0);
const result = generator.generateFromUncompressedSplatArray(splats);
fs.writeFileSync(output, Buffer.from(result.bufferData));
"""


def ksplat(sample: Dict[str, Any], sources: List[Tuple[Dict[str, Any], bytes]]) -> bytes:
    """The source .ply converted to .ksplat by @mkkellogg/gaussian-splats-3d at the pinned
    `version` (with `three` at `threeVersion`, its peer), compression level `level` (default 1).
    splat-transform reads .ksplat but cannot write it. Installed under the 7-day release-age floor."""
    ((source, data),) = sources
    with tempfile.TemporaryDirectory(prefix="vams-ksplat-") as tmp:
        work = Path(tmp)
        src = work / source["path"].rsplit("/", 1)[-1]
        out = work / "out.ksplat"
        script = work / "create-ksplat.mjs"
        src.write_bytes(data)
        script.write_text(KSPLAT_SCRIPT, encoding="utf-8")
        (work / "package.json").write_text('{"private": true}\n', encoding="utf-8")
        steps = [
            [
                "npm",
                "install",
                "--no-save",
                "--no-audit",
                "--no-fund",
                "--min-release-age=7",
                f"@mkkellogg/gaussian-splats-3d@{sample['version']}",
                f"three@{sample['threeVersion']}",
            ],
            ["node", str(script), str(src), str(out), str(sample.get("level", 1))],
        ]
        for argv in steps:
            print(f"  $ {' '.join(argv)}")
            proc = subprocess.run(argv, cwd=work, capture_output=True, text=True)
            if proc.returncode != 0:
                raise SeedError(
                    f"{sample['id']}: {argv[0]} failed ({proc.returncode})\n"
                    f"stdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}"
                )
        if not out.exists():
            raise SeedError(f"{sample['id']}: create-ksplat wrote no output")
        return out.read_bytes()


SH_C0 = 0.28209479177387814


def ply_to_splat(sample: Dict[str, Any], sources: List[Tuple[Dict[str, Any], bytes]]) -> bytes:
    """A binary little-endian 3D Gaussian splat .ply (float properties only) written as raw .splat
    rows, the headerless 32-byte layout antimatter15/splat introduced: position (3 float32), scale
    (3 float32, exp of the log scale), RGBA (4 uint8, base colour from f_dc_*, alpha the sigmoid of
    opacity) and the normalized rotation quaternion (4 uint8, q * 128 + 128). @playcanvas/
    splat-transform reads .splat but does not write it, hence this transform."""
    ((source, data),) = sources
    marker = b"end_header\n"
    end = data.find(marker)
    if not data.startswith(b"ply\n") or end < 0:
        raise SeedError(f"{sample['id']}: {source['id']} is not a PLY file")
    header = data[: end + len(marker)].decode("latin-1")
    if "format binary_little_endian" not in header:
        raise SeedError(f"{sample['id']}: {source['id']} is not a binary little-endian PLY")
    count = int(header.split("element vertex ", 1)[1].split("\n", 1)[0])
    props = [line.split() for line in header.splitlines() if line.startswith("property ")]
    if any(p[1] != "float" for p in props):
        raise SeedError(f"{sample['id']}: {source['id']} has non-float properties")
    names = [p[2] for p in props]
    needed = ["x", "y", "z", "scale_0", "scale_1", "scale_2", "f_dc_0", "f_dc_1", "f_dc_2"]
    needed += ["opacity", "rot_0", "rot_1", "rot_2", "rot_3"]
    if any(n not in names for n in needed):
        raise SeedError(f"{sample['id']}: {source['id']} lacks 3D Gaussian splat properties")
    index = {n: names.index(n) for n in needed}
    stride = len(names)
    body = data[end + len(marker) :]
    if len(body) < count * stride * 4:
        raise SeedError(f"{sample['id']}: {source['id']} is shorter than its header says")
    values = struct.unpack_from(f"<{count * stride}f", body)

    def byte(value: float) -> int:
        return max(0, min(255, round(value)))

    rows = bytearray()
    for i in range(count):
        row = values[i * stride : (i + 1) * stride]
        f = lambda name: row[index[name]]  # noqa: E731
        q = [f("rot_0"), f("rot_1"), f("rot_2"), f("rot_3")]
        norm = math.sqrt(sum(v * v for v in q)) or 1.0
        rows += struct.pack(
            "<6f8B",
            f("x"),
            f("y"),
            f("z"),
            math.exp(f("scale_0")),
            math.exp(f("scale_1")),
            math.exp(f("scale_2")),
            byte((0.5 + SH_C0 * f("f_dc_0")) * 255),
            byte((0.5 + SH_C0 * f("f_dc_1")) * 255),
            byte((0.5 + SH_C0 * f("f_dc_2")) * 255),
            byte(255 / (1 + math.exp(-f("opacity")))),
            *[byte(v / norm * 128 + 128) for v in q],
        )
    return bytes(rows)


# Each transform takes the derived sample's manifest entry and its sources' (entry, bytes) pairs.
TRANSFORMS: Dict[str, Callable[[Dict[str, Any], List[Tuple[Dict[str, Any], bytes]]], bytes]] = {
    "prepend-utf8-bom": lambda sample, sources: UTF8_BOM + sources[0][1],
    "zip": zip_sources,
    "splat-transform": splat_transform,
    "las-to-ply": las_to_ply,
    "ply-to-splat": ply_to_splat,
    "ksplat": ksplat,
}
SINGLE_SOURCE_TRANSFORMS = {
    "prepend-utf8-bom",
    "splat-transform",
    "las-to-ply",
    "ply-to-splat",
    "ksplat",
}
REQUIRED_KEYS = {"id", "viewer", "path", "licence", "source"}


def source_ids(sample: Dict[str, Any]) -> List[str]:
    sources = sample.get("derivedFrom", [])
    return [sources] if isinstance(sources, str) else list(sources)


def validate_manifest(samples: List[Dict[str, Any]]) -> None:
    """Fail before any download or upload when an entry cannot be produced as written."""
    ids = [sample.get("id") for sample in samples]
    paths = [sample.get("path") for sample in samples]
    problems = []
    if len(set(ids)) != len(ids):
        problems.append("sample ids repeat")
    if len(set(paths)) != len(paths):
        problems.append("sample paths repeat")
    for sample in samples:
        sid = sample.get("id", "<no id>")
        missing = REQUIRED_KEYS - sample.keys()
        if missing:
            problems.append(f"{sid}: missing {sorted(missing)}")
        if not str(sample.get("path", "")).startswith("/"):
            problems.append(f"{sid}: path must start with /")
        kinds = [key for key in ("url", "generated", "derivedFrom") if key in sample]
        if len(kinds) != 1:
            problems.append(f"{sid}: needs exactly one of url, generated, derivedFrom")
        if "url" in sample:
            if not str(sample["url"]).startswith("https://"):
                problems.append(f"{sid}: url must be https")
            if not isinstance(sample.get("size"), int) or len(str(sample.get("sha256", ""))) != 64:
                problems.append(f"{sid}: a url sample needs size and sha256")
        if "generated" in sample and sample["generated"] not in GENERATORS:
            problems.append(f"{sid}: unknown generator {sample['generated']}")
        if "derivedFrom" in sample:
            transform = sample.get("transform")
            sources = source_ids(sample)
            if transform not in TRANSFORMS:
                problems.append(f"{sid}: unknown transform {transform}")
            if not sources or any(source not in ids for source in sources):
                problems.append(f"{sid}: derivedFrom names an unknown sample")
            if transform in SINGLE_SOURCE_TRANSFORMS and len(sources) != 1:
                problems.append(f"{sid}: {transform} takes exactly one source")
            if transform == "splat-transform" and not sample.get("version"):
                problems.append(f"{sid}: splat-transform needs a pinned version")
            if transform == "ksplat" and not (sample.get("version") and sample.get("threeVersion")):
                problems.append(f"{sid}: ksplat needs a pinned version and threeVersion")
        required = sample.get("requiresFeature", [])
        if not isinstance(required, (str, list)):
            problems.append(f"{sid}: requiresFeature must be a string or a list")
    if problems:
        raise SeedError("invalid sample manifest:\n  " + "\n  ".join(problems))


def required_features(sample: Dict[str, Any]) -> List[str]:
    required = sample.get("requiresFeature", [])
    return [required] if isinstance(required, str) else list(required)


def download(sample: Dict[str, Any], cache: Path) -> bytes:
    """The sample's bytes, from `cache` when an earlier run fetched them, after checking size and
    SHA-256 against the manifest."""
    cached = cache / sample["id"]
    if cached.exists():
        data = cached.read_bytes()
    else:
        url = sample["url"]
        if not url.startswith("https://"):
            raise SeedError(f"{sample['id']}: refusing a non-HTTPS sample URL: {url}")
        print(f"  v {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "vams-seed-viewer-samples"})
        # nosec B310: the scheme is checked to be https above.
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # nosec
            data = response.read()
    if len(data) != sample["size"]:
        raise SeedError(f"{sample['id']}: expected {sample['size']} bytes, got {len(data)}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != sample["sha256"]:
        raise SeedError(f"{sample['id']}: SHA-256 {digest} does not match the manifest")
    cached.write_bytes(data)
    return data


def sample_bytes(sample: Dict[str, Any], by_id: Dict[str, Dict[str, Any]], cache: Path) -> bytes:
    if "url" in sample:
        return download(sample, cache)
    if "generated" in sample:
        return GENERATORS[sample["generated"]](sample)
    sources = [
        (by_id[source], sample_bytes(by_id[source], by_id, cache)) for source in source_ids(sample)
    ]
    return TRANSFORMS[sample["transform"]](sample, sources)


def upload(cli: Cli, database_id: str, asset_id: str, key: str, data: bytes) -> None:
    """Upload `data` to `key` (asset-relative, leading slash) unless the asset already has it."""
    folder, _, filename = key.rpartition("/")
    if not cli.dry_run and version_count(cli, database_id, asset_id, key.lstrip("/")) is not None:
        print(f"  = {key} exists")
        return
    with tempfile.TemporaryDirectory(prefix="vams-seed-") as tmp:
        path = Path(tmp) / filename
        path.write_bytes(data)
        cli.run(
            "file",
            "upload",
            str(path),
            "-d",
            database_id,
            "-a",
            asset_id,
            "--asset-location",
            f"{folder}/" if folder else "/",
            "--hide-progress",
            "--json-output",
        )
    print(f"  ^ uploaded {key} ({len(data)} bytes)")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", required=True, help="vamscli profile to seed")
    parser.add_argument("--prefix", default="e2e-viewers", help="database and asset name prefix")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to write the fixture JSON")
    parser.add_argument("--manifest", default=str(MANIFEST), help="sample manifest to read")
    parser.add_argument("--cache", help="directory to keep downloaded samples in between runs")
    parser.add_argument(
        "--samples",
        help="comma-separated sample ids to seed (default: all); their sources are fetched too",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the CLI calls only")
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    samples = manifest["samples"]
    try:
        validate_manifest(samples)
    except SeedError as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1
    by_id = {sample["id"]: sample for sample in samples}
    if args.samples:
        wanted = [sid.strip() for sid in args.samples.split(",") if sid.strip()]
        unknown = [sid for sid in wanted if sid not in by_id]
        if unknown:
            print(f"seed failed: unknown sample ids {unknown}", file=sys.stderr)
            return 1
        samples = [by_id[sid] for sid in wanted]
    cli = Cli(args.profile, dry_run=args.dry_run)
    database_id = f"{args.prefix}-db"
    asset_name = f"{args.prefix}-samples"

    try:
        ensure_authenticated(cli)
        ensure_database(cli, database_id)
        asset_id = ensure_asset(cli, database_id, asset_name)
        with tempfile.TemporaryDirectory(prefix="vams-viewer-samples-") as tmp:
            cache = Path(args.cache) if args.cache else Path(tmp)
            cache.mkdir(parents=True, exist_ok=True)
            for sample in samples:
                data = sample_bytes(sample, by_id, cache)
                upload(cli, database_id, asset_id, sample["path"], data)
    except SeedError as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1

    fixture = {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "profile": args.profile,
        "databaseId": database_id,
        "assetId": asset_id,
        "samples": {
            sample["id"]: {
                "key": sample["path"],
                "viewer": sample["viewer"],
                "requiresFeature": required_features(sample),
                **({"expect": sample["expect"]} if "expect" in sample else {}),
            }
            for sample in samples
        },
    }
    if args.dry_run:
        print(json.dumps(fixture, indent=2))
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {out}\n\n    export E2E_VIEWER_SAMPLES={out.resolve()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

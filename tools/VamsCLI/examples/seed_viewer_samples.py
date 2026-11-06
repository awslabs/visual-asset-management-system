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
import json
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

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


def text_sample() -> bytes:
    return (
        "VAMS viewer sample: plain text line 1\n"
        "VAMS viewer sample: plain text line 2\n"
        "VAMS viewer sample: plain text line 3\n"
    ).encode("utf-8")


GENERATORS = {"off-cube": off_cube, "text": text_sample}
TRANSFORMS = {"prepend-utf8-bom": lambda data: UTF8_BOM + data}


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
        return GENERATORS[sample["generated"]]()
    if "derivedFrom" in sample:
        source = by_id[sample["derivedFrom"]]
        return TRANSFORMS[sample["transform"]](sample_bytes(source, by_id, cache))
    raise SeedError(f"{sample['id']}: no url, generator or source in the manifest")


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
    parser.add_argument("--dry-run", action="store_true", help="print the CLI calls only")
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    samples = manifest["samples"]
    by_id = {sample["id"]: sample for sample in samples}
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

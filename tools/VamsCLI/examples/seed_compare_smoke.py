#!/usr/bin/env python3
"""
Seed a VAMS deployment with the fixtures the compare-mode e2e specs need.

Idempotent and profile-parameterized: run it as often as you like against any deployment the
`vamscli` profile points at. It creates (if missing) two databases, one distributable asset in each,
uploads a handful of text files plus one binary (a 1x1 PNG generated here, nothing checked in), and
re-uploads one text file three times so it carries >= 3 versions. It then writes a JSON fixture the
Playwright specs read through the `E2E_COMPARE_SEED` environment variable.

Run steps (from the repo root):

    # 1. Configure + log in once (never put credentials in this script or in the repo)
    vamscli --profile <name> setup ...            # if the profile does not exist yet
    vamscli --profile <name> auth login

    # 2. Seed (safe to repeat)
    python3 tools/VamsCLI/examples/seed_compare_smoke.py --profile <name>

    # 3. Point the e2e suite at the fixture it printed
    export E2E_COMPARE_SEED=/abs/path/to/web/e2e/fixtures/compare-seed.json
    cd web && npm run e2e -- e2e/seeded.compare.spec.ts

What the seed contains (prefix defaults to `e2e-compare`):

    <prefix>-db-a / <prefix>-asset-a : /notes.txt (>= 3 versions), /config.json, /README.md,
                                       /pixel.png, plus ONE asset-version snapshot taken before the
                                       last notes.txt upload (so the Versions tab has a file list
                                       whose notes.txt differs from latest)
    <prefix>-db-b / <prefix>-asset-b : /notes.txt, /config.json   (different content from asset-a)

The `vamscli` group-level `--profile` option is PREPENDED before every subcommand (it is rejected
after the subcommand). Only stdlib is used; every CLI call is list-form argv, never a shell string.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "web" / "e2e" / "fixtures" / "compare-seed.json"

TEXT_FILE = "notes.txt"
JSON_FILE = "config.json"
MARKDOWN_FILE = "README.md"
BINARY_FILE = "pixel.png"
VERSIONED_FILE = TEXT_FILE
VERSIONED_FILE_MIN_VERSIONS = 3

# Content per asset. Asset A's notes.txt is uploaded once per entry, oldest first, so the last entry
# is the "latest" version and every earlier one differs from it (the differ has something to show).
ASSET_A_NOTES_VERSIONS = [
    "VAMS compare smoke\nline two\nline three\nversion one\n",
    "VAMS compare smoke\nline two changed\nline three\nversion two\n",
    "VAMS compare smoke\nline two changed\nline three\nline four added\nversion three\n",
]
ASSET_A_FILES = {
    JSON_FILE: json.dumps({"name": "asset-a", "threshold": 1, "tags": ["alpha"]}, indent=2) + "\n",
    MARKDOWN_FILE: "# Asset A\n\nSeeded by seed_compare_smoke.py for the compare-mode e2e specs.\n",
}
ASSET_B_FILES = {
    TEXT_FILE: "VAMS compare smoke\nasset B has its own notes\nversion one\n",
    JSON_FILE: json.dumps({"name": "asset-b", "threshold": 2, "tags": ["beta"]}, indent=2) + "\n",
}


class SeedError(RuntimeError):
    """A CLI step failed in a way the seed cannot recover from."""


# --------------------------------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------------------------------


class Cli:
    """Runs `vamscli --profile <name> <subcommand ...>` and parses `--json-output` responses."""

    def __init__(self, profile: str, dry_run: bool = False, executable: str = "vamscli"):
        self.profile = profile
        self.dry_run = dry_run
        self.executable = executable

    def argv(self, *subcommand: str) -> List[str]:
        # The profile is a GROUP-level option and must come before the subcommand.
        return [self.executable, "--profile", self.profile, *subcommand]

    def run(self, *subcommand: str, check: bool = True) -> subprocess.CompletedProcess:
        argv = self.argv(*subcommand)
        print(f"  $ {' '.join(argv)}")
        if self.dry_run:
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
        proc = subprocess.run(argv, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise SeedError(
                f"command failed ({proc.returncode}): {' '.join(argv)}\n"
                f"stdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}"
            )
        return proc

    def run_json(self, *subcommand: str, check: bool = True) -> Optional[Any]:
        """Run with `--json-output` and parse stdout; None when the command failed (check=False)."""
        proc = self.run(*subcommand, "--json-output", check=check)
        if proc.returncode != 0:
            return None
        text = proc.stdout.strip()
        if not text:
            return {}
        # JSON mode prints only JSON, but be tolerant of a stray status line before it.
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=0)
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError as exc:
            raise SeedError(f"could not parse JSON from: {' '.join(subcommand)}\n{text}") from exc


def is_already_exists(proc: subprocess.CompletedProcess) -> bool:
    blob = f"{proc.stdout}\n{proc.stderr}".lower()
    return "already exists" in blob or "alreadyexists" in blob


def items_of(payload: Any) -> List[Dict[str, Any]]:
    """The list of records in a list-style response (`{"Items": [...]}` or a bare list)."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("Items", "items", "assets", "message"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict) and isinstance(value.get("Items"), list):
                return [x for x in value["Items"] if isinstance(x, dict)]
    return []


# --------------------------------------------------------------------------------------------------
# Seeding steps
# --------------------------------------------------------------------------------------------------


def ensure_authenticated(cli: Cli) -> None:
    status = cli.run_json("auth", "status", check=False)
    authenticated = bool(isinstance(status, dict) and status.get("authenticated"))
    if cli.dry_run or authenticated:
        return
    login = " ".join(cli.argv("auth", "login"))
    raise SeedError(
        f"profile '{cli.profile}' is not authenticated. Log in first, then re-run:\n    {login}"
    )


def ensure_database(cli: Cli, database_id: str) -> None:
    existing = cli.run_json("database", "get", "-d", database_id, check=False)
    if existing is not None and not cli.dry_run:
        print(f"  = database {database_id} exists")
        return
    proc = cli.run(
        "database",
        "create",
        "-d",
        database_id,
        "--description",
        "Compare-mode e2e smoke fixtures (seed_compare_smoke.py)",
        "--json-output",
        check=False,
    )
    if proc.returncode == 0 or is_already_exists(proc):
        print(f"  + database {database_id}")
        return
    raise SeedError(
        f"could not create database {database_id}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def find_asset_by_name(cli: Cli, database_id: str, name: str) -> Optional[str]:
    """assetId of the asset named `name` in `database_id`, or None. Ids are server-generated, so a
    re-run must find the earlier asset by NAME."""
    payload = cli.run_json(
        "assets", "list", "-d", database_id, "--auto-paginate", "--show-archived", check=False
    )
    for item in items_of(payload):
        if item.get("assetName") == name and item.get("assetId"):
            return str(item["assetId"])
    return None


def ensure_asset(cli: Cli, database_id: str, name: str) -> str:
    existing = find_asset_by_name(cli, database_id, name)
    if existing:
        print(f"  = asset {name} exists ({existing})")
        return existing
    created = cli.run_json(
        "assets",
        "create",
        "-d",
        database_id,
        "--name",
        name,
        "--description",
        "Compare-mode e2e smoke fixture asset",
        "--distributable",
    )
    if cli.dry_run:
        return f"<dry-run:{name}>"
    asset_id = created.get("assetId") if isinstance(created, dict) else None
    if not asset_id:
        # Some deployments return the record under a message envelope; fall back to a lookup.
        asset_id = find_asset_by_name(cli, database_id, name)
    if not asset_id:
        raise SeedError(f"asset {name} was created but its assetId could not be determined")
    print(f"  + asset {name} ({asset_id})")
    return str(asset_id)


def upload_bytes(cli: Cli, database_id: str, asset_id: str, filename: str, data: bytes) -> None:
    """Upload `data` as `/<filename>` in the asset. Re-uploading the same key writes a new S3
    version (the buckets are versioned), which is how the >= 3 versions of notes.txt are made."""
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
            "/",
            "--hide-progress",
            "--json-output",
        )
    print(f"  ^ uploaded /{filename} ({len(data)} bytes)")


def png_1x1() -> bytes:
    """A valid 1x1 opaque PNG, built from struct + zlib so no binary is checked in."""

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit, RGB
    raw = b"\x00" + bytes([0x33, 0x99, 0xCC])  # filter byte + one RGB pixel
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def version_count(cli: Cli, database_id: str, asset_id: str, filename: str) -> Optional[int]:
    """Number of S3 versions of `/<filename>`, or None when the file does not exist or cannot be
    read."""
    info = cli.run_json(
        "file",
        "info",
        "-d",
        database_id,
        "-a",
        asset_id,
        "-p",
        f"/{filename}",
        "--include-versions",
        check=False,
    )
    if not isinstance(info, dict):
        return None
    for key in ("versions", "Versions"):
        if isinstance(info.get(key), list):
            return len(info[key])
    nested = info.get("message") if isinstance(info.get("message"), dict) else None
    if nested and isinstance(nested.get("versions"), list):
        return len(nested["versions"])
    # The file exists but the response carried no version list: count it as one version.
    return 1


def ensure_file(cli: Cli, database_id: str, asset_id: str, filename: str, data: bytes) -> None:
    """Upload `/<filename>` only when the asset does not already have it (keeps re-runs from piling
    up versions of files whose history the specs do not care about)."""
    if not cli.dry_run and version_count(cli, database_id, asset_id, filename) is not None:
        print(f"  = /{filename} exists")
        return
    upload_bytes(cli, database_id, asset_id, filename, data)


def ensure_asset_version(cli: Cli, database_id: str, asset_id: str) -> None:
    """Create ONE asset-version snapshot when the asset has none. The Versions tab lists the files
    of a snapshot, so the per-row version "Compare" action needs one to exist."""
    listed = cli.run_json(
        "asset-version", "list", "-d", database_id, "-a", asset_id, "--auto-paginate", check=False
    )
    existing = items_of(listed) if listed is not None else []
    if existing and not cli.dry_run:
        print(f"  = asset version exists ({len(existing)} total)")
        return
    cli.run(
        "asset-version",
        "create",
        "-d",
        database_id,
        "-a",
        asset_id,
        "--comment",
        "compare-smoke snapshot (seed_compare_smoke.py)",
        "--use-latest-files",
        "--json-output",
    )
    print("  + asset version (snapshot of the current files)")


def seed(cli: Cli, prefix: str) -> Dict[str, Any]:
    db_a, db_b = f"{prefix}-db-a", f"{prefix}-db-b"
    name_a, name_b = f"{prefix}-asset-a", f"{prefix}-asset-b"

    print("Checking authentication")
    ensure_authenticated(cli)

    print("Databases")
    ensure_database(cli, db_a)
    ensure_database(cli, db_b)

    print("Assets")
    asset_a = ensure_asset(cli, db_a, name_a)
    asset_b = ensure_asset(cli, db_b, name_b)

    print(f"Files → {db_a}/{asset_a}")
    # Top up the versioned file: each pass uploads only the missing versions, so a re-run keeps the
    # count at or above the minimum without growing it unboundedly. All but the LAST missing version
    # go in before the asset-version snapshot, the last one after it — so the snapshot pins
    # notes.txt to an older S3 version than "latest" and the version-list Compare has a real diff.
    have = 0 if cli.dry_run else (version_count(cli, db_a, asset_a, VERSIONED_FILE) or 0)
    needed = max(0, VERSIONED_FILE_MIN_VERSIONS - have)
    pending = ASSET_A_NOTES_VERSIONS[len(ASSET_A_NOTES_VERSIONS) - needed :]
    before_snapshot, after_snapshot = pending[:-1], pending[-1:]
    if needed == 0:
        print(f"  = /{VERSIONED_FILE} already has {have} versions")
    for content in before_snapshot:
        upload_bytes(cli, db_a, asset_a, VERSIONED_FILE, content.encode("utf-8"))
    for filename, content in ASSET_A_FILES.items():
        ensure_file(cli, db_a, asset_a, filename, content.encode("utf-8"))
    ensure_file(cli, db_a, asset_a, BINARY_FILE, png_1x1())
    ensure_asset_version(cli, db_a, asset_a)
    for content in after_snapshot:
        upload_bytes(cli, db_a, asset_a, VERSIONED_FILE, content.encode("utf-8"))

    print(f"Files → {db_b}/{asset_b}")
    for filename, content in ASSET_B_FILES.items():
        ensure_file(cli, db_b, asset_b, filename, content.encode("utf-8"))

    final_versions = None if cli.dry_run else version_count(cli, db_a, asset_a, VERSIONED_FILE)
    if final_versions is not None and final_versions < VERSIONED_FILE_MIN_VERSIONS:
        raise SeedError(
            f"/{VERSIONED_FILE} has {final_versions} versions after seeding; expected >= "
            f"{VERSIONED_FILE_MIN_VERSIONS}. Is bucket versioning enabled on this deployment?"
        )

    return {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "profile": cli.profile,
        "databases": [db_a, db_b],
        "assets": {
            "a": {
                "databaseId": db_a,
                "assetId": asset_a,
                "files": [f"/{TEXT_FILE}", f"/{JSON_FILE}", f"/{MARKDOWN_FILE}", f"/{BINARY_FILE}"],
            },
            "b": {
                "databaseId": db_b,
                "assetId": asset_b,
                "files": [f"/{TEXT_FILE}", f"/{JSON_FILE}"],
            },
        },
        "textFile": TEXT_FILE,
        "jsonFile": JSON_FILE,
        "markdownFile": MARKDOWN_FILE,
        "binaryFile": BINARY_FILE,
        "versionedFile": VERSIONED_FILE,
        "versionedFileMinVersions": VERSIONED_FILE_MIN_VERSIONS,
    }


# --------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", required=True, help="vamscli profile to seed (required)")
    parser.add_argument(
        "--prefix", default="e2e-compare", help="id/name prefix (default: e2e-compare)"
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"where to write the JSON fixture (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the CLI calls without running them"
    )
    parser.add_argument(
        "--vamscli",
        default=os.environ.get("VAMSCLI_BIN", "vamscli"),
        help="vamscli executable (default: vamscli on PATH)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    cli = Cli(args.profile, dry_run=args.dry_run, executable=args.vamscli)
    try:
        fixture = seed(cli, args.prefix)
    except SeedError as exc:
        print(f"\nseed failed: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(
            f"\n'{args.vamscli}' was not found on PATH. Install the CLI "
            "(tools/VamsCLI, `pip install -e .`) or pass --vamscli /path/to/vamscli.",
            file=sys.stderr,
        )
        return 1

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")
    print("Point the e2e suite at it:")
    print(f"    export E2E_COMPARE_SEED={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

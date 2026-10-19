# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fetch the two large NIST fixtures (FM8's X7, FM9's and FM10's X9) that are not committed.

Both files are members of the NIST MBE PMI Validation and Conformance Testing project's public archive
(``NIST-PMI-STEP-Files.zip``, about 14 MB, work of the U.S. Government, public domain). The script downloads the
archive over HTTPS, reads the two members by name, and writes each under its fixture name only when its SHA-256
equals the one recorded in ``fixtures/SHA256SUMS.txt``. A fixture already on disk with the right digest is not
fetched again. Standard library only.

    python fetch_fixtures.py [--dest fixtures] [--archive NIST-PMI-STEP-Files.zip]

``--archive`` reads a copy of the archive you downloaded yourself instead of fetching it.
"""

import argparse
import hashlib
import io
import os
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_URL = "https://www.nist.gov/system/files/documents/noindex/2024/06/19/NIST-PMI-STEP-Files.zip"
ARCHIVE_MAX_BYTES = 64 * 1024 * 1024
TIMEOUT_SECONDS = 120

# fixture name -> the archive member it is (matched on the member's base name) and its recorded SHA-256
FETCHED = {
    "X7-nist_stc_09_asme1_ap242-e3_5MB.stp": {
        "member": "nist_stc_09_asme1_ap242-e3.stp",
        "sha256": "737423afcb223b87d92a4ea7646a97d8d55fb21cc24ba2fb72b952336bcf1fad",
    },
    "X9-nist_ftc_09_asme1_ap242-e1_multiroot_6MB.stp": {
        "member": "nist_ftc_09_asme1_ap242-e1.stp",
        "sha256": "f1215fe15a78085a9fa78dd81714caf774b59071be92580b04ebfdc19f52a1bd",
    },
}


class FetchError(RuntimeError):
    """The archive or a member could not be obtained, or a digest does not match."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path):
    with open(path, "rb") as fh:
        return _sha256(fh.read())


def download_archive(url=ARCHIVE_URL):
    """The archive bytes, bounded to ARCHIVE_MAX_BYTES."""
    if not url.startswith("https://"):
        raise FetchError("the archive is fetched over HTTPS only")
    request = urllib.request.Request(url, headers={"User-Agent": "vams-cad-step-agent-evaluation"})
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - fixed https URL
        data = response.read(ARCHIVE_MAX_BYTES + 1)
    if len(data) > ARCHIVE_MAX_BYTES:
        raise FetchError(f"the archive exceeds {ARCHIVE_MAX_BYTES} bytes")
    return data


def extract(archive_bytes, dest):
    """Write every FETCHED fixture missing from ``dest`` out of the archive; returns the names written. Members
    are read into memory by name and written under the fixture's own name, never under the archive's path."""
    written = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        by_base = {os.path.basename(info.filename): info for info in archive.infolist() if not info.is_dir()}
        for name, spec in FETCHED.items():
            target = os.path.join(dest, name)
            if os.path.isfile(target) and _file_sha256(target) == spec["sha256"]:
                continue
            info = by_base.get(spec["member"])
            if info is None:
                raise FetchError(f"the archive has no member {spec['member']}")
            data = archive.read(info)
            if _sha256(data) != spec["sha256"]:
                raise FetchError(f"{spec['member']} does not match the recorded sha256 of {name}")
            with open(target, "wb") as fh:
                fh.write(data)
            written.append(name)
    return written


def missing(dest):
    return [name for name, spec in FETCHED.items()
            if not (os.path.isfile(os.path.join(dest, name)) and _file_sha256(os.path.join(dest, name)) == spec["sha256"])]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", default=os.path.join(HERE, "fixtures"))
    parser.add_argument("--archive", help="a local copy of NIST-PMI-STEP-Files.zip")
    args = parser.parse_args(argv)
    if not missing(args.dest):
        print("every fetched fixture is present and verified")
        return 0
    if args.archive:
        with open(args.archive, "rb") as fh:
            archive_bytes = fh.read()
    else:
        archive_bytes = download_archive()
    written = extract(archive_bytes, args.dest)
    print("written and verified: " + ", ".join(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())

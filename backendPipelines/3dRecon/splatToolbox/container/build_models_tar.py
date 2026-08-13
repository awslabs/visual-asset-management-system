#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY

"""Builds models.tar.gz locally, mirroring what the model_deployment Lambda does.

Output: <script_dir>/models/models.tar.gz
        Ready to mount as /opt/ml/input/data/model/ for local debug runs.

Usage:
    python build_models_tar.py
    python build_models_tar.py --upload s3://your-bucket-name
"""

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def download(url: str, dest: Path) -> None:
    """Download a file from url to dest with a progress bar."""
    print(f"  Downloading {dest.name} ...", flush=True)
    headers = {"User-Agent": "Mozilla/5.0"}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=60) as resp:  # nosemgrep: dynamic-urllib-use-detected
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_pct = -1
            start = time.time()
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(512 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = int(downloaded / total * 100)
                        if pct >= last_pct + 10:
                            elapsed = time.time() - start
                            speed = downloaded / elapsed / 1024 / 1024 if elapsed else 0
                            print(f"    {pct}%  {downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB  {speed:.1f} MB/s", flush=True)
                            last_pct = pct
    except (HTTPError, URLError) as e:
        print(f"  ERROR downloading {url}: {e}")
        sys.exit(1)
    print(f"  Done: {dest.name} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")


def download_u2net(tmp: Path) -> Path:
    """Download and assemble U2NET model files from split parts."""
    u2net_dir = tmp / ".u2net"
    u2net_dir.mkdir(parents=True, exist_ok=True)
    base = "https://github.com/nadermx/backgroundremover/raw/main/models"

    # u2net.pth from 4 parts
    parts = ["u2aa", "u2ab", "u2ac", "u2ad"]
    part_paths = []
    for part in parts:
        p = u2net_dir / f"u2net.pth.{part}"
        download(f"{base}/{part}", p)
        part_paths.append(p)
    with open(u2net_dir / "u2net.pth", "wb") as out:
        for p in part_paths:
            out.write(p.read_bytes())
            p.unlink()

    # u2netp.pth
    download(f"{base}/u2netp.pth", u2net_dir / "u2netp.pth")

    # u2net_human_seg.pth from 4 parts
    human_parts = ["u2haa", "u2hab", "u2hac", "u2had"]
    human_part_paths = []
    for part in human_parts:
        p = u2net_dir / f"u2net_human_seg.pth.{part}"
        download(f"{base}/{part}", p)
        human_part_paths.append(p)
    with open(u2net_dir / "u2net_human_seg.pth", "wb") as out:
        for p in human_part_paths:
            out.write(p.read_bytes())
            p.unlink()

    return u2net_dir


def build(tmp: Path) -> dict:
    """Download all model files into tmp and return a mapping of {arcname: local_path}."""
    entries = {}

    # SAM2
    print("\n--- SAM2 ---")
    sam2 = tmp / "sam2.1_hiera_large.pt"
    download("https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt", sam2)
    entries["sam2.1_hiera_large.pt"] = sam2

    # U2NET
    print("\n--- U2NET ---")
    u2net_dir = download_u2net(tmp)
    entries[".u2net"] = u2net_dir

    # COLMAP vocab tree
    print("\n--- COLMAP vocab tree ---")
    vocab = tmp / "vocab_tree_flickr100K_words32K.bin"
    download(
        "https://github.com/ZachMckennedyFWig/ColmapFaissVocabTrees/raw/main/vocab_tree_flickr100K_words32K.bin",
        vocab,
    )
    entries["vocab_tree_flickr100K_words32K.bin"] = vocab

    # PyTorch hub checkpoints
    print("\n--- PyTorch models ---")
    torch_dir = tmp / ".cache" / "torch" / "hub" / "checkpoints"
    torch_dir.mkdir(parents=True, exist_ok=True)
    torch_models = [
        ("https://download.pytorch.org/models/mobilenet_v3_large-8738ca79.pth",          "mobilenet_v3_large-8738ca79.pth"),
        ("https://download.pytorch.org/models/fasterrcnn_resnet50_fpn_coco-258fb6c6.pth", "fasterrcnn_resnet50_fpn_coco-258fb6c6.pth"),
        ("https://download.pytorch.org/models/vgg16-397923af.pth",                        "vgg16-397923af.pth"),
        ("https://download.pytorch.org/models/alexnet-owt-7be5be79.pth",                  "alexnet-owt-7be5be79.pth"),
    ]
    for url, name in torch_models:
        download(url, torch_dir / name)
    entries[".cache"] = tmp / ".cache"

    # Stable Diffusion XL placeholder (downloaded at runtime inside container)
    print("\n--- Stable Diffusion XL placeholder ---")
    sd_dir = tmp / "stable-diffusion-xl-base-1.0"
    sd_dir.mkdir(parents=True, exist_ok=True)
    (sd_dir / "download_at_runtime.txt").write_text(
        "This model will be downloaded at runtime to avoid storage limits"
    )
    entries["stable-diffusion-xl-base-1.0"] = sd_dir

    return entries


def create_archive(entries: dict, archive: Path) -> None:
    """Pack all entries into a tar.gz archive."""
    print(f"\n--- Creating archive: {archive} ---")
    # Write to a temp file in the same directory first to avoid permission
    # issues with pre-existing files on network filesystems (e.g. EFS).
    tmp_archive = archive.with_suffix(".tmp.gz")
    try:
        with tarfile.open(tmp_archive, "w:gz") as tar:
            for arcname, local_path in entries.items():
                print(f"  Adding {arcname}")
                tar.add(local_path, arcname=arcname)
        shutil.move(str(tmp_archive), archive)
    except Exception:
        tmp_archive.unlink(missing_ok=True)
        raise
    size_mb = archive.stat().st_size / 1024 / 1024
    print(f"Archive created: {archive} ({size_mb:.1f} MB)")


def upload_to_s3(archive: Path, bucket: str) -> None:
    """Upload the archive to s3://<bucket>/models/models.tar.gz."""
    try:
        import boto3
    except ImportError:
        print("ERROR: boto3 is required for S3 upload. Install with: pip install boto3")
        sys.exit(1)
    s3_key = "models/models.tar.gz"
    print(f"\n--- Uploading to s3://{bucket}/{s3_key} ---")
    s3 = boto3.client("s3")
    s3.upload_file(str(archive), bucket, s3_key)
    print("Upload complete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--upload", metavar="s3://BUCKET", help="Upload models.tar.gz to S3 after building")
    parser.add_argument("--output-dir", default=None, help="Directory to write models.tar.gz (default: <script_dir>/models)")
    args = parser.parse_args()

    script_dir = Path(__file__).parent.resolve()
    out_dir = Path(args.output_dir) if args.output_dir else script_dir / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "models.tar.gz"

    print("=== Building models.tar.gz ===")
    print(f"Output: {archive}")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        entries = build(tmp_dir)
        create_archive(entries, archive)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if args.upload:
        bucket = args.upload.removeprefix("s3://").rstrip("/")
        upload_to_s3(archive, bucket)

    print("\n=== Done ===")
    print("To use locally, mount the models/ directory:")
    print("  -v $(pwd)/models:/opt/ml/input/data/model")


if __name__ == "__main__":
    main()

# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Packaged data: schemas, defaults, IAM action manifests, upgrade matrix, suite catalog."""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent


def path(*parts: str) -> Path:
    return DATA_DIR.joinpath(*parts)


def text(*parts: str) -> str:
    return path(*parts).read_text(encoding="utf-8")

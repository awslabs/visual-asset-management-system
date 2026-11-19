#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The text USD pre-check: the budget from the task's memory, the header classification, and the
run ending before pxr reads anything."""
import json
import zipfile

import pytest

import memory_budget
from miris_upload_container_harness import ASSET_ID, definition, fake_usd

pytestmark = pytest.mark.unit

MIB = 1024 ** 2
GIB = 1024 ** 3


@pytest.mark.parametrize("memory, budget", [(2 * GIB, 192 * MIB), (4 * GIB, 448 * MIB)])
def test_the_text_budget_is_an_eighth_of_memory_after_the_headroom(memory, budget):
    assert memory_budget.text_usd_budget_bytes(memory) == budget


def test_the_memory_limit_is_the_smallest_readable_bound(tmp_path):
    (tmp_path / "memory.max").write_text("max\n")
    environ = {"VAMS_TASK_MEMORY_MIB": "4096"}
    assert memory_budget.task_memory_bytes(environ) == 4 * GIB
    assert memory_budget.memory_limit_bytes(environ, str(tmp_path)) == 4 * GIB
    (tmp_path / "memory.max").write_text(str(3 * GIB))
    assert memory_budget.memory_limit_bytes(environ, str(tmp_path)) == 3 * GIB
    # An unreadable value is ignored, leaving the cgroup limit.
    assert memory_budget.memory_limit_bytes({"VAMS_TASK_MEMORY_MIB": "4g"}, str(tmp_path)) == 3 * GIB


def test_text_layers_count_and_crate_layers_do_not(tmp_path):
    text = tmp_path / "scene.usd"
    text.write_bytes(b"#usda 1.0\n" + b" " * 990)
    crate = tmp_path / "geo.usdc"
    crate.write_bytes(b"PXR-USDC" + b"\0" * 5000)
    texture = tmp_path / "wood.png"
    texture.write_bytes(b"#usda" + b"\0" * 5000)

    assert memory_budget.total_text_bytes([str(text), str(crate), str(texture)]) == 1000


def test_a_usdz_counts_its_text_members(tmp_path):
    package = tmp_path / "pump.usdz"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_STORED) as z:
        z.writestr("pump.usda", b"#usda 1.0\n" + b" " * 290)
        z.writestr("geo.usdc", b"PXR-USDC" + b"\0" * 4000)
        z.writestr("textures/wood.png", b"\x89PNG" + b"\0" * 4000)

    assert memory_budget.text_bytes(str(package)) == 300


def test_text_over_the_budget_exits_before_any_parse(container, monkeypatch, capsys):
    root = f"{ASSET_ID}/"
    # 513 MiB of task memory leaves a budget of 128 KiB.
    monkeypatch.setenv("VAMS_TASK_MEMORY_MIB", "513")
    container.s3.add("asset-bucket", f"{root}scene.usda", b"#usda 1.0\n" + b" " * 200_000)
    container.s3.add("asset-bucket", f"{root}geo.usdc", b"PXR-USDC" + b"\0" * 500_000)
    fake_usd(container, monkeypatch, {})

    def no_parse(*args, **kwargs):
        raise AssertionError("pxr was asked to read a layer")

    for name in ("direct_references", "compute_dependencies", "check_geometry", "package_usdz"):
        monkeypatch.setattr(container.main, name, no_parse)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda"))

    assert exited.value.code == container.main.EXIT_TEXT_USD_TOO_LARGE == 17
    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"text_usd_too_large"' in line)
    assert entry["text_bytes"] == 200_010 and entry["limit_bytes"] == 128 * 1024
    assert entry["files"] == [{"file": "scene.usda", "bytes": 200_010}]
    container.miris_cls.assert_not_called()

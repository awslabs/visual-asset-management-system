# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Where an agent's diagnostics go: small text beside the run report, everything else off the results prefix.

The workflow's output step reads EVERY object under the results prefix as UTF-8 text and records the content
on the execution. The first two finished Blender-agent runs uploaded their renders there, and both were
reported FAILED -- "Reading a pipeline results file failed" -- with passing models sitting in the asset. A
PNG cannot be decoded as text; a 450 KB event transcript would not fit the DynamoDB item that records the
results either. So the upload partitions by kind and size, and a small index of the bulky files' keys goes
with the text so the execution still points at them.
"""

import importlib.util
import json
import os

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def main_module():
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main_diag", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def uploads(main_module, monkeypatch):
    seen = []
    monkeypatch.setattr(main_module.s3, "upload",
                        lambda bucket, key, path: seen.append((bucket, key, os.path.basename(path))) or key)
    return seen


def _files(tmp_path):
    (tmp_path / "DONE.json").write_text('{"status": "complete"}', encoding="utf-8")
    (tmp_path / "notes.md").write_text("# notes", encoding="utf-8")
    (tmp_path / "scripts_build_v001.py").write_text("import bpy", encoding="utf-8")
    (tmp_path / "render_v004_front.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 5000)
    (tmp_path / "agent_events.jsonl").write_text("{}\n" * 60_000, encoding="utf-8")  # ~180 KB of text
    return [str(tmp_path / name) for name in
            ("DONE.json", "notes.md", "scripts_build_v001.py", "render_v004_front.png", "agent_events.jsonl")]


@pytest.mark.unit
class TestOnlySmallTextReachesTheResultsPrefix:
    RESULTS = {"bucketName": "asset-bucket", "objectDir": "pipelines/p/j/output/exec1/results/"}
    AUX = {"bucketName": "aux-bucket", "objectDir": "aux/exec1/"}

    def test_binary_and_oversize_files_go_to_the_auxiliary_prefix(self, main_module, uploads, tmp_path):
        main_module.upload_diagnostics(_files(tmp_path), self.RESULTS, self.AUX, "Job_42")
        results_keys = sorted(k for b, k, _ in uploads if b == "asset-bucket")
        aux_keys = sorted(k for b, k, _ in uploads if b == "aux-bucket")
        assert results_keys == [
            "pipelines/p/j/output/exec1/results/agent/DONE.json",
            "pipelines/p/j/output/exec1/results/agent/bulky_diagnostics.json",
            "pipelines/p/j/output/exec1/results/agent/notes.md",
            "pipelines/p/j/output/exec1/results/agent/scripts_build_v001.py",
        ]
        assert aux_keys == [
            "aux/exec1/imageTo3d/agent/Job_42/agent_events.jsonl",
            "aux/exec1/imageTo3d/agent/Job_42/render_v004_front.png",
        ]

    def test_the_index_names_every_bulky_file_by_its_s3_location(self, main_module, uploads, tmp_path):
        paths = _files(tmp_path)
        main_module.upload_diagnostics(paths, self.RESULTS, self.AUX, "Job_42")
        index = json.loads((tmp_path / "bulky_diagnostics.json").read_text(encoding="utf-8"))
        locations = sorted(entry["s3"] for entry in index["files"])
        assert locations == ["s3://aux-bucket/aux/exec1/imageTo3d/agent/Job_42/agent_events.jsonl",
                             "s3://aux-bucket/aux/exec1/imageTo3d/agent/Job_42/render_v004_front.png"]
        assert all(entry["bytes"] > 0 for entry in index["files"])

    def test_nothing_under_the_results_prefix_is_binary_or_over_the_limit(self, main_module, uploads, tmp_path):
        """The invariant the workflow's reader relies on, checked against the files actually uploaded."""
        paths = _files(tmp_path)
        main_module.upload_diagnostics(paths, self.RESULTS, self.AUX, "Job_42")
        for bucket, key, name in uploads:
            if bucket != "asset-bucket":
                continue
            data = (tmp_path / name).read_bytes()
            assert len(data) <= main_module.DIAGNOSTIC_RESULTS_MAX_BYTES, name
            data.decode("utf-8")  # raises if it is not text

    def test_without_an_auxiliary_prefix_bulky_files_are_kept_off_the_results_and_not_lost_silently(
            self, main_module, uploads, tmp_path, monkeypatch):
        logged = []
        monkeypatch.setattr(main_module.logger, "info", lambda message, *a, **k: logged.append(str(message)))
        main_module.upload_diagnostics(_files(tmp_path), self.RESULTS, {}, "Job_42")
        assert all(b == "asset-bucket" for b, _, _ in uploads)
        assert not any(k.endswith((".png", ".jsonl")) for _, k, _ in uploads)
        assert any("render_v004_front.png" in line for line in logged), logged

    def test_no_results_prefix_uploads_nothing(self, main_module, uploads, tmp_path):
        main_module.upload_diagnostics(_files(tmp_path), {}, self.AUX, "Job_42")
        assert uploads == []

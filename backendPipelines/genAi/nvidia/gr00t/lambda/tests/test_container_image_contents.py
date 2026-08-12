#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every local module the container entrypoint imports must be COPYed into the image.

The Dockerfile lists the files it copies explicitly, so adding a module next to __main__.py is not
enough — it has to be named in that COPY line too. Miss it and the failure is a
ModuleNotFoundError at container start, which on a GPU queue costs a full scale-up, a job launch and a
FAILED execution to discover. That is exactly how the evaluation module first shipped.
"""

import ast
import os
import re

import pytest

CONTAINER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "container"))


def _copied_files():
    """Filenames named on the Dockerfile COPY line that targets /opt/ml/code."""
    dockerfile = os.path.join(CONTAINER_DIR, "Dockerfile")
    with open(dockerfile, encoding="utf-8") as fh:
        text = fh.read()
    copied = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY ") or "/opt/ml/code" not in stripped:
            continue
        # COPY a.py b.py ... /opt/ml/code/  -> everything between the verb and the destination.
        parts = re.split(r"\s+", stripped)[1:-1]
        copied.update(os.path.basename(p) for p in parts)
    return copied


def _local_imports(module_filename):
    """Top-level module names imported by a container file that resolve to a sibling .py file."""
    path = os.path.join(CONTAINER_DIR, module_filename)
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n for n in names if os.path.exists(os.path.join(CONTAINER_DIR, f"{n}.py"))}


@pytest.mark.unit
class TestContainerImageContents:
    def test_entrypoint_local_imports_are_all_copied(self):
        copied = _copied_files()
        missing = sorted(
            f"{name}.py" for name in _local_imports("__main__.py")
            if f"{name}.py" not in copied
        )
        assert not missing, (
            "__main__.py imports these sibling modules but the Dockerfile does not COPY them, so the "
            f"container would fail at import: {missing}")

    def test_transitive_local_imports_are_copied(self):
        # A copied module importing an un-copied one fails just as hard, one level later.
        copied = _copied_files()
        missing = []
        for module in sorted(copied):
            if not module.endswith(".py"):
                continue
            for name in _local_imports(module):
                if f"{name}.py" not in copied:
                    missing.append(f"{module} imports {name}.py")
        assert not missing, f"un-copied transitive imports: {missing}"

    def test_the_evaluation_module_ships(self):
        # Named explicitly: it is the module whose omission caused a FAILED GPU job.
        assert "evaluation.py" in _copied_files()


@pytest.mark.unit
class TestInputAssetIsNeverWrittenBack:
    """The container may REPAIR its local copy of the dataset (evaluation writes meta/modality.json
    when the export lacks one), but it must never push that back to the user's asset.

    Enforced by reading the source rather than by review: the guarantee rests on exactly one upload
    call whose source is OUTPUT_DIR, which is easy to break by adding one convenient sync.
    """

    def _main_source(self):
        with open(os.path.join(CONTAINER_DIR, "__main__.py"), encoding="utf-8") as fh:
            return fh.read()

    def _upload_call_args(self, source):
        """First argument of every upload_output_to_s3 CALL (excluding its own def line)."""
        args = []
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("def ") or "upload_output_to_s3(" not in stripped:
                continue
            inner = stripped.split("upload_output_to_s3(", 1)[1]
            args.append(inner.split(",")[0].strip().rstrip(")").strip())
        return args

    def test_the_only_upload_source_is_the_output_dir(self):
        calls = self._upload_call_args(self._main_source())
        assert calls, "upload_output_to_s3 is no longer called; re-check what this test guards"
        assert set(calls) == {"OUTPUT_DIR"}, (
            f"something other than OUTPUT_DIR is uploaded: {sorted(set(calls))}. The input asset must "
            "never be written back.")

    def test_the_input_dir_is_only_ever_a_sync_DESTINATION(self):
        """INPUT_DIR may receive a download; it must never be the SOURCE of one.

        Checked on the helper signatures rather than the raw arg text: the two download helpers take
        (s3_path, local_dir) and the upload takes (local_dir, s3_path), so a new upload of the input
        tree would have to introduce a new call, which the test above also catches.
        """
        source = self._main_source()
        # INPUT_DIR appears only as a download target and as a read path for config/dataset resolution.
        for line in source.splitlines():
            if "INPUT_DIR" not in line or line.strip().startswith("#"):
                continue
            stripped = line.strip()
            uploads = ("upload_output_to_s3(" in stripped
                       or ("s3 sync" in stripped and "INPUT_DIR" in stripped.split("sync")[0]))
            assert not uploads, f"INPUT_DIR is being uploaded: {stripped[:120]}"

    def test_evaluation_repairs_the_local_copy_only(self):
        """The modality repair writes under the DATASET path (a child of INPUT_DIR), and evaluation.py
        contains no S3 upload at all — so the repair cannot escape the container."""
        with open(os.path.join(CONTAINER_DIR, "evaluation.py"), encoding="utf-8") as fh:
            evaluation = fh.read()
        assert "ensure_dataset_modality_file" in evaluation
        for forbidden in ("s3 sync", '"s3", "sync"', "upload_output_to_s3", "put_object"):
            assert forbidden not in evaluation, (
                f"evaluation.py contains '{forbidden}' — the dataset repair must stay local to the "
                "container and never reach the input asset.")

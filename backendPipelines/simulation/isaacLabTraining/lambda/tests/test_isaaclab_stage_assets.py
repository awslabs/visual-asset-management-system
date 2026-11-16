#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for ``trainingConfig.stageAssetPaths``: the executing asset's files the container stages.

The paths are operator-supplied (a template body or a JSON input file) and the job role can read
every asset bucket, so openPipeline accepts only relative paths inside the asset root the workflow
manifest names, and counts what they select against the file and byte limits before a Batch job is
submitted. A run that leaves the setting out builds the job configuration it built without it."""

import ast
import importlib
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

_STAGING_MODULE = os.path.join(os.path.dirname(_LAMBDA_DIR), "container", "utils", "aws", "staging.py")

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(k, v)

_BUCKET = "asset-bucket"
_ROOT = "xid130a6/"


def _load():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


class FakeBucket:
    """An S3 client over an in-memory key -> size map: prefix listings in key order, ``MaxKeys``,
    pages of ``page_size`` keys, and a count of the pages read so a test can see a listing stop."""

    def __init__(self, objects, page_size=1000):
        self.objects = dict(objects)
        self.page_size = page_size
        self.pages_read = 0
        outer = self

        class _Paginator:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for k in outer.objects if k.startswith(Prefix))
                for start in range(0, len(keys), outer.page_size):
                    outer.pages_read += 1
                    yield {"Contents": [{"Key": k, "Size": outer.objects[k]}
                                        for k in keys[start:start + outer.page_size]]}

        self._paginator = _Paginator()

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self._paginator

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))[:MaxKeys]
        return {"Contents": [{"Key": k, "Size": self.objects[k]} for k in keys]} if keys else {}

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.objects.get(Key, 0)}

    def get_object(self, Bucket, Key, **kwargs):
        raise AssertionError("no input file is read in these tests")


# The executing asset's files, a sibling asset in the same bucket, and the VAMS-owned objects that
# are not asset files: a folder marker and a file preview.
_OBJECTS = {
    _ROOT: 0,
    _ROOT + "configs/train.json": 120,
    _ROOT + "checkpoints/E1/model_9.pt": 300,
    _ROOT + "environments/pkg.whl": 4_000,
    _ROOT + "Isaac/": 0,
    _ROOT + "Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd": 4_204,
    _ROOT + "Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd.previewFile.gif": 900,
    _ROOT + "Isaac/Robots/NVIDIA/NovaCarter/Parts/wheel.usd": 1_000,
    _ROOT + "Isaac/Sensors/HESAI/XT-32.usd": 2_000,
    _ROOT + "Isaac/Props/KLT_Bin/small_KLT_visual_collision.usd": 3_000,
    _ROOT + "Isaac2/unrelated.usd": 50,
    _ROOT + "scene.usd": 10,
    _ROOT + "scene.usd.previewFile.png": 5,
    "xother999/Isaac/Robots/secret.usd": 7,
}


def _event(training_config=None, **overrides):
    event = {
        "jobName": "isaaclab-job-abcd1234",
        "bucketAsset": _BUCKET,
        "inputAssetLocationKey": _ROOT,
        "inputS3AssetFilePath": f"s3://{_BUCKET}/{_ROOT}configs/train.txt",
        "outputS3AssetFilesPath": f"s3://{_BUCKET}/pipelines/p1/JOB/output/E1/files/",
        "externalSfnTaskToken": "tok-123",
        "trainingConfig": training_config or {},
    }
    event.update(overrides)
    return event


def _definition(event, objects=None):
    mod = _load()
    with patch.object(mod, "s3_client", FakeBucket(_OBJECTS if objects is None else objects)):
        return json.loads(mod.lambda_handler(event, MagicMock())["definition"])


def _selected(paths, objects=None, **kwargs):
    mod = _load()
    fake = FakeBucket(_OBJECTS if objects is None else objects, **kwargs)
    with patch.object(mod, "s3_client", fake):
        files = mod.list_stage_asset_files(_BUCKET, _ROOT, [mod.normalize_stage_asset_path(p) for p in paths])
    return {k[len(_ROOT):]: v for k, v in files.items()}, fake


@pytest.mark.unit
class TestStagePathValidation:
    @pytest.mark.parametrize("value, expected", [
        ("Isaac/", "Isaac/"),
        ("/Isaac/Robots/", "Isaac/Robots/"),
        ("configs/train.json", "configs/train.json"),
        ("  Isaac/Sensors  ", "Isaac/Sensors"),
        ("/", ""),
        ("Isaac/Props/KLT_Bin/small KLT.usd", "Isaac/Props/KLT_Bin/small KLT.usd"),
    ])
    def test_a_path_inside_the_asset_is_accepted(self, value, expected):
        assert _load().normalize_stage_asset_path(value) == expected

    @pytest.mark.parametrize("value", [
        "../other-asset/", "Isaac/../../other", "./Isaac", "Isaac/./Robots", "Isaac//Robots",
        "s3://asset-bucket/xother999/", "https://example.com/a.usd", "file:///etc/passwd",
        "C:\\Isaac", "Isaac\\Robots", "Isaac/\x00.usd", "Isaac/\nRobots/", "Isaac/\x7f",
    ])
    def test_a_path_that_could_leave_the_asset_is_rejected(self, value):
        with pytest.raises(ValueError, match="stageAssetPaths"):
            _load().normalize_stage_asset_path(value)

    @pytest.mark.parametrize("value", ["", "   ", None, 7, ["Isaac/"], {"path": "Isaac/"}])
    def test_a_blank_or_non_string_entry_is_rejected(self, value):
        with pytest.raises(ValueError, match="non-blank strings"):
            _load().normalize_stage_asset_path(value)

    def test_an_over_long_path_is_rejected(self):
        mod = _load()
        with pytest.raises(ValueError, match="at most 1024 characters"):
            mod.normalize_stage_asset_path("a/" * 600)

    def test_a_long_rejected_value_is_cut_short_in_the_message(self):
        with pytest.raises(ValueError) as raised:
            _load().normalize_stage_asset_path("../" + "x" * 900)
        assert len(str(raised.value)) < 300


@pytest.mark.unit
class TestStagePathList:
    @pytest.mark.parametrize("value", [None, "", "   ", []])
    def test_no_setting_stages_nothing(self, value):
        assert _load().stage_asset_paths({"stageAssetPaths": value}) == []

    def test_an_absent_setting_stages_nothing(self):
        assert _load().stage_asset_paths({}) == []

    def test_one_path_may_be_given_as_a_string(self):
        assert _load().stage_asset_paths({"stageAssetPaths": "/Isaac/"}) == ["Isaac/"]

    def test_repeated_paths_are_kept_once_in_order(self):
        paths = _load().stage_asset_paths({"stageAssetPaths": ["Isaac/Sensors/", "/Isaac/", "Isaac/Sensors/"]})
        assert paths == ["Isaac/Sensors/", "Isaac/"]

    def test_more_paths_than_the_limit_are_rejected(self):
        mod = _load()
        too_many = [f"folder{i}/" for i in range(mod.MAX_STAGE_ASSET_PATHS + 1)]
        with pytest.raises(ValueError, match=f"at most {mod.MAX_STAGE_ASSET_PATHS}"):
            mod.stage_asset_paths({"stageAssetPaths": too_many})

    @pytest.mark.parametrize("value", [{"a": "Isaac/"}, 3, True])
    def test_a_value_that_is_not_a_list_or_a_string_is_rejected(self, value):
        with pytest.raises(ValueError, match="must be a list"):
            _load().stage_asset_paths({"stageAssetPaths": value})

    def test_paths_up_to_the_total_length_are_accepted(self):
        mod = _load()
        # Two entries of 1,024 characters (the per-entry maximum), 2,048 in total.
        paths = ["a" * 1023 + "/", "b" * 1023 + "/"]
        assert mod.stage_asset_paths({"stageAssetPaths": paths}) == paths
        assert sum(len(p) for p in paths) == mod.MAX_STAGE_ASSET_PATHS_TOTAL_LENGTH

    def test_paths_over_the_total_length_are_rejected_before_any_listing(self):
        mod = _load()
        paths = ["a" * 1023 + "/", "b" * 1023 + "/", "c"]
        with pytest.raises(ValueError, match="total 2049 characters; at most 2048 are allowed"):
            mod.stage_asset_paths({"stageAssetPaths": paths})

    def test_the_total_counts_the_paths_as_validated(self):
        # Leading slashes and surrounding blanks are not sent to the job, so they do not count:
        # 1,024 + 1,024 + 3 characters as written, 1,023 + 1,023 + 2 = 2,048 as validated.
        mod = _load()
        paths = [" /" + "a" * 1022 + "/ ", "/" + "b" * 1022 + "/", "/cc"]
        assert mod.stage_asset_paths({"stageAssetPaths": paths}) == ["a" * 1022 + "/", "b" * 1022 + "/", "cc"]


def _stage_definition_with(objects_extra, paths):
    """lambda_handler on a run that stages ``paths`` from the test asset plus ``objects_extra``."""
    mod = _load()
    objects = {**_OBJECTS, **{_ROOT + k: v for k, v in objects_extra.items()}}
    event = _event({"mode": "train", "stageAssetPaths": paths})
    sfn = MagicMock()
    with patch.object(mod, "s3_client", FakeBucket(objects)), patch.object(mod, "sfn_client", sfn):
        try:
            return json.loads(mod.lambda_handler(event, MagicMock())["definition"]), sfn
        except ValueError as error:
            return error, sfn


@pytest.mark.unit
class TestFilesTheContainerCannotWrite:
    @pytest.mark.parametrize("key", [
        "Isaac/../escape.usd", "Isaac/./here.usd", "Isaac//double.usd", "Isaac/trailing/./x.usd",
    ])
    def test_a_key_with_a_dot_or_empty_segment_fails_before_a_job_is_submitted(self, key):
        result, sfn = _stage_definition_with({key: 1}, ["Isaac/"])
        assert isinstance(result, ValueError)
        assert "cannot be staged: its path has a '.', '..' or empty segment" in str(result)
        sfn.send_task_failure.assert_called_once()

    @pytest.mark.parametrize("name", ["n" * 256, "é" * 128])
    def test_a_name_longer_than_the_file_system_holds_fails_before_a_job_is_submitted(self, name):
        result, sfn = _stage_definition_with({f"Isaac/{name}": 1}, ["Isaac/"])
        assert isinstance(result, ValueError)
        assert "longer than 255 bytes" in str(result)
        sfn.send_task_failure.assert_called_once()

    def test_a_name_of_255_bytes_is_staged(self):
        definition, _ = _stage_definition_with({"Isaac/" + "n" * 255: 1}, ["Isaac/"])
        assert definition["stageAssetFiles"]["fileCount"] == 5

    def test_a_file_and_a_folder_of_the_same_path_fail_before_a_job_is_submitted(self):
        # A bare path names the file and the folder of that name, so both are selected.
        result, sfn = _stage_definition_with({"layout/scene": 3, "layout/scene/part.usd": 4}, ["layout/scene"])
        assert isinstance(result, ValueError)
        assert "a folder of other selected files has the same path" in str(result)
        sfn.send_task_failure.assert_called_once()

    def test_a_file_beside_a_selected_folder_of_its_name_is_not_a_conflict(self):
        definition, _ = _stage_definition_with({"layout/scene": 3, "layout/scene/part.usd": 4},
                                               ["layout/scene/"])
        assert definition["stageAssetFiles"]["fileCount"] == 1


@pytest.mark.unit
class TestStagedSelection:
    def test_a_folder_selects_the_files_below_it_only(self):
        files, _ = _selected(["Isaac/Robots/"])
        assert sorted(files) == ["Isaac/Robots/NVIDIA/NovaCarter/Parts/wheel.usd",
                                 "Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd"]

    def test_a_bare_folder_name_does_not_select_its_prefix_siblings(self):
        files, _ = _selected(["Isaac"])
        assert "Isaac2/unrelated.usd" not in files
        assert "Isaac/Sensors/HESAI/XT-32.usd" in files

    def test_a_bare_file_name_selects_that_file(self):
        files, _ = _selected(["scene.usd"])
        assert files == {"scene.usd": 10}

    def test_the_whole_asset_stays_inside_its_root(self):
        files, _ = _selected(["/"])
        assert "configs/train.json" in files and "Isaac2/unrelated.usd" in files
        assert not any("secret" in key for key in files)

    def test_previews_and_folder_markers_are_not_staged(self):
        files, _ = _selected(["/"])
        assert not any(".previewFile." in key for key in files)
        assert not any(key.endswith("/") or key == "" for key in files)

    def test_overlapping_selections_count_each_file_once(self):
        files, _ = _selected(["Isaac/", "Isaac/Robots/", "Isaac/Sensors/HESAI/XT-32.usd"])
        assert len(files) == 4
        assert sum(files.values()) == 4_204 + 1_000 + 2_000 + 3_000

    @pytest.mark.parametrize("path", [
        "Isaac/Missing/", "missing.usd", "Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd.previewFile.gif",
    ])
    def test_a_selection_that_names_no_file_is_rejected(self, path):
        with pytest.raises(ValueError, match="names no file in the asset"):
            _selected([path])

    def test_too_many_files_are_rejected_and_the_listing_stops(self):
        mod = _load()
        objects = {f"{_ROOT}big/f{i:05d}.usd": 1 for i in range(5_000)}
        with patch.object(mod, "MAX_STAGED_ASSET_FILES", 150):
            fake = FakeBucket(objects, page_size=100)
            with patch.object(mod, "s3_client", fake), pytest.raises(ValueError, match="more than 150 files"):
                mod.list_stage_asset_files(_BUCKET, _ROOT, ["big/"])
        assert fake.pages_read == 2

    def test_too_many_bytes_are_rejected(self):
        mod = _load()
        objects = {f"{_ROOT}big/a.usd": 600, f"{_ROOT}big/b.usd": 600}
        with patch.object(mod, "MAX_STAGED_ASSET_BYTES", 1_000), \
                patch.object(mod, "s3_client", FakeBucket(objects)), \
                pytest.raises(ValueError, match="GiB of files"):
            mod.list_stage_asset_files(_BUCKET, _ROOT, ["big/"])


@pytest.mark.unit
class TestJobConfiguration:
    @pytest.mark.parametrize("mode_config", [
        {"mode": "train", "task": "Vams-Nav-v0"},
        {"mode": "evaluate", "task": "Vams-Nav-Play-v0", "checkpointPath": "checkpoints/E1/model_9.pt"},
    ])
    def test_the_job_receives_the_asset_root_and_the_validated_paths(self, mode_config):
        definition = _definition(_event({**mode_config, "stageAssetPaths": ["/Isaac/Robots/", "Isaac/Sensors/"]}))
        assert definition["stageAssetFiles"] == {
            "rootS3Uri": f"s3://{_BUCKET}/{_ROOT}",
            "paths": ["Isaac/Robots/", "Isaac/Sensors/"],
            "fileCount": 3,
            "totalBytes": 4_204 + 1_000 + 2_000,
        }

    def test_the_root_comes_from_the_manifest_not_the_run_configuration(self):
        definition = _definition(_event({"mode": "train", "stageAssetPaths": ["Isaac/"],
                                         "bucketAsset": "other-bucket",
                                         "inputAssetLocationKey": "xother999/"}))
        assert definition["stageAssetFiles"]["rootS3Uri"] == f"s3://{_BUCKET}/{_ROOT}"

    @pytest.mark.parametrize("mode_config", [
        {"mode": "train"},
        {"mode": "evaluate", "checkpointPath": "checkpoints/E1/model_9.pt"},
    ])
    def test_a_run_without_the_setting_keeps_its_job_configuration(self, mode_config):
        definition = _definition(_event(mode_config))
        assert "stageAssetFiles" not in definition

    def test_a_missing_asset_root_fails_closed(self):
        mod = _load()
        event = _event({"mode": "train", "stageAssetPaths": ["Isaac/"]}, inputAssetLocationKey="")
        with patch.object(mod, "s3_client", FakeBucket(_OBJECTS)), \
                patch.object(mod, "sfn_client", MagicMock()) as sfn, \
                pytest.raises(ValueError, match="missing bucketAsset or inputAssetLocationKey"):
            mod.lambda_handler(event, MagicMock())
        sfn.send_task_failure.assert_called_once()

    def test_a_rejected_path_fails_the_run_before_a_job_is_submitted(self):
        mod = _load()
        event = _event({"mode": "train", "stageAssetPaths": ["../xother999/"]})
        with patch.object(mod, "s3_client", FakeBucket(_OBJECTS)), \
                patch.object(mod, "sfn_client", MagicMock()) as sfn, \
                pytest.raises(ValueError, match="stay inside the asset"):
            mod.lambda_handler(event, MagicMock())
        sfn.send_task_failure.assert_called_once()


def _constant(node):
    """The value of a module-level constant expression: a literal, or ``*`` and ``**`` of literals."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _constant(node.left) * _constant(node.right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        return _constant(node.left) ** _constant(node.right)
    raise AssertionError(f"not a constant expression: {ast.dump(node)}")


def _literal_assignments(path: str, name: str) -> list:
    """Every constant assigned to ``name`` at module level in ``path``, read from the source text.

    The container is its own code bundle, so its package cannot be imported beside this one; parsing
    the source finds each declaration, and each caller asserts how many it found."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    found.append(_constant(node.value))
    return found


def _container_staging():
    """The container's staging module, loaded alone from its file: it imports only the standard
    library and boto3, which this suite's environment has."""
    spec = importlib.util.spec_from_file_location("isaaclab_container_staging", _STAGING_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _outcome(function, *args):
    """``("ok", result)`` or ``("rejected", None)`` for a ValueError, so two copies can be compared."""
    try:
        return "ok", function(*args)
    except ValueError:
        return "rejected", None


# Paths both layers must treat alike: accepted ones normalize to the same path, rejected ones are
# rejected by both.
_PARITY_PATHS = [
    "Isaac/", "/Isaac/Robots/", "Isaac", "/", "  Isaac/Sensors  ", "a/b/c.usd", "Isaac/Props/small KLT.usd",
    "Isaac/ü/é.usd", "x" * 1024, "x" * 1025, "", "   ", None, 7, ["Isaac/"],
    "../other/", "Isaac/../..", "./Isaac", "Isaac/./Robots", "Isaac//Robots", "Isaac/.",
    "s3://asset-bucket/x/", "https://example.com/a.usd", "file:///etc/passwd", "C:/Isaac", "C:\\Isaac",
    "Isaac\\Robots", "Isaac/\x00.usd", "Isaac/\nRobots/", "Isaac/\x7f", "Isaac/\x1f",
]

# Asset-relative file paths both layers must judge alike.
_PARITY_RELATIVE_FILES = [
    "Isaac/Robots/a.usd", "a", "Isaac/" + "n" * 255, "Isaac/" + "n" * 256, "Isaac/" + "é" * 127 + "e",
    "Isaac/" + "é" * 128, "Isaac/../a", "Isaac/./a", "Isaac//a", "../a", "./a", "/a", "a/", "",
]


@pytest.mark.unit
class TestTheContainerAppliesTheSameLimits:
    @pytest.mark.parametrize("name", ["MAX_STAGE_ASSET_PATHS", "MAX_STAGE_ASSET_PATH_LENGTH",
                                      "MAX_STAGE_ASSET_PATHS_TOTAL_LENGTH", "MAX_STAGED_ASSET_FILES",
                                      "MAX_STAGED_ASSET_BYTES", "MAX_STAGED_NAME_BYTES",
                                      "PREVIEW_FILE_MARKER"])
    def test_the_two_copies_agree(self, name):
        container_values = _literal_assignments(_STAGING_MODULE, name)
        assert len(container_values) == 1, f"{name} is declared {len(container_values)} times in staging.py"
        assert container_values[0] == getattr(_load(), name)

    @pytest.mark.parametrize("value", _PARITY_PATHS)
    def test_both_layers_normalize_a_path_alike(self, value):
        lambda_side = _outcome(_load().normalize_stage_asset_path, value)
        assert _outcome(_container_staging().normalize_stage_asset_path, value) == lambda_side

    @pytest.mark.parametrize("relative", _PARITY_RELATIVE_FILES)
    def test_both_layers_judge_a_file_path_alike(self, relative):
        assert _container_staging().unstageable_reason(relative) == _load().unstageable_reason(relative)

    @pytest.mark.parametrize("relatives", [
        ["a/b.usd", "a/c.usd"], ["a", "a/b.usd"], ["a/b", "a/b/c/d.usd"], ["ab", "a/b.usd"], ["a.usd", "a/x"],
    ])
    def test_both_layers_find_the_same_file_and_folder_conflicts(self, relatives):
        files = {_ROOT + r: 1 for r in relatives}
        lambda_side = _outcome(_load().require_stageable, files, _ROOT)
        assert _outcome(_container_staging().require_stageable, files, _ROOT) == lambda_side

    def test_both_layers_bound_the_total_path_length_alike(self):
        mod, container = _load(), _container_staging()
        for paths in (["a" * 1023 + "/", "b" * 1023 + "/"], ["a" * 1023 + "/", "b" * 1023 + "/", "c"]):
            lambda_side = _outcome(mod.stage_asset_paths, {"stageAssetPaths": paths})
            container_side = _outcome(container.parse_stage_config,
                                      {"rootS3Uri": f"s3://{_BUCKET}/{_ROOT}", "paths": paths})
            assert lambda_side[0] == container_side[0]
            if lambda_side[0] == "ok":
                assert container_side[1][2] == lambda_side[1]

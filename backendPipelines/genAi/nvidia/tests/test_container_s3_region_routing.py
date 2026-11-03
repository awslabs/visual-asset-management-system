#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The five deployable NVIDIA containers address each asset bucket in the bucket's own Region.

An asset bucket may be in another Region than the deployment. The pipeline definition carries a
``bucketRegions`` map (bucket name -> Region, deployment-Region buckets left out) that
``constructPipeline`` built from the Regions ``vamsExecute`` resolved from the manifest; the container
registers it before its first S3 command (``manifest_io.register_bucket_regions``) and every ``aws s3``
/ ``aws s3api`` command it issues against an asset bucket carries ``--region`` for that bucket
(``manifest_io.s3_region_args``). The model-cache bucket (``S3_MODEL_BUCKET``, in ``model_manager``) is
in the deployment Region and is deliberately outside the rule.

The helper is duplicated per container because each is its own Docker build context whose Dockerfile
COPYs an explicit file list, so no shared module is importable at container runtime. The copies are
compared structurally here, so an edit to one that is not propagated fails. The reference container
``cosmos/predict/containerv1`` is outside the set, as it is for every other check in this directory.
"""

import ast
import importlib.util
import os
import re
import sys
from unittest.mock import patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NVIDIA = os.path.dirname(HERE)

# Container directory per deployable entry point.
CONTAINERS = {
    "cosmos3": "cosmos/3/container",
    "predict-v2.5": "cosmos/predict/containerv2.5",
    "reason": "cosmos/reason/container",
    "transfer": "cosmos/transfer/container",
    "gr00t": "gr00t/container",
}
# The functions whose source must not drift between copies.
ROUTING_HELPERS = ("register_bucket_regions", "bucket_region", "s3_region_args")
# Modules whose `aws s3` / `aws s3api` commands address asset buckets, so each must carry --region.
ASSET_BUCKET_MODULES = ("__main__.py", "manifest_io.py")
# The one module that addresses the model-cache bucket only (deployment Region, no --region).
MODEL_CACHE_MODULE = "model_manager.py"

_AWS_S3_ARGV = re.compile(r'\[\s*"aws",\s*"s3(?:api)?"')


def _path(name, module):
    return os.path.join(NVIDIA, *CONTAINERS[name].split("/"), module)


def _read(name, module):
    with open(_path(name, module), encoding="utf-8") as f:
        return f.read()


def _load_manifest_io(name):
    """One container's manifest_io, hermetically: the module name is shared by every container."""
    spec = importlib.util.spec_from_file_location(f"manifest_io_{name.replace('-', '_').replace('.', '_')}",
                                                  _path(name, "manifest_io.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_entrypoint(name):
    """One container's __main__.py under an alias, with its sibling modules resolved from its own
    directory and not left behind for the next container's load."""
    container_dir = os.path.dirname(_path(name, "__main__.py"))
    siblings = ("inference", "model_manager", "manifest_io", "evaluation")
    saved = {sibling: sys.modules.get(sibling) for sibling in siblings}
    sys.path.insert(0, container_dir)
    try:
        for sibling in siblings:
            sys.modules.pop(sibling, None)
        spec = importlib.util.spec_from_file_location(
            f"entrypoint_{name.replace('-', '_').replace('.', '_')}", _path(name, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        # The entry point's own manifest_io, so the registry the test fills is the one its commands read.
        return module, sys.modules["manifest_io"]
    finally:
        sys.path.remove(container_dir)
        for sibling, previous in saved.items():
            if previous is None:
                sys.modules.pop(sibling, None)
            else:
                sys.modules[sibling] = previous


def _argv_lists(source):
    """Every `["aws", "s3", ...]` / `["aws", "s3api", ...]` list literal in `source`, as text."""
    spans = []
    for match in _AWS_S3_ARGV.finditer(source):
        depth, i = 0, match.start()
        while i < len(source):
            if source[i] == "[":
                depth += 1
            elif source[i] == "]":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        spans.append(source[match.start():i + 1])
    return spans


def _helper_shapes(name):
    """The AST of each routing helper (and the registry assignment), docstrings stripped, so the copies
    compare on structure rather than on text."""
    tree = ast.parse(_read(name, "manifest_io.py"))
    shapes = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ROUTING_HELPERS:
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                body = body[1:]
            shapes[node.name] = ast.dump(ast.Module(body=body, type_ignores=[]))
            shapes[node.name + ":args"] = ast.dump(node.args)
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_bucket_regions" for t in node.targets):
            shapes["_bucket_regions"] = ast.dump(node)
    return shapes


def test_the_covered_set_is_exactly_the_five_deployable_containers():
    assert set(CONTAINERS) == {"cosmos3", "predict-v2.5", "reason", "transfer", "gr00t"}
    for name in CONTAINERS:
        for module in ASSET_BUCKET_MODULES + (MODEL_CACHE_MODULE,):
            assert os.path.isfile(_path(name, module)), (name, module)


def test_the_routing_helpers_are_structurally_identical_across_the_five_copies():
    shapes = {name: _helper_shapes(name) for name in CONTAINERS}
    for name, shape in shapes.items():
        assert set(shape) >= set(ROUTING_HELPERS) | {"_bucket_regions"}, (name, sorted(shape))
    reference = shapes["cosmos3"]
    for name, shape in shapes.items():
        assert shape == reference, f"{name}'s routing helpers drifted from cosmos3's"


@pytest.mark.parametrize("name", sorted(CONTAINERS))
class TestS3RegionArgs:
    def test_a_registered_bucket_yields_region_args_and_others_none(self, name):
        mio = _load_manifest_io(name)
        mio.register_bucket_regions({"remote-bkt": "eu-west-1", "far-bkt": "ap-southeast-2"})
        assert mio.s3_region_args("s3://remote-bkt/xid/clip.mp4") == ["--region", "eu-west-1"]
        assert mio.s3_region_args("s3://far-bkt/") == ["--region", "ap-southeast-2"]
        assert mio.s3_region_args("s3://local-bkt/xid/clip.mp4") == []
        assert mio.s3_region_args("/tmp/output/clip.mp4") == []
        assert mio.s3_region_args("") == [] and mio.s3_region_args(None) == []

    def test_the_first_registered_argument_decides_and_local_paths_are_skipped(self, name):
        mio = _load_manifest_io(name)
        mio.register_bucket_regions({"remote-bkt": "eu-west-1"})
        assert mio.s3_region_args("/tmp/out.mp4", "s3://remote-bkt/k") == ["--region", "eu-west-1"]
        assert mio.s3_region_args("s3://local-bkt/k", "/tmp/in.mp4") == []

    def test_an_older_definition_registers_nothing_and_a_re_registration_replaces(self, name):
        mio = _load_manifest_io(name)
        mio.register_bucket_regions({"remote-bkt": "eu-west-1"})
        mio.register_bucket_regions(None)
        assert mio.s3_region_args("s3://remote-bkt/k") == []
        mio.register_bucket_regions({"": "eu-west-1", "bkt": "", "bkt2": None, "kept": "us-west-2"})
        assert mio.bucket_region("s3://kept/k") == "us-west-2"
        assert mio.bucket_region("s3://bkt/k") == "" and mio.bucket_region("s3://bkt2/k") == ""


@pytest.mark.parametrize("name", sorted(CONTAINERS))
def test_every_asset_bucket_s3_command_carries_the_region_args(name):
    """Source rule: each `aws s3` / `aws s3api` argv literal in the asset-bucket modules spreads
    `s3_region_args(...)` into the list, so no command addresses an asset bucket in the CLI's default
    Region. The model-cache module is the control: its commands address the deployment-Region cache
    bucket and carry none."""
    for module in ASSET_BUCKET_MODULES:
        lists = _argv_lists(_read(name, module))
        assert lists, f"{name}/{module} issues no aws s3 command; the scan would assert nothing"
        for argv in lists:
            assert "s3_region_args(" in argv, f"{name}/{module}: {argv}"
    cache_lists = _argv_lists(_read(name, MODEL_CACHE_MODULE))
    assert cache_lists
    assert not any("s3_region_args(" in argv for argv in cache_lists), name


@pytest.mark.parametrize("name", sorted(CONTAINERS))
def test_the_entry_point_registers_the_definition_map_before_its_first_s3_command(name):
    source = _read(name, "__main__.py")
    register = source.index('manifest_io.register_bucket_regions(definition.get("bucketRegions"))')
    load = source.index("definition = load_pipeline_definition()")
    assert load < register
    # Nothing between loading the definition and registering the map issues an S3 command.
    assert not _argv_lists(source[load:register])


@pytest.mark.parametrize("name", sorted(CONTAINERS))
def test_the_entry_points_download_and_upload_pass_region_for_a_registered_bucket(name, tmp_path):
    """Behavioural check through the real helpers: with `remote-bkt` registered, the entry point's
    download/upload (or sync, for GR00T) helpers hand `aws` a `--region` for it and none for a bucket
    in the deployment Region."""
    entry, mio = _load_entrypoint(name)
    mio.register_bucket_regions({"remote-bkt": "eu-west-1"})
    calls = []

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        # `aws s3 cp <s3> <local>` leaves a file behind, which some entry points stat afterwards.
        if argv[:3] == ["aws", "s3", "cp"] and not str(argv[4]).startswith("s3://"):
            target = tmp_path.joinpath(argv[4]) if not os.path.isabs(argv[4]) else argv[4]
            with open(target, "wb") as f:
                f.write(b"x")

        class Result:
            returncode = 0
            stderr = ""
            stdout = "[]"
        return Result()

    local = tmp_path / "f.bin"
    local.write_bytes(b"x")
    with patch.object(entry.subprocess, "run", fake_run):
        if name == "gr00t":
            entry.download_asset_from_s3("s3://remote-bkt/xid/asset/", tmp_path / "in")
            entry.upload_output_to_s3(tmp_path, "s3://local-bkt/pipelines/out/", "gr00tOutput_1")
        else:
            entry.download_from_s3("s3://remote-bkt/xid/clip.mp4", tmp_path / "in" / "clip.mp4")
            entry.upload_to_s3(local, "s3://local-bkt/pipelines/out/clip.mp4")
    assert len(calls) == 2
    remote, local_call = calls
    assert remote[:3] == ["aws", "s3", "sync" if name == "gr00t" else "cp"]
    assert remote[-2:] == ["--region", "eu-west-1"]
    assert "--region" not in local_call

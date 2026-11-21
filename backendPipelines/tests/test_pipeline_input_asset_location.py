#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pipelines take where an input sits in its asset from the manifest, not from the S3 key.

Each manifest input entry carries the file's asset-relative ``relativePath`` and its asset root
(``assetRootS3Key`` in the bucket ``assetRootBucket``). A pipeline whose outputs keep the input's
subfolder carries the first entry's values the way it carries ``inputVersionId``: ``vamsExecute`` copies
them as ``inputRelativePath``, ``inputAssetRootS3Key`` and ``inputAssetRootBucket``, ``openPipeline``
forwards them in the state-machine input, and ``constructPipeline`` hands them to the container, which
takes the subfolder from the folder of ``relativePath``. Searching the key for the asset ID stays only as
the fallback for a launch without them: the key of an asset created on an existing bucket folder does
not contain the asset ID, and from the second workflow step on an input is keyed under the run's output
folder.

The chain checks call each pipeline's own handlers with stub events, one hop at a time: ``vamsExecute``
with a manifest whose first entry names the location, ``openPipeline`` with the event ``vamsExecute``
sends, and the builder with the state-machine input. Every module is loaded by path with its own lambda
directory first on ``sys.path`` (each pipeline vendors a ``manifestHelper`` and a ``customLogging`` of the
same name), and ``boto3.client`` hands it stubs, so nothing reaches AWS. The stub
event names an asset root and a relative path that the input key does not contain, so what reaches the
next hop can only have come from the event. Each pipeline's own suite pins what the container does with
the values (``PER_PIPELINE_SUITES``).

Durable (root CLAUDE.md Rule 13): the chain checks run over every pipeline in
``MANIFEST_PATH_PIPELINES``, and the container checks over every Cosmos entrypoint in the tree, so a
pipeline copied from one of these keeps the rule.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pipelines that carry the first input entry's location through vamsExecute -> openPipeline ->
# constructPipeline (or the module that builds the container definition). ``extension`` is the input's
# extension, which every openPipeline gate admits; ``config`` is the run's input configuration (served at
# its S3 location), ``event`` adds the run settings a handler requires, and ``env`` the variables a module
# reads beyond ``ENV``.
MANIFEST_PATH_PIPELINES = {
    "preview/3dThumbnail": {
        "vamsExecute": ["vamsExecutePreview3dThumbnailPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".glb"},
    "conversion/coordinateTransform": {
        "vamsExecute": ["vamsExecuteCoordinateTransformPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".e57"},
    "conversion/cogConvert": {
        "vamsExecute": ["vamsExecuteCogConvertPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".tif"},
    "genAi/nvidia/cosmos/3": {
        "vamsExecute": ["vamsExecuteCosmos3Pipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".mp4",
        "event": {"cosmosPrompt": "a robot arm stacks two boxes"}},
    "genAi/nvidia/cosmos/predict": {
        "vamsExecute": ["vamsExecuteCosmosVideo2WorldPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".mp4",
        "event": {"cosmosPrompt": "a robot arm stacks two boxes"}},
    "genAi/nvidia/cosmos/reason": {
        "vamsExecute": ["vamsExecuteCosmosReasonPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".mp4"},
    "genAi/nvidia/cosmos/transfer": {
        "vamsExecute": ["vamsExecuteCosmosTransferPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".mp4"},
    "genAi/cadStepAgent": {
        "vamsExecute": ["vamsExecuteCadStepAgentPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".step",
        "config": {"prompt": "Add a 3 mm fillet to every outer edge"},
        # The subfolder is resolved in the Lambda; the container receives it as relativeSubdir. Without a
        # location it refuses a key that is under neither the asset ID nor a run's output folder.
        "builderReads": ("inputRelativePath",), "refusesWithoutLocation": "OutputNameError"},
    "genAi/imageTo3d": {
        "vamsExecute": ["vamsExecuteImageTo3dPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".png"},
    "multi/modelOps": {
        "vamsExecute": ["vamsExecuteModelOps.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py", "extension": ".glb",
        "config": {"outputType": ".usdz"}, "builderReads": ("inputRelativePath",)},
    "multi/rapidPipelineEKS": {
        "vamsExecute": ["vamsExecuteRapidPipelineEKS.py"],
        "openPipeline": "openPipeline.py", "builder": "consolidated_handler.py", "extension": ".glb",
        "env": {"CONTAINER_IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/rapid-pipeline:test"},
        # Only the relative path travels: the state machine enumerates its task payload.
        "fields": ("inputRelativePath",), "builderReads": ("inputRelativePath",)},
}

# The suite of each pipeline that pins what its builder and container do with the location.
PER_PIPELINE_SUITES = {
    "preview/3dThumbnail": "preview/3dThumbnail/lambda/tests/test_thumbnail_input_asset.py",
    "conversion/coordinateTransform": "conversion/coordinateTransform/lambda/tests/test_coord_input_asset.py",
    "conversion/cogConvert": "conversion/cogConvert/lambda/tests/test_cog_input_asset.py",
    "genAi/nvidia/cosmos/3": "tests/test_pipeline_input_asset_location.py",
    "genAi/nvidia/cosmos/predict": "tests/test_pipeline_input_asset_location.py",
    "genAi/nvidia/cosmos/reason": "tests/test_pipeline_input_asset_location.py",
    "genAi/nvidia/cosmos/transfer": "genAi/nvidia/cosmos/transfer/lambda/tests/test_cosmos_transfer_control_input.py",
    "genAi/cadStepAgent": "genAi/cadStepAgent/lambda/tests/test_cad_step_agent_handlers.py",
    "genAi/imageTo3d": "genAi/imageTo3d/lambda/tests/test_imageto3d_input_asset.py",
    "multi/modelOps": "multi/modelOps/lambda/tests/test_output_relative_subdir.py",
    "multi/rapidPipelineEKS": "multi/rapidPipelineEKS/lambda/tests/test_output_relative_subdir.py",
}

FIELDS = ("inputRelativePath", "inputAssetRootS3Key", "inputAssetRootBucket")
ENTRY_NAMES = {"inputRelativePath": "relativePath", "inputAssetRootS3Key": "assetRootS3Key",
               "inputAssetRootBucket": "assetRootBucket"}

# The input sits at the top of an existing bucket folder; the manifest names a subfolder, root and root
# bucket the key does not contain, so a value that reaches the next hop came from the event.
INPUT_BUCKET = "abkt"
INPUT_KEY = "projects/site-a/input{ext}"
ASSET_ID = "xidA"
LOCATION = {"inputRelativePath": "/r382folder/input{ext}", "inputAssetRootS3Key": "r382root/",
            "inputAssetRootBucket": "r382bucket"}
# What the builder output must contain for each field it reads: the folder of the relative path (a
# builder may hand on the path or only its folder), the root and the root bucket.
MARKERS = {"inputRelativePath": "r382folder", "inputAssetRootS3Key": "r382root",
           "inputAssetRootBucket": "r382bucket"}

RUN_BUCKET = "run-bkt"
MANIFEST_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json"
CONFIG_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
OUTPUT_PREFIX = "pipelines/p1/JOB/output/E1/"

ENV = {
    "AWS_REGION": "us-east-1",
    "AWS_DEFAULT_REGION": "us-east-1",
    "OPEN_PIPELINE_FUNCTION_NAME": "vams-open-pipeline",
    "OPEN_PIPELINE_FUNCTION_NAME_EKS": "vams-open-pipeline",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:Pipeline",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/Pipeline",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/Pipeline:*",
}


def _lambda_dir(pipeline):
    return os.path.join(_REPO_ROOT, "backendPipelines", *pipeline.split("/"), "lambda")


def _lambda_path(pipeline, name):
    return os.path.join(_lambda_dir(pipeline), name)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _local_module_names(directory):
    names = set()
    for entry in os.listdir(directory):
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isfile(os.path.join(directory, entry, "__init__.py")):
            names.add(entry)
    return names


@contextlib.contextmanager
def _pipeline_lambdas(pipeline, env=None, objects=None):
    """Load modules of one pipeline's lambda directory: its directory leads ``sys.path``, every module
    named like one of its files is taken out of ``sys.modules`` for the duration (and restored after),
    the environment its handlers read at import and at call time is set, and ``boto3.client`` returns
    one stub per service (``_client_stub``), at import and at call time alike. Yields the loader and
    the stubs by service name."""
    directory = _lambda_dir(pipeline)
    local = _local_module_names(directory)

    def owned(name):
        return name.split(".", 1)[0] in local

    saved = {name: module for name, module in sys.modules.items() if owned(name)}
    for name in saved:
        del sys.modules[name]
    sys.path.insert(0, directory)
    loaded = []

    def load(file_name):
        alias = f"asset_location_{pipeline.replace('/', '_')}_{file_name[:-3]}"
        spec = importlib.util.spec_from_file_location(alias, os.path.join(directory, file_name))
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        loaded.append(alias)
        spec.loader.exec_module(module)
        return module

    stubs = {}

    def client(service_name, *_args, **_kwargs):
        if service_name not in stubs:
            stubs[service_name] = _client_stub(service_name, objects or {})
        return stubs[service_name]

    try:
        with patch.dict(os.environ, dict(ENV, **(env or {}))), patch("boto3.client", side_effect=client):
            yield load, stubs
    finally:
        sys.path.remove(directory)
        for name in [name for name in sys.modules if owned(name)] + loaded:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _client_stub(service, objects):
    """A stand-in for one boto3 client: S3 serves ``objects`` (key -> JSON document) and answers HeadObject,
    Lambda and Step Functions accept the call, every other call returns a MagicMock."""
    client = MagicMock(name=f"{service}-client")
    if service == "s3":
        def get_object(Bucket, Key, **_kwargs):
            body = json.dumps(objects.get(Key, {})).encode("utf-8")
            return {"Body": io.BytesIO(body), "ContentLength": len(body)}

        client.get_object.side_effect = get_object
        client.head_object.return_value = {"ContentLength": 1024, "VersionId": "v1",
                                           "ContentType": "application/octet-stream"}
    elif service == "lambda":
        client.invoke.side_effect = lambda **_kwargs: {"StatusCode": 200, "Payload": io.BytesIO(b"{}")}
    elif service == "stepfunctions":
        client.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:1:execution:Pipeline:job", "startDate": "2026-01-01"}
    elif service == "events":
        client.put_events.return_value = {"FailedEntryCount": 0, "Entries": [{"EventId": "e1"}]}
    return client


def _manifest(extension, with_location=True):
    entry = {"bucket": INPUT_BUCKET, "key": INPUT_KEY.format(ext=extension), "assetId": ASSET_ID,
             "databaseId": "db1", "versionId": ""}
    if with_location:
        entry.update({ENTRY_NAMES[field]: value.format(ext=extension) for field, value in LOCATION.items()})
    return {
        "inputFiles": [entry],
        "outputs": {"bucket": RUN_BUCKET, "files": OUTPUT_PREFIX + "files/",
                    "previews": OUTPUT_PREFIX + "previews/", "metadata": OUTPUT_PREFIX + "metadata/",
                    "results": OUTPUT_PREFIX + "results/"},
        "auxBucket": "aux-bkt", "auxTempPrefix": f"{ASSET_ID}/input{extension}/pipelines/p1/",
        "inputMetadataS3Location": f"s3://{RUN_BUCKET}/pipelines/workflowExecutionInputs/E1/metadata.json",
        "systemConfig": {"orchestrationBusArn": "arn:aws:events:us-east-1:1:event-bus/vams-orchestration",
                         "orchestrationEventPrefix": "vams.test.execution.E1.pipeline.P1"},
    }


def _state_input(pipeline, with_location=True):
    """The event an openPipeline and a builder receive: the vamsExecute payload fields any of them read."""
    extension = MANIFEST_PATH_PIPELINES[pipeline]["extension"]
    event = {
        "jobName": "job-382",
        "inputS3AssetFilePath": f"s3://{INPUT_BUCKET}/{INPUT_KEY.format(ext=extension)}",
        "outputS3AssetFilesPath": f"s3://{RUN_BUCKET}/{OUTPUT_PREFIX}files/",
        "outputS3AssetPreviewPath": f"s3://{RUN_BUCKET}/{OUTPUT_PREFIX}previews/",
        "outputS3AssetMetadataPath": f"s3://{RUN_BUCKET}/{OUTPUT_PREFIX}metadata/",
        "outputS3AssetResultsPath": f"s3://{RUN_BUCKET}/{OUTPUT_PREFIX}results/",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://aux-bkt/{ASSET_ID}/input{extension}/pipelines/p1/",
        "assetId": ASSET_ID,
        "databaseId": "db1",
        "inputMetadataS3Location": f"s3://{RUN_BUCKET}/pipelines/workflowExecutionInputs/E1/metadata.json",
        "inputConfigurationS3Location": f"s3://{RUN_BUCKET}/{CONFIG_KEY}",
        "inputVersionId": "",
        "inputBucketRegion": "",
        "outputBucketRegion": "",
        "sfnExternalTaskToken": "tok-382",
        "externalSfnTaskToken": "tok-382",
        "executingUserName": "user1",
        "executingRequestContext": {},
        "orchestrationEventPrefix": "vams.test.execution.E1.pipeline.P1",
        "outputFileType": ".glb",
        "operation": "CONSTRUCT_PIPELINE",
    }
    event.update(MANIFEST_PATH_PIPELINES[pipeline].get("event", {}))
    if with_location:
        event.update({field: value.format(ext=extension) for field, value in LOCATION.items()})
    return event


def _objects(pipeline, manifest=None):
    """The S3 documents a run reads: its manifest and its input configuration."""
    objects = {CONFIG_KEY: MANIFEST_PATH_PIPELINES[pipeline].get("config", {})}
    if manifest is not None:
        objects[MANIFEST_KEY] = manifest
    return objects


def _fields(pipeline):
    return MANIFEST_PATH_PIPELINES[pipeline].get("fields", FIELDS)


def _context():
    """A Lambda context with the attributes a handler logs or budgets against."""
    context = MagicMock(aws_request_id="req-382", function_name="vams-test", function_version="$LATEST")
    context.get_remaining_time_in_millis.return_value = 300000
    return context


def _run_vams_execute(pipeline, name, with_location):
    spec = MANIFEST_PATH_PIPELINES[pipeline]
    manifest = _manifest(spec["extension"], with_location)
    body = {"TaskToken": "tok-382", "executingUserName": "user1", "executingRequestContext": {},
            "inputManifestS3Location": f"s3://{RUN_BUCKET}/{MANIFEST_KEY}",
            "inputConfigurationS3Location": f"s3://{RUN_BUCKET}/{CONFIG_KEY}"}
    with _pipeline_lambdas(pipeline, spec.get("env"), _objects(pipeline, manifest)) as (load, stubs):
        module = load(name)
        with patch("time.sleep"):
            response = module.lambda_handler({"body": json.dumps(body)}, _context())
    assert response.get("statusCode") == 200, (pipeline, name, response)
    calls = [c for c in stubs["lambda"].invoke.call_args_list
             if c.kwargs.get("FunctionName") == ENV["OPEN_PIPELINE_FUNCTION_NAME"]]
    assert calls, (pipeline, name, "openPipeline was not invoked")
    return json.loads(calls[0].kwargs["Payload"])


def _run_open_pipeline(pipeline, with_location):
    spec = MANIFEST_PATH_PIPELINES[pipeline]
    env = dict(spec.get("env", {}), ALLOWED_INPUT_FILEEXTENSIONS=spec["extension"])
    with _pipeline_lambdas(pipeline, env, _objects(pipeline)) as (load, stubs):
        module = load(spec["openPipeline"])
        response = module.lambda_handler(_state_input(pipeline, with_location), _context())
    assert stubs["stepfunctions"].start_execution.called, (pipeline, "the state machine was not started", response)
    return json.loads(stubs["stepfunctions"].start_execution.call_args.kwargs["input"])


def _run_builder(pipeline, with_location):
    spec = MANIFEST_PATH_PIPELINES[pipeline]
    with _pipeline_lambdas(pipeline, spec.get("env"), _objects(pipeline)) as (load, _stubs):
        module = load(spec["builder"])
        result = module.lambda_handler(_state_input(pipeline, with_location), _context())
    return json.dumps(result, default=str)


@pytest.mark.parametrize("pipeline", sorted(MANIFEST_PATH_PIPELINES))
class TestTheChainCarriesTheLocation:
    def test_vams_execute_copies_the_first_entrys_fields(self, pipeline):
        extension = MANIFEST_PATH_PIPELINES[pipeline]["extension"]
        expected = {field: LOCATION[field].format(ext=extension) for field in _fields(pipeline)}
        for name in MANIFEST_PATH_PIPELINES[pipeline]["vamsExecute"]:
            payload = _run_vams_execute(pipeline, name, with_location=True)
            assert {field: payload.get(field) for field in expected} == expected, name
            # An entry written before the fields sends them empty.
            payload = _run_vams_execute(pipeline, name, with_location=False)
            assert [payload.get(field) for field in expected] == [""] * len(expected), name

    def test_open_pipeline_forwards_them(self, pipeline):
        extension = MANIFEST_PATH_PIPELINES[pipeline]["extension"]
        expected = {field: LOCATION[field].format(ext=extension) for field in _fields(pipeline)}
        sfn_input = _run_open_pipeline(pipeline, with_location=True)
        assert {field: sfn_input.get(field) for field in expected} == expected
        # An event from an older vamsExecute forwards them empty.
        sfn_input = _run_open_pipeline(pipeline, with_location=False)
        assert [sfn_input.get(field) for field in expected] == [""] * len(expected)

    def test_the_builder_hands_them_on(self, pipeline):
        spec = MANIFEST_PATH_PIPELINES[pipeline]
        with_location = _run_builder(pipeline, with_location=True)
        if spec.get("refusesWithoutLocation"):
            with pytest.raises(Exception) as refused:
                _run_builder(pipeline, with_location=False)
            assert type(refused.value).__name__ == spec["refusesWithoutLocation"]
            without = ""
        else:
            without = _run_builder(pipeline, with_location=False)
        for field in spec.get("builderReads", FIELDS):
            assert MARKERS[field] in with_location, (field, with_location)
            assert MARKERS[field] not in without, (field, without)


def test_every_listed_pipeline_names_the_suite_that_pins_it():
    assert sorted(PER_PIPELINE_SUITES) == sorted(MANIFEST_PATH_PIPELINES)
    for path in PER_PIPELINE_SUITES.values():
        assert os.path.isfile(os.path.join(_REPO_ROOT, "backendPipelines", *path.split("/"))), path


# --------------------------------------------------------------------------------------------------
# Cosmos: the four constructPipeline Lambdas put the fields on the definition, and every container
# entrypoint takes the subfolder from relativePath.
# --------------------------------------------------------------------------------------------------

COSMOS_LAMBDAS = ("genAi/nvidia/cosmos/3", "genAi/nvidia/cosmos/predict", "genAi/nvidia/cosmos/reason",
                  "genAi/nvidia/cosmos/transfer")
_COSMOS_ROOT = os.path.join(_REPO_ROOT, "backendPipelines", "genAi", "nvidia", "cosmos")
COSMOS_CONTAINERS = sorted(
    os.path.relpath(root, _COSMOS_ROOT).replace(os.sep, "/")
    for root, _, names in os.walk(_COSMOS_ROOT)
    if "__main__.py" in names and os.sep + "tests" not in root)
# Sibling modules a Cosmos entrypoint imports by bare name, which every NVIDIA container defines.
_SIBLINGS = ("inference", "model_manager", "manifest_io", "manifestHelper", "customLogging",
             "customLogging.logger")


def _load_isolated(directory, file_name, alias):
    saved = {name: sys.modules.get(name) for name in _SIBLINGS + ("huggingface_hub",)}
    sys.path.insert(0, directory)
    try:
        for name in _SIBLINGS:
            sys.modules.pop(name, None)
        # The predict v1 entrypoint's model manager imports huggingface_hub at load; the path
        # helpers under test never call it.
        if importlib.util.find_spec("huggingface_hub") is None:
            stub = type(sys)("huggingface_hub")
            stub.snapshot_download = MagicMock()
            sys.modules["huggingface_hub"] = stub
        spec = importlib.util.spec_from_file_location(alias, os.path.join(directory, file_name))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(directory)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module


def test_every_cosmos_container_is_checked():
    assert COSMOS_CONTAINERS == ["3/container", "predict/containerv1", "predict/containerv2.5",
                                 "reason/container", "transfer/container"]


@pytest.mark.parametrize("pipeline", COSMOS_LAMBDAS)
def test_cosmos_construct_pipeline_puts_the_location_on_the_definition(pipeline):
    module = _load_isolated(os.path.dirname(_lambda_path(pipeline, "constructPipeline.py")),
                            "constructPipeline.py", f"construct_{pipeline.replace('/', '_')}")
    event = {"inputS3AssetFilePath": "s3://abkt/projects/site-a/clips/source.mp4",
             "outputS3AssetFilesPath": "s3://run-bkt/pipelines/p1/J/output/E1/files/", "assetId": "xidA",
             "inputRelativePath": "/clips/source.mp4", "inputAssetRootS3Key": "projects/site-a/",
             "inputAssetRootBucket": "abkt"}
    command = module.lambda_handler(event, MagicMock())["definition"]
    definition = json.loads(command[-1])
    assert {field: definition[field] for field in FIELDS} == {
        "inputRelativePath": "/clips/source.mp4", "inputAssetRootS3Key": "projects/site-a/",
        "inputAssetRootBucket": "abkt"}
    # An event from an older Lambda carries none, and the definition names them empty.
    del event["inputRelativePath"], event["inputAssetRootS3Key"], event["inputAssetRootBucket"]
    definition = json.loads(module.lambda_handler(event, MagicMock())["definition"][-1])
    assert [definition[field] for field in FIELDS] == ["", "", ""]


@pytest.fixture(scope="module", params=COSMOS_CONTAINERS)
def cosmos_entrypoint(request):
    directory = os.path.join(_COSMOS_ROOT, *request.param.split("/"))
    return _load_isolated(directory, "__main__.py", f"cosmos_entry_{request.param.replace('/', '_')}")


class TestCosmosSubfolder:
    def test_an_asset_on_an_existing_bucket_folder(self, cosmos_entrypoint):
        uri = "s3://abkt/projects/site-a/clips/day1/source.mp4"
        # The key does not contain the asset ID: the search alone writes at the top of the folder.
        assert cosmos_entrypoint.compute_relative_subdir(uri, "xidA") == ""
        assert cosmos_entrypoint.compute_relative_subdir(uri, "xidA", "/clips/day1/source.mp4") == "clips/day1/"

    def test_a_chained_input(self, cosmos_entrypoint):
        uri = "s3://run-bkt/pipelines/p0/J/output/E1/files/clips/source.mp4"
        assert cosmos_entrypoint.compute_relative_subdir(uri, "xidA") == ""
        assert cosmos_entrypoint.compute_relative_subdir(uri, "xidA", "/clips/source.mp4") == "clips/"

    def test_same_named_inputs_in_two_folders_keep_apart(self, cosmos_entrypoint):
        left = cosmos_entrypoint.compute_relative_subdir(
            "s3://abkt/projects/site-a/left/source.mp4", "xidA", "/left/source.mp4")
        right = cosmos_entrypoint.compute_relative_subdir(
            "s3://abkt/projects/site-a/right/source.mp4", "xidA", "/right/source.mp4")
        assert (left, right) == ("left/", "right/")

    def test_a_file_at_the_asset_root(self, cosmos_entrypoint):
        assert cosmos_entrypoint.compute_relative_subdir("s3://abkt/xidA/source.mp4", "xidA", "/source.mp4") == ""

    def test_without_the_manifest_the_key_is_searched(self, cosmos_entrypoint):
        assert cosmos_entrypoint.compute_relative_subdir("s3://abkt/xidA/clips/source.mp4", "xidA") == "clips/"

#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Shared loader and fakes for the system/genAiMetadata lambda tests.

Every handler is loaded BY PATH under a name only this suite uses, with ``boto3.client`` and
``boto3.resource`` patched for the duration of the module body, so no real client is built and no
other pipeline's same-named module (``openPipeline``, ``manifestHelper``, ...) can answer for this
one. The basename is suite-private for the same reason (backendPipelines/CLAUDE.md, Test Conventions).
"""

import importlib
import importlib.util
import io
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
LAMBDA_DIR = os.path.dirname(TESTS_DIR)
PIPELINE_ROOT = os.path.dirname(LAMBDA_DIR)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(PIPELINE_ROOT)))
VAMS_SCHEMA_DIR = os.path.join(PIPELINE_ROOT, "vamsSchema")
VIEWER_CONFIG = os.path.join(REPO_ROOT, "web", "src", "visualizerPlugin", "config", "viewerConfig.json")
BACKEND_METADATA_MODEL = os.path.join(REPO_ROOT, "backend", "backend", "models", "metadata.py")

TASK_TOKEN = "tok-sysgenai"

# Modules this pipeline's handlers import by bare name. Each is dropped from sys.modules before a
# load so the copy under LAMBDA_DIR answers, not a same-named module another pipeline's suite loaded.
_LOCAL_MODULES = ("manifestHelper", "fileClassifier", "analysisCommon", "metadataCatalog",
                  "classificationVocabulary", "vectorsearch", "vectorsearch.embeddings")

DEFAULT_ENV = {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:SystemGenAiMetadata",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.png,.pdf",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/VAMSStateMachine-SystemGenAiMetadataTest",
    "STATE_MACHINE_LOG_GROUP_ARN":
        "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/VAMSStateMachine-SystemGenAiMetadataTest:*",
    "VECTOR_SEARCH_ENABLED": "true",
    "USE_FARGATE_RENDERER": "false",
    "MAX_INPUT_FILE_SIZE_MB": "2048",
    "MAX_POINT_CLOUD_POINTS": "20000000",
    "BEDROCK_ANALYSIS_MODEL_ID": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
    "EMBEDDING_DIMENSIONS": "1024",
}


def stub_custom_logging():
    """customLogging is a per-pipeline package with an identical logger in each; stubbed so the
    handlers import without aws_lambda_powertools."""
    if "customLogging" not in sys.modules:
        pkg = types.ModuleType("customLogging")
        logger = types.ModuleType("customLogging.logger")
        logger.safeLogger = lambda **kw: MagicMock()
        pkg.logger = logger
        sys.modules["customLogging"] = pkg
        sys.modules["customLogging.logger"] = logger


def _drop_local_modules():
    for name in _LOCAL_MODULES:
        sys.modules.pop(name, None)


def load_handler(stem, env=None):
    """This pipeline's ``<stem>.py`` executed fresh under a suite-private name.

    Environment overrides hold only while the module body runs and are restored afterwards; the
    module-level boto3 clients are MagicMocks the test replaces with fakes."""
    stub_custom_logging()
    overrides = dict(DEFAULT_ENV)
    overrides.update(env or {})
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    _drop_local_modules()
    sys.path.insert(0, LAMBDA_DIR)
    path = os.path.join(LAMBDA_DIR, f"{stem}.py")
    assert os.path.isfile(path), path
    module_name = f"sysgenai_{stem}_undertest"
    try:
        with patch("boto3.client", side_effect=lambda *a, **k: MagicMock()), \
                patch("boto3.resource", side_effect=lambda *a, **k: MagicMock()):
            spec = importlib.util.spec_from_file_location(module_name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)
    finally:
        sys.path.remove(LAMBDA_DIR)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    loaded = os.path.normcase(os.path.normpath(os.path.abspath(mod.__file__)))
    assert loaded == os.path.normcase(os.path.normpath(path)), (
        f"module shadow: loaded {mod.__file__}, expected {path}")
    return mod


def load_local(name):
    """A non-handler module of this pipeline (fileClassifier, analysisCommon, manifestHelper,
    vectorsearch.embeddings) under its real name, resolved from LAMBDA_DIR."""
    stub_custom_logging()
    _drop_local_modules()
    sys.path.insert(0, LAMBDA_DIR)
    try:
        mod = importlib.import_module(name)
    finally:
        sys.path.remove(LAMBDA_DIR)
    expected_dir = os.path.normcase(os.path.normpath(LAMBDA_DIR))
    assert os.path.normcase(os.path.normpath(mod.__file__)).startswith(expected_dir), mod.__file__
    return mod


def _decorator_passthrough(*args, **kwargs):
    """Stands in for ``root_validator``/``validator``: ``@root_validator`` hands the function straight
    through, ``@validator("field")`` returns an identity decorator."""
    if args and callable(args[0]) and not kwargs:
        return args[0]
    return lambda function: function


class _InertBaseModel:
    """A BaseModel that accepts class keywords (``extra='ignore'``) and validates nothing."""

    def __init_subclass__(cls, **kwargs):
        pass


def load_backend_metadata_validators():
    """``backend/backend/models/metadata.py`` loaded by path with its pydantic model layer inert.

    The pipeline suite runs in an interpreter whose pydantic is v2 (the backend suite runs in its own
    venv), under which the module's ``@root_validator`` models fail at import. Only the module-level
    functions are wanted here — ``validate_metadata_value_common`` and the ``_validate_*`` GeoJSON
    checks have no pydantic dependency — so ``aws_lambda_powertools.utilities.parser`` and
    ``pydantic.Field`` are replaced for the load, and so is ``geojson``: the module imports that
    third-party package and never uses it, and this suite must not depend on it being installed.
    Callers assert a positive AND a negative control through the returned module so a load that
    produced nothing usable cannot pass silently."""
    stub_custom_logging()
    assert os.path.isfile(BACKEND_METADATA_MODEL), BACKEND_METADATA_MODEL
    parser = types.ModuleType("aws_lambda_powertools.utilities.parser")
    parser.BaseModel = _InertBaseModel
    parser.root_validator = _decorator_passthrough
    parser.validator = _decorator_passthrough
    pydantic_stub = types.ModuleType("pydantic")
    pydantic_stub.Field = lambda *args, **kwargs: None
    stubs = {
        "aws_lambda_powertools": types.ModuleType("aws_lambda_powertools"),
        "aws_lambda_powertools.utilities": types.ModuleType("aws_lambda_powertools.utilities"),
        "aws_lambda_powertools.utilities.parser": parser,
        "pydantic": pydantic_stub,
        "geojson": types.ModuleType("geojson"),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("sysgenai_backend_metadata_model", BACKEND_METADATA_MODEL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return mod


class _FakeBody:
    """The streaming body a fake get_object returns: ``read()`` and ``iter_chunks(n)``."""

    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def iter_chunks(self, chunk_size):
        for start in range(0, len(self._data), chunk_size):
            yield self._data[start:start + chunk_size]


class FakeS3:
    """An in-memory S3 keyed by (bucket, key); records every put and get in order."""

    def __init__(self, objects=None, heads=None):
        self.objects = dict(objects or {})
        self.heads = dict(heads or {})
        self.puts = []
        self.gets = []

    def put_object(self, Bucket, Key, Body, **kwargs):
        data = Body if isinstance(Body, bytes) else str(Body).encode("utf-8")
        self.objects[(Bucket, Key)] = data
        self.puts.append((Bucket, Key))
        return {}

    def get_object(self, Bucket, Key, **kwargs):
        self.gets.append((Bucket, Key, dict(kwargs)))
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": Key}}, "GetObject")
        data = self.objects[(Bucket, Key)]
        byte_range = kwargs.get("Range")
        if byte_range:
            end = int(byte_range.split("-")[1])
            data = data[:end + 1]
        return {"Body": _FakeBody(data)}

    def head_object(self, Bucket, Key, **kwargs):
        if (Bucket, Key) in self.heads:
            return dict(self.heads[(Bucket, Key)])
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        data = self.objects[(Bucket, Key)]
        return {"ContentLength": len(data), "ETag": '"etag-%d"' % len(data),
                "ContentType": "application/octet-stream", "VersionId": "v1"}

    def json_at(self, bucket, key):
        return json.loads(self.objects[(bucket, key)].decode("utf-8"))

    def put_json(self, bucket, key, body):
        self.objects[(bucket, key)] = json.dumps(body).encode("utf-8")


class FakeBedrock:
    """Scripted ``converse``: each script entry is a response dict or an exception, in call order.
    An exhausted script repeats its last entry."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def converse_response(text, input_tokens=100, output_tokens=50):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens,
                  "totalTokens": input_tokens + output_tokens},
    }


def client_error(code, message="", operation="Converse"):
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def read_json_file(path):
    with io.open(path, encoding="utf-8") as handle:
        return json.load(handle)

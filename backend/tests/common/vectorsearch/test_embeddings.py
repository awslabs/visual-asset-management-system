# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bedrock text embeddings, one adapter per model family, behind a single ``embed_text``.

The pipeline embeds documents and the NLP route embeds queries through the same function, so the two
sides can only agree if the request shape is pinned here: body keys per family, the purpose mapping
for asymmetric models, the per-family truncation constants asserted BY NAME, the vector length check,
and which Bedrock errors are non-retryable. The client is a hand-written fake (house style) that
records what it was sent and answers from a scripted body -- moto has no usable Bedrock runtime.

The module is loaded by file path; it is also imported by the root conftest as
``common.vectorsearch.embeddings`` for the handlers.
"""

import ast
import importlib.util
import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "vectorsearch", "embeddings.py"
)

TITAN = "amazon.titan-embed-text-v2:0"
NOVA = "amazon.nova-2-multimodal-embeddings-v1:0"
COHERE = "cohere.embed-multilingual-v3"


@pytest.fixture
def emb():
    spec = importlib.util.spec_from_file_location("embeddings_under_test", os.path.abspath(_MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBedrock:
    """Records the invoke_model kwargs and answers with a scripted JSON body (or raises)."""

    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error
        self.calls = []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        raw = json.dumps(self.body).encode("utf-8")
        return {"body": StreamingBody(io.BytesIO(raw), len(raw)), "contentType": "application/json"}


def _client_error(code, message="rejected"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "InvokeModel")


@pytest.mark.unit
class TestConstantsByName:
    def test_truncation_constants(self, emb):
        assert emb.TITAN_V2_MAX_INPUT_CHARS == 30_000
        assert emb.NOVA_MME_TEXT_MAX_CHARS == 8_192
        assert emb.COHERE_V3_MAX_CHARS == 2_048

    def test_purposes(self, emb):
        assert emb.PURPOSES == ("index", "query")


@pytest.mark.unit
class TestSelfContainment:
    """The file is vendored byte-identically into the pipeline Lambda, so it may import only the
    standard library and boto3/botocore."""

    def test_imports_are_stdlib_and_boto_only(self):
        with open(os.path.abspath(_MODULE_PATH), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                roots.add((node.module or "").split(".")[0])
        assert roots <= {"json", "re", "typing", "boto3", "botocore"}, sorted(roots)

    def test_import_creates_no_client(self, emb):
        assert emb._default_client is None


@pytest.mark.unit
class TestModelFamilies:
    def test_slug(self, emb):
        assert emb.slug_model_id(TITAN) == "amazon-titan-embed-text-v2-0"
        assert emb.slug_model_id("Cohere.Embed-Multilingual-V3") == "cohere-embed-multilingual-v3"
        assert emb.slug_model_id("--a__b--") == "a-b"

    def test_default_model_yields_the_registry_index_name(self, emb):
        """The literal the CDK index name is built from (``vec-${slugModelId(id)}-${dims}``); the
        TypeScript ``slugModelId`` test pins this same string, so the two implementations agree on the
        default deployment without a cross-language harness."""
        assert f"vec-{emb.slug_model_id(TITAN)}-1024" == "vec-amazon-titan-embed-text-v2-0-1024"

    @pytest.mark.parametrize("model_id, family", [
        (TITAN, "titan-v2"), (NOVA, "nova-mme"), (COHERE, "cohere-v3"), ("cohere.embed-english-v3", "cohere-v3"),
    ])
    def test_family_dispatch(self, emb, model_id, family):
        assert emb.model_family(model_id) == family

    @pytest.mark.parametrize("model_id", ["cohere.embed-v4:0", "amazon.titan-embed-image-v1", "anthropic.claude-haiku-4-5", ""])
    def test_unknown_family_is_an_error(self, emb, model_id):
        with pytest.raises(emb.EmbeddingModelError) as excinfo:
            emb.model_family(model_id)
        assert excinfo.value.code == "UnsupportedModel"


@pytest.mark.unit
class TestTruncation:
    @pytest.mark.parametrize("model_id, limit", [(TITAN, 30_000), (NOVA, 8_192), (COHERE, 2_048)])
    def test_truncates_to_the_family_constant(self, emb, model_id, limit):
        text = "x" * (limit + 1)
        assert len(emb.truncate_for_model(text, model_id)) == limit
        assert emb.truncate_for_model("short", model_id) == "short"

    def test_unknown_model_raises(self, emb):
        with pytest.raises(emb.EmbeddingModelError):
            emb.truncate_for_model("x", "unknown.model")


@pytest.mark.unit
class TestRequestBodies:
    def test_titan_body_and_purpose_independence(self, emb):
        index = emb.build_request_body("hello", model_id=TITAN, dimensions=1024, purpose="index")
        query = emb.build_request_body("hello", model_id=TITAN, dimensions=1024, purpose="query")
        assert index == {"inputText": "hello", "dimensions": 1024, "normalize": True}
        assert query == index

    def test_nova_body_maps_purpose(self, emb):
        index = emb.build_request_body("hello", model_id=NOVA, dimensions=1024, purpose="index")
        query = emb.build_request_body("hello", model_id=NOVA, dimensions=3072, purpose="query")
        assert index == {
            "schemaVersion": "nova-multimodal-embed-v1",
            "taskType": "SINGLE_EMBEDDING",
            "singleEmbeddingParams": {
                "embeddingPurpose": "GENERIC_INDEX",
                "embeddingDimension": 1024,
                "text": {"truncationMode": "END", "value": "hello"},
            },
        }
        assert query["singleEmbeddingParams"]["embeddingPurpose"] == "TEXT_RETRIEVAL"
        assert query["singleEmbeddingParams"]["embeddingDimension"] == 3072

    def test_cohere_body_maps_purpose(self, emb):
        index = emb.build_request_body("hello", model_id=COHERE, dimensions=1024, purpose="index")
        query = emb.build_request_body("hello", model_id=COHERE, dimensions=1024, purpose="query")
        assert index == {"texts": ["hello"], "input_type": "search_document", "truncate": "END"}
        assert query["input_type"] == "search_query"

    def test_body_text_is_truncated(self, emb):
        body = emb.build_request_body("x" * 40_000, model_id=TITAN, dimensions=1024)
        assert len(body["inputText"]) == 30_000

    @pytest.mark.parametrize("model_id, dimensions", [(TITAN, 1000), (TITAN, 3072), (NOVA, 512), (COHERE, 512)])
    def test_unsupported_dimensions_are_rejected(self, emb, model_id, dimensions):
        with pytest.raises(emb.EmbeddingModelError) as excinfo:
            emb.build_request_body("hello", model_id=model_id, dimensions=dimensions)
        assert excinfo.value.code == "UnsupportedDimensions"

    @pytest.mark.parametrize("model_id, dimensions", [(TITAN, 256), (TITAN, 512), (NOVA, 384), (COHERE, 1024)])
    def test_supported_dimensions_are_accepted(self, emb, model_id, dimensions):
        emb.build_request_body("hello", model_id=model_id, dimensions=dimensions)

    def test_invalid_purpose_is_a_programming_error(self, emb):
        with pytest.raises(ValueError):
            emb.build_request_body("hello", model_id=TITAN, dimensions=1024, purpose="rank")

    @pytest.mark.parametrize("text", ["", "   "])
    def test_empty_text_is_rejected(self, emb, text):
        with pytest.raises(emb.EmbeddingModelError) as excinfo:
            emb.build_request_body(text, model_id=TITAN, dimensions=1024)
        assert excinfo.value.code == "EmptyInput"


@pytest.mark.unit
class TestEmbedText:
    def test_titan_vector_is_returned_as_floats_of_the_requested_length(self, emb):
        client = FakeBedrock(body={"embedding": [0.001 * i for i in range(256)], "inputTextTokenCount": 2})
        vector = emb.embed_text("hello world", model_id=TITAN, dimensions=256, purpose="index", client=client)
        assert len(vector) == 256 and all(isinstance(v, float) for v in vector)
        assert vector[1] == 0.001

    def test_request_kwargs_are_the_bedrock_invoke_contract(self, emb):
        client = FakeBedrock(body={"embedding": [0.0] * 256, "inputTextTokenCount": 1})
        emb.embed_text("hello", model_id=TITAN, dimensions=256, client=client)
        (call,) = client.calls
        assert call["modelId"] == TITAN
        assert call["contentType"] == "application/json"
        assert call["accept"] == "application/json"
        assert json.loads(call["body"]) == {"inputText": "hello", "dimensions": 256, "normalize": True}

    def test_nova_response_is_read_from_the_embeddings_list(self, emb):
        client = FakeBedrock(body={"embeddings": [{"embeddingType": "TEXT", "embedding": [0.1] * 256}]})
        vector = emb.embed_text("hello", model_id=NOVA, dimensions=256, purpose="query", client=client)
        assert vector == [0.1] * 256
        assert json.loads(client.calls[0]["body"])["singleEmbeddingParams"]["embeddingPurpose"] == "TEXT_RETRIEVAL"

    def test_cohere_response_is_the_first_text_vector(self, emb):
        client = FakeBedrock(body={"embeddings": [[0.2] * 1024], "id": "x", "response_type": "embeddings_floats", "texts": ["hello"]})
        assert emb.embed_text("hello", model_id=COHERE, dimensions=1024, client=client) == [0.2] * 1024

    def test_wrong_vector_length_is_a_dimension_mismatch(self, emb):
        client = FakeBedrock(body={"embedding": [0.0] * 255})
        with pytest.raises(emb.EmbeddingModelError) as excinfo:
            emb.embed_text("hello", model_id=TITAN, dimensions=256, client=client)
        assert excinfo.value.code == "DimensionMismatch"

    def test_body_without_a_vector_is_a_bad_response(self, emb):
        client = FakeBedrock(body={"message": "no embedding here"})
        with pytest.raises(emb.EmbeddingModelError) as excinfo:
            emb.embed_text("hello", model_id=TITAN, dimensions=256, client=client)
        assert excinfo.value.code == "BadResponse"

    @pytest.mark.parametrize("code", ["ValidationException", "AccessDeniedException", "ResourceNotFoundException"])
    def test_non_retryable_bedrock_errors_become_embedding_model_errors(self, emb, code):
        client = FakeBedrock(error=_client_error(code, "input too long"))
        with pytest.raises(emb.EmbeddingModelError) as excinfo:
            emb.embed_text("hello", model_id=TITAN, dimensions=256, client=client)
        assert excinfo.value.code == code
        assert "input too long" in str(excinfo.value)

    def test_throttling_propagates_as_the_client_error(self, emb):
        """Control: the adapter does not swallow every ClientError -- throttling is the caller's to classify."""
        client = FakeBedrock(error=_client_error("ThrottlingException"))
        with pytest.raises(ClientError):
            emb.embed_text("hello", model_id=TITAN, dimensions=256, client=client)

    def test_default_client_is_built_lazily_with_the_retry_config(self, emb):
        built = MagicMock()
        built.invoke_model.side_effect = FakeBedrock(body={"embedding": [0.0] * 256}).invoke_model
        with patch.object(emb.boto3, "client", return_value=built) as factory:
            emb.embed_text("hello", model_id=TITAN, dimensions=256)
            emb.embed_text("again", model_id=TITAN, dimensions=256)
        factory.assert_called_once_with("bedrock-runtime", config=emb.retry_config)
        assert emb._default_client is built


@pytest.mark.unit
class TestRoundVector:
    def test_rounds_to_nine_significant_digits(self, emb):
        assert emb.round_vector([0.123456789012345, 1e-12, -0.5, 0.0]) == [0.123456789, 1e-12, -0.5, 0.0]

    def test_length_and_type_are_preserved(self, emb):
        out = emb.round_vector([1, 2.5, 3])
        assert out == [1.0, 2.5, 3.0] and all(isinstance(v, float) for v in out)

    def test_sig_is_configurable(self, emb):
        assert emb.round_vector([0.123456789], sig=3) == [0.123]

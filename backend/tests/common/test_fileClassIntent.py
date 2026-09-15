# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File-type intent words in a natural-language query map to fileClass ids.

The module is loaded by file path: the root conftest registers ``common`` as a MagicMock package, so a
plain ``from common.vectorsearch import fileClassIntent`` would resolve to a mock attribute.
"""

import importlib.util
import os

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
_MODULE = os.path.join(_BACKEND, "common", "vectorsearch", "fileClassIntent.py")
_CLASSIFIER = os.path.join(
    _BACKEND, "..", "..", "backendPipelines", "system", "genAiMetadata", "lambda", "fileClassifier.py"
)


def _load(name, file_path):
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(file_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def intent():
    return _load("fileClassIntent_under_test", _MODULE)


def test_every_class_has_a_phrase_and_intent_words(intent):
    assert set(intent.FILE_CLASS_PHRASES) == set(intent.FILE_CLASSES)
    assert set(intent.INTENT_WORDS) == set(intent.FILE_CLASSES)
    for cls in intent.FILE_CLASSES:
        if cls == "other":
            # "file" names no kind of file, so the fallback class carries no intent words.
            assert intent.INTENT_WORDS[cls] == ()
        else:
            assert intent.INTENT_WORDS[cls], cls
            assert intent.FILE_CLASS_PHRASES[cls].lower() in intent.INTENT_WORDS[cls]


@pytest.mark.parametrize(
    "query,expected_word",
    [
        ("footage of a harbour crane", "footage"),
        ("a PDF manual for the pump", "pdf"),
        ("Point Cloud of the warehouse", "point cloud"),
        ("the 3D Tiles tileset of the campus", "3d tiles"),
    ],
)
def test_detects_case_insensitively_on_word_boundaries(intent, query, expected_word):
    classes = intent.detect(query)
    assert len(classes) == 1
    assert expected_word in intent.INTENT_WORDS[classes[0]]


def test_substring_is_not_a_word(intent):
    # 'scanner' contains 'scan' (point cloud); 'clipboard' contains 'clip' (video); 'files' contains 'file'.
    assert intent.detect("scanner clipboard files") == []


def test_multiple_classes_come_back_in_registry_order(intent):
    classes = intent.detect("a photo and a video of the site")
    assert len(classes) == 2
    assert classes == sorted(classes, key=intent.FILE_CLASSES.index)


def test_no_intent_is_empty(intent):
    assert intent.detect("blue rusty thing near the fence") == []
    assert intent.detect("a file about the pump") == []
    assert intent.detect("") == []


@pytest.mark.skipif(not os.path.exists(_CLASSIFIER), reason="fileClassifier.py not in the tree")
def test_class_ids_and_phrases_match_the_classifier(intent):
    classifier = _load("fileClassifier_for_intent", _CLASSIFIER)
    assert tuple(intent.FILE_CLASSES) == tuple(classifier.FILE_CLASSES)
    assert intent.FILE_CLASS_PHRASES == classifier.FILE_CLASS_PHRASES

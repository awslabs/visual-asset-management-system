# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-110 / S2-BACKEND-114 / S2-BACKEND-116: the confirmation interlocks must actually run.

Four confirmation fields -- `DeleteMetadataSchemaRequestModel.confirmDelete`,
`ResetPasswordRequestModel.confirmReset`, `UnarchiveAssetRequestModel.confirmUnarchive` and
`DeleteAssetRequestModel.confirmPermanentDelete` -- are declared `Field(default=False)` with a
validator that rejects a falsy value. In pydantic 1.10.13 a plain `@validator` does **not** run when
the field is absent from the body (`Config.validate_all` is False and `ModelField.validate_always` is
False), so a body that simply omits the field parsed cleanly with the default -- satisfying the
interlock it exists to enforce. `always=True` is what makes it live; `models/executions.py` already
uses that spelling.

A per-module guard is what let the two `assetsV3` cases hide after the first two were fixed, so the
sweep at the bottom of this file covers **every** module under `backend/backend/models/`: a
confirmation field is either live, or listed there with the reason it is not.

## Why this asserts `__fields__` rather than reading the declaration

A declaration proves nothing here: the defect *was* a correctly spelled, correctly named validator
that pydantic never invoked. `validate_always` on the parsed `ModelField` is the switch pydantic
itself consults, so asserting it is asserting the live wiring. The behavioural cases below then
confirm the consequence, and `field_info.extra` is asserted empty because pydantic v1 also swallows
an unrecognized `Field()` kwarg into it -- which would make a constraint look declared and validate
nothing.
"""

import importlib
import os
import pathlib

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError

import models
import models.assetsV3
import models.metadataSchema
import models.user
from models.assetsV3 import DeleteAssetRequestModel, UnarchiveAssetRequestModel
from models.metadataSchema import DeleteMetadataSchemaRequestModel
from models.user import ResetPasswordRequestModel

# (model class, confirmation field name)
INTERLOCKS = [
    (DeleteMetadataSchemaRequestModel, "confirmDelete"),
    (ResetPasswordRequestModel, "confirmReset"),
    (UnarchiveAssetRequestModel, "confirmUnarchive"),
    (DeleteAssetRequestModel, "confirmPermanentDelete"),
]

_IDS = [f"{model.__name__}.{field}" for model, field in INTERLOCKS]


@pytest.mark.unit
class TestInterlockValidatorsAreLive:
    @pytest.mark.parametrize("model, field_name", INTERLOCKS, ids=_IDS)
    def test_validator_runs_for_an_absent_value(self, model, field_name):
        """The switch pydantic consults: without it the validator is dead code for an omitted field."""
        assert model.__fields__[field_name].validate_always is True

    @pytest.mark.parametrize("model, field_name", INTERLOCKS, ids=_IDS)
    def test_field_declares_no_swallowed_kwargs(self, model, field_name):
        assert not (model.__fields__[field_name].field_info.extra or {})

    @pytest.mark.parametrize("model, field_name", INTERLOCKS, ids=_IDS)
    def test_the_field_still_defaults_to_unconfirmed(self, model, field_name):
        """The default must stay falsy: an interlock that defaults to confirmed is not an interlock."""
        assert model.__fields__[field_name].default is False


@pytest.mark.unit
class TestUnconfirmedBodiesAreRejected:
    @pytest.mark.parametrize("model, field_name", INTERLOCKS, ids=_IDS)
    @pytest.mark.parametrize("body_label", ["empty body", "explicit false", "unrelated field only"])
    def test_absent_or_false_confirmation_is_rejected(self, model, field_name, body_label):
        body = {
            "empty body": {},
            "explicit false": {field_name: False},
            "unrelated field only": {"metadataSchemaId": "schema-abc"},
        }[body_label]
        with pytest.raises(ValidationError):
            parse(body, model=model)

    @pytest.mark.parametrize("model, field_name", INTERLOCKS, ids=_IDS)
    def test_direct_construction_is_rejected_too(self, model, field_name):
        """`Model()` and `parse({})` must agree -- a handler may build the model either way."""
        with pytest.raises(ValidationError):
            model()

    @pytest.mark.parametrize("model, field_name", INTERLOCKS, ids=_IDS)
    def test_explicit_true_is_accepted(self, model, field_name):
        """Positive control: the interlock must not reject a properly confirmed request."""
        parsed = parse({field_name: True}, model=model)
        assert getattr(parsed, field_name) is True


_MODELS_DIR = pathlib.Path(models.__file__).resolve().parent
_BACKEND_ROOT = _MODELS_DIR.parent

# Confirmation fields that deliberately carry no live model-level interlock. Keyed
# (module, class, field); the value states why, and for a handler-checked field it names the
# handler source that performs the check -- asserted below, so an entry cannot rot into a silent
# exemption. Anything NOT listed here must be `validate_always`.
EXEMPT_CONFIRMATIONS = {
    ("assetsV3", "DeleteFileRequestModel", "confirmPermanentDelete"): (
        "handler-checked: handlers/assets/assetFiles.py rejects a falsy value before deleting"),
    ("assetsV3", "ArchiveAssetRequestModel", "confirmArchive"): (
        "advisory intent signal, not an interlock: archiving is reversible, the operation is "
        "gated by authorization alone, and api/assets.md documents the field as optional"),
    # Declared, never consulted -- neither a validator nor a handler reads these. Owned by the
    # shards for models/roleConstraints.py and models/tag.py, which must decide whether the
    # confirmation is part of those request contracts at all (the request models themselves are
    # currently unused). Listed rather than asserted-inert so making one live stays green.
    ("roleConstraints", "DeleteRoleRequestModel", "confirmDelete"): "pending disposition",
    ("tag", "DeleteTagRequestModel", "confirmDelete"): "pending disposition",
    ("tag", "DeleteTagTypeRequestModel", "confirmDelete"): "pending disposition",
}

# Exemptions whose stated guard is a handler check: (module, class, field) -> handler source.
HANDLER_CHECKED_BY = {
    ("assetsV3", "DeleteFileRequestModel", "confirmPermanentDelete"):
        "handlers/assets/assetFiles.py",
}


#: Floor for the number of model source files the sweep must reach. Well below the current
#: count, so ordinary additions and removals do not touch it, but far enough above zero that a
#: glob which matches nothing (or only the top directory) fails instead of passing vacuously.
MIN_MODEL_FILES_SWEPT = 20


def _module_name_for(path):
    """`models.assetsV3` / `models.assets` / `models` for a file under the models tree.

    A model module can live in a subpackage (`backend/backend/models/assets/`), so the module
    name is derived from the path relative to the package root rather than from the file stem --
    `models.<stem>` would raise ModuleNotFoundError for a nested file. `__init__.py` maps to its
    package, which is why it is swept rather than skipped: a model class declared there is a
    confirmation field like any other.
    """
    parts = list(path.relative_to(_MODELS_DIR).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(["models"] + parts)


def _model_source_files():
    """Every .py file in the models tree, found recursively.

    `glob("*.py")` reaches only the top directory, which silently excluded the real
    `backend/backend/models/assets/` subpackage -- the same per-module blind spot this sweep
    exists to remove, one directory down. `rglob` recurses; `test_the_sweep_reaches_every_model_
    source_file` cross-checks the result against an independent `os.walk`, so a wrong pattern
    cannot quietly shrink the sweep again.
    """
    return sorted(_MODELS_DIR.rglob("*.py"))


def _confirmation_fields():
    """Every confirm* field declared by every model module, as (module, class, field, ModelField).

    The module key is the last dotted component, so a nested module reports the name it is
    listed under in EXEMPT_CONFIRMATIONS.
    """
    found = []
    for path in _model_source_files():
        module = importlib.import_module(_module_name_for(path))
        module_key = module.__name__.split(".")[-1]
        for class_name in dir(module):
            candidate = getattr(module, class_name)
            fields = getattr(candidate, "__fields__", None)
            if not isinstance(fields, dict):
                continue
            if getattr(candidate, "__module__", "") != module.__name__:
                continue
            for field_name, field in fields.items():
                if field_name.startswith("confirm"):
                    found.append((module_key, class_name, field_name, field))
    return found


@pytest.mark.unit
class TestEveryConfirmationFieldIsLiveOrListed:
    """Repo-wide sweep of `backend/backend/models/`, because a per-module guard hid two of these.

    The assertion is one-directional on purpose: an unlisted field that is not `validate_always`
    fails, but a listed field that someone later makes live still passes. Tightening a
    confirmation must never turn this red.
    """

    def test_the_sweep_reaches_every_model_source_file(self):
        """A broken pattern must fail here rather than shrink the sweep silently.

        Two independent checks, because the count and the enumeration fail differently: a floor
        catches a pattern that matches nothing, and a comparison against `os.walk` -- a
        different mechanism from `rglob` -- catches one that matches only some directories. The
        earlier `glob("*.py")` passed every assertion in this file while skipping the
        `models/assets/` subpackage entirely.
        """
        swept = {path.resolve() for path in _model_source_files()}

        assert len(swept) >= MIN_MODEL_FILES_SWEPT, (
            f"the sweep found only {len(swept)} model source file(s) under {_MODELS_DIR}; a "
            f"pattern that matches (almost) nothing makes every verdict below vacuous")

        walked = {
            pathlib.Path(root, name).resolve()
            for root, _dirs, names in os.walk(_MODELS_DIR)
            if "__pycache__" not in pathlib.Path(root).parts
            for name in names
            if name.endswith(".py")
        }
        assert walked - swept == set(), (
            "these model source files are not swept, so a confirmation field declared in one "
            f"of them is unchecked: {sorted(str(path) for path in walked - swept)}")

    def test_the_sweep_actually_finds_the_known_interlocks(self):
        """Positive control: a sweep that discovers nothing would pass vacuously."""
        discovered = {(mod, cls, field) for mod, cls, field, _ in _confirmation_fields()}
        for model, field_name in INTERLOCKS:
            key = (model.__module__.split(".")[-1], model.__name__, field_name)
            assert key in discovered, (
                f"the sweep did not reach {key}, so its verdict covers less than it claims; "
                f"discovered: {sorted(discovered)}")

    def test_no_unlisted_confirmation_field_is_inert(self):
        inert = sorted(
            f"{mod}.{cls}.{field}"
            for mod, cls, field, model_field in _confirmation_fields()
            if not model_field.validate_always and (mod, cls, field) not in EXEMPT_CONFIRMATIONS
        )
        assert inert == [], (
            "A confirmation field whose validator lacks always=True does not run for an omitted "
            "value, so the interlock is unenforceable. Either add always=True or list the field "
            "in EXEMPT_CONFIRMATIONS with the reason. Offenders:\n  " + "\n  ".join(inert))

    @pytest.mark.parametrize(
        "key,handler_source",
        sorted(HANDLER_CHECKED_BY.items()),
        ids=[".".join(key) for key in sorted(HANDLER_CHECKED_BY)],
    )
    def test_a_handler_checked_exemption_is_really_checked_there(self, key, handler_source):
        """The exemption stands only while the named handler still reads the field."""
        source = (_BACKEND_ROOT / handler_source).read_text(encoding="utf-8")
        assert key[2] in source, (
            f"{'.'.join(key)} is exempt because {handler_source} checks it, but that file no "
            f"longer mentions the field -- the confirmation is now enforced nowhere")

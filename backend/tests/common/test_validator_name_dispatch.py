# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `validate()` dispatcher fails CLOSED on a validator name it does not implement.

The dispatcher resolves a field's check out of `_VALIDATOR_DISPATCH`, so the mapping IS the set of
names it implements -- there is no second list that can drift from it. What the lookup replaced was a
flat sequence of `if v['validator'] == '<NAME>'` comparisons with no terminal else: a misspelled name
(`STRING_1024`, `ASSETID`) matched none of them and fell through to `return (True, "")` having had no
rule applied at all, so the field read as validated while nothing validated it.

Three things are pinned here, because each one on its own is insufficient:

  * an unimplemented name raises rather than passing;
  * `commentBody` -- the one free-text field the comments API accepts -- carries a real length bound
    (`STRING_16384`), so an oversize body is rejected as a 400 rather than failing at the DynamoDB
    item-size limit as a 500;
  * every validator name a handler or model NAMES has an entry in the mapping. That is what makes
    raising safe: a name the mapping omits would break a live endpoint rather than quietly widen it.

OVER-TIGHTENING CATCHERS are mandatory here: a name missing from the mapping raises, so an
incomplete mapping would reject legitimate input on any of the 200-plus `validate()` call sites.
`TestEveryKnownNameStillAcceptsALegitimateValue` carries one real value per implemented name.
"""

import importlib.util
import pathlib
import re
import sys

import pytest


def _validators():
    """Resolve the module the way handlers and models do -- through `sys.modules` at call time, so
    the stand-in the autouse mock fixture installs (which re-exports the real module) is what is
    exercised, not whatever was bound at collection time."""
    return sys.modules['common.validators']


def _validate(params):
    return _validators().validate(params)


def _dispatch_names():
    """The names `_VALIDATOR_DISPATCH` implements, read from the SHIPPED module on disk.

    Not via `sys.modules['common.validators']`. That resolves to `tests/mocks/common/validators.py`,
    which re-exports the real module with `if not _name.startswith('_')` -- so `validate` and the
    pattern constants arrive and the mapping, being private, never does. Reaching for it through the
    mock raises `AttributeError` and the guard reports a missing dispatcher rather than a drifted one.

    Behavioural assertions deliberately keep using `_validators()`, because a model resolves `validate`
    through `sys.modules` at call time and must be exercised the same way. Only this introspection of
    a private name needs the module from disk.
    """
    spec = importlib.util.spec_from_file_location(
        '_shipped_common_validators',
        str(pathlib.Path(__file__).resolve().parents[2] / 'backend' / 'common' / 'validators.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = set(module._VALIDATOR_DISPATCH)
    assert names, 'the shipped dispatch mapping is empty; this guard would pass vacuously'
    return names


# One legitimate value per implemented validator name. Every entry is a shape VAMS actually stores.
LEGITIMATE_VALUES = {
    'ID': 'my-pipeline-1',
    'ASSET_ID': 'my.asset v2',
    'UUID': 'fd79afb2-f12d-4a10-809c-78c98007da91',
    'GUID': 'fd79afb2f12d4a10809c78c98007da91',
    'SAGEMAKER_NOTEBOOK_ID': 'nb-1',
    'ID_ARRAY': ['my-pipeline-1'],
    'UUID_ARRAY': ['fd79afb2-f12d-4a10-809c-78c98007da91'],
    'EMAIL_ARRAY': ['user@example.com'],
    'USERID_ARRAY': ['first.last@example.com'],
    'STRING_16384': 'a comment body',
    'STRING_30': 'short',
    'STRING_256': 'x' * 256,
    'STRING_256_ARRAY': ['x' * 256],
    'STRING_JSON': '{"a": 1}',
    'FILE_NAME': 'model.glb',
    'FILE_EXTENSION': '.glb',
    'RELATIVE_FILE_PATH': '/folder/file.txt',
    'RELATIVE_FILE_PATH_ARRAY': ['/folder/file.txt'],
    'DOWNLOAD_KEY_ARRAY': ['/folder/file.txt'],
    'ASSET_PATH': 'assetId/file.txt',
    'ASSET_PATH_PIPELINE': 'pipelines/wf/exec/output/step/',
    'ASSET_AUXILIARYPREVIEW_PATH': 'assetId/preview/thumb.png',
    'OBJECT_NAME': 'My Asset 1',
    'OBJECT_NAME_ARRAY': ['My Asset 1'],
    'EMAIL': 'user@example.com',
    'USERID': 'first.last@example.com',
    'REGEX': '.*',
    'NUMBER': '42',
    'BOOL': 'true',
    'ISO8601_UTC': '2026-01-31T12:00:00Z',
    'SQS_QUEUE_URL': 'https://sqs.us-east-1.amazonaws.com/123456789012/my-queue',
    'EVENTBRIDGE_BUS_ARN': 'arn:aws:events:us-east-1:123456789012:event-bus/my-bus',
    'EVENTBRIDGE_SOURCE': 'com.example.vams',
    'EVENTBRIDGE_DETAIL_TYPE': 'VAMS Pipeline Complete',
    'ARN': 'arn:aws:states:us-east-1:123456789012:execution:sm:exec',
    'CLOUDWATCH_LOG_GROUP_ARN': 'arn:aws:logs:us-east-1:123456789012:log-group:/aws/my-group',
    'CLOUDWATCH_LOG_GROUP_NAME': '/aws/vendedlogs/my-group',
    'LOG_STREAM_NAME': 'stream/1',
    'S3_BUCKET_NAME': 'my-vams-bucket',
}


@pytest.mark.unit
class TestAnUnknownValidatorNameIsRejected:
    """Without the lookup, a name the dispatcher does not implement reported the field valid
    unexamined."""

    @pytest.mark.parametrize('name', [
        'NOT_A_REAL_VALIDATOR',
        'ASSETID',        # a plausible typo of ASSET_ID
        'STRING_1024',    # a plausible typo of STRING_256
        'id',             # right name, wrong case
        '',
    ])
    def test_an_unimplemented_name_raises(self, name):
        with pytest.raises(Exception, match='not a known validator'):
            _validate({'field': {'value': 'anything', 'validator': name}})

    def test_a_non_string_name_raises_rather_than_reaching_the_array_substring_checks(self):
        # `"_ARRAY" in v['validator']` runs before the lookup and would raise TypeError on a
        # non-string, which surfaces as a 500 with no indication of the cause. The name is read
        # defensively there so this message is what a caller gets instead.
        for name in (None, 5, ['ID']):
            with pytest.raises(Exception, match='not a known validator'):
                _validate({'field': {'value': 'anything', 'validator': name}})

    def test_a_missing_validator_key_raises_the_same_way(self):
        with pytest.raises(Exception, match='not a known validator'):
            _validate({'field': {'value': 'anything'}})

    def test_a_populated_field_is_what_the_name_is_checked_for(self):
        # The check runs after the empty/optional short-circuits, so it is reached exactly when
        # there is a value to apply a rule to. A required field with a value is that case.
        with pytest.raises(Exception, match='not a known validator'):
            _validate({'field': {'value': ['a'], 'validator': 'TYPO_ARRAY'}})


@pytest.mark.unit
class TestAnEmptyOptionalFieldNeedsNoValidator:
    """The name is checked AFTER the empty/optional short-circuits, and that ordering is deliberate.

    An optional field with nothing in it is skipped before its validator name is ever consulted --
    that is how the loop has always worked, because there is no value for a rule to apply to. Moving
    the membership check ahead of those short-circuits turned every such field into a 500: a caller
    that names a validator it does not use, on a field it left empty, was previously accepted and
    would have started failing.

    The distinction the ordering buys is worth stating plainly. A misspelled name on a field that
    carries a value is a code defect and must raise, because otherwise no rule is applied and the
    field is reported valid regardless -- the fail-open this lookup exists to close. A
    misspelled name on an empty optional field decides nothing either way.
    """

    def test_an_unknown_name_on_an_empty_optional_field_is_accepted(self):
        assert _validate({'field': {'value': None, 'validator': 'TYPO', 'optional': True}}) == (True, '')

    def test_the_same_holds_for_an_empty_string_and_an_empty_array(self):
        assert _validate({'field': {'value': '', 'validator': 'TYPO', 'optional': True}}) == (True, '')
        assert _validate({'field': {'value': [], 'validator': 'TYPO_ARRAY', 'optional': True}}) == (True, '')

    def test_an_absent_validator_key_on_an_empty_optional_field_is_accepted(self):
        assert _validate({'field': {'value': None, 'optional': True}}) == (True, '')

    def test_a_required_empty_field_still_reports_the_required_error_not_a_name_error(self):
        """Positive control on the ordering: the empty check must still win for a required field."""
        (valid, message) = _validate({'field': {'value': None, 'validator': 'STRING_256'}})
        assert valid is False
        assert 'required field' in message

    def test_a_later_field_is_still_validated_after_an_empty_optional_one(self):
        """The short-circuit is a `continue`, not a `return`; an unknown name must not mask that."""
        (valid, message) = _validate({
            'skipped': {'value': None, 'validator': 'TYPO', 'optional': True},
            'checked': {'value': '!!', 'validator': 'ID'},
        })
        assert valid is False
        assert 'checked' in message


@pytest.mark.unit
class TestStringIsImplementedAndBounded:
    """`commentBody` is the only field validated by `STRING_16384`, and it is written straight into
    the comment item, so the length bound here is the only thing standing between a caller and the
    DynamoDB 400 KB item limit."""

    def test_an_oversize_body_is_rejected(self):
        (valid, message) = _validate({'commentBody': {'value': 'a' * 100000,
                                                      'validator': 'STRING_16384'}})
        assert valid is False
        assert 'commentBody' in message

    def test_the_bound_is_16384_characters(self):
        # Asserted as a number rather than read back off the module: the cap is the contract the
        # comments API documents, so a tightening has to fail here instead of following the code.
        assert _validate({'commentBody': {'value': 'a' * 16384,
                                          'validator': 'STRING_16384'}})[0] is True
        assert _validate({'commentBody': {'value': 'a' * 16385,
                                          'validator': 'STRING_16384'}})[0] is False

    def test_an_ordinary_comment_body_is_still_accepted(self):
        """OVER-TIGHTENING CATCHER: a bound that rejected real comments would be an outage on the
        comments endpoints, which is indistinguishable from a correct fix without this."""
        body = ("Reviewed the turbine housing scan -- the mesh normals on the inner flange look "
                "inverted. Re-running the conversion pipeline with smoothing disabled.\n"
                "See /outputs/step-2/housing.glb for the comparison.")
        assert _validate({'commentBody': {'value': body, 'validator': 'STRING_16384'}})[0] is True

    def test_a_long_review_note_is_accepted(self):
        """OVER-TIGHTENING CATCHER for the bound itself. A review note carrying a pasted log excerpt
        runs to several thousand characters and saves today, so a cap set below that is an outage for
        exactly the callers the bound is meant to keep working."""
        assert _validate({'commentBody': {'value': 'a' * 8000,
                                          'validator': 'STRING_16384'}})[0] is True

    def test_an_empty_body_is_still_a_required_field_error_not_a_length_error(self):
        """CONTROL that the generic pre-checks still run ahead of the length branch."""
        (valid, message) = _validate({'commentBody': {'value': '',
                                                      'validator': 'STRING_16384'}})
        assert valid is False
        assert 'required' in message


@pytest.mark.unit
class TestEveryKnownNameStillAcceptsALegitimateValue:
    """OVER-TIGHTENING CATCHERS. The lookup that decides a name is unknown raises, so a name wrongly
    absent from the mapping would raise on legitimate input at every one of its call sites."""

    def test_the_table_covers_every_implemented_name(self):
        # Otherwise a name added to the mapping without a value here goes unexercised.
        assert set(LEGITIMATE_VALUES) == _dispatch_names()

    @pytest.mark.parametrize('name', sorted(LEGITIMATE_VALUES))
    def test_a_legitimate_value_is_accepted(self, name):
        (valid, message) = _validate({'field': {'value': LEGITIMATE_VALUES[name],
                                                'validator': name}})
        assert valid is True, f"{name} rejected {LEGITIMATE_VALUES[name]!r}: {message}"


@pytest.mark.unit
class TestEveryDeclaredNameIsImplemented:
    """Source-level guard, in the one direction that can still go wrong.

    The mapping is now the only list of implemented names, so the two-way drift check this class used
    to carry -- a parallel membership set against a chain of equality branches -- has no second list
    to compare and is gone. What remains is the direction that matters and that no behavioural test
    can cover: a call site naming a validator the mapping does not implement RAISES, so such a name
    breaks a live endpoint rather than quietly widening it. This walks the package and fails on one.
    """

    @staticmethod
    def _backend_root():
        return pathlib.Path(__file__).resolve().parents[2] / 'backend'

    def test_the_mapping_stays_the_only_list_of_implemented_names(self):
        """Guards the premise that let the drift check be dropped.

        If a second membership list is reintroduced beside the mapping, or an equality branch is
        added back inside validate(), the two can disagree and this file's coverage silently narrows
        to whichever one the dispatcher actually consults.
        """
        source = (self._backend_root() / 'common' / 'validators.py').read_text(encoding='utf-8')
        assert '_VALIDATOR_DISPATCH' in source, 'the dispatch mapping is gone'
        assert 'KNOWN_VALIDATORS' not in source,             'a parallel membership list is back: restore a drift check or remove the list'
        body = source[source.index('def validate(values):'):]
        assert not re.findall(r"v\['validator'\] == '([A-Z_0-9]+)'", body),             'an equality branch is back in validate(); a name it adds is invisible to the mapping'

    def test_every_validator_name_any_handler_or_model_declares_is_implemented(self):
        """Reads the literal names only; the two call sites that pass a name through a variable
        (`registerPipelineExecution._field_valid` and `assetsV3.validate_asset_identifiers`) both
        source it from a fixed dict of names covered here."""
        root = self._backend_root()
        assert root.is_dir(), f"{root} is not a directory; if the package moved this guard is vacuous"
        known = _dispatch_names()
        pattern = re.compile(r"""['"]validator['"]\s*:\s*['"]([A-Za-z_0-9]+)['"]""")
        declared = {}
        scanned = 0
        for path in root.rglob('*.py'):
            scanned += 1
            for name in pattern.findall(path.read_text(encoding='utf-8')):
                declared.setdefault(name, str(path))
        assert scanned > 100, f"only {scanned} module(s) scanned under {root}"
        assert len(declared) > 20, f"only {len(declared)} validator name(s) found under {root}"
        unknown = {name: where for name, where in declared.items() if name not in known}
        assert not unknown, f"validator names no branch implements: {unknown}"


@pytest.mark.unit
class TestAssetPathPipelineValidatesTheVamsAreaRelativeForm:
    """S11-EXTERNALS3-005 / S2-BACKEND-100. The end-state lambda validates its output path keys, then
    joins the run bucket's `baseAssetsPrefix` onto them (executionRecords.run_bucket_key). The order is
    load-bearing in both directions and this pins it:

      * validating the RELATIVE form keeps the anchor at `pipelines/`, so a direct invocation of that
        lambda cannot name an arbitrary folder for it to ingest from;
      * a fix that had prefixed the keys BEFORE validating would fail every run on a prefixed default
        bucket and ingest nothing -- a strictly worse outcome than writing at the bucket root.
    """

    RELATIVE = 'pipelines/p1/job-1/output/EXEC1/files/'

    def _check(self, value):
        return _validate({'assetFilesPathPipelineKey':
                          {'value': value, 'validator': 'ASSET_PATH_PIPELINE'}})

    def test_the_relative_form_the_state_machine_supplies_is_accepted(self):
        valid, message = self._check(self.RELATIVE)
        assert valid, message

    def test_a_prefixed_key_is_rejected(self):
        """The negative that makes the ordering a contract rather than a coincidence: if this ever
        starts passing, the validator has been relaxed and prefixing before validating would go
        unnoticed."""
        valid, _message = self._check('vams-assets/' + self.RELATIVE)
        assert not valid

    def test_a_key_under_an_empty_leading_segment_is_rejected(self):
        """A prefix joined unconditionally onto a '/' baseAssetsPrefix yields a leading slash."""
        valid, _message = self._check('/' + self.RELATIVE)
        assert not valid

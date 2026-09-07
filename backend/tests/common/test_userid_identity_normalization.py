# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

r"""A user id stays Unicode; two spellings of one name do not become two identities.

`\w` on a `str` pattern is Unicode-aware, so `userid_pattern` accepts a user id in any script. That is
the decided position, not an oversight: an ASCII-only class (`re.ASCII`, or spelling the class out as
`[A-Za-z0-9_]`) would refuse every non-Latin external-IDP username, a larger break than the problem it
addresses. Two mechanisms stand in its place, and this file pins both.

  * **NFKC normalization**, applied before validation and before storage on every path that writes or
    looks up a user id. It folds the compatibility spellings of one name together -- a fullwidth 'ａ'
    for 'a', a decomposed accent for a composed one -- so the id that was checked is the id that is
    stored, and the id a later lookup builds. Normalizing on only some of those paths would be worse
    than not normalizing at all: the mismatch would make a lookup miss a row that exists.
  * **A confusable skeleton**, compared at user creation only. NFKC does NOT fold a Cyrillic or Greek
    letter onto its Latin lookalike, so 'аdmin' (Cyrillic U+0430) would otherwise stay a distinct
    DynamoDB key that reads as 'admin' in the permissions editor, the user-roles list and every audit
    record. Creation refuses an id whose skeleton collides with an existing one; an id already stored
    keeps working, which is why the check is at creation and nowhere else.

`TestNonAsciiUserIdsAreAccepted` is the positive control for the decision. An ASCII-only class passes
every other class in this file and fails that one.
"""

import pathlib
import re

import pytest

from common.validators import (
    confusable_skeleton,
    filename_pattern,
    find_confusable_userid,
    normalize_userid,
    normalize_userid_array,
    object_name_pattern,
    userid_pattern,
    validate,
    validate_asset_id,
    validate_filename,
    validate_userid,
)

# Legitimate user ids an external identity provider can issue. None of them is an impersonation of
# anything: they are ordinary names in their own scripts.
LEGITIMATE_NON_ASCII_USER_IDS = [
    '山田.太郎',
    'Müller.Hans',
    'Ισίδωρος',
    'Иван.Петров',
    'محمد.علي',
    'josé.pérez@example.com',
]

FULLWIDTH_A = 'ａ'     # NFKC folds this to 'a'
CYRILLIC_A = 'а'      # reads as 'a'; NFKC does NOT fold it
CYRILLIC_O = 'о'      # reads as 'o'
GREEK_OMICRON = 'ο'   # reads as 'o'
ARMENIAN_OH = 'օ'     # also reads as 'o' -- outside the mapping, deliberately


@pytest.mark.unit
class TestNonAsciiUserIdsAreAccepted:
    """POSITIVE CONTROL for the decision: Unicode user ids are supported, so they must validate."""

    @pytest.mark.parametrize('value', LEGITIMATE_NON_ASCII_USER_IDS)
    def test_a_legitimate_non_ascii_user_id_is_accepted(self, value):
        (valid, message) = validate_userid('userId', value)
        assert valid is True, (
            f"{value!r} is a legitimate external-IDP user id and must be accepted: {message}")

    @pytest.mark.parametrize('value', LEGITIMATE_NON_ASCII_USER_IDS)
    def test_it_is_accepted_through_the_dispatcher(self, value):
        assert validate({'userId': {'value': value, 'validator': 'USERID'}})[0] is True

    def test_it_is_accepted_as_a_userid_array_element(self):
        """USERID_ARRAY is the shape user-role assignment and subscription targets use."""
        values = ['real.user@example.com'] + LEGITIMATE_NON_ASCII_USER_IDS
        assert validate({'userIds': {'value': values, 'validator': 'USERID_ARRAY'}})[0] is True

    def test_the_pattern_is_the_unicode_aware_one(self):
        """Pinned literally. Spelling the class out in ASCII, or compiling with re.ASCII, is the
        rejected position -- it would fail this assertion as well as the ones above."""
        assert userid_pattern == r'^[\w\-\.\+\@]{3,256}$'
        assert re.compile(userid_pattern).fullmatch('Иван.Петров') is not None

    @pytest.mark.parametrize('value', [
        'first.last@example.com',
        'first.last+vams@example.com',
        'user_name-1',
        'SYSTEM_USER',
        'abc',
        'a' * 256,
    ])
    def test_an_ascii_user_id_is_still_accepted(self, value):
        assert validate_userid('userId', value)[0] is True, value

    @pytest.mark.parametrize('value,why', [
        ('ab', 'below the 3-character minimum'),
        ('a' * 257, 'above the 256-character maximum'),
        ('first last', 'a space is not in the class'),
        ('a/b', 'a path separator is not in the class'),
        ('user\ttab', 'a control character is not in the class'),
    ])
    def test_the_rule_still_rejects_what_it_always_rejected(self, value, why):
        """CONTROL. Without these, an accept above could be validation not running at all rather
        than the value being allowed."""
        assert validate_userid('userId', value)[0] is False, f"{value!r} must stay rejected ({why})"


@pytest.mark.unit
class TestFileNameRulesAreAlsoNotAsciiOnly:
    """The decision covered user ids AND filenames, so `filename_pattern` must stay Unicode too.

    `filename_pattern` backs both FILE_NAME and ASSET_ID, and ASSET_ID is validated on read paths as
    well as writes -- narrowing it would make an already-stored non-ASCII assetId unaddressable, not
    merely unnameable.

    The rule is SPLIT, and this class covers one half of it. The shared validator stays Unicode for
    addressability, which is what is asserted here; separately, an assetId a caller supplies when
    CREATING an asset is refused when it is not ASCII, at `create_asset()` rather than in the shared
    rule (`tests/handlers/assets/test_createAsset_ascii_asset_id.py`). So "a non-ASCII assetId is
    accepted" below means accepted by the shared rule -- it is not a statement that one can still be
    created, and it does not contradict the create-time gate.
    """

    def test_the_filename_pattern_is_unicode_aware(self):
        assert r'\w' in filename_pattern

    @pytest.mark.parametrize('value', ['pумп.glb', 'モデル.glb', 'Gebäude.ifc'])
    def test_a_non_ascii_asset_id_is_accepted(self, value):
        (valid, message) = validate_asset_id('assetId', value)
        assert valid is True, f"{value!r} must stay a valid assetId: {message}"

    @pytest.mark.parametrize('value', ['住宅.txt', 'plan_überarbeitet.pdf'])
    def test_a_non_ascii_file_name_is_accepted(self, value):
        (valid, message) = validate_filename('fileName', value)
        assert valid is True, f"{value!r} must stay a valid file name: {message}"

    @pytest.mark.parametrize('value', ['../../etc/passwd', 'a/b', 'a\\b', 'name<bad>', 'trailing.'])
    def test_the_asset_id_rule_still_blocks_what_it_always_blocked(self, value):
        """CONTROL: assetId is interpolated into S3 keys, so breadth must not be absence of a rule."""
        assert validate_asset_id('assetId', value)[0] is False, value

    def test_the_object_name_rule_is_ascii_and_that_is_pre_existing(self):
        """Recorded, not changed. `object_name_pattern` covers authored labels (assetName, dbName),
        never a user id, and has always been ASCII -- so it is not evidence of an intended
        ASCII-only identifier policy, and nothing here widened or narrowed it."""
        assert object_name_pattern == r'^[a-zA-Z0-9\-._\s]{1,256}$'


@pytest.mark.unit
class TestNfkcNormalization:

    def test_two_spellings_of_one_name_resolve_to_the_same_value(self):
        assert normalize_userid(FULLWIDTH_A + 'dmin.user') == 'admin.user'
        assert normalize_userid(FULLWIDTH_A + 'dmin.user') == normalize_userid('admin.user')

    def test_a_decomposed_accent_resolves_to_the_composed_one(self):
        # The two spellings are indistinguishable in a source file, so the inequality below is
        # asserted first: an editor that normalized this file turns it red instead of turning
        # the test into a tautology.
        decomposed = 'josé.perez'   # 'e' + COMBINING ACUTE ACCENT
        composed = 'josé.perez'      # 'e' with an acute accent
        assert decomposed != composed
        assert normalize_userid(decomposed) == normalize_userid(composed) == composed

    def test_an_ascii_user_id_is_returned_unchanged(self):
        """The overwhelming majority of stored ids are ASCII: normalization must be a no-op for them,
        or every existing lookup would start missing its row."""
        for value in ['first.last@example.com', 'SYSTEM_USER', 'user_name-1']:
            assert normalize_userid(value) == value

    def test_normalization_is_idempotent(self):
        once = normalize_userid(FULLWIDTH_A + 'dmin.user')
        assert normalize_userid(once) == once

    def test_a_non_string_passes_through(self):
        """The caller's own type check reports a non-string; normalization must not mask it."""
        assert normalize_userid(None) is None
        assert normalize_userid(7) == 7

    def test_the_array_form_normalizes_each_element(self):
        assert normalize_userid_array([FULLWIDTH_A + 'bc', 'x.y']) == ['abc', 'x.y']
        assert normalize_userid_array('not-a-list') == 'not-a-list'

    def test_a_legitimate_non_ascii_id_survives_normalization(self):
        """NFKC folds compatibility spellings, not scripts: a Japanese or Cyrillic name is unchanged
        and still validates afterwards."""
        for value in LEGITIMATE_NON_ASCII_USER_IDS:
            normalized = normalize_userid(value)
            assert validate_userid('userId', normalized)[0] is True, value


@pytest.mark.unit
class TestModelsStoreTheNormalizedUserId:
    """Normalization has to happen where the value is stored, not only in a helper.

    Each model below is the one a write path parses, so what it holds after parsing is what reaches
    DynamoDB (or the Cognito pool). Two spellings must arrive as one stored value.
    """

    def test_cognito_user_creation_stores_the_normalized_id(self):
        from models.user import CreateCognitoUserRequestModel
        fullwidth = CreateCognitoUserRequestModel(
            userId=FULLWIDTH_A + 'dmin.user', email='a@example.com')
        plain = CreateCognitoUserRequestModel(userId='admin.user', email='a@example.com')
        assert fullwidth.userId == plain.userId == 'admin.user'

    @pytest.mark.parametrize('model_name', ['CreateUserRolesRequestModel',
                                            'UpdateUserRolesRequestModel'])
    def test_user_role_writes_store_the_normalized_id(self, model_name):
        import models.roleConstraints as roleConstraints
        model = getattr(roleConstraints, model_name)(
            userId=FULLWIDTH_A + 'dmin.user', roleName=['admin'])
        assert model.userId == 'admin.user'

    def test_user_role_deletion_looks_up_the_normalized_id(self):
        from models.roleConstraints import DeleteUserRolesRequestModel
        assert DeleteUserRolesRequestModel(
            userId=FULLWIDTH_A + 'dmin.user').userId == 'admin.user'

    def test_a_constraint_user_permission_stores_the_normalized_id(self):
        """This id becomes part of the denormalized row's key and is what Casbin compares the
        caller's own identity against, so it must carry the same spelling as that identity."""
        from models.roleConstraints import UserPermissionModel
        assert UserPermissionModel(userId=FULLWIDTH_A + 'dmin.user', permission='GET',
                                   permissionType='allow').userId == 'admin.user'

    def test_api_key_creation_stores_the_normalized_id(self):
        from models.apiKeys import CreateApiKeyRequestModel
        assert CreateApiKeyRequestModel(apiKeyName='k', userId=FULLWIDTH_A + 'dmin.user',
                                        description='d').userId == 'admin.user'

    def test_a_non_ascii_id_still_parses_through_the_models(self):
        """OVER-TIGHTENING CATCHER: normalization must not have turned into rejection."""
        from models.user import CreateCognitoUserRequestModel
        assert CreateCognitoUserRequestModel(
            userId='Иван.Петров', email='ivan@example.com').userId == 'Иван.Петров'


@pytest.mark.unit
class TestConfusableSkeleton:

    @pytest.mark.parametrize('value,reads_as', [
        (CYRILLIC_A + 'dmin', 'admin'),
        ('r' + CYRILLIC_O + CYRILLIC_O + 't', 'root'),
        ('vams' + GREEK_OMICRON + 'perator', 'vamsoperator'),
        (FULLWIDTH_A + 'dmin', 'admin'),                      # folded by the NFKC pass first
    ])
    def test_a_lookalike_has_the_same_skeleton_as_what_it_reads_as(self, value, reads_as):
        assert confusable_skeleton(value) == confusable_skeleton(reads_as)

    def test_creation_finds_the_existing_user_a_lookalike_collides_with(self):
        existing = ['operator.one', 'admin', 'reader']
        assert find_confusable_userid(CYRILLIC_A + 'dmin', existing) == 'admin'

    @pytest.mark.parametrize('value', LEGITIMATE_NON_ASCII_USER_IDS)
    def test_a_legitimate_non_ascii_id_collides_with_nothing(self, value):
        """OVER-TIGHTENING CATCHER, and the reason the check is a skeleton comparison rather than a
        script restriction: an ordinary non-Latin username must still be creatable."""
        existing = ['admin', 'operator.one', 'ivan.petrov', 'jose.perez@example.com']
        assert find_confusable_userid(value, existing) is None, value

    def test_an_exact_duplicate_is_not_reported_as_a_lookalike(self):
        """An id equal to an existing one is a plain duplicate, answered by Cognito itself."""
        assert find_confusable_userid('admin', ['admin']) is None

    def test_ascii_ids_that_merely_resemble_each_other_are_not_folded(self):
        """CONTROL on the mapping's scope. '0'/'O' and '1'/'l' are distinguishable to a reader, and
        folding them would refuse ordinary user ids that differ only there."""
        assert find_confusable_userid('user0', ['userO']) is None
        assert find_confusable_userid('user1', ['userl']) is None

    def test_case_is_not_folded(self):
        """CONTROL: 'Admin' and 'admin' are visually distinct, so they are two ids, not a lookalike."""
        assert find_confusable_userid('Admin', ['admin']) is None

    def test_the_mapping_is_deliberately_partial(self):
        """The Unicode confusables table is a data file this repository does not carry, so the
        mapping covers the Cyrillic and Greek lookalikes and NFKC handles the compatibility blocks.
        A lookalike from another script -- Armenian U+0585 here -- is NOT caught. Asserted rather
        than left as prose so the limitation is a recorded scope decision: widening the mapping
        turns this red and whoever widens it updates the claim.
        """
        assert find_confusable_userid('r' + ARMENIAN_OH + 'ot', ['root']) is None

    def test_a_non_string_existing_entry_is_skipped(self):
        assert find_confusable_userid('admin', [None, 7, 'admin']) is None


@pytest.mark.unit
class TestEveryUserIdEntryPointNormalizes:
    """Source-level completeness guard for the "same point on every path" property.

    The behavioural tests above prove the helper and the models; they cannot prove that a NEW route
    accepting a user id remembers to normalize it. A path that validates and stores a user id without
    normalizing re-creates exactly the split this fix closes -- two spellings, two rows -- and the
    lookup that misses a stored row is invisible until a user reports it.
    """

    _EXEMPT = {
        # The dispatcher and the normalizers themselves.
        'common/validators.py',
    }

    @staticmethod
    def _files_validating_a_userid():
        root = pathlib.Path(__file__).resolve().parents[2] / 'backend'
        matches = {}
        for path in sorted(root.rglob('*.py')):
            text = path.read_text(encoding='utf-8')
            if re.search(r"""['"]validator['"]\s*:\s*['"]USERID(_ARRAY)?['"]""", text):
                matches[path.relative_to(root).as_posix()] = text
        return matches

    def test_every_such_file_also_normalizes(self):
        files = self._files_validating_a_userid()
        # Non-vacuous: the USERID sites are spread over the auth, subscription, workflow-output and
        # model modules. A count this far below the real one means the walk found the wrong tree.
        assert len(files) >= 10, f"expected the USERID sites, found {sorted(files)}"
        missing = [name for name, text in files.items()
                   if name not in self._EXEMPT
                   and 'normalize_userid' not in text]
        assert not missing, (
            f"these files validate a userId without normalizing it first: {missing}")

    def test_the_claims_boundary_normalizes_the_caller_identity(self):
        """The authenticated identity is the one user id no request body carries, so it is
        normalized where every handler receives it."""
        root = pathlib.Path(__file__).resolve().parents[2] / 'backend'
        text = (root / 'handlers' / 'auth' / '__init__.py').read_text(encoding='utf-8')
        assert 'normalize_userid_array(tokens)' in text

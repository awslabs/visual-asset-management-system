# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Archive status of an S3 key, as decided by common.s3.is_object_version_archived.

The helper is the mandated O(1) replacement for handler-local version scans (backend/CLAUDE.md
Rule 14), so every caller that reports whether a file is archived inherits its answer. Its
missing-object fallback lists with ``Prefix=key``, and a prefix query also returns LONGER sibling
keys -- ``file.glb`` matches ``file.glb.previewFile.png`` -- so the returned delete markers have to
be matched back to the exact key. Counting them instead reports a key that does not exist as
archived whenever an archived sibling happens to start with its name.

tests/conftest.py replaces the whole ``common.s3`` module with a mock (the real one builds a boto3
client at import), so the function under test is loaded from source into a private namespace, the
same way tests/common/test_dynamodb_query_all_items.py loads query_all_items.

That mock is the SECOND body of this fallback, and the classes at the end of this file cover it
alongside the shipped one. Handler tests under assetFiles / assetService / assetVersions /
uploadFile / sqsUploadFileLarge / sqsBucketSync reach the archive check through the registered
``common.s3``, so a double that answers differently -- or reads a different page -- makes those
suites agree with a contract the deployment does not have.
"""

import os
import sys

import pytest
from botocore.exceptions import ClientError

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "s3.py"
)


class _SilentLogger:
    """Stand-in for the module's safeLogger; the helper only warns through it."""

    def warning(self, *args, **kwargs):
        pass


def _load_helper():
    """Extract the page-size constants and is_object_version_archived from the real source."""
    with open(_MODULE_PATH, encoding="utf-8") as f:
        source = f.read()

    start = source.index("S3_VERSIONS_PAGE_SIZE = ")
    end = source.index("\ndef list_all_object_versions(", start)
    segment = source[start:end]
    assert "def is_object_version_archived(" in segment, "loader missed the function under test"

    namespace = {"ClientError": ClientError, "logger": _SilentLogger(), "s3c": None}
    exec(compile(segment, _MODULE_PATH, "exec"), namespace)
    return namespace


_NAMESPACE = _load_helper()
is_object_version_archived = _NAMESPACE["is_object_version_archived"]
S3_VERSIONS_PAGE_SIZE = _NAMESPACE["S3_VERSIONS_PAGE_SIZE"]

BUCKET = "asset-bucket"


def _client_error(code, operation="HeadObject"):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class _FakeS3:
    """An S3 stand-in over ``key -> (live version ids, delete marker, is the marker current)``.

    Models the four behaviours the helper's answer depends on, and nothing else. Each was
    measured against moto before being encoded here, because the helper's correctness rests on
    them rather than on anything in VAMS:

    * HeadObject without a version id answers 404 for a key whose CURRENT version is a delete
      marker, and for a key with no versions at all -- indistinguishably, which is why the
      listing fallback exists. A key whose current version is live answers 200 even when an
      older delete marker remains in its history.
    * HeadObject WITH a version id answers 405 MethodNotAllowed for a delete-marker version.
    * ``Prefix`` is a prefix match, not an exact match, and keys sort ascending -- so a key
      always precedes its own longer siblings and leads the page.
    * Every entry carries ``IsLatest``. A delete marker is latest only while it is the current
      version; a restored key keeps the old marker with ``IsLatest: False``.

    The combined Versions + DeleteMarkers count is capped at ``MaxKeys``, per key newest first.
    """

    def __init__(self, objects=None, head_error=None):
        # objects: {key: {"versions": [ids...], "marker": id or None, "marker_current": bool}}
        self.objects = dict(objects or {})
        self.head_error = head_error
        self.listing_calls = []

    @staticmethod
    def _marker_is_current(state):
        return bool(state.get("marker")) and state.get("marker_current", True)

    def head_object(self, Bucket, Key, VersionId=None):
        if self.head_error is not None:
            raise self.head_error
        state = self.objects.get(Key)
        if VersionId is not None:
            if state is not None and VersionId == state.get("marker"):
                raise _client_error("MethodNotAllowed")
            if state is not None and VersionId in state.get("versions", []):
                return {"VersionId": VersionId}
            raise _client_error("404")
        # `force_head_404` exists only to reach the listing in a state S3 cannot produce -- a key
        # with a live current version AND a stale delete marker. See TestOnlyTheCurrentVersionDecides.
        if state is None or self._marker_is_current(state) or state.get("force_head_404"):
            raise _client_error("404")
        return {"VersionId": state["versions"][-1]}

    def list_object_versions(self, Bucket, Prefix, MaxKeys=None, **kwargs):
        self.listing_calls.append({"Prefix": Prefix, "MaxKeys": MaxKeys})
        entries = []
        for key in sorted(self.objects):
            if not key.startswith(Prefix):
                continue
            state = self.objects[key]
            marker_current = self._marker_is_current(state)
            if marker_current and state.get("marker"):
                entries.append(("DeleteMarkers",
                                {"Key": key, "VersionId": state["marker"], "IsLatest": True}))
            for position, version_id in enumerate(reversed(state.get("versions", []))):
                entries.append(("Versions", {"Key": key, "VersionId": version_id,
                                             "IsLatest": position == 0 and not marker_current}))
            if state.get("marker") and not marker_current:
                # A restored key: the marker is older than the current live version.
                entries.append(("DeleteMarkers",
                                {"Key": key, "VersionId": state["marker"], "IsLatest": False}))
        if MaxKeys is not None:
            entries = entries[:MaxKeys]
        response = {"Versions": [], "DeleteMarkers": []}
        for list_name, entry in entries:
            response[list_name].append(entry)
        return response


def _archived(*versions):
    return {"versions": list(versions), "marker": "dm-1", "marker_current": True}


def _live(*versions):
    return {"versions": list(versions), "marker": None, "marker_current": False}


def _restored(*versions):
    """Archived once, then restored by a newer upload: the marker survives, but is not latest."""
    return {"versions": list(versions), "marker": "dm-1", "marker_current": False}


@pytest.mark.unit
class TestArchiveStatusOfTheKeyItself:
    """Positive controls: the answers callers already depend on must not change."""

    def test_key_whose_current_version_is_a_delete_marker_is_archived(self):
        s3 = _FakeS3({"a/pump.e57": _archived("v1")})

        assert is_object_version_archived(BUCKET, "a/pump.e57", client=s3) is True

    def test_live_key_is_not_archived_and_needs_no_listing(self):
        s3 = _FakeS3({"a/pump.e57": _live("v1")})

        assert is_object_version_archived(BUCKET, "a/pump.e57", client=s3) is False
        # The head answered on its own, so the O(1) contract in the docstring still holds.
        assert s3.listing_calls == []

    def test_key_with_no_versions_at_all_is_not_archived(self):
        s3 = _FakeS3({})

        assert is_object_version_archived(BUCKET, "a/pump.e57", client=s3) is False

    def test_a_live_sibling_does_not_hide_the_keys_own_delete_marker(self):
        """Guards against over-narrowing the fix: the exact key's marker still decides."""
        s3 = _FakeS3({
            "a/model.glb": _archived("v1"),
            "a/model.glb.previewFile.png": _live("p1"),
        })

        assert is_object_version_archived(BUCKET, "a/model.glb", client=s3) is True


@pytest.mark.unit
class TestPrefixSiblingsDoNotDecideArchiveStatus:
    """The defect: entries returned by the prefix listing must be matched back to the key."""

    def test_absent_key_that_prefixes_an_archived_sibling_is_not_archived(self):
        """`pump.e5` never existed; only `pump.e57` is archived."""
        s3 = _FakeS3({"a/pump.e57": _archived("v1")})

        assert is_object_version_archived(BUCKET, "a/pump.e5", client=s3) is False
        # The fallback did run -- otherwise this would pass for the wrong reason.
        assert [call["Prefix"] for call in s3.listing_calls] == ["a/pump.e5"]

    def test_missing_base_file_is_not_archived_by_its_archived_preview_file(self):
        """The naming shape this actually happens with: `<file>.previewFile.png`."""
        s3 = _FakeS3({"a/model.glb.previewFile.png": _archived("p1")})

        assert is_object_version_archived(BUCKET, "a/model.glb", client=s3) is False

    def test_the_fallback_reads_one_entry_because_the_key_leads_its_own_prefix(self):
        """The O(1) claim in the docstring, and the ordering argument that licenses it.

        A one-row page IS conclusive here, which was measured against moto rather than reasoned
        about: keys sort ascending and this key is a prefix of every longer sibling, so its own
        entries lead the page and cannot be paged past -- confirmed with 1,100 sibling keys, where
        a 1,000-entry page still carried the key's own delete marker first. Reading a wide page
        instead costs a 1,000-entry response on a path the docstring advertises as O(1), and buys
        nothing, because a sibling's entry can never answer for this key anyway (the exact-key
        match below).
        """
        s3 = _FakeS3({"a/pump.e57": _archived("v1")})

        is_object_version_archived(BUCKET, "a/pump.e5", client=s3)

        assert s3.listing_calls, "the fallback listing never ran"
        assert [call["MaxKeys"] for call in s3.listing_calls] == [1], s3.listing_calls


@pytest.mark.unit
class TestOnlyTheCurrentVersionDecides:
    """A delete marker that is not the current version must not report a key archived.

    Be clear about what this covers, because it is a defensive guard and not a live defect. The
    helper reaches the listing only on a 404 from HeadObject, and S3 answers 404 without a version
    id exactly when the current version is a delete marker or the key has no versions. A key with
    a stale delete marker necessarily has a newer live version, so it answers 200 and the listing
    never runs -- meaning no reachable S3 state can put a non-current delete marker for the queried
    key in front of the match.

    Matching on the key alone is therefore correct today, but only by way of an invariant that is
    invisible at the listing call site. `IsLatest` states it where the decision is made.

    Two redundancies worth naming, so nobody reads more into these tests than they carry. Given
    `MaxKeys=1`, the `IsLatest` term cannot change any answer: the one entry returned is always the
    key's newest version, so a stale delete marker is never in the list to be matched. Removing
    `IsLatest` while keeping `MaxKeys=1` leaves all of these green -- measured. What the second test
    below DOES discriminate is the previous form of this fallback, which read a 1,000-entry page and
    matched on the key alone; against that, it fails. So `IsLatest` earns its place as the guard
    that survives a later widening of `MaxKeys`, not as a fix for a reachable defect.
    """

    def test_a_restored_key_is_not_archived(self):
        """The reachable half: HeadObject settles it and the surviving marker is never consulted."""
        s3 = _FakeS3({"a/model.glb": _restored("v1", "v2")})

        assert is_object_version_archived(BUCKET, "a/model.glb", client=s3) is False
        assert s3.listing_calls == [], "the stale marker was consulted when it need not have been"

    def test_the_match_rejects_a_stale_marker_for_the_key_itself(self):
        """The unreachable half, stated as such.

        HeadObject is forced to 404 while the key's own delete marker is NOT current -- a
        combination real S3 will not return. This is the test that fails against a wide-page
        fallback matching on the key alone, which is what the wide-page form would have answered
        True for.
        """
        s3 = _FakeS3({"a/model.glb": _restored("v1", "v2")})
        s3.objects["a/model.glb"]["force_head_404"] = True

        assert is_object_version_archived(BUCKET, "a/model.glb", client=s3) is False
        assert s3.listing_calls, "the fallback listing never ran, so nothing was proven"

    def test_a_currently_archived_key_is_still_archived(self):
        """Positive control for the IsLatest guard: it must not reject a live delete marker."""
        s3 = _FakeS3({"a/model.glb": _archived("v1")})

        assert is_object_version_archived(BUCKET, "a/model.glb", client=s3) is True


@pytest.mark.unit
class TestVersionIdPath:
    """Regression coverage for the branch the fix does not touch."""

    def test_delete_marker_version_is_archived(self):
        s3 = _FakeS3({"a/model.glb": _archived("v1")})

        assert is_object_version_archived(BUCKET, "a/model.glb", "dm-1", client=s3) is True

    def test_live_version_is_not_archived(self):
        s3 = _FakeS3({"a/model.glb": _archived("v1")})

        assert is_object_version_archived(BUCKET, "a/model.glb", "v1", client=s3) is False

    def test_unknown_version_is_not_archived_and_never_lists(self):
        s3 = _FakeS3({"a/model.glb": _archived("v1")})

        assert is_object_version_archived(BUCKET, "a/model.glb", "v-nope", client=s3) is False
        assert s3.listing_calls == []


@pytest.mark.unit
class TestBestEffortOnUnexpectedErrors:
    def test_an_unexpected_head_error_answers_false_rather_than_raising(self):
        """Callers treat this helper as best-effort; an AccessDenied must not fail their request."""
        s3 = _FakeS3({}, head_error=_client_error("AccessDenied"))

        assert is_object_version_archived(BUCKET, "a/model.glb", client=s3) is False


_DOUBLE_PATH = os.path.join(os.path.dirname(__file__), "..", "mocks", "common", "s3.py")


def _double():
    """The tests/mocks/common/s3.py stand-in, resolved the way handler tests reach it.

    Read out of ``sys.modules`` rather than loaded from the path a second time: a second load is a
    different module object with its own globals, so it could answer correctly while the object
    tests/conftest.py registered -- the one handlers actually call -- stayed wrong. The path
    assertion is what ties the two together.
    """
    module = sys.modules["common.s3"]
    assert os.path.realpath(module.__file__) == os.path.realpath(_DOUBLE_PATH), module.__file__
    return module


IMPLEMENTATIONS = ("shipped", "double")


def _implementation(name):
    """Either body of the same fallback: the deployed helper, or the double handler tests call."""
    return is_object_version_archived if name == "shipped" else _double().is_object_version_archived


class _WidePageS3(_FakeS3):
    """A listing that serves the whole prefix page, ignoring the MaxKeys it was asked for.

    Not a state S3 produces: ``max-keys`` caps the combined Versions + DeleteMarkers count and
    truncates mid-key when it must -- the ListObjectVersions ``max-keys=3`` sample returns two
    versions of a single key and reports ``NextKeyMarker`` plus ``NextVersionIdMarker`` both inside
    that one key's history. This is the only configuration in which the ``IsLatest`` term is
    observable, because honouring ``MaxKeys=1`` puts the key's own current version in the single
    slot and a stale delete marker never reaches the match.
    """

    def list_object_versions(self, Bucket, Prefix, MaxKeys=None, **kwargs):
        response = super().list_object_versions(Bucket, Prefix, MaxKeys=None, **kwargs)
        self.listing_calls[-1]["MaxKeys"] = MaxKeys  # what was asked for, not what was served
        return response


class _EmptyPageS3:
    """A listing that OMITS ``Versions``/``DeleteMarkers`` instead of returning empty lists.

    This is the shape boto3 hands back for a prefix that matches nothing, and the one case a
    MagicMock cannot stand in for: ``mock.get("DeleteMarkers")`` yields a truthy child mock, so a
    "the key was found" assertion passes over a page that carries no keys at all.
    """

    def __init__(self):
        self.listing_calls = []

    def head_object(self, Bucket, Key, VersionId=None):
        raise _client_error("404")

    def list_object_versions(self, Bucket, Prefix, MaxKeys=None, **kwargs):
        self.listing_calls.append({"Prefix": Prefix, "MaxKeys": MaxKeys})
        return {"IsTruncated": False, "Name": Bucket, "Prefix": Prefix, "MaxKeys": MaxKeys}


def _wide_page_over_a_stale_marker():
    """The forced-404 restored key, served on a page wide enough to reach its stale marker."""
    s3 = _WidePageS3({"a/model.glb": _restored("v1", "v2")})
    s3.objects["a/model.glb"]["force_head_404"] = True
    return s3, "a/model.glb"


def _state_own_current_delete_marker():
    return _FakeS3({"a/model.glb": _archived("v1")}), "a/model.glb", True


def _state_absent_key_prefixing_an_archived_sibling():
    return _FakeS3({"a/pump.e57": _archived("v1")}), "a/pump.e5", False


def _state_missing_base_file_with_an_archived_preview():
    return _FakeS3({"a/model.glb.previewFile.png": _archived("p1")}), "a/model.glb", False


def _state_live_key_with_an_archived_sibling():
    return _FakeS3({
        "a/model.glb": _live("v1"),
        "a/model.glb.previewFile.png": _archived("p1"),
    }), "a/model.glb", False


def _state_archived_key_with_a_live_sibling():
    return _FakeS3({
        "a/model.glb": _archived("v1"),
        "a/model.glb.previewFile.png": _live("p1"),
    }), "a/model.glb", True


def _state_restored_key():
    return _FakeS3({"a/model.glb": _restored("v1", "v2")}), "a/model.glb", False


def _state_forced_404_over_a_stale_marker():
    s3 = _FakeS3({"a/model.glb": _restored("v1", "v2")})
    s3.objects["a/model.glb"]["force_head_404"] = True
    return s3, "a/model.glb", False


def _state_no_versions_at_all():
    return _FakeS3({}), "a/model.glb", False


PARITY_STATES = [
    _state_own_current_delete_marker,
    _state_absent_key_prefixing_an_archived_sibling,
    _state_missing_base_file_with_an_archived_preview,
    _state_live_key_with_an_archived_sibling,
    _state_archived_key_with_a_live_sibling,
    _state_restored_key,
    _state_forced_404_over_a_stale_marker,
    _state_no_versions_at_all,
]


@pytest.mark.unit
class TestOneEntryIsConclusiveAtScale:
    """Why ``MaxKeys=1`` is sufficient, executed rather than asserted in a docstring.

    Three documented ListObjectVersions properties license the single-entry request, and the pair
    of tests below is what fails if any of them is re-modelled or the request is "fixed" into a
    wide listing:

    * ``max-keys`` caps the combined Versions + DeleteMarkers count, not the number of distinct
      keys -- the API reference's ``max-keys=3`` sample truncates inside one key's version history.
      So ``MaxKeys=1`` is one ENTRY.
    * Keys come back in ascending order, and a key is a prefix of every longer sibling the same
      ``Prefix`` query returns, so its own entries lead the page and cannot be paged past.
    * Within a key, versions come back newest first (the reference's own ``--prefix`` samples show
      the ``IsLatest: true`` delete marker ahead of the older versions, timestamps descending), so
      the leading entry is this key's CURRENT version.

    Widening the page buys nothing on top of the exact-key match, and costs a 1,000-entry response
    on a path documented as O(1). The counts here are the 1,100-sibling measurement the docstring
    at ``test_the_fallback_reads_one_entry_because_the_key_leads_its_own_prefix`` cites, run.
    """

    SIBLING_COUNT = 1100

    def _archived_siblings(self):
        return {f"a/pump.e57.chunk{i:04d}": _archived("v1") for i in range(self.SIBLING_COUNT)}

    @pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
    def test_the_keys_own_marker_leads_a_page_full_of_longer_siblings(self, implementation):
        """Sibling-first ordering would answer False here -- a false negative on an archived key."""
        objects = self._archived_siblings()
        objects["a/pump.e57"] = _archived("v1")
        s3 = _FakeS3(objects)

        assert _implementation(implementation)(BUCKET, "a/pump.e57", client=s3) is True
        assert [call["MaxKeys"] for call in s3.listing_calls] == [1], s3.listing_calls

    @pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
    def test_an_absent_key_stays_unarchived_however_many_siblings_it_prefixes(self, implementation):
        """The paired negative: 1,100 archived siblings, and the key itself has no versions."""
        s3 = _FakeS3(self._archived_siblings())

        assert _implementation(implementation)(BUCKET, "a/pump.e5", client=s3) is False
        # The fallback ran, so False is not a short-circuit.
        assert [call["Prefix"] for call in s3.listing_calls] == ["a/pump.e5"]

    @pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
    def test_a_page_that_omits_the_version_lists_is_not_archived(self, implementation):
        """The empty-page shape: absent keys rather than empty lists, as boto3 returns it."""
        s3 = _EmptyPageS3()

        assert _implementation(implementation)(BUCKET, "a/model.glb", client=s3) is False
        assert [call["MaxKeys"] for call in s3.listing_calls] == [1], s3.listing_calls


@pytest.mark.unit
class TestTheIsLatestTermIsObservableOnlyOnAWidePage:
    """What pins ``and marker.get('IsLatest')`` in both bodies of the fallback.

    Every state reachable through ``MaxKeys=1`` is non-discriminating for this term, as
    TestOnlyTheCurrentVersionDecides says: the one entry returned is always the key's newest
    version, so a stale delete marker is never in the list to be matched, and removing the term
    leaves those tests green. Serving the whole page is the only way to put a non-current delete
    marker in front of the match, and it is the configuration the term exists for -- a later
    widening of ``MaxKeys`` (or a reviewer who "restores" the page-size constant) reintroduces
    exactly the state below.
    """

    def test_the_wide_page_carries_a_stale_marker_a_key_only_match_would_accept(self):
        """Harness control. Without this, the two arms below would prove nothing."""
        s3, key = _wide_page_over_a_stale_marker()

        page = s3.list_object_versions(BUCKET, key, MaxKeys=1)

        assert page["DeleteMarkers"] == [{"Key": key, "VersionId": "dm-1", "IsLatest": False}]
        # Matching on the key alone accepts that marker; the term under test is what overrides it.
        assert any(marker.get("Key") == key for marker in page["DeleteMarkers"]) is True
        assert [call["MaxKeys"] for call in s3.listing_calls] == [1], s3.listing_calls

    @pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
    def test_a_stale_marker_on_a_wide_page_is_rejected(self, implementation):
        s3, key = _wide_page_over_a_stale_marker()

        assert _implementation(implementation)(BUCKET, key, client=s3) is False
        assert s3.listing_calls, "the fallback listing never ran, so nothing was proven"

    @pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
    def test_a_current_marker_on_a_wide_page_is_still_accepted(self, implementation):
        """Paired positive control: rejecting the stale marker is not rejecting every marker."""
        s3 = _WidePageS3({"a/model.glb": _archived("v1")})

        assert _implementation(implementation)(BUCKET, "a/model.glb", client=s3) is True


@pytest.mark.unit
class TestTheDoubleAnswersLikeTheShippedHelper:
    """The double's docstring claims it mirrors the real helper's contract; this is that claim.

    Registered as ``common.s3``, it -- not the module loaded at the top of this file -- decides
    archive status for every assetFiles / assetService / assetVersions / uploadFile /
    sqsUploadFileLarge / sqsBucketSync test. No handler test reaches its fallback branch (each
    fake they drive either answers 200 from head_object or patches the wrapper out), so these are
    the only tests that execute that line.
    """

    @pytest.mark.parametrize("build", PARITY_STATES,
                             ids=lambda build: build.__name__[len("_state_"):])
    def test_both_implementations_give_the_same_answer_from_the_same_request(self, build):
        shipped_client, key, expected = build()
        double_client, _, _ = build()

        assert is_object_version_archived(BUCKET, key, client=shipped_client) is expected
        assert _double().is_object_version_archived(BUCKET, key, client=double_client) is expected
        # Equal answers alone would also hold for two bodies wrong in the same way, which is why
        # `expected` is stated per state above. This adds the REQUEST: same prefix, same MaxKeys,
        # same number of listings -- so a double that reads 1,000 entries fails here even while
        # agreeing on every answer.
        assert double_client.listing_calls == shipped_client.listing_calls

    def test_the_double_still_exports_the_page_size_constants(self):
        """assetFiles.py:38 imports S3_VERSIONS_PAGE_SIZE from common.s3, which resolves here.

        The single-entry fallback no longer references it, so removing it as dead code raises
        ImportError at module import for every assetFiles handler test.
        """
        module = _double()

        assert module.S3_VERSIONS_PAGE_SIZE == 1000
        assert module.S3_OBJECTS_PAGE_SIZE == 1000

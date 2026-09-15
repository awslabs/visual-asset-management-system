# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The class guard for over-tight and vacuous assertions in bound/refusal tests.

Ten rounds of review have found the same mistake one file over from where it was last removed: a
test that asserts a BOUND or a REFUSAL pins a call count, an exact call sequence, or the exact text
of a read expression -- so a strictly SAFER implementation fails it -- or it iterates a collection of
recorded calls with nothing proving the collection is non-empty, so a broken implementation that
issues no read at all passes. Fixing instances by hand guarantees an eleventh round; this file is
what fails when the class reappears.

WHY THE SCOPE IS A FILE LIST AND NOT THE TREE. `assertionShapes` finds candidates across the whole of
`backend/tests`, and the large majority are legitimate: `assert_called_once` on an ALLOW path
("the write happened", "the email was sent") is the property under test, not a defect. The defect is
pinning a count where the property is "this was refused" or "this was bounded", and that distinction
is not recoverable from syntax. So there are four sweeps, each with its own scope:

  - CLEANED_FILES -- the files these sweeps made conform. They must stay at ZERO.
  - the BOUND/REFUSAL FAMILY -- every `backend/tests` file whose name says paging/bounds/cap/budget/
    ceiling (or that imports `tests/pagingStub.py`). Each is held to an UPPER BOUND on its remaining
    instances (`KNOWN_REMAINING`), so a file can only get better; a family file absent from the
    ledger must be at zero, which is what makes a NEW bounds test unable to be born with a pin.
  - WATCHED_FILES -- measured files the family pattern cannot reach because their NAME says nothing
    about a bound, held as upper bounds all the same. Broadening the family pattern to catch them
    would pull in a large unmeasured set and turn this suite red on work nobody did.
  - the NEVER-LEGITIMATE CATEGORIES -- the five shapes with no allow-path reading at all, ratcheted
    at measured per-file ceilings across the WHOLE tree (`NEVER_LEGITIMATE_CEILINGS`). The family
    pattern cannot reach a file whose name says nothing about a bound, and 22 instances of these
    five sat in exactly such files (`test_metadata_total_budget.py` among them, whose name says
    "budget" while the pattern did not). A file absent from that ledger is held at zero.

WHAT IS ENFORCED NOWHERE, stated rather than implied: the large majority of candidates, all of them in
the four categories that DO have a legitimate reading -- `pinned-call-count`, `pinned-exact-sequence`,
`pinned-collection-length` and `pinned-source-text-count` (the shape a deliberate structural guard uses
when it counts the paginator-backed reads in a module). Those are reported by the scanner for a human to
disposition, not failed here, because a blanket ban on `assert_called_once` would be wrong and would be
ignored.

Deliberately no counts are quoted above. An earlier version of this docstring stated exact totals, and
they had drifted within the same review round with nothing able to notice -- a stale measurement in the
one file whose subject is measurements that cannot fail. For the current population, run the scanner
from `backend/` (the `backend.tests.*` import path this module uses is set up by conftest and is not
available outside pytest, so the command uses the `tests.*` form):

    python -c "import sys; sys.path.insert(0, '.'); from pathlib import Path; \
from tests._assertion_shapes import assertionShapes as a; \
files = [f for f in Path('tests').rglob('test_*.py') if 'fixtures' not in f.parts]; \
print('files:', len(files), 'candidates:', sum(len(a.scan_file(f)) for f in files))"

What IS enforced is the shape of the ledgers, not their size: every ledger key must still name a file the
corresponding sweep actually walks (otherwise its ceiling silently enforces nothing), and each sweep
asserts its corpus is non-empty before trusting a zero from it.

WHY THERE IS A POSITIVE CONTROL. This review has had three instrumentation failures where a detector
silently returned zero and the zero read as good news. `test_the_detector_reports_every_planted_shape`
therefore scans two fixtures of deliberately planted instances on EVERY run and fails if the detector
cannot see them, and `test_the_detector_reports_nothing_on_the_safe_shapes` scans a third fixture of
the CORRECT forms, so a detector that flagged everything could not pass either. A zero from the sweep
below is only worth something because both controls ran beside it.
"""

import re
from pathlib import Path

import pytest

from backend.tests._assertion_shapes import assertionShapes

TESTS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

PLANTED_FIXTURES = ("pinned_shapes.py.txt", "vacuous_shapes.py.txt")
SAFE_FIXTURE = "safe_shapes.py.txt"
PLANT = re.compile(r"#\s*PLANT:\s*([a-z-]+)\s*$")

# A test file whose subject is a bound or a refusal, by name or by its use of the shared paging stub.
# The family is about the SUBJECT, not the word: a test named for the budget it bounds belongs to it
# as much as one named for a page. `budget` is here because `assertionShapes.BOUND_CONSTANT_FRAGMENTS`
# has always included BUDGET while this pattern did not, which is how a file named for one came to
# hold three never-legitimate instances outside every sweep.
FAMILY_NAME = re.compile(r"paging|bound|cap|budget|ceiling|quota|exhaust|truncat", re.IGNORECASE)

# Files this sweep brought to zero. They are held at zero rather than at a ceiling.
CLEANED_FILES = (
    "handlers/roles/test_roleService_paging.py",
    # Its own docstring named this sibling, and the two shapes removed from the paging file survived
    # here verbatim on the same production function -- an assert_called_once_with on the role-row
    # delete and an exact-string comparison on the cascade read's FilterExpression.
    "handlers/roles/test_roleService_delete_cascade.py",
    "handlers/addon/physna/test_physnaAssetSyncHalfFailureAck.py",
    "handlers/pipelines/test_pipelineService_update_integrity.py",
    "common/test_triggerTemplateValidation_scan_bounds.py",
)

# The remaining known population in the bound/refusal family, as an UPPER BOUND per file. A shrink-
# only ledger: lower the number when you fix one, and delete the entry when a file reaches zero. It
# is deliberately not an equality -- another author fixing one of these must not turn this red.
#
# Raising a ceiling is allowed for a genuine ALLOW-path assertion (a write happened), or for an
# instance a WIDENED scanner newly sees in code nobody changed -- with the reason on the line either
# way. Everything else gets fixed instead: assert set containment over meaningful tuples, or an UPPER
# bound on the cost, and guard every iteration with a non-emptiness claim.
KNOWN_REMAINING = {
    "common/test_dynamodb_query_all_items.py": 6,
    "common/workflows/test_defaultBucket_paging.py": 1,
    "handlers/assetLinks/test_assetLinksService_paging.py": 3,
    "handlers/auth/test_apiKeyService_paging.py": 3,
    "handlers/pipelines/test_pipelineService_bounds.py": 6,
    # 5 -> 8: three instances the widened scanner newly sees, none of them new code. Two are
    # positive controls counting the paginator forms in the file's OWN fixture string -- a count over
    # a fixture the test owns, not over production behaviour -- and one is an exact-list pin on a
    # produced label list.
    "handlers/pipelines/test_pipelineService_paging.py": 8,
    "handlers/search/test_simple_search_paging_and_merge.py": 2,
    "handlers/tags/test_tagService_paging.py": 2,
    "handlers/tagTypes/test_tagTypeService_paging.py": 4,
    "handlers/workflows/test_executions_authz_bound.py": 8,
    "handlers/workflows/test_processOutput_failure_and_paging.py": 1,
    # New family entry: the widened FAMILY_NAME reaches it. Its instances are all pre-existing.
    "handlers/workflows/test_metadata_total_budget.py": 7,
    "handlers/workflows/test_r2_concurrency_and_abort_bounds.py": 1,
    # 5 -> 7: the same two fixture-owned paginator-form counts as the pipelines file above.
    "handlers/workflows/test_workflowService_paging.py": 7,
    "test_pagingStub.py": 1,
}


# Files a review has MEASURED that the family pattern cannot reach, held as upper bounds regardless
# of their name. The family is selected by filename, so a file testing a refusal whose name says
# nothing about a bound sits outside every sweep -- and the one below was found holding live exact-
# count pins on the same production function as its already-cleaned sibling
# (`test_triggerTemplateValidation_scan_bounds.py`, in CLEANED_FILES). Broadening FAMILY_NAME to
# reach it would pull in a large set of unmeasured files and turn this suite red on work nobody did,
# so measured files are named here instead.
#
# Same shrink-only discipline as KNOWN_REMAINING: lower the number when you fix one, delete the entry
# at zero, and give a reason on the line when raising it.
WATCHED_FILES = {
    # 25 -> 16: the nine `len(errors) == 1` pins became "it was flagged" plus set containment over
    # the errors on the offender's name. The remainder are `assert_called_once` on read counts after a
    # refusal (an added safety re-check would fail them) mixed with genuine allow-path write
    # assertions, which the scanner cannot tell apart -- so they are bounded rather than banned.
    "common/test_triggerTemplateValidation.py": 16,
}


# The categories with no ALLOW-path reading at all. `assert_called_once` on a write is legitimate
# because "the write happened" can be the property under test; none of these five has such a reading:
#
#   vacuous-recorded-iteration    -- passes over zero recorded calls, so it cannot fail
#   wording-classified-collection -- splits a returned set on a prose substring, so a rewording moves
#                                    an entry to the other class silently
#   pinned-bound-equality         -- asks for EXACTLY the budget, so a cheaper read AND a tightened
#                                    production cap both fail it
#   pinned-read-expression        -- pins one spelling of a filter / projection / condition
#   pinned-message-wording        -- pins the exact wording of one produced message
#
# So these are ratcheted across the WHOLE tree rather than only inside the family.
NEVER_LEGITIMATE = (
    "vacuous-recorded-iteration",
    "wording-classified-collection",
    "pinned-bound-equality",
    "pinned-read-expression",
    "pinned-message-wording",
)

# Measured per-file ceilings for those five, tree-wide. Shrink-only, like KNOWN_REMAINING: lower the
# number when you fix one, delete the entry at zero. A file absent from this ledger is held at ZERO,
# which is what stops a new instance being born in a file whose name says nothing about a bound.
#
# The overlap with KNOWN_REMAINING is deliberate rather than redundant: a family file's ceiling bounds
# ALL its categories, so on its own it would let a legitimate allow-path pin be swapped for one of
# these five without a word.
NEVER_LEGITIMATE_CEILINGS = {
    "common/test_dynamodb_query_all_items.py": 6,
    "common/workflows/test_defaultBucket_paging.py": 1,
    "handlers/assetLinks/test_assetLinksService_paging.py": 3,
    "handlers/auth/test_apiKeyService_paging.py": 3,
    "handlers/authz/test_policy_build_table_reads.py": 1,
    "handlers/databases/test_buckets_scoping.py": 2,
    "handlers/indexing/test_sqsBucketSync_asset_type_guard.py": 1,
    "handlers/indexing/test_sqsBucketSync_orchestration_publish_logging.py": 1,
    "handlers/pipelines/test_pipelineService_bounds.py": 5,
    "handlers/pipelines/test_pipelineService_paging.py": 5,
    "handlers/search/test_pagination_performance.py": 1,
    "handlers/tags/test_tagService_paging.py": 2,
    "handlers/tagTypes/test_tagTypeService_paging.py": 2,
    "handlers/userRoles/test_userRolesService_authz_fail_closed.py": 2,
    "handlers/workflows/test_asset_execution_list_dual_cursor.py": 1,
    "handlers/workflows/test_execution_output_asset_authorization.py": 1,
    "handlers/workflows/test_executionService_wb53.py": 1,
    "handlers/workflows/test_global_list_filtered_page_walk.py": 2,
    "handlers/workflows/test_metadata_total_budget.py": 3,
    "handlers/workflows/test_wb6_vamsSchemaImport.py": 2,
    "handlers/workflows/test_workflow_handlers_wire.py": 1,
    "handlers/workflows/test_workflowService.py": 2,
    "handlers/workflows/test_workflowService_paging.py": 5,
}


def _planted(path):
    """Every (line, category) a fixture declares, read from its own PLANT markers."""
    planted = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        marker = PLANT.search(line)
        if marker:
            planted.add((number, marker.group(1)))
    return planted


def _findings(path):
    return assertionShapes.scan_source(path.read_text(encoding="utf-8"), path.name)


def _family_files():
    """The bound/refusal family: files whose subject is a cost or a denial."""
    for path in sorted(TESTS.rglob("test_*.py")):
        if FIXTURES in path.parents:
            continue
        if FAMILY_NAME.search(path.name):
            yield path
            continue
        if "pagingStub" in path.read_text(encoding="utf-8"):
            yield path


@pytest.mark.unit
def test_the_detector_reports_every_planted_shape():
    """THE POSITIVE CONTROL. A zero from an unproven detector is worth less than no detector.

    Both directions of the mistake are planted in the fixtures, and each plant declares the category
    it expects on its own line. A detector that stopped seeing a shape -- a refactor that broke the
    AST walk, a category rename, a narrowed subject list -- fails here rather than reporting a
    reassuring zero from the sweeps below.
    """
    missing, unexpected, covered = [], [], set()
    for name in PLANTED_FIXTURES:
        path = FIXTURES / name
        planted = _planted(path)
        assert planted, f"{name} declares no PLANT markers, so it controls nothing"
        reported = {(finding.line, finding.category) for finding in _findings(path)}
        covered |= {category for _line, category in planted}
        missing += [f"{name}:{line} expected [{category}]"
                    for line, category in sorted(planted - reported)]
        unexpected += [f"{name}:{line} reported [{category}] with no PLANT marker"
                       for line, category in sorted(reported - planted)]

    if missing:
        raise AssertionError(
            "the detector is BLIND to planted instances, so every zero it reports is meaningless:\n"
            + "\n".join(missing))
    assert not unexpected, (
        "the detector reported instances the fixture does not declare; either the fixture line is a "
        "real finding and needs a PLANT marker, or the detector over-flags:\n" + "\n".join(unexpected))
    assert covered == set(assertionShapes.CATEGORIES), (
        f"a detector category is not exercised by any fixture, so nothing proves it still fires: "
        f"{sorted(set(assertionShapes.CATEGORIES) - covered)}")


@pytest.mark.unit
def test_the_detector_reports_nothing_on_the_safe_shapes():
    """THE NEGATIVE CONTROL. A detector that flags every mock assertion is one that gets ignored.

    Each function in the safe fixture is a CORRECT form of an assertion one of the categories
    describes -- set containment over meaningful tuples, an upper bound on a cost, a guarded
    iteration, a self-guarding ``any()``, a write on an allow path, a refusal asserted by absence.
    """
    findings = _findings(FIXTURES / SAFE_FIXTURE)
    assert findings == [], (
        "the detector flags a correct assertion form, which makes the sweeps below unreadable:\n"
        + "\n".join(repr(finding) for finding in findings))


@pytest.mark.unit
def test_the_files_this_sweep_cleaned_stay_clean():
    """The four files the tenth round fixed. Zero, not a ceiling."""
    offenders = []
    for relative in CLEANED_FILES:
        path = TESTS / relative
        assert path.is_file(), f"{relative} has moved; update CLEANED_FILES"
        offenders += [repr(finding) for finding in assertionShapes.scan_file(path)]
    assert not offenders, (
        "a pinned or vacuous assertion is back in a file this sweep cleaned:\n"
        + "\n".join(offenders))


@pytest.mark.unit
def test_no_bound_or_refusal_test_grows_a_new_instance():
    """The ratchet over the whole bound/refusal family: instances may only decrease.

    A family file absent from KNOWN_REMAINING is held at zero, so a NEW paging/bounds test cannot be
    born with a pinned count or a vacuous iteration -- which is how every one of the ten rounds
    started.
    """
    family = list(_family_files())
    # The sweep walks a glob, and a glob that matches nothing makes every assertion below
    # hold vacuously -- the exact failure mode this file exists to prevent, in this file.
    # The floor is the ledger: every KNOWN_REMAINING key names a family file, so the corpus
    # cannot be smaller than the ledger without a rename or a move having gone unnoticed.
    relatives = {path.relative_to(TESTS).as_posix() for path in family}
    missing = sorted(set(KNOWN_REMAINING) - relatives)
    assert not missing, (
        "a file with a KNOWN_REMAINING ceiling is no longer matched by the family pattern, so "
        "its ceiling now enforces nothing -- it was renamed, moved or deleted. Update the "
        "ledger key or the pattern: " + ", ".join(missing))
    assert len(family) >= len(KNOWN_REMAINING), (
        f"the family sweep found only {len(family)} files against a ledger of "
        f"{len(KNOWN_REMAINING)}; the corpus is not being walked")

    over_ceiling = []
    for path in family:
        relative = path.relative_to(TESTS).as_posix()
        findings = assertionShapes.scan_file(path)
        ceiling = KNOWN_REMAINING.get(relative, 0)
        if len(findings) > ceiling:
            over_ceiling.append(
                f"{relative}: {len(findings)} instances, ceiling {ceiling}\n    "
                + "\n    ".join(f"line {finding.line} [{finding.category}] {finding.detail}"
                                for finding in findings))
    assert not over_ceiling, (
        "a bound/refusal test gained an over-tight or vacuous assertion. Fix the shape (set "
        "containment over meaningful tuples, an UPPER bound on the cost, a non-emptiness guard "
        "before any iteration), or -- only for a genuine allow-path assertion -- raise that file's "
        "ceiling in KNOWN_REMAINING with the reason on the line:\n\n" + "\n\n".join(over_ceiling))


@pytest.mark.unit
def test_no_watched_file_outside_the_family_grows_a_new_instance():
    """Measured files the family pattern cannot reach, held as upper bounds.

    The family is selected by FILENAME, so a test whose subject is a refusal but whose name says
    nothing about a bound is invisible to every other sweep here. That is not hypothetical: the file
    in WATCHED_FILES was found holding live exact-count pins on the same production function as its
    already-cleaned sibling, one directory over, outside every other sweep here.
    """
    over_ceiling = []
    for relative, ceiling in sorted(WATCHED_FILES.items()):
        path = TESTS / relative
        # A watched entry naming a file that no longer exists enforces nothing, and would go on
        # reporting clean forever.
        assert path.is_file(), (
            f"WATCHED_FILES names {relative}, which is not a file -- it was renamed, moved or "
            f"deleted, so its ceiling enforces nothing")
        findings = assertionShapes.scan_file(path)
        if len(findings) > ceiling:
            over_ceiling.append(
                f"{relative}: {len(findings)} instances, ceiling {ceiling}\n    "
                + "\n    ".join(f"line {finding.line} [{finding.category}] {finding.detail}"
                                for finding in findings))
    assert not over_ceiling, (
        "a watched test gained an over-tight or vacuous assertion. Fix the shape, or -- only for a "
        "genuine allow-path assertion -- raise that file's ceiling in WATCHED_FILES with the reason "
        "on the line:\n\n" + "\n\n".join(over_ceiling))


@pytest.mark.unit
def test_no_never_legitimate_shape_appears_anywhere_off_its_ledger():
    """The five shapes with no allow-path reading, ratcheted over the whole of `backend/tests`.

    The family pattern reaches a file only through its NAME (or its use of the paging stub), and 22
    instances of these five categories sat in files it cannot see -- a returned set split on prose in
    a search-performance test, a read expression pinned in an authz test, a page size pinned EQUAL to
    its cap in a databases test. None of them can be read as "the write happened", so none needs the
    path distinction that keeps the family sweep narrow; they are simply held where they are.
    """
    scanned = [path for path in sorted(TESTS.rglob("test_*.py"))
               if FIXTURES not in path.parents]
    # Same vacuity guard as the family sweep: a glob that matches nothing passes silently.
    # The floor is deliberately far below the real count (the backend suite is in the
    # hundreds of files) so it catches a broken walk without pinning a file count that any
    # new test would have to update.
    assert len(scanned) >= 100, (
        f"the whole-tree sweep found only {len(scanned)} test files, so every ceiling below "
        f"holds vacuously; the walk is broken rather than the tree being clean")
    relatives = {path.relative_to(TESTS).as_posix() for path in scanned}
    missing = sorted(set(NEVER_LEGITIMATE_CEILINGS) - relatives)
    assert not missing, (
        "a file with a NEVER_LEGITIMATE_CEILINGS ceiling is no longer in the scanned tree, so "
        "its ceiling enforces nothing: " + ", ".join(missing))

    over_ceiling = []
    for path in scanned:
        relative = path.relative_to(TESTS).as_posix()
        findings = [finding for finding in assertionShapes.scan_file(path)
                    if finding.category in NEVER_LEGITIMATE]
        ceiling = NEVER_LEGITIMATE_CEILINGS.get(relative, 0)
        if len(findings) > ceiling:
            over_ceiling.append(
                f"{relative}: {len(findings)} never-legitimate instances, ceiling {ceiling}\n    "
                + "\n    ".join(f"line {finding.line} [{finding.category}] {finding.detail}"
                                for finding in findings))
    assert not over_ceiling, (
        "a test grew an assertion in a category that has no correct reading: it either cannot fail "
        "(a recorded iteration nothing proves non-empty) or it cannot pass a strictly safer "
        "implementation (a pinned filter text, a request pinned EQUAL to its cap, a returned set "
        "split on prose). Fix the shape -- these have no ceiling-raising exception:\n\n"
        + "\n\n".join(over_ceiling))

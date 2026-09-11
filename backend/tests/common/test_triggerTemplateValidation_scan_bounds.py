# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The pipeline-save trigger-template warning check must be bounded and projected.

Three costs scale with the size of the deployment, and a page cap bounds only the first: the pages
SCANNED, the per-match `get_trigger_row` reads (one sequential DynamoDB `get_item` each), and the
warning strings returned inline in the save response. The last two scale with MATCHES rather than with
pages, so bounding the scan alone still permits thousands of sequential reads and a multi-megabyte
warning list inside one synchronous save (Rule 15). All three are covered here, each with the same
incompleteness requirement.

`pipeline_trigger_template_warnings` runs on every pipeline create/update whose systemConfig sets
`requireTemplate`, so the cost of one synchronous save was O(all workflows) RCU on the full item shape
plus O(matching workflows) sequential get_items. The owner requires the check to stay on the
synchronous save path, so the fix is to bound and project it, not to move it.

Two things make this function unusually easy to "fix" wrongly, and both are covered here:

  - It swallows every exception and returns `[]` (best-effort by design). A broken projection or a
    broken pagination loop therefore produces NO warnings and NO error, which is indistinguishable
    from "correctly configured". Every test below asserts a warning is PRESENT for a misconfigured
    workflow, so an empty result cannot pass.
  - A hard cap that truncates silently is worse than the slow scan: an operator cannot tell an empty
    warning list from a truncated one. The cap must report its own incompleteness.

The sibling `triggers_referencing_template` shows the GSI pattern, but the tempting equivalent here —
`WorkflowsByDateGSI`, the constant-`allListPartition` index — is a SPARSE index: a workflow row
written without `allListPartition` is invisible to it. That is exactly the legacy/migrated row most
likely to be misconfigured, so the sparse-index case is pinned as a regression guard rather than left
to live data.

The owner's constraint that the check stays on the SYNCHRONOUS save path is pinned beside its caller,
in tests/handlers/pipelines/test_pipeline_save_warnings_sync_path.py."""

from unittest.mock import MagicMock

import pytest

from backend.backend.common.workflows.triggerTemplateValidation import (
    pipeline_trigger_template_warnings,
)

# Read calls a bounded implementation must stay under. Not the exact budget the fix picks -- a
# generous ceiling (the sibling bound in pipelineService is 20 pages), so any sane cap passes and an
# exhaustive walk does not.
READ_CALL_CEILING = 50

# Where the stub gives up rather than let an unbounded loop hang the suite.
RUNAWAY_READ_CALLS = 500

PROJECTED_ATTRIBUTES = ("databaseId", "workflowId", "specifiedPipelines")

# Attributes a real workflow row (common.workflows.workflowRecords.build_workflow_record) carries and
# this check never reads. Naming them is what makes the projection assertion non-vacuous: excluding
# real attributes is the byte reduction, and each of these is served by the stub row below so the
# absence assertion has something it could have found.
UNNEEDED_ATTRIBUTES = ("systemConfig", "description", "jobNames", "subDashboardUrl")


def _workflow_row(workflow_id, pipeline_composite="db1:pipe1", with_list_partition=True):
    row = {"databaseId": "db1", "workflowId": workflow_id,
           "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": pipeline_composite}],
           # Present so the "not projected" assertion is about real attributes, not invented ones.
           "systemConfig": {"outputLocationType": "asset"}, "description": "d",
           "jobNames": ["job1"], "subDashboardUrl": ""}
    if with_list_partition:
        row["allListPartition"] = "ALL"
    return row


def _projected_attributes(projection):
    """The attribute names a ProjectionExpression string requests."""
    return {name.strip() for name in (projection or "").split(",") if name.strip()}


def _workflows_named(warnings, candidates):
    """Which of `candidates` the returned warnings name, whatever sentence surrounds the name.

    What separates a per-workflow report from the incompleteness signal is not a phrase but a
    fact: the report names the workflow it is about, and the aggregate signal names none.
    Splitting the list on a prose substring would instead pin the WORDING -- a reworded message
    would change class silently, which is the opposite of what these tests state they allow.

    Candidate ids are matched as substrings, so pass ids that are not prefixes of one another
    when the exact SET matters (`wf1` is a substring of `wf10`).
    """
    return {workflow for workflow in candidates if any(workflow in w for w in warnings)}


def _unattributed(warnings, candidates):
    """The returned strings naming none of `candidates`: the aggregate/incompleteness signals."""
    return [w for w in warnings if not any(workflow in w for workflow in candidates)]


def _trigger_with_no_default(_db, _wf):
    """An auto-trigger that picked NO default template for the pipeline -> a warning is due."""
    return {"triggerType": "fileUpload", "triggerConfig": {"defaultTemplateIds": {}}}


# Ceilings for the per-match trigger read and the returned warning set. Generous like
# READ_CALL_CEILING -- well above any sane cap the fix might pick, well below the number of matching
# workflows the scenarios below serve, so they prove a bound exists without pinning its value.
TRIGGER_LOOKUP_CEILING = 300
WARNING_CEILING = 60
MATCHING_WORKFLOWS = 600


class _RecordingTrigger:
    """A `get_trigger_row` that records every call, so the per-match fan-out is measurable.

    The production callable (pipelineService._get_fileupload_trigger_row) is one DynamoDB `get_item`
    per invocation, issued sequentially on the synchronous save path -- which is what makes the
    per-match fan-out a cost the page cap does not bound.
    """

    def __init__(self, row):
        self.row = row
        self.calls = []

    def __call__(self, workflow_database_id, workflow_id):
        self.calls.append((workflow_database_id, workflow_id))
        return self.row


def _configured_trigger():
    """An auto-trigger that DID pick a default template -> costs a read, produces no warning."""
    return _RecordingTrigger(
        {"triggerType": "fileUpload",
         "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "tpl1"}}})


def _misconfigured_trigger():
    return _RecordingTrigger(
        {"triggerType": "fileUpload", "triggerConfig": {"defaultTemplateIds": {}}})


def _one_page_of_matches(count):
    """A single scan page (no LastEvaluatedKey) of `count` workflows that all use the pipeline.

    One page means the page cap cannot fire, so any incompleteness the walk reports here is about
    the trigger-read or warning bound rather than about paging.
    """
    return _RecordingTable(
        [{"Items": [_workflow_row(f"wf{index}") for index in range(count)]}])


class _RecordingTable:
    """A workflows table that records every read and serves `pages` in order.

    Records `scan` and `query` alike, so the assertions hold whichever read the fix uses. Pages are
    dicts already shaped as a DynamoDB response ({'Items': [...], 'LastEvaluatedKey': {...}}). When
    `repeat_last` is set the final page is served forever (with its LastEvaluatedKey), modelling a
    table the walk can never exhaust -- until RUNAWAY_READ_CALLS, where it raises so an unbounded loop
    fails the test instead of hanging it."""

    def __init__(self, pages, repeat_last=False):
        self.pages = pages
        self.repeat_last = repeat_last
        self.calls = []

    def _next(self, kwargs):
        self.calls.append(kwargs)
        if len(self.calls) > RUNAWAY_READ_CALLS:
            raise AssertionError(
                f"the workflows read was still paging after {RUNAWAY_READ_CALLS} calls")
        index = len(self.calls) - 1
        if index < len(self.pages):
            return self.pages[index]
        if self.repeat_last:
            return self.pages[-1]
        return {"Items": []}

    def scan(self, **kwargs):
        return self._next(kwargs)

    def query(self, **kwargs):
        return self._next(kwargs)


def _unbounded_table():
    """A table whose pages never run out and whose rows never match the pipeline (so any warning the
    function returns is about its own incompleteness, not about a workflow)."""
    return _RecordingTable(
        [{"Items": [_workflow_row("other", pipeline_composite="db1:unrelated")],
          "LastEvaluatedKey": {"workflowId": "other"}}],
        repeat_last=True)


@pytest.mark.unit
class TestWorkflowReadIsProjected:
    """The read must be projected rather than fetching whole workflow items, and must name every
    attribute the check uses. `specifiedPipelines` has to be projected as the WHOLE top-level
    attribute: the membership test looks up the literal inner key `pipelineDatabaseId:pipelineId`,
    which is not a projectable path.

    What is asserted is what a ProjectionExpression actually guarantees: the three needed attributes
    are requested, and attributes a real workflow row carries but this check never reads are not.
    Naming a fourth needed attribute later is a legitimate change and does not fail these."""

    def test_the_read_requests_every_attribute_the_check_needs(self):
        """FIX-062: a ProjectionExpression naming databaseId, workflowId and specifiedPipelines."""
        table = _RecordingTable([{"Items": [_workflow_row("wf1")]}])
        warnings = pipeline_trigger_template_warnings(
            table, _trigger_with_no_default, "db1", "pipe1", True)
        # The check still WORKS -- a projection that drops specifiedPipelines yields zero warnings,
        # which would otherwise read as "correctly configured". Stated over the workflows NAMED
        # rather than as a count, so an extra string about the walk's own completeness would not
        # fail it while a missing report would.
        assert _workflows_named(warnings, ["wf1"]) == {"wf1"}, (
            f"the misconfigured workflow was not reported: {warnings}")
        assert not _unattributed(warnings, ["wf1"]), (
            f"one fully-read page reported its own incompleteness: {warnings}")
        assert table.calls, "no read was issued"
        projection = table.calls[0].get("ProjectionExpression")
        assert projection, "the read fetched whole workflow items (no ProjectionExpression)"
        assert set(PROJECTED_ATTRIBUTES) <= _projected_attributes(projection), (
            f"an attribute the check reads is not projected: {projection!r}")

    def test_the_read_does_not_request_attributes_the_check_never_uses(self):
        """The byte reduction: real workflow attributes this check ignores stay unfetched.

        The stub row serves each of them, so an unprojected read would have returned them.
        """
        row = _workflow_row("wf1")
        for attribute in UNNEEDED_ATTRIBUTES:
            assert attribute in row, (
                f"{attribute} is not on the stub row, so its absence from the projection proves "
                f"nothing")
        table = _RecordingTable([{"Items": [row]}])
        pipeline_trigger_template_warnings(table, _trigger_with_no_default, "db1", "pipe1", True)
        assert table.calls, "no read was issued"
        # A read with NO ProjectionExpression requests every attribute, which is the whole-item fetch
        # this test exists to bound -- and it yields an EMPTY requested set, so the exclusion below
        # would hold vacuously over exactly the case that fails the property.
        projection = table.calls[0].get("ProjectionExpression")
        assert projection, (
            "the read fetched whole workflow items (no ProjectionExpression), so every attribute "
            "below was requested")
        requested = _projected_attributes(projection)
        assert not (requested & set(UNNEEDED_ATTRIBUTES)), (
            f"the read requests attributes the check never uses: "
            f"{sorted(requested & set(UNNEEDED_ATTRIBUTES))}")


@pytest.mark.unit
class TestWorkflowReadIsBounded:
    """A bounded walk, and a bound that says so."""

    def test_the_walk_is_capped(self):
        """FIX-062: the read stops after a bounded number of pages."""
        table = _unbounded_table()
        pipeline_trigger_template_warnings(table, _trigger_with_no_default, "db1", "pipe1", True)
        assert len(table.calls) <= READ_CALL_CEILING, (
            f"the workflows read issued {len(table.calls)} calls; a bounded walk must stop")

    def test_a_capped_walk_reports_that_it_is_incomplete(self):
        """FIX-062: hitting the cap surfaces an explicit marker, not just a shorter list.

        The scenario deliberately contains NO misconfigured workflow, so anything returned here is
        the incompleteness report itself — the assertion needs no assumption about its wording."""
        table = _unbounded_table()
        warnings = pipeline_trigger_template_warnings(
            table, _trigger_with_no_default, "db1", "pipe1", True)
        assert warnings, (
            "a truncated check returned no signal, so an operator cannot tell a truncated result "
            "from a clean one")
        assert all(isinstance(w, str) and w.strip() for w in warnings), (
            "the caller (pipelineService._pipeline_save_warnings) extends a list of strings")

    def test_no_incompleteness_is_reported_under_the_cap(self):
        """FIX-062 control: the marker must be conditional on actually truncating.

        Without this, the test above is satisfied by an unconditional marker on every save, which
        would be meaningless. Two pages, nothing misconfigured -> no warnings at all. Passes today
        and must keep passing after the fix."""
        table = _RecordingTable([
            {"Items": [_workflow_row("wfA", pipeline_composite="db1:unrelated")],
             "LastEvaluatedKey": {"workflowId": "wfA"}},
            {"Items": [_workflow_row("wfB", pipeline_composite="db1:unrelated")]},
        ])
        assert pipeline_trigger_template_warnings(
            table, _trigger_with_no_default, "db1", "pipe1", True) == []
        # Direction-correct: the second page must be reached (a walk that stopped at page one would
        # have missed it) and the walk must still be bounded. An implementation that issues an extra
        # read is not a regression, so the count is not pinned.
        assert 2 <= len(table.calls) <= READ_CALL_CEILING, (
            f"the second page was not reached, or the walk is unbounded: {len(table.calls)} calls")


@pytest.mark.unit
class TestPaginationAndCompleteness:
    """The behavioural half. These pass today and are the control for the projection/cap tests: the
    function returns [] on ANY internal error, so a change that breaks it produces no warning and no
    exception, and a test that only asserts 'no exception' passes on a completely broken read."""

    def test_a_match_on_the_second_page_still_warns(self):
        """FIX-062 control: the walk follows LastEvaluatedKey across pages.

        The only misconfigured workflow is on page two, so a fix that stops after the first page
        (or forgets ExclusiveStartKey) silently reports nothing."""
        table = _RecordingTable([
            {"Items": [_workflow_row("wfA", pipeline_composite="db1:unrelated")],
             "LastEvaluatedKey": {"workflowId": "wfA"}},
            {"Items": [_workflow_row("wfB")]},
        ])
        warnings = pipeline_trigger_template_warnings(
            table, _trigger_with_no_default, "db1", "pipe1", True)
        # The SET of workflows named: wfB is reported and the unrelated wfA is not. A count would
        # also fail an implementation that said something about its own completeness.
        assert _workflows_named(warnings, ["wfA", "wfB"]) == {"wfB"}, warnings
        assert "db1:wfB" in " ".join(warnings), warnings
        # The cursor was threaded, asserted over the set of reads rather than at a pinned index.
        assert {"workflowId": "wfA"} in [call.get("ExclusiveStartKey") for call in table.calls], (
            f"page one's LastEvaluatedKey was never sent back as ExclusiveStartKey: {table.calls}")
        assert 2 <= len(table.calls) <= READ_CALL_CEILING, (
            f"the walk is unbounded or never paged: {len(table.calls)} calls")

    def test_a_workflow_row_without_allListPartition_is_still_found(self):
        """FIX-062 control: the read must not become sparse-index-only.

        `WorkflowsByDateGSI` partitions on the constant `allListPartition`, so a row written without
        that attribute — the v2.5->v2.6 migrated shape, and anything written by a path that forgets
        it — is absent from the index entirely. Switching this scan to that GSI would stop warning
        for exactly the legacy workflows most likely to be misconfigured, with no error. The stub
        models that: `query` on the index serves only rows carrying the attribute, `scan` serves
        everything.

        Passes today (the scan sees the row) and must keep passing after the fix."""
        rows = [_workflow_row("legacy-wf", with_list_partition=False)]

        table = MagicMock()
        table.scan.side_effect = lambda **kwargs: {"Items": list(rows)}
        table.query.side_effect = lambda **kwargs: {
            "Items": [r for r in rows if r.get("allListPartition")]}

        warnings = pipeline_trigger_template_warnings(
            table, _trigger_with_no_default, "db1", "pipe1", True)
        assert _workflows_named(warnings, ["legacy-wf"]) == {"legacy-wf"}, (
            "a workflow row with no allListPartition was not seen; the sparse by-date GSI cannot "
            f"back this check: {warnings}")
        assert "db1:legacy-wf" in " ".join(warnings), warnings


@pytest.mark.unit
class TestTheTriggerReadFanOutIsBounded:
    """The page cap bounds the SCAN. It bounds neither of the two costs that scale with MATCHES.

    Every matching workflow costs one further sequential `get_trigger_row` (a DynamoDB `get_item` in
    production), so a page cap of 20 x 200 still permits thousands of sequential reads inside one
    synchronous pipeline save. The scenarios here put every match on a SINGLE page, so the page cap
    cannot fire and the only thing that can stop the walk is a bound on the reads themselves.
    """

    def test_the_per_match_trigger_read_is_bounded(self):
        """One page, MATCHING_WORKFLOWS matches, none of them misconfigured.

        The trigger is configured, so no warning is due for any of them -- the reads are pure cost.
        A walk that reads every match issues MATCHING_WORKFLOWS get_items on the save path.
        """
        table = _one_page_of_matches(MATCHING_WORKFLOWS)
        trigger = _configured_trigger()
        pipeline_trigger_template_warnings(table, trigger, "db1", "pipe1", True)
        assert len(trigger.calls) <= TRIGGER_LOOKUP_CEILING, (
            f"the walk issued {len(trigger.calls)} sequential trigger reads for "
            f"{MATCHING_WORKFLOWS} matching workflows; the per-match fan-out is unbounded")

    def test_stopping_at_the_read_bound_reports_that_it_is_incomplete(self):
        """A truncated warning set must not read as "nothing is misconfigured".

        Nothing in this scenario is misconfigured, so anything returned is the incompleteness report
        itself -- the assertion needs no assumption about its wording.
        """
        table = _one_page_of_matches(MATCHING_WORKFLOWS)
        trigger = _configured_trigger()
        warnings = pipeline_trigger_template_warnings(table, trigger, "db1", "pipe1", True)
        assert warnings, (
            "the walk stopped before examining every matching workflow and said nothing, so an "
            "operator cannot tell a truncated result from a clean one")
        assert all(isinstance(w, str) and w.strip() for w in warnings), (
            "the caller (pipelineService._pipeline_save_warnings) extends a list of strings")

    def test_no_incompleteness_is_reported_when_every_match_was_examined(self):
        """Control: the marker must be conditional on actually stopping early.

        Three matches, all examined, none misconfigured -> no warnings at all. Without this, the
        test above is satisfied by an unconditional marker on every save.
        """
        table = _one_page_of_matches(3)
        trigger = _configured_trigger()
        assert pipeline_trigger_template_warnings(table, trigger, "db1", "pipe1", True) == []
        # Direction-correct, over the SET of workflows whose trigger was read rather than over a
        # call total: examining FEWER than all three is the regression this control exists to catch,
        # while a repeated read, or a read of an unrelated workflow, is not one. A count would fail a
        # strictly safer implementation of the very cost the fix set out to bound.
        examined = set(trigger.calls)
        assert {("db1", f"wf{index}") for index in range(3)} <= examined, (
            f"the three matching workflows were not all examined: {sorted(examined)}")
        assert len(trigger.calls) <= TRIGGER_LOOKUP_CEILING, (
            f"three matching workflows cost {len(trigger.calls)} sequential trigger reads")


@pytest.mark.unit
class TestTheReturnedWarningSetIsBounded:
    """The list is serialized into the pipeline save response body (pipelineService.create_pipeline /
    update_pipeline put it under `warnings`), so its size is a response bound -- Rule 15. One
    ~300-character string per misconfigured workflow reaches megabytes on a large deployment."""

    def test_the_number_of_returned_warnings_is_bounded(self):
        table = _one_page_of_matches(MATCHING_WORKFLOWS)
        trigger = _misconfigured_trigger()
        warnings = pipeline_trigger_template_warnings(table, trigger, "db1", "pipe1", True)
        assert len(warnings) <= WARNING_CEILING, (
            f"{MATCHING_WORKFLOWS} misconfigured workflows produced {len(warnings)} warning "
            f"strings in one save response; the returned set is unbounded")

    def test_a_bounded_warning_set_says_it_is_incomplete(self):
        """Truncating the list of affected workflows must be visible, not just shorter.

        Every match here IS misconfigured, so the per-workflow warnings are legitimate; the extra
        assertion is that at least one returned string is not one of them.
        """
        table = _one_page_of_matches(MATCHING_WORKFLOWS)
        trigger = _misconfigured_trigger()
        warnings = pipeline_trigger_template_warnings(table, trigger, "db1", "pipe1", True)
        served = [f"wf{index}" for index in range(MATCHING_WORKFLOWS)]
        assert _workflows_named(warnings, served), (
            f"no workflow was reported at all, so nothing was truncated: {warnings}")
        assert _unattributed(warnings, served), (
            f"{MATCHING_WORKFLOWS} affected workflows produced {len(warnings)} strings, every one "
            f"of them about a single workflow, so the truncation carries no signal: {warnings}")

    def test_every_affected_workflow_is_named_under_the_bound(self):
        """Control: the bound must not be achieved by reporting fewer than it could.

        Three misconfigured workflows, all named, and no incompleteness signal added.
        """
        table = _one_page_of_matches(3)
        trigger = _misconfigured_trigger()
        warnings = pipeline_trigger_template_warnings(table, trigger, "db1", "pipe1", True)
        served = ["wf0", "wf1", "wf2"]
        # Direction-correct rather than a count of 3: every affected workflow is named, the response
        # bound still holds, and nothing beyond the per-workflow reports is returned -- the sibling
        # test above identifies the incompleteness signal the same way, as a returned string that
        # names no workflow. An implementation that reported the three in two strings, or worded
        # them differently, is not a regression; one that dropped a workflow, or claimed a complete
        # list was truncated, is.
        assert _workflows_named(warnings, served) == set(served), (
            f"an affected workflow was not reported: {warnings}")
        assert not _unattributed(warnings, served), (
            f"the complete list of 3 affected workflows also carries an incompleteness signal, "
            f"although nothing was truncated: {warnings}")
        assert len(warnings) <= WARNING_CEILING, warnings

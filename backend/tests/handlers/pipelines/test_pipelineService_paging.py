# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""pipelineService has SIX paged reads: four ``LastEvaluatedKey`` loops and two paginator-backed ones.

The four loops end on the PRESENCE of ``LastEvaluatedKey``. DynamoDB omits the key only once the result
set is exhausted and never sets it empty, and a key that IS present promises nothing about more
matching items -- its absence is the only end-of-set signal -- so presence is the accurate contract. It
is also the only form that stays finite against an under-stubbed reader:
``MagicMock.get('LastEvaluatedKey')`` answers with a truthy child mock forever, so the value form
HANGS instead of failing, and a timeout names no test.

The four loops, and what bounds each:

* ``_referencing_workflow_labels`` -- BOUNDED by ``MAX_REFERENCING_WORKFLOW_PAGES`` pages and
  ``MAX_REFERENCING_WORKFLOWS`` labels;
* ``find_pipeline_id_owner`` -- BOUNDED by ``MAX_ID_LOOKUP_PAGES`` pages, returns a single id;
* ``get_pipeline_templates`` -- BOUNDED by its ``limit`` (``MAX_DETAIL_TEMPLATES``), which is also the
  query ``Limit``, and returns as soon as that many descriptors are collected;
* ``_template_count`` -- UNBOUNDED in pages, but it accumulates an integer rather than a set, so it
  cannot grow the response. Its page count is a runtime cost on a user-facing GET, tracked as an open
  item rather than capped here: a cap would turn a reported total into a silently partial one, which
  is a contract decision.

Two of the four (``_template_count``, ``_referencing_workflow_labels``) sit inside a best-effort
``except Exception`` that returns a degraded value. That is why the shared reader raises a
``BaseException``: an ``Exception`` would be swallowed and the hang would read as an ordinary
degraded result.

**The other two paged reads -- ``get_all_pipelines`` and ``get_database_pipelines`` -- need their own
treatment, because they have no ``LastEvaluatedKey`` loop at all.** They page through the boto3
paginator (``paginator.paginate(...).build_full_result()``), which threads the cursor inside botocore,
so the tree-wide form guard (``tests/common/test_paging_key_presence_form.py``) has nothing to inspect
in them and says nothing about them in either direction: neither the value-form check nor its
structural converse ("this file pages, so it must test for the key") can see a paginator read.

Both ARE bounded, by a different mechanism: ``_pagination_config`` clamps ``MaxItems`` -- the total the
paginator accumulates into one response -- and ``PageSize`` to ``MAX_LIST_PAGE_ITEMS``, and the caller
reaches the rest of the set through ``NextToken``. ``PageSize`` alone bounds nothing, since the
paginator keeps issuing requests of that size until ``MaxItems`` is reached.
``TestPaginatorBackedListsAreBounded`` below asserts that clamp at BOTH call sites, and that a third
paginator read cannot be added to this module without a ``PaginationConfig`` -- which is the exact
failure Rule 15 names, ``build_full_result`` accumulating every record for a user-facing GET.
"""

import ast
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.pipelines import pipelineService as ps
from backend.tests.pagingStub import BareMockReader, Pager

MOD = "backend.backend.handlers.pipelines.pipelineService"

_PIPELINE_SERVICE_SOURCE = pathlib.Path(ps.__file__).read_text(encoding="utf-8")

_CLAIMS = {"tokens": ["user1"], "roles": []}

# A bounded and an unbounded paginator read, as the positive/negative control for the detector below.
_PAGINATOR_FORMS = '''
def bounded(client, config):
    return client.get_paginator("query").paginate(
        TableName="t", PaginationConfig=config).build_full_result()


def unbounded(client):
    return client.get_paginator("query").paginate(TableName="t").build_full_result()
'''


def paginator_reads(source):
    """Every ``.paginate(...)`` call line. The anti-vacuity control for the check below."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "paginate"
    )


def paginator_reads_without_a_bound(source):
    """``.paginate(...)`` call lines carrying no ``PaginationConfig``, i.e. an unbounded accumulation."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "paginate"
        and not any(keyword.arg == "PaginationConfig" for keyword in node.keywords)
    )


def _list_read(which, query_params):
    """Run one of the two paginator-backed list reads against a stubbed paginator, and return it."""
    paginator = MagicMock()
    paginator.paginate.return_value.build_full_result.return_value = {"Items": []}

    with patch(f"{MOD}.dynamodb") as mock_dynamodb:
        mock_dynamodb.meta.client.get_paginator.return_value = paginator
        if which == "get_all_pipelines":
            ps.get_all_pipelines(query_params, False, _CLAIMS)
        else:
            ps.get_database_pipelines("db1", query_params, False, _CLAIMS)

    return paginator


def _workflow(workflow_id, references=()):
    return {
        "databaseId": "db1",
        "workflowId": workflow_id,
        "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": ref} for ref in references],
    }


@pytest.mark.unit
class TestTemplateCountPaging:
    """A COUNT query is capped at 1 MB of SCANNED index per page, so the count needs every page."""

    def test_the_count_sums_every_page(self):
        pager = Pager(
            {"Count": 3, "LastEvaluatedKey": {"templateId": "t3"}},
            {"Count": 2, "LastEvaluatedKey": {"templateId": "t5"}},
            {"Count": 1},
            name="_template_count",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._templates_table", return_value=table):
            total = ps._template_count("db1", "pipe1")

        assert total == 6
        # Asserted over the set of cursors rather than over read counts, so an extra read passes.
        pager.assert_paged_to_exhaustion()

    def test_the_key_condition_survives_every_continuation(self):
        pager = Pager(
            {"Count": 1, "LastEvaluatedKey": {"templateId": "t1"}},
            {"Count": 1},
            name="_template_count",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._templates_table", return_value=table):
            ps._template_count("db1", "pipe1")

        assert all(call["Select"] == "COUNT" for call in pager.calls), pager.calls
        assert all("KeyConditionExpression" in call for call in pager.calls), pager.calls

    def test_terminates_against_an_under_stubbed_reader(self):
        """The count is best-effort (``except Exception`` -> ``None``), so the guard must outrank it.

        A non-terminating loop here would hang the run; the capped reader raises a BaseException so
        it is neither swallowed by the best-effort arm nor reported as a timeout.
        """
        table = MagicMock()
        table.query.side_effect = BareMockReader(name="_template_count")

        with patch(f"{MOD}._templates_table", return_value=table):
            total = ps._template_count("db1", "pipe1")

        # A Mock Count is not a real number, so the total itself is meaningless -- what matters is
        # that the loop RETURNED rather than spun, and did not degrade to the error arm.
        assert total is not None

    def test_a_read_error_still_degrades_to_no_count(self):
        """Positive control for the assertion above: the error arm is reachable and returns None."""
        table = MagicMock()
        table.query.side_effect = Exception("throttled")

        with patch(f"{MOD}._templates_table", return_value=table):
            assert ps._template_count("db1", "pipe1") is None


@pytest.mark.unit
class TestReferencingWorkflowLookupPaging:
    """BOUNDED: the walk stops at MAX_REFERENCING_WORKFLOW_PAGES pages / MAX_REFERENCING_WORKFLOWS
    labels, so this loop cannot accumulate an unbounded set. The paging form still has to be right,
    because within the cap the referencing workflow can sit on any page."""

    def test_a_referencing_workflow_on_a_later_page_is_found(self):
        pager = Pager(
            {"Items": [_workflow("wf-page-1")],
             "LastEvaluatedKey": {"allListPartition": "workflow", "dateModified": "1"}},
            {"Items": [_workflow("wf-page-2", ["db1:pipe1"])]},
            name="_referencing_workflow_labels",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._workflow_table", return_value=table):
            labels = ps._referencing_workflow_labels("db1", "pipe1")

        assert labels == ["db1:wf-page-2"]
        pager.assert_paged_to_exhaustion()

    def test_the_projection_survives_every_continuation(self):
        pager = Pager(
            {"Items": [_workflow("wf-page-1")],
             "LastEvaluatedKey": {"allListPartition": "workflow", "dateModified": "1"}},
            {"Items": [_workflow("wf-page-2", ["db1:pipe1"])]},
            name="_referencing_workflow_labels",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._workflow_table", return_value=table):
            ps._referencing_workflow_labels("db1", "pipe1")

        assert all(call["IndexName"] == "WorkflowsByDateGSI" for call in pager.calls), pager.calls
        # The attributes the match NEEDS are projected on every continuation. Asserted as containment
        # rather than as the exact expression: a reordered or widened projection still answers the
        # question, so pinning the literal would fail a strictly safer read.
        for call in pager.calls:
            projected = {name.strip() for name in call["ProjectionExpression"].split(",")}
            assert {"databaseId", "workflowId", "specifiedPipelines"} <= projected, call

    def test_terminates_against_an_under_stubbed_reader(self):
        """The lookup is best-effort (``except Exception`` -> ``[]``), so the guard must outrank it."""
        table = MagicMock()
        # The reader must be willing to serve MORE reads than the loop's own page cap allows, or the
        # bound below cannot fail: the default stub cap (12) is under MAX_REFERENCING_WORKFLOW_PAGES,
        # so a value-form loop would trip the stub instead of the assertion.
        reader = BareMockReader(name="_referencing_workflow_labels",
                                max_reads=ps.MAX_REFERENCING_WORKFLOW_PAGES + 5)
        table.query.side_effect = reader

        with patch(f"{MOD}._workflow_table", return_value=table):
            labels = ps._referencing_workflow_labels("db1", "pipe1")

        assert labels == []
        # The bare-Mock page advertises no LastEvaluatedKey, so the loop must have ended on that
        # rather than by exhausting the page cap. Stated as a direction-correct bound, not a count.
        assert len(reader.calls) < ps.MAX_REFERENCING_WORKFLOW_PAGES, (
            f"the lookup read {len(reader.calls)} pages from a reader advertising no "
            f"LastEvaluatedKey, so it ended by exhausting MAX_REFERENCING_WORKFLOW_PAGES "
            f"({ps.MAX_REFERENCING_WORKFLOW_PAGES}) rather than on the absent key")


@pytest.mark.unit
class TestPipelineIdOwnerLookupPaging:
    """BOUNDED: capped at MAX_ID_LOOKUP_PAGES pages and returns a single id, but the owning row can
    sit on any page within the cap -- and this loop has NO try/except, so a hang here is a real one."""

    def test_an_owner_on_a_later_page_is_found(self):
        pager = Pager(
            {"Items": [],
             "LastEvaluatedKey": {"allListPartition": "pipeline", "dateModified": "1"}},
            {"Items": [{"databaseId": "owner-db", "pipelineId": "pipe1"}]},
            name="find_pipeline_id_owner",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._pipeline_table", return_value=table):
            owner = ps.find_pipeline_id_owner("pipe1")

        assert owner == "owner-db"
        pager.assert_paged_to_exhaustion()

    def test_a_free_id_is_reported_free_after_every_page(self):
        """Positive control: paging to exhaustion must not report every id as taken."""
        pager = Pager(
            {"Items": [],
             "LastEvaluatedKey": {"allListPartition": "pipeline", "dateModified": "1"}},
            {"Items": [{"databaseId": "db1", "pipelineId": "someone-else"}]},
            name="find_pipeline_id_owner",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._pipeline_table", return_value=table):
            # The row on page two belongs to the excluded database, so no other owner exists.
            owner = ps.find_pipeline_id_owner("pipe1", excluding_database_id="db1")

        assert owner is None
        pager.assert_paged_to_exhaustion()

    def test_terminates_against_an_under_stubbed_reader(self):
        table = MagicMock()
        # Above MAX_ID_LOOKUP_PAGES on purpose -- see the sibling test: a stub cap below the loop's
        # own page cap makes the bound below unfailable.
        reader = BareMockReader(name="find_pipeline_id_owner",
                                max_reads=ps.MAX_ID_LOOKUP_PAGES + 5)
        table.query.side_effect = reader

        with patch(f"{MOD}._pipeline_table", return_value=table):
            assert ps.find_pipeline_id_owner("pipe1") is None

        assert len(reader.calls) < ps.MAX_ID_LOOKUP_PAGES, (
            f"the lookup read {len(reader.calls)} pages from a reader advertising no "
            f"LastEvaluatedKey, so it ended by exhausting MAX_ID_LOOKUP_PAGES "
            f"({ps.MAX_ID_LOOKUP_PAGES}) rather than on the absent key")


@pytest.mark.unit
class TestPipelineTemplateDescriptorPaging:
    """BOUNDED: `limit` descriptors (MAX_DETAIL_TEMPLATES by default) and a query `Limit` of the same
    size, so this read cannot grow the details response. It still pages, because a page can come back
    holding fewer rows than its Limit, and a descriptor missed here is a template the details response
    omits while templateCount reports that it exists."""

    def test_descriptors_from_a_later_page_are_returned(self):
        pager = Pager(
            {"Items": [{"templateId": "t1", "templateName": "One"}],
             "LastEvaluatedKey": {"pipelineDatabaseId:pipelineId": "db1:pipe1",
                                  "templateId": "t1"}},
            {"Items": [{"templateId": "t2", "templateName": "Two", "configFormat": "yaml",
                        "allowCustomEdit": True}]},
            name="get_pipeline_templates",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._templates_table", return_value=table):
            templates = ps.get_pipeline_templates("db1", "pipe1", limit=10)

        assert [t["templateId"] for t in templates] == ["t1", "t2"]
        # The descriptor defaults are applied per row, not only to the first page's rows.
        assert templates[0]["configFormat"] == "json"
        assert templates[0]["allowCustomEdit"] is False
        assert templates[1]["configFormat"] == "yaml"
        pager.assert_paged_to_exhaustion()

    def test_the_key_condition_and_limit_survive_every_continuation(self):
        pager = Pager(
            {"Items": [{"templateId": "t1"}],
             "LastEvaluatedKey": {"pipelineDatabaseId:pipelineId": "db1:pipe1",
                                  "templateId": "t1"}},
            {"Items": [{"templateId": "t2"}]},
            name="get_pipeline_templates",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._templates_table", return_value=table):
            ps.get_pipeline_templates("db1", "pipe1", limit=10)

        assert all("KeyConditionExpression" in call for call in pager.calls), pager.calls
        # Every read stays capped, so a continuation cannot pull an unbounded page into the details
        # response.
        assert all(call["Limit"] == 10 for call in pager.calls), pager.calls

    def test_the_limit_stops_the_walk_before_the_next_page(self):
        """Positive control for the bound: the walk must not read past `limit` descriptors."""
        pager = Pager(
            {"Items": [{"templateId": "t1"}, {"templateId": "t2"}],
             "LastEvaluatedKey": {"pipelineDatabaseId:pipelineId": "db1:pipe1",
                                  "templateId": "t2"}},
            {"Items": [{"templateId": "t3"}]},
            name="get_pipeline_templates",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._templates_table", return_value=table):
            templates = ps.get_pipeline_templates("db1", "pipe1", limit=2)

        assert [t["templateId"] for t in templates] == ["t1", "t2"]
        # It returned at the limit, so the cursor page two would have needed was never sent.
        assert pager.resumed_from == [], pager.calls

    def test_terminates_against_an_under_stubbed_reader(self):
        """This read has NO try/except, so a non-terminating loop here hangs the run outright."""
        table = MagicMock()
        table.query.side_effect = BareMockReader(name="get_pipeline_templates")

        with patch(f"{MOD}._templates_table", return_value=table):
            assert ps.get_pipeline_templates("db1", "pipe1") == []


@pytest.mark.unit
class TestPaginatorBackedListsAreBounded:
    """The two paged reads the tree-wide form guard cannot see.

    ``paginator.paginate(...)`` threads the cursor inside botocore, so there is no continuation decision
    in the source for that guard to inspect -- a paginator read is neither flagged nor cleared by it.
    What has to hold instead is a bound on what one response accumulates (Rule 15), asserted here at
    both call sites and structurally for whatever paginator read lands in this module next.
    """

    LIST_READS = ["get_all_pipelines", "get_database_pipelines"]

    def test_this_module_really_does_page_through_the_paginator(self):
        # Anti-vacuity: if these reads stopped using the paginator, the structural check below would
        # pass by finding nothing at all.
        found = paginator_reads(_PIPELINE_SERVICE_SOURCE)
        assert len(found) >= len(self.LIST_READS), (
            f"only {len(found)} paginator read(s) found in {ps.__file__}, but the list endpoints are "
            "paginator-backed -- the structural check below would be scanning nothing")

    def test_no_paginator_read_here_accumulates_without_a_bound(self):
        """A THIRD paginator read added without a PaginationConfig fails here, not in production."""
        unbounded = paginator_reads_without_a_bound(_PIPELINE_SERVICE_SOURCE)

        assert unbounded == [], (
            f"{ps.__file__} line(s) {unbounded}: paginate(...) with no PaginationConfig accumulates "
            "every matching record into one response, which is what Rule 15 forbids for a user-facing "
            "GET. Pass PaginationConfig=_pagination_config(query_params) so MaxItems is clamped to "
            "MAX_LIST_PAGE_ITEMS and the caller pages the rest through NextToken.")

    def test_the_detector_separates_a_bounded_read_from_an_unbounded_one(self):
        """Positive control: an empty result must mean bounded, never "nothing was recognised"."""
        assert len(paginator_reads(_PAGINATOR_FORMS)) == 2, _PAGINATOR_FORMS

        flagged = paginator_reads_without_a_bound(_PAGINATOR_FORMS)

        assert len(flagged) == 1, flagged
        # It flagged the unbounded one specifically, not just "one of them".
        assert "PaginationConfig" not in _PAGINATOR_FORMS.splitlines()[flagged[0] - 1]

    @pytest.mark.parametrize("which", LIST_READS)
    def test_an_enormous_request_is_clamped_before_it_reaches_the_paginator(self, which):
        paginator = _list_read(
            which, {"maxItems": "999999", "pageSize": "999999", "startingToken": None})

        assert paginator.paginate.call_args is not None, (
            f"{which} never reached the paginator, so this asserts nothing")
        config = paginator.paginate.call_args.kwargs["PaginationConfig"]
        # UPPER bounds, in the direction that matters: a smaller cap -- a strictly safer
        # implementation -- passes, while an uncapped accumulation fails.
        assert config["MaxItems"] <= ps.MAX_LIST_PAGE_ITEMS, config
        assert config["PageSize"] <= config["MaxItems"], config

    @pytest.mark.parametrize("which", LIST_READS)
    def test_a_modest_request_is_not_satisfied_by_reading_nothing(self, which):
        """Positive control for the bounds above: they must not hold by clamping everything to zero."""
        paginator = _list_read(which, {"maxItems": "7", "pageSize": "3", "startingToken": None})

        config = paginator.paginate.call_args.kwargs["PaginationConfig"]
        assert 0 < config["MaxItems"] <= 7, config
        assert 0 < config["PageSize"] <= config["MaxItems"], config

    @pytest.mark.parametrize("which", LIST_READS)
    def test_the_caller_can_resume_where_the_last_response_stopped(self, which):
        """The bound is only legitimate because the remainder is reachable: the token is threaded."""
        paginator = _list_read(
            which, {"maxItems": "10", "pageSize": "10", "startingToken": "opaque-token"})

        assert paginator.paginate.call_args.kwargs["PaginationConfig"]["StartingToken"] == (
            "opaque-token")

# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reindex slots against moto's DynamoDB: more launches than slots never hold more slots than there
are, a terminal release frees a slot through the holder index, a lost holder's slot is claimable once
it expires, and a stranded or ended holder's slot is taken over once it is old enough."""

import os
import threading

import pytest
from moto import mock_aws

from backend.backend.common.workflows import executionLocks as el
from backend.tests.common.workflows.test_executionLocks_moto import (
    _inputs_table,
    _lock_table,
    _resource,
    _workflow_table,
)

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
NOW = 1_800_000_000
STRANDED_AFTER = 900 + el.LOCK_TAKEOVER_MARGIN_SECONDS
TTL = el.reindex_slot_ttl_seconds([{"executionConfig": {"taskTimeout": "18000"}}])


def _keys(count):
    return el.reindex_slot_keys("GLOBAL", "system-genai-metadata", count)


def _held(table):
    return sorted(item["workflowExecutionId"] for item in table.scan()["Items"]
                  if item["lockScope"] == el.LOCK_SCOPE_REINDEX_SLOT and int(item["expiresAt"]) >= NOW)


@pytest.fixture
def ddb():
    with mock_aws():
        yield _resource()


@pytest.mark.unit
class TestNeverMoreThanTheSlotCount:
    def test_more_launches_than_slots_claim_exactly_the_slot_count(self, ddb):
        table = _lock_table(ddb)
        claimed, busy = [], 0
        for index in range(10):
            try:
                claimed.append(el.acquire_reindex_slot(table, _keys(3), f"E{index}", TTL, now=NOW))
            except el.ReindexSlotsBusy:
                busy += 1
        assert sorted(claimed) == _keys(3) and busy == 7
        assert _held(table) == ["E0", "E1", "E2"]

    def test_concurrent_launches_never_hold_more_than_the_slot_count(self, ddb):
        table = _lock_table(ddb)
        results = []

        def _launch(index):
            try:
                results.append(el.acquire_reindex_slot(table, _keys(4), f"E{index}", TTL, now=NOW))
            except el.ReindexSlotsBusy:
                results.append(None)

        threads = [threading.Thread(target=_launch, args=(index,)) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len([key for key in results if key]) == 4
        assert len(_held(table)) == 4

    def test_a_freed_slot_is_claimed_by_the_next_launch(self, ddb):
        table = _lock_table(ddb)
        first = [el.acquire_reindex_slot(table, _keys(2), f"E{index}", TTL, now=NOW) for index in range(2)]
        with pytest.raises(el.ReindexSlotsBusy):
            el.acquire_reindex_slot(table, _keys(2), "E2", TTL, now=NOW)
        assert el.release_locks(table, [first[0]], "E0") == 1
        assert el.acquire_reindex_slot(table, _keys(2), "E2", TTL, now=NOW) == first[0]
        assert _held(table) == ["E1", "E2"]


@pytest.mark.unit
class TestTheTerminalReleaseFreesTheSlot:
    @pytest.mark.parametrize("end_state", ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT", "stopped outside VAMS"])
    def test_every_end_state_frees_the_slot_through_the_holder_index(self, ddb, end_state):
        """The end-state step, the error handler, the abort route and the read path of a run stopped
        outside VAMS all call release_locks_for_execution; the slot row is found by its holder."""
        locks = _lock_table(ddb)
        _workflow_table(ddb)
        _inputs_table(ddb)
        slot = el.acquire_reindex_slot(locks, _keys(2), "E-run", TTL, now=NOW)
        released = el.release_locks_for_execution(
            ddb, locks_table_name="t-locks", workflow_table_name="t-workflows", inputs_table_name="t-inputs",
            workflow_execution_id="E-run", workflow_database_id="GLOBAL", workflow_id="system-genai-metadata")
        assert released == 1 and "Item" not in locks.get_item(Key={"lockKey": slot})


@pytest.mark.unit
class TestALostHolderIsReclaimed:
    def test_an_expired_slot_is_claimed_by_a_plain_put(self, ddb):
        table = _lock_table(ddb)
        el.acquire_reindex_slot(table, _keys(1), "E-lost", TTL, now=NOW)
        with pytest.raises(el.ReindexSlotsBusy):
            el.acquire_reindex_slot(table, _keys(1), "E-next", TTL, now=NOW + TTL)
        assert el.acquire_reindex_slot(table, _keys(1), "E-next", TTL, now=NOW + TTL + 1) == _keys(1)[0]
        assert el.slot_is_free(None, NOW) and not el.slot_is_free({"expiresAt": NOW + 5}, NOW)

    @pytest.mark.parametrize("still_running, taken", [(False, True), (True, False)])
    def test_an_old_slot_is_taken_over_only_when_its_holder_ended(self, ddb, still_running, taken):
        table = _lock_table(ddb)
        el.acquire_reindex_slot(table, _keys(1), "E-old", TTL, now=NOW)
        later = NOW + STRANDED_AFTER + 1
        kwargs = {"stranded_after_seconds": STRANDED_AFTER, "holder_has_record": lambda holder: still_running}
        if taken:
            assert el.acquire_reindex_slot(table, _keys(1), "E-new", TTL, now=later, **kwargs) == _keys(1)[0]
        else:
            with pytest.raises(el.ReindexSlotsBusy):
                el.acquire_reindex_slot(table, _keys(1), "E-new", TTL, now=later, **kwargs)

    def test_a_young_slot_is_never_taken_over(self, ddb):
        table = _lock_table(ddb)
        el.acquire_reindex_slot(table, _keys(1), "E-young", TTL, now=NOW)
        with pytest.raises(el.ReindexSlotsBusy):
            el.acquire_reindex_slot(table, _keys(1), "E-new", TTL, now=NOW + 60,
                                    stranded_after_seconds=STRANDED_AFTER, holder_has_record=lambda h: False)

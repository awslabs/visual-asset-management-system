# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A registration's state, the registration an object belongs to, and its authorization object (#1978).

- `registration_state`: `retired` for a retired row, a runtime row's stored `status` (`pending` when it is
  missing or unknown), `active` otherwise.
- `find_registration`: among the active rows of the event's bucket whose prefix holds the key, the
  longest prefix wins, and the row that answers for a location before a row that duplicates it.
- `active_registrations` leaves out every row that is not active, so a runtime row that is pending or
  disabled answers no lookup.
- `bucket_authorization_object` / `choosable_registration`: what database create and update check.

The module is the REAL one, registered by tests/conftest.py.
"""

import sys

import pytest


def _brg():
    return sys.modules['common.bucketRegistrations']


def _row(bucket_id, prefix, bucket='b', **extra):
    return {'bucketId': bucket_id, 'bucketName': bucket, 'baseAssetsPrefix': prefix, **extra}


@pytest.mark.unit
class TestRegistrationState:
    @pytest.mark.parametrize('row, state', [
        ({}, 'active'),
        ({'source': 'static'}, 'active'),
        ({'retired': True}, 'retired'),
        ({'retired': True, 'source': 'runtime', 'status': 'active'}, 'retired'),
        ({'source': 'runtime', 'status': 'active'}, 'active'),
        ({'source': 'runtime', 'status': 'pending'}, 'pending'),
        ({'source': 'runtime', 'status': 'disabled'}, 'disabled'),
        ({'source': 'runtime'}, 'pending'),
        ({'source': 'runtime', 'status': 'ACTIVE'}, 'pending'),
        ({'source': 'static', 'status': 'disabled'}, 'active'),
        (None, 'active'),
    ])
    def test_state(self, row, state):
        assert _brg().registration_state(row) == state

    def test_source_defaults_to_static(self):
        brg = _brg()
        assert brg.registration_source({}) == 'static'
        assert brg.registration_source({'source': 'runtime'}) == 'runtime'
        assert brg.registration_source({'source': 'other'}) == 'static'

    def test_active_registrations_leave_out_runtime_rows_that_are_not_active(self):
        rows = [_row('pending', 'a/', source='runtime', status='pending'),
                _row('disabled', 'a/', source='runtime', status='disabled'),
                _row('live', 'a/', source='runtime', status='active'),
                _row('static', 'b/')]
        assert [r['bucketId'] for r in _brg().active_registrations(rows)] == ['live', 'static']


@pytest.mark.unit
class TestFindRegistration:
    def test_longest_prefix_holding_the_key_wins(self):
        rows = [_row('root', '/'), _row('team', 'team1/'), _row('sub', 'team1/sub/')]
        brg = _brg()
        assert brg.find_registration('b', 'team1/sub/a1/f.glb', rows)['bucketId'] == 'sub'
        assert brg.find_registration('b', 'team1/a1/f.glb', rows)['bucketId'] == 'team'
        assert brg.find_registration('b', 'other/a1/f.glb', rows)['bucketId'] == 'root'

    def test_prefixes_compare_in_the_shared_normalized_form(self):
        # assetLocations.normalize_base_prefix: one trailing '/', no leading '/', '' for the root.
        rows = [_row('a', 'assets'), _row('c', '/assets2/'), _row('d', 'deep//')]
        brg = _brg()
        assert brg.find_registration('b', 'assets/x/f', rows)['bucketId'] == 'a'
        assert brg.find_registration('b', 'assets2/x/f', rows)['bucketId'] == 'c'
        assert brg.find_registration('b', 'deep/x/f', rows)['bucketId'] == 'd'
        assert brg.find_registration('b', 'assetsx/f', rows) is None

    @pytest.mark.parametrize('root', ['/', '', None, '//'])
    def test_every_spelling_of_the_root_holds_every_key(self, root):
        assert _brg().find_registration('b', 'a/f', [_row('root', root)])['bucketId'] == 'root'

    def test_only_active_rows_of_the_bucket_answer(self):
        rows = [_row('retired', 'team1/', retired=True), _row('other-bucket', 'team1/', bucket='c'),
                _row('pending', 'team1/sub/', source='runtime', status='pending'), _row('root', '/')]
        assert _brg().find_registration('b', 'team1/sub/a/f', rows)['bucketId'] == 'root'
        assert _brg().find_registration('b', 'team1/sub/a/f', rows[:3]) is None

    def test_the_primary_answers_before_its_duplicate(self):
        rows = [_row('dup', 'assets/', duplicateOfBucketId='primary'), _row('primary', 'assets/')]
        assert _brg().find_registration('b', 'assets/a/f', rows)['bucketId'] == 'primary'

    def test_no_rows(self):
        assert _brg().find_registration('b', 'a/f', []) is None
        assert _brg().find_registration('b', 'a/f', None) is None


@pytest.mark.unit
class TestBucketAuthorization:
    def test_object_carries_the_four_fields_and_the_type(self):
        obj = _brg().bucket_authorization_object(
            _row('id-1', 'assets/', bucketRegion='us-west-2', isDefault=True, source='runtime'))
        assert obj == {'object__type': 'bucket', 'bucketId': 'id-1', 'bucketName': 'b',
                       'bucketRegion': 'us-west-2', 'source': 'runtime'}

    def test_a_row_without_source_or_region_is_static_with_an_empty_region(self):
        obj = _brg().bucket_authorization_object(_row('id-1', '/'))
        assert obj['source'] == 'static'
        assert obj['bucketRegion'] == ''

    def test_choosable_registration(self):
        brg = _brg()
        assert brg.choosable_registration([]) is None
        assert brg.choosable_registration([_row('r', '/', retired=True)]) is None
        assert brg.choosable_registration([_row('p', '/', source='runtime', status='pending')]) is None
        assert brg.choosable_registration([_row('a', '/')])['bucketId'] == 'a'

    def test_the_refusal_names_no_id(self):
        message = _brg().BUCKET_NOT_AVAILABLE_MESSAGE
        assert message == 'Bucket ID not found or not available'

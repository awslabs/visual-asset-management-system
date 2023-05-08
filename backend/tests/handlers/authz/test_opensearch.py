# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from backend.handlers.authz.opensearch import AuthEntities
import pytest


# a constraint object looks like this
# {
#     "entityType": "constraint",
#     "constraintId": "0346b172-486a-421c-af57-387f33ce474c",
#     "groupPermissions": [
#         {
#             "permission": "Read",
#             "id": "76892a34-46d6-47d2-8c75-91e38ee4590e",
#             "groupId": "vams:all_users"
#         },
#         {
#             "permission": "Edit",
#             "id": "76892a34-46d6-47d2-8c75-91e38ee4590e",
#             "groupId": "vams:all_users"
#         },
#         {
#             "permission": "Admin",
#             "id": "f08d8687-4bb1-4548-8768-452c50cf8e5e",
#             "groupId": "XXXXXXXXXXXXXXXXX"
#         }],
#     "description": "The description of this constraint",
#     "name": "nameoftheconstraint",
#     "criteria": [
#         {
#             "id": "ctzl02dw4e",
#             "field": "labels",
#             "value": "top-secret",
#             "operator": "contains"
#         },
#         {
#             "id": "ctzl02dw4e",
#             "field": "labels",
#             "value": "private",
#             "operator": "does_not_contain"
#         },
#         {
#             "id": "cjb0luxsq5",
#             "field": "f2",
#             "value": "restricted1, restricted2",
#             "operator": "is_one_of"
#         },
#         {
#             "id": "cjb0luxsq5",
#             "field": "f2",
#             "value": "restricted,private,secret",
#             "operator": "is_not_one_of"
#         }
#     ]
# }

@pytest.fixture
def claims_fixture():
    return [{'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Admin',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'vams:all_users'},
                                  {'permission': 'Admin',
                                   'id': 'f08d8687-4bb1-4548-8768-452c50cf8e5e',
                                   'groupId': 'arccos@amazon.com'}],
             'description': 'This constraint requires groups. ',
             'name': 'groupsconstraint',
             'criteria': [{'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'top-secret',
                           'operator': 'contains'}]},
            {'entityType': 'constraint',
             'constraintId': '0a19333c-f50e-48a6-8614-a8ccbfce6878',
             'groupPermissions': [{'permission': 'Edit',
                                   'id': 'a43c6471-7550-4df7-b2fd-324acf559cc1',
                                   'groupId': '008fe5a3-6769-4915-a6b2-348ff61241a0'}],
             'description': 'test6',
             'name': 'test6',
             'criteria': [{'id': 'cjb0luxsq5',
                           'field': 'f2',
                           'value': 'restricted',
                           'operator': 'is_one_of'}]},
            {'entityType': 'constraint',
             'constraintId': '25239109-0329-4c99-8fbc-9bbfa7546e1c',
             'groupPermissions': [{'permission': 'Edit',
                                   'id': '2f591908-6769-42eb-ba21-1cd741579890',
                                   'groupId': '63030da9-4e79-402b-9d0d-78694467cdd1'}],
             'description': 'test4',
             'name': 'test3',
             'criteria': [{'id': 'cos7jgsq57',
                           'field': 'f1',
                           'value': 'secret',
                           'operator': 'contains'}]},
            {'entityType': 'constraint',
             'constraintId': '8d516da7-b69a-4178-b911-7f2954bae82e',
             'groupPermissions': [{'permission': 'Edit',
                                   'id': '7e90345e-82ae-472c-958a-aadeed35a56a',
                                   'groupId': '63030da9-4e79-402b-9d0d-78694467cdd1'}],
             'description': 'this tests a second constraint',
             'name': 'test2',
             'criteria': [{'id': 'cc1kc7z7at',
                           'field': 'labels',
                           'value': 'not this one',
                           'operator': 'is_not_one_of'}]},
            {'entityType': 'constraint',
             'constraintId': 'e82f202b-22ea-4d58-a9b4-875648ab7d59',
             'groupPermissions': [{'permission': 'Read',
                                   'id': 'e3b4149e-18dd-4f22-b6cc-eb6419a76d7f',
                                   'groupId': '63030da9-4e79-402b-9d0d-78694467cdd1'}],
             'description': 'test5',
             'name': 'test5',
             'criteria': [{'id': 'cr29eeuku0',
                           'field': 'f1',
                           'value': 'restricted',
                           'operator': 'is_not_one_of'}]}]


@pytest.fixture
def claims_fixture_is_one_of_operator():
    return [{'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Admin',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'vams:all_users'},
                                  {'permission': 'Admin',
                                   'id': 'f08d8687-4bb1-4548-8768-452c50cf8e5e',
                                   'groupId': 'XXXXXXXXXXXXXXXXX'}],
             'description': 'This constraint requires groups. ',
             'name': 'groupsconstraint',
             'criteria': [{'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'top-secret',
                           'operator': 'is_one_of'}]}]


@pytest.fixture
def claims_fixture_contains_operator_single_criteria():
    return [{'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Admin',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'vams:all_users'},
                                  {'permission': 'Admin',
                                   'id': 'f08d8687-4bb1-4548-8768-452c50cf8e5e',
                                   'groupId': 'XXXXXXXXXXXXXXXXX'}],
             'description': 'This constraint requires groups. ',
             'name': 'groupsconstraint',
             'criteria': [{'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'top-secret',
                           'operator': 'contains'}]}]


def test_contains_operator(claims_fixture_contains_operator_single_criteria):
    auth = AuthEntities(None)
    result = auth.claims_to_opensearch_filters(claims_fixture_contains_operator_single_criteria, {"vams:all_users"})
    assert result['query']['query_string']['query'] == '(labels:(top-secret))'


@pytest.fixture
def claims_fixture_contains_multiple_criteria():
    return [{'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Admin',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'vams:all_users'},
                                  {'permission': 'Admin',
                                   'id': 'f08d8687-4bb1-4548-8768-452c50cf8e5e',
                                   'groupId': 'XXXXXXXXXXXXXXXXX'}],
             'description': 'This constraint requires groups. ',
             'name': 'groupsconstraint',
             'criteria': [{'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'top-secret',
                           'operator': 'contains'},
                          {'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'private',
                           'operator': 'contains'}]}]


def test_contains_multiple_criteria(claims_fixture_contains_multiple_criteria):
    auth = AuthEntities(None)
    result = auth.claims_to_opensearch_filters(claims_fixture_contains_multiple_criteria, {"vams:all_users"})
    assert result['query']['query_string']['query'] == '(labels:(top-secret) AND labels:(private))'


@pytest.fixture
def claims_fixture_is_one_of_operator():
    return [{'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Admin',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'vams:all_users'},
                                  ],
             'description': 'description of the group',
             'name': 'groupsconstraint',
             'criteria': [{'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'top-secret, private, quiet',
                           'operator': 'is_one_of'},
                          {'id': 'ctzl02dw4e',
                           'field': 'secondfield',
                           'value': 'top-secret, hidden',
                           'operator': 'is_one_of'},
                          ]}]


def test_is_one_of_operator(claims_fixture_is_one_of_operator):
    auth = AuthEntities(None)
    result = auth.claims_to_opensearch_filters(claims_fixture_is_one_of_operator, {"vams:all_users"})
    assert result['query']['query_string']['query'] == \
        '(labels:("top-secret" OR "private" OR "quiet") AND secondfield:("top-secret" OR "hidden"))'


@pytest.fixture
def claims_fixture_is_not_one_of_operator():
    return [{'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Admin',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'vams:all_users'},
                                  ],
             'description': 'description of the group',
             'name': 'groupsconstraint',
             'criteria': [{'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'top-secret, private, quiet',
                           'operator': 'is_not_one_of'},
                          {'id': 'ctzl02dw4e',
                           'field': 'secondfield',
                           'value': 'top-secret, hidden',
                           'operator': 'is_not_one_of'},
                          ]}]


def test_is_not_one_of_operator(claims_fixture_is_not_one_of_operator):
    auth = AuthEntities(None)
    result = auth.claims_to_opensearch_filters(claims_fixture_is_not_one_of_operator, {"vams:all_users"})
    assert result['query']['query_string']['query'] == \
        '(-labels:("top-secret" OR "private" OR "quiet") AND -secondfield:("top-secret" OR "hidden"))'


@pytest.fixture
def claims_fixture_does_not_contain_operator():
    return [{'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Admin',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'vams:all_users'},
                                  ],
             'description': 'description of the group',
             'name': 'groupsconstraint',
             'criteria': [{'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'top-secret',
                           'operator': 'does_not_contain'},
                          {'id': 'ctzl02dw4e',
                           'field': 'secondfield',
                           'value': 'hidden',
                           'operator': 'does_not_contain'},
                          ]}]


def test_does_not_contain_operator(claims_fixture_does_not_contain_operator):
    auth = AuthEntities(None)
    result = auth.claims_to_opensearch_filters(claims_fixture_does_not_contain_operator, {"vams:all_users"})
    assert result['query']['query_string']['query'] == \
        '(-labels:(top-secret) AND -secondfield:(hidden))'


@pytest.fixture
def claims_fixture_2_claims_2_groups():
    return [{'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Read',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'team3'},
                                  {'permission': 'Edit',
                                   'id': 'f08d8687-4bb1-4548-8768-452c50cf8e5e',
                                   'groupId': 'team1'}],
             'description': 'This constraint requires groups. ',
             'name': 'groupsconstraint',
             'criteria': [
                 {'id': 'ctzl02dw4e',
                           'field': 'labels',
                           'value': 'top-secret',
                           'operator': 'contains'},
                 {'id': 'ctzl02dw4e',
                  'field': 'level',
                           'value': 'top-secret',
                           'operator': 'contains'},

             ]},
            {'entityType': 'constraint',
             'constraintId': '0346b172-486a-421c-af57-387f33ce474c',
             'groupPermissions': [{'permission': 'Read',
                                   'id': '76892a34-46d6-47d2-8c75-91e38ee4590e',
                                   'groupId': 'team2'},
                                  {'permission': 'Edit',
                                   'id': 'f08d8687-4bb1-4548-8768-452c50cf8e5e',
                                   'groupId': 'team1'},
                                  {'permission': 'Admin',
                                   'id': 'f08d8687-4bb1-4548-8768-452c50cf8e5e',
                                   'groupId': 'team3'}
                                   
                                   ],
             'description': 'This constraint requires groups. ',
             'name': 'groupsconstraint',
             'criteria': [
                 {'id': 'ctzl02dw4e',
                           'field': 'f2',
                           'value': 'private',
                           'operator': 'contains'},
                 {'id': 'ctzl02dw4e',
                  'field': 'f3',
                           'value': 'secret',
                           'operator': 'contains'},

             ]}]


def test_2_claims_2_groups(claims_fixture_2_claims_2_groups):
    auth = AuthEntities(None)
    result = auth.claims_to_opensearch_filters(claims_fixture_2_claims_2_groups, {"team1"})
    assert result['query']['query_string']['query'] == \
        '(labels:(top-secret) AND level:(top-secret)) OR (f2:(private) AND f3:(secret))'



def test_by_permission(claims_fixture_2_claims_2_groups):
    auth = AuthEntities(None)
    result = auth.claims_to_opensearch_agg(claims_fixture_2_claims_2_groups, {"team1"})
    assert result is not None

    # the result should include a filters aggregation wiht 3 buckets (read, edit,admin) using query_string
    # when the particular group does not have a permission, it should be omitted

    example = {
        "aggs": {
            "permissions": {
                "filters": {
                    "filters": {
                        "Read": {
                            "query_string": {
                                "query": "(labels:(top-secret) AND level:(top-secret))"
                            }
                        },
                        "Edit": {
                            "query_string": {
                                "query": "(f2:(private) AND f3:(secret))"
                            }
                        }
                    }
                }
            }
        }
    }

    # team1 only has Edit access to both claims, so the Read and Admin buckets should be omitted
    assert result['aggs']['permissions'] is not None
    assert result['aggs']['permissions']['filters']['filters']['Edit']['query_string']['query'] == \
        "(labels:(top-secret) AND level:(top-secret)) OR (f2:(private) AND f3:(secret))"

    assert "Read" not in result['aggs']['permissions']['filters']['filters']
    assert "Admin" not in result['aggs']['permissions']['filters']['filters']
    assert "Edit" in result['aggs']['permissions']['filters']['filters']

    # team3 has Admin and Edit access in two claims
    result = auth.claims_to_opensearch_agg(claims_fixture_2_claims_2_groups, {"team3"})
    assert "Read" in result['aggs']['permissions']['filters']['filters']
    assert "Edit" not in result['aggs']['permissions']['filters']['filters']
    assert "Admin" in result['aggs']['permissions']['filters']['filters']
    assert  result['aggs']['permissions']['filters']['filters']['Read']['query_string']['query'] == \
           "(labels:(top-secret) AND level:(top-secret))"
    assert  result['aggs']['permissions']['filters']['filters']['Admin']['query_string']['query'] == \
        "(f2:(private) AND f3:(secret))"


    # what if someone is a member of all 3 teams?
    result = auth.claims_to_opensearch_agg(claims_fixture_2_claims_2_groups, {"team1", "team2", "team3"})
    assert "Read" in result['aggs']['permissions']['filters']['filters']
    assert "Edit" in result['aggs']['permissions']['filters']['filters']
    assert "Admin" in result['aggs']['permissions']['filters']['filters']
    assert  result['aggs']['permissions']['filters']['filters']['Read']['query_string']['query'] == \
           "(labels:(top-secret) AND level:(top-secret)) OR (f2:(private) AND f3:(secret))"
    assert  result['aggs']['permissions']['filters']['filters']['Edit']['query_string']['query'] == \
           "(labels:(top-secret) AND level:(top-secret)) OR (f2:(private) AND f3:(secret))"
    assert  result['aggs']['permissions']['filters']['filters']['Admin']['query_string']['query'] == \
           "(f2:(private) AND f3:(secret))"

    result = auth.claims_to_opensearch_agg(claims_fixture_2_claims_2_groups, {"team3"})
    assert result['aggs']['permissions']['filters']['filters']['Read']['query_string']['query'] == \
           "(labels:(top-secret) AND level:(top-secret))"



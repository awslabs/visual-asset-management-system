# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import os
from boto3.dynamodb.conditions import Key, Attr


class AuthEntities:

    def __init__(self, table):
        self.table = table

    def all_constraints(self):
        attrs = "name,groupPermissions,constraintId,description,criteria,entityType".split(",")
        keys_attrs = {"#{f}".format(f=f): f for f in attrs}
        # print(keys_attrs)
        result = self.table.query(
            # Limit=1,
            ExpressionAttributeNames=keys_attrs,
            ProjectionExpression=",".join(keys_attrs.keys()),
            KeyConditionExpression=Key("entityType").eq("constraint"),
            # FilterExpression=Attr("groupPermissions/groupId").eq(user_or_group)
        )
        return result['Items']

    def group_or_user_to_fine_grained_claims(self, groups):
        constraints = self.all_constraints()
        for item in constraints:
            if len(groups & set([gp['groupId'] for gp in item['groupPermissions']])) > 0:
                yield item

    def _format_one_of_criteria(self, criteria):
        values = criteria['value'].split(",")
        values = ["\"{}\"".format(s.strip()) for s in values]
        values = " OR ".join(values)
        return f"{criteria['field']}:({values})"

    def claims_to_opensearch_filters(self, claims, groups):

        by_operator = {
            "contains": [],
            "does_not_contain": [],
            "is_one_of": [],
            "is_not_one_of": [],
        }
        claim_predicates = []
        for claim in claims:
            group_permission = [p for p in claim['groupPermissions'] if p['groupId'] in groups]

            predicates = []
            if len(group_permission) == 0:
                continue
            
            for criteria in claim['criteria']:

                if criteria['operator'] == "contains":
                    predicates.append(f"{criteria['field']}:({criteria['value']})")

                if criteria['operator'] == "does_not_contain":
                    predicates.append(f"-{criteria['field']}:({criteria['value']})")

                if criteria['operator'] == "is_one_of":
                    values_str = self._format_one_of_criteria(criteria)
                    predicates.append(f"{values_str}")

                if criteria['operator'] == "is_not_one_of":
                    values_str = self._format_one_of_criteria(criteria)
                    predicates.append(f"-{values_str}")

            claim_predicates.append("(" + " AND ".join(predicates) + ")")

        return {
            "query": {
                "query_string": {
                    "query": " OR ".join(claim_predicates)
                }
            }
        }

    def claims_to_opensearch_agg(self, claims, groups):

        permissions = {
            "Read": [],
            "Edit": [],
            "Admin": []
        }
        for claim in claims:
            group_permission = [p for p in claim['groupPermissions'] if p['groupId'] in groups]

            # The group permission structure is as follows:
            # {
            #     "groupId": "group-id",
            #     "permissions": "PERMISSION"
            # }
            # Where PERMISSION is one of Read, Edit, Admin.
            # A group can have only 1 permission in a set of groups in a claim.
            #
            # Aggregate the criteria for each group by the permission.
            #
            for permission in permissions.keys():
                for group in group_permission:
                    if group['permission'] == permission:
                        permissions[permission].append(claim)

        aggs = {
            "aggs": {
                "permissions": {
                    "filters": {
                        "filters": {

                        }
                    }
                }
            }
        }

        for permission in permissions.keys():
            query_string = self.claims_to_opensearch_filters(permissions[permission], groups)['query']['query_string']
            if query_string['query'] == "":
                continue

            aggs["aggs"]["permissions"]["filters"]["filters"][permission] = {
                "query_string": query_string
            }

        return aggs

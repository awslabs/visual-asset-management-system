# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import os
import time
from boto3.dynamodb.types import TypeDeserializer
from casbin import FastEnforcer
from casbin import model
from casbin.persist.adapters import string_adapter
from simpleeval import AttributeDoesNotExist
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from datetime import datetime, timedelta
from common.constants import PERMISSION_CONSTRAINT_FIELDS, PERMISSION_CONSTRAINT_POLICY
from locked_dict import locked_dict

# Duration to refresh cache for next invocation - this can be tweaked for performance/consistency needs
#
CASBIN_REFRESH_POLICY_SECONDS = 30

# Amount of attempts to retry fetching of policy from DynamoDB
#
CASBIN_GET_POLICY_RETRY_ATTEMPTS = 3

# Amount of seconds in between attempts to retry fetching of policy from DynamoDB
#
CASBIN_GET_POLICY_RETRY_DELAY_SECONDS = 1

# Controls whether global dictionaries (below) are locked during write access.
# If this lambda runs on 1 vCPU, you might consider setting the value to True. In that case,
# no concerns about reads/writes to the global dictionaries (below) conflicting.
#
CASBIN_NO_DICTIONARY_LOCKING = False

# Defines a boilerplate Deny-all policy for casbin enforcer - for cases where policy_text can't be determined
# (allowing existing enforcement call sites to continue to work without any additional changes)
#
POLICY_TEXT_DENY_ALL = "g,,\0,*,deny\np,,,*,deny"

# Tracks users and their policy_text (which could span multiple roles)
#
casbin_user_policy_map = {} if CASBIN_NO_DICTIONARY_LOCKING else locked_dict.LockedDict()

# Tracks users and their casbin enforcer
#
casbin_user_enforcer_map = {} if CASBIN_NO_DICTIONARY_LOCKING else locked_dict.LockedDict()

logger = safeLogger(service="AuthzInit")

deserializer = TypeDeserializer()
_dynamodb_client = boto3.client("dynamodb")
paginator = _dynamodb_client.get_paginator("scan")

# Determine if MFA is enabled from claims
def is_mfa_enabled(claims_and_roles):
    mfaEnabled = False
    if "mfaEnabled" in claims_and_roles:
        mfaEnabled = claims_and_roles["mfaEnabled"]
    return mfaEnabled

# Wrap CasbinEnforcerService objects, caching them to corresponding users to improve performance
# Policy updates within CasbinEnforcerProxy objects will occur separately.
# CasbinEnforcer acts as the Proxy/intermediary to the Service object.
#
class CasbinEnforcer:
    def __init__(self, claims_and_roles):
        global casbin_user_enforcer_map
        self.service_object = None
        user_id = claims_and_roles["tokens"][0]
        mfaEnabled = is_mfa_enabled(claims_and_roles)
        if user_id in casbin_user_enforcer_map:
            # Previously cached user-specific enforcers?
            #
            self.service_object = casbin_user_enforcer_map[user_id]
            self.service_object._mfaEnabled = mfaEnabled
        else:
            self.service_object = CasbinEnforcerService(user_id, mfaEnabled)
            # Cache the user-specific enforcer future calls
            #
            casbin_user_enforcer_map[user_id] = self.service_object

    def enforce(self, obj, act):
        return self.service_object.enforce(obj, act)

    def enforceAPI(self, lambdaEvent, apiMethodOverrideValue = ''):
        claims_and_roles = request_to_claims(lambdaEvent)

        if 'requestContext' in lambdaEvent and 'http' in lambdaEvent['requestContext']:
            # This should be a regular API Gateway call
            #

            http_method = lambdaEvent['requestContext']['http']['method']
            if apiMethodOverrideValue != '':
                http_method = apiMethodOverrideValue

            request_object = {
                "object__type": "api",
                "route__path": lambdaEvent['requestContext']['http']['path'] #"/" + event['requestContext']['http']['path'].split("/")[1]
            }

            return self.service_object.enforce(request_object, http_method)

        # elif 'lambdaCrossCall' in lambdaEvent:
        #     # This is a cross-call from another approved lambda.
        #     # Credentials logic in claims_and_roles should already account for this
        #     #

        #     #Auto approve if executing username in token
        #     if(len(claims_and_roles["tokens"]) > 0):
        #         return True
        #     else:
        #         return False
        else:
            #This is not a normal structered call so automatiacally fail
            return False

class CasbinEnforcerService:
    def __init__(self, user_id, mfa_enabled):
        # Handle user policy-specific caching and updates (globally)
        #
        global casbin_user_policy_map

        self._auth_table_name = ""
        self._user_roles_table_name = ""
        self._roles_table_name = ""
        self._user_id = user_id
        self._mfaEnabled = mfa_enabled
        self._dateTime_Cached = datetime.now()
        self._enforcer = None

        try:
            self._user_roles_table_name = os.environ["USER_ROLES_TABLE_NAME"]
            self._auth_table_name = os.environ["AUTH_TABLE_NAME"]
            self._roles_table_name = os.environ["ROLES_TABLE_NAME"]
        except KeyError as ex:
            logger.exception("Failed to find environment variables")
            raise Exception("Failed to initialize Casbin Enforcer as required environment variables are not defined")

        self._model_text = PERMISSION_CONSTRAINT_POLICY
        # Routines below have exception handling already covered
        #
        policy_text = None
        if self._user_id in casbin_user_policy_map:
            # Use previously cached user-specific policies?
            #
            policy_text = casbin_user_policy_map[self._user_id]
        else:
            policy_text = self._create_policy_text()
        self._create_casbin_enforcer(policy_text)

    def _read_policies_from_table(self, role_name):

        # See: UserRolesStorageTable in: infra/lib/nestedStacks/auth/constructs/*.ts (for groupPermissions)
        # The ABAC system will eventually have three methods to tie a user to a constraint.
        # First two are implemented:
        #	Direct constraint assignment (userPermissions)
        # 	Role-based approach (groupPermissions)
        # 	Attribute-based approach (attributePermissions)
        #
        page_iterator = paginator.paginate(
            TableName=self._auth_table_name,
            FilterExpression="""
                entityType = :constraintEntityType and
                (userPermissions[0].userId = :userId or groupPermissions[0].groupId = :roleName)
                """,
            PaginationConfig={
                "MaxItems": 1000,
                "PageSize": 1000,
                'StartingToken': None
            },
            ExpressionAttributeValues={
                ":constraintEntityType": {
                    "S": "constraint"
                },
                ":userId": {
                    "S": self._user_id
                },
                ":roleName": {
                    "S": role_name
                },
            }
        ).build_full_result()

        pageIteratorItems = []
        pageIteratorItems.extend(page_iterator['Items'])

        while 'NextToken' in page_iterator:
            nextToken = page_iterator['NextToken']
            page_iterator = paginator.paginate(
                TableName=self._auth_table_name,
                FilterExpression="""
                    entityType = :constraintEntityType and
                    (userPermissions[0].userId = :userId or groupPermissions[0].groupId = :roleName)
                    """,
                PaginationConfig={
                    "MaxItems": 1000,
                    "PageSize": 1000,
                    "StartingToken": nextToken
                },
                ExpressionAttributeValues={
                    ":constraintEntityType": {
                        "S": "constraint"
                    },
                    ":userId": {
                        "S": self._user_id
                    },
                    ":roleName": {
                        "S": role_name
                    },
                }
            ).build_full_result()
            pageIteratorItems.extend(page_iterator['Items'])

        items = []
        for item in pageIteratorItems:
            deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
            items.append(deserialized_document)

        return items

    def _read_current_user_roles_from_table(self):

        # See: UserRolesStorageTable in: infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts
        #
        page_iterator = paginator.paginate(
            TableName=self._user_roles_table_name,
            FilterExpression="userId = :userId",
            ExpressionAttributeValues={":userId": {"S": self._user_id}},
            PaginationConfig={
                "MaxItems": 1000,
                "PageSize": 1000,
                'StartingToken': None
            }
        ).build_full_result()

        pageIteratorItems = []
        pageIteratorItems.extend(page_iterator['Items'])

        while 'NextToken' in page_iterator:
            nextToken = page_iterator['NextToken']
            page_iterator = paginator.paginate(
                TableName=self._user_roles_table_name,
                FilterExpression="userId = :userId",
                ExpressionAttributeValues={":userId": {"S": self._user_id}},
                PaginationConfig={
                    "MaxItems": 1000,
                    "PageSize": 1000,
                    "StartingToken": nextToken
                }
            ).build_full_result()
            pageIteratorItems.extend(page_iterator['Items'])

        items = []
        for item in pageIteratorItems:
            deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
            items.append(deserialized_document)

        return items

    def _read_mfaNotRequired_roles_from_table(self):
        # Returns roles that align to the users self._mfaEnabled value
        # roleName is required for the relevant user roles check
        #

        filter_expression = (
            'attribute_exists(roleName) AND '
            '(attribute_not_exists(mfaRequired) OR mfaRequired = :mfa_value)'
        )

        # Expression attribute values
        expression_attr_values = {
            ':mfa_value': {"BOOL": False}
        }

        page_iterator = paginator.paginate(
            TableName=self._roles_table_name,
            FilterExpression=filter_expression,
            ExpressionAttributeValues=expression_attr_values,
            PaginationConfig={
                "MaxItems": 1000,
                "PageSize": 1000,
                'StartingToken': None
            }
        ).build_full_result()

        pageIteratorItems = []
        pageIteratorItems.extend(page_iterator['Items'])

        while 'NextToken' in page_iterator:
            nextToken = page_iterator['NextToken']
            page_iterator = paginator.paginate(
                TableName=self._roles_table_name,
                FilterExpression=filter_expression,
                ExpressionAttributeValues=expression_attr_values,
                PaginationConfig={
                    "MaxItems": 1000,
                    "PageSize": 1000,
                    'StartingToken': nextToken
                }
            ).build_full_result()
            pageIteratorItems.extend(page_iterator['Items'])

        items = []
        for item in pageIteratorItems:
            deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
            items.append(deserialized_document)

        return items

    def _generate_criteria_object_rules(self, policyCriteria):
        obj_rule = []
        for criterion in policyCriteria:
            if criterion["operator"] == "equals":
                obj_rule.append(
                    f"""regexMatch(r.obj.{criterion['field']}, '^{criterion['value']}$')"""
                )
            elif criterion["operator"] == "contains":
                obj_rule.append(
                    f"""regexMatch(r.obj.{criterion['field']}, '.*{criterion['value']}.*')"""
                )
            elif criterion["operator"] == "does_not_contain":
                obj_rule.append(
                    f"""!(regexMatch(r.obj.{criterion['field']}, '.*{criterion['value']}.*'))"""
                )
            elif criterion["operator"] == "starts_with":
                obj_rule.append(
                    f"""regexMatch(r.obj.{criterion['field']}, '^{criterion['value']}.*')"""
                )
            elif criterion["operator"] == "ends_with":
                obj_rule.append(
                    f"""regexMatch(r.obj.{criterion['field']}, '.*{criterion['value']}$')"""
                )
            elif criterion["operator"] == "is_one_of":
                obj_rule.append(
                    f"""'{criterion['value']}' in r.obj.{criterion['field']}"""
                )
            elif criterion["operator"] == "is_not_one_of":
                obj_rule.append(
                    f"""!'{criterion['value']}' in r.obj.{criterion['field']}"""
                )
        return obj_rule

    # Returns a guaranteed valid policy statement.
    # Note: a deny all policy_text value (POLICY_TEXT_DENY_ALL) is returned if policy_text cannot be determined
    #
    def _create_policy_text(self):
        policy_text = None
        # Improves resiliency for obtaining policy text in case of API failures
        # when fetching policies from DynamoDB. Helps to prevent cascading failures
        # in case of authorization failure. Authorization is critical.
        #
        for i in range(0, CASBIN_GET_POLICY_RETRY_ATTEMPTS):
            try:
                policy_text = self._create_policy_text_helper()
            except Exception as e:
                logger.exception(e)
                # Avoid assuming that policy_text was valid on failure
                #
                policy_text = None
            if not policy_text:
                logger.info(f"Failed to retrieve policy_text. Retry count {str(i)}.")
                time.sleep(CASBIN_GET_POLICY_RETRY_DELAY_SECONDS)
            else:
                # Successfully retrieved policy_text
                #
                break
        if not policy_text:
            # Do not authorize access when policy_text is not correctly obtained.
            # Avoid assuming that the last copy of policy_text is up-to-date on failure.
            # And refresh cached entry on next authorization request.
            #
            if self._user_id in casbin_user_policy_map:
                del casbin_user_policy_map[self._user_id]
            # Create a dummy enforcer to automatically deny authorization until policy_text
            # can be retrieved normally.
            #
            policy_text = POLICY_TEXT_DENY_ALL
            logger.info("Failed to determine policy_text after multiple attempts. Denying all access.")
        else:
            # Cache the user-specific policies for future calls (also done on enforce() calls)
            #
            casbin_user_policy_map[self._user_id] = policy_text
            self._dateTime_Cached = datetime.now()
        return policy_text
    def _create_policy_text_helper(self):
        # If the user is signed in with MFA, read all roles with actions and generate policy text
        # If not, get all related user roles with MFA attribute set to False and generate policy text
        #
        if self._mfaEnabled:
            user_roles_from_table = self._read_current_user_roles_from_table()
        else:
            relevant_NonMFA_roles = self._read_mfaNotRequired_roles_from_table()
            relevant_NonMFA_role_names = [role["roleName"] for role in relevant_NonMFA_roles]
            all_user_roles_from_table = self._read_current_user_roles_from_table()
            user_roles_from_table = [user_role for user_role in all_user_roles_from_table if user_role["roleName"] in relevant_NonMFA_role_names]

        policies_from_table_by_roles = []

        policy_text = ""
        new_line = "\n"

        # Append roles
        for user_role in user_roles_from_table:
            policies_from_table_by_roles.append(self._read_policies_from_table(user_role["roleName"]))
            policy_text = (
                f"{policy_text}{new_line if len(policy_text) > 0 else ''}"
                f"""g, user::{user_role["userId"]}, 'role::{user_role["roleName"]}'"""
            )

        # Append policies
        for policies_from_table_by_role in policies_from_table_by_roles:
            for policy in policies_from_table_by_role:

                if "criteriaAnd" not in policy:
                    policy["criteriaAnd"] = []

                #Backwards compatability - add criteria to criteriaAnd (field name change to make way for OR criteria)
                if "criteria" in policy:
                    policy["criteriaAnd"].append(policy["criteria"])

                # Get the explicit criteria for the object to be of the type mentioned in "objectType"
                obj_rule_ObjectType = self._generate_criteria_object_rules(
                    [{
                        "field": "object__type",
                        "operator": "equals",
                        "value": policy["objectType"]
                    }])

                #Generate object rules
                obj_rule_And = []
                obj_rule_Or = []
                if "criteriaAnd" in policy:
                    obj_rule_And = self._generate_criteria_object_rules(policy["criteriaAnd"])
                if "criteriaOr" in policy:
                    obj_rule_Or = self._generate_criteria_object_rules(policy["criteriaOr"])

                if "groupPermissions" in policy:
                    for group_permission in policy["groupPermissions"]:
                        if len(obj_rule_And) > 0:
                            policy_text = (
                                f"{policy_text}{new_line if len(policy_text) > 0 else ''}"
                                f"""p, 'role::{group_permission["groupId"]}', {obj_rule_ObjectType[0]} && {" && ".join(obj_rule_And)}, {group_permission["permission"]}, {group_permission["permissionType"] or 'allow'}"""
                            )
                        if len(obj_rule_Or) > 0:
                            policy_text = (
                                f"{policy_text}{new_line if len(policy_text) > 0 else ''}"
                                f"""p, 'role::{group_permission["groupId"]}', {obj_rule_ObjectType[0]} && ({" || ".join(obj_rule_Or)}), {group_permission["permission"]}, {group_permission["permissionType"] or 'allow'}"""
                            )

                if "userPermissions" in policy:
                    for user_permission in policy["userPermissions"]:
                        if len(obj_rule_And) > 0:
                            policy_text = (
                                f"{policy_text}{new_line if len(policy_text) > 0 else ''}"
                                f"""p, user::{user_permission["userId"]}, {obj_rule_ObjectType[0]} && {" && ".join(obj_rule_And)}, {user_permission["permission"]}, {user_permission["permissionType"] or 'allow'}"""
                            )
                        if len(obj_rule_Or) > 0:
                            policy_text = (
                                f"{policy_text}{new_line if len(policy_text) > 0 else ''}"
                                f"""p, user::{user_permission["userId"]}, {obj_rule_ObjectType[0]} && ({" || ".join(obj_rule_Or)}), {user_permission["permission"]}, {user_permission["permissionType"] or 'allow'}"""
                            )
        #logger.info(policy_text)
        return policy_text

    # Creates a Casbin enforcer object from the policy_text.
    # Note: if the policy_text is invalid, then this method will attempt to create a Casbin enforcer object with a
    # deny-all policy. If Casbin fails to initialize, then this method will 'skip' the internal Casbin enforcer
    # (None) and automatically deny all requests.
    #
    def _create_casbin_enforcer(self, policy_text):
        try:
            self._enforcer = self._create_casbin_enforcer_helper(policy_text)
        except Exception as e:
            logger.info("Casbin Enforcer policy_text is invalid. Denying all access.")
            logger.exception(e)
            try:
                self._enforcer = self._create_casbin_enforcer_helper(POLICY_TEXT_DENY_ALL)
            except Exception as inner_exception:
                logger.info("Failed to initialize Casbin Enforcer authorization library. Denying all access.")
                logger.exception(inner_exception)
                # Prevent direct Casbin API enforce() call failures via proxy wrapper check
                #
                self._enforcer = None

    def _create_casbin_enforcer_helper(self, policy_text):
        new_model = model.Model()
        new_model.load_model_from_text(self._model_text)
        new_string_adapter = string_adapter.StringAdapter(policy_text)
        _enforcer = FastEnforcer(model=new_model, adapter=new_string_adapter, enable_log=True)
        return _enforcer

    def enforce(self, obj, act):
        global CASBIN_REFRESH_POLICY_SECONDS
        global casbin_user_policy_map

        sub = f"user::{self._user_id}"

        # If the internal Casbin module is not functioning, then immediately deny all access
        #
        if self._enforcer is None:
            return False

        # Check if the Casbin policy cache is older than CASBIN_REFRESH_POLICY_SECONDS seconds; if so update cache.
        # Note: global variables update user roles if they changed in a timely fashion
        #
        if (datetime.now() - timedelta(seconds=CASBIN_REFRESH_POLICY_SECONDS)) > self._dateTime_Cached:
            logger.info("Casbin Policy Cache Expiration - Refreshing Policy")
            # Refresh cache. Alternatively, it's possible to use DynamoDB Streams to detect changes in DB against
            # a cache flag.
            #
            if self._user_id in casbin_user_policy_map:
                del casbin_user_policy_map[self._user_id]

            policy_text = self._create_policy_text()
            self._create_casbin_enforcer(policy_text)

            if POLICY_TEXT_DENY_ALL == policy_text:
                # Upon policy_text failure, have future calls re-instate the cache entry and enforcer
                # Avoid relying on stale cache as long-term fallback option
                #
                self._enforcer = None
                return False

        enhanced_object = PERMISSION_CONSTRAINT_FIELDS
        enhanced_object.update(obj)

        try:
            return self._enforcer.enforce(sub, enhanced_object, act)
        except AttributeDoesNotExist as er:
            logger.info("Enhancing object with unrelated attributes")
            enhanced_object.update({
                er.attr: ""
            })
            try:
                return self._enforcer.enforce(sub, enhanced_object, act)
            except Exception as e:
                logger.info("Enforcer logic failed - please check your policy text.")
                logger.exception(e)
                return False
        except Exception as e:
            logger.info("Enforcer logic failed - please check your policy text.")
            logger.exception(e)
            return False

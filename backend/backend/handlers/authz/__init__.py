# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import ast
import boto3
from botocore.config import Config
import os
import re
import threading
import time
import json
from boto3.dynamodb.types import TypeDeserializer
from casbin import FastEnforcer
from casbin import model
from casbin.effect import Effector, effect_to_bool
from casbin.persist.adapters import string_adapter
from casbin.util import util as casbin_util
from simpleeval import AttributeDoesNotExist
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_authorization, log_authorization_api
from handlers.auth import request_to_claims
from datetime import datetime, timedelta
from common.constants import (
    PERMISSION_CONSTRAINT_FIELDS,
    PERMISSION_CONSTRAINT_POLICY,
    ALWAYS_ALLOWED_OBJECT_KEYS,
    get_constraint_fields_for_object_type,
)
from locked_dict import locked_dict
from common.resourceNames import ResourceKeys, get_table_name

# Duration to refresh cache for next invocation - this can be tweaked for performance/consistency needs
#
CASBIN_REFRESH_POLICY_SECONDS = 60

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

# Optional role granted to an authenticated user who has NO role assignments at all.
# Gives baseline access (e.g. read-only) to identities that are not provisioned into the
# user-roles table, such as federated IdP logins. Empty string (the default) disables the
# behavior entirely, which is the pre-existing deny-by-default posture.
#
# Set from the CDK config value app.authProvider.authorizerOptions.defaultUserRoleName, which
# every Lambda receives as the DEFAULT_ROLE_NAME environment variable
# (infra/lib/helper/security.ts). The named role must exist in the roles table; if it does not,
# it is dropped with a logged error rather than passed to Casbin as a role with no policy.
DEFAULT_ROLE_NAME = os.environ.get("DEFAULT_ROLE_NAME", "").strip()

# Defines a boilerplate Deny-all policy for casbin enforcer - for cases where policy_text can't be determined
# (allowing existing enforcement call sites to continue to work without any additional changes)
#
POLICY_TEXT_DENY_ALL = "g,,\0,*,deny\np,,,*,deny"

# An object type name written into a policy line's opening type check as it is (see
# CasbinEnforcerService._object_type_rule). Every registered object type has this shape.
#
_PLAIN_OBJECT_TYPE_NAME = re.compile(r"[A-Za-z0-9_]+")

# Archived assets and deleted databases are stored under "<databaseId>#deleted". Authorization
# allows such a record only when it passes both on its stored id and on its database id
# (CasbinEnforcerService.enforce, _evaluated_database_id).
#
ARCHIVED_DATABASE_ID_SUFFIX = "#deleted"

# Tracks users and their policy_text (which could span multiple roles)
#
casbin_user_policy_map = {} if CASBIN_NO_DICTIONARY_LOCKING else locked_dict.LockedDict()

# Tracks users and their casbin enforcer
#
casbin_user_enforcer_map = {} if CASBIN_NO_DICTIONARY_LOCKING else locked_dict.LockedDict()

logger = safeLogger()

deserializer = TypeDeserializer()
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
_dynamodb_client = boto3.client("dynamodb", config=retry_config)


class ExpressionCachingEnforcer(FastEnforcer):
    """A ``FastEnforcer`` that compiles each distinct matcher expression once per instance.

    The policy model's matcher is ``g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act``.
    Because it contains ``eval()``, pycasbin cannot compile the matcher once per ``enforce()``:
    ``CoreEnforcer.enforce_ex`` (casbin 1.36.0, ``core_enforcer.py`` L453-L456) inlines each
    policy line's ``obj_rule`` into the matcher text (``util.replace_eval``) and calls
    ``self._get_expression(exp_with_rule, functions)`` for EVERY line on EVERY request.
    ``_get_expression`` (L538-L544) builds a new ``casbin.util.expression.SimpleEval``, whose
    constructor (``util/expression.py`` L34) runs ``ast.parse`` on the text. Parsing dominates the
    per-line cost, so a decision over N policy lines costs N parses, and a list endpoint that
    authorizes each item pays that again per item.

    This subclass overrides ``_get_expression`` and keys a per-INSTANCE dict on the full inlined
    expression text it receives -- the matcher with that line's ``obj_rule`` already substituted,
    so a policy line whose rule text changes is a different key and compiles afresh. Only the
    COMPILED EXPRESSION is cached, never a decision: every ``enforce()`` still evaluates every
    policy line it walks against the request (``ObjectTypeRoutingEnforcer`` below decides which
    lines those are). The cache is bounded by the number of distinct policy lines the instance
    holds.

    The cache MUST stay on the instance. ``enforce_ex`` rebinds ``functions["g"]`` on every call
    to a closure over THIS enforcer's role manager (L402-L409), and the compiled expression keeps
    a reference to that ``functions`` dict. A cache shared across enforcers would evaluate one
    user's role links for another. One ``CasbinEnforcerService`` owns one enforcer, so this cache
    is per user and is discarded with the service on the 60 s TTL / MFA-change invalidation in
    ``CasbinEnforcer.__init__``. On a hit the ``functions`` reference is refreshed so correctness
    never depends on pycasbin handing out the same dict object each call.

    Footprint: each cached expression keeps its parsed AST plus the last request's parameter dict,
    about 10.8 KiB per distinct policy line (a 130-line service measures ~1.8 MiB against ~0.4 MiB
    uncached, 4.4x), so a user's enforcer grows with the number of distinct lines their roles
    carry. ``casbin_user_enforcer_map`` refreshes an entry on the TTL but never evicts an idle
    user -- pre-existing behaviour, not introduced here -- so a warm container holds that footprint
    for every distinct user it has served.

    A compiled ``SimpleEval`` stores the request parameters on itself while it evaluates
    (``simpleeval`` ``self.names``), so a shared instance is not safe to evaluate from two threads
    at once. Handlers call ``enforce()`` from the request thread only, but as this is
    authorization code the instance serializes ``enforce_ex`` behind its own lock rather than
    relying on callers to keep it that way; with no contention the lock costs well under a
    microsecond per decision.
    """

    def __init__(self, model=None, adapter=None, enable_log=False, cache_key_order=None):
        # Instance attributes, assigned BEFORE the parent constructor so they exist for any
        # evaluation the parent might perform while loading. Never class-level: see docstring.
        self._expression_cache = {}
        self._enforce_lock = threading.RLock()
        super().__init__(model=model, adapter=adapter, enable_log=enable_log, cache_key_order=cache_key_order)

    def enforce_ex(self, *rvals):
        with self._enforce_lock:
            return super().enforce_ex(*rvals)

    def _get_expression(self, expr, functions=None):
        """Return the compiled expression for ``expr``, compiling it on first sight.

        Overrides the ``@staticmethod`` on ``CoreEnforcer``; the parent calls it as
        ``self._get_expression(...)`` so an instance method binds. ``expr`` is the FULL matcher
        text with the policy line's rule inlined, which is the only key that is both complete and
        stable -- a line index would alias two different rules whenever the policy is rebuilt.
        """
        expression = self._expression_cache.get(expr)
        if expression is None:
            expression = FastEnforcer._get_expression(expr, functions)
            self._expression_cache[expr] = expression
        elif functions is not None and expression.functions is not functions:
            # pycasbin 1.36.0 passes the enforcer's own function-map dict every call, so this
            # branch is defensive: it keeps ``g`` bound to the caller's role manager even if a
            # later release hands out a copy.
            expression.functions = functions
        return expression


# The pattern of an object-type gate on a literal type name: '^<name>\Z' with a name of letters,
# digits and underscores only, none of which is regex syntax, so `re.match` on it holds exactly when
# the request's object__type equals <name>.
_LITERAL_OBJECT_TYPE_GATE = re.compile(r"\^([A-Za-z0-9_]+)\\Z")


def _is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _literal_gate_object_type(tree):
    """The object type a compiled matcher expression is gated on, or None when it has no such gate.

    ``tree`` is the expression ``enforce_ex`` evaluates for one policy line. Returns ``T`` only for
    the shape ``g(r_sub, p_sub) and (<rule>) and ...`` whose ``<rule>`` is, or is an ``and`` that
    opens with, ``regexMatch(r_obj.object__type, '^T\\Z')`` on a literal ``T``. simpleeval evaluates
    an ``and`` left to right and stops at the first falsy operand, so for a request whose
    object__type is a string other than ``T`` that expression evaluates the role lookup and at most
    the gate, and returns ``False``.
    """
    if not (isinstance(tree, ast.BoolOp) and isinstance(tree.op, ast.And) and len(tree.values) >= 2):
        return None
    role_check, rule = tree.values[0], tree.values[1]
    if not (isinstance(role_check, ast.Call) and _is_name(role_check.func, "g") and not role_check.keywords
            and len(role_check.args) == 2 and _is_name(role_check.args[0], "r_sub")
            and _is_name(role_check.args[1], "p_sub")):
        return None
    if isinstance(rule, ast.BoolOp) and isinstance(rule.op, ast.And):
        rule = rule.values[0]
    if not (isinstance(rule, ast.Call) and _is_name(rule.func, "regexMatch") and not rule.keywords
            and len(rule.args) == 2):
        return None
    subject, pattern = rule.args
    if not (isinstance(subject, ast.Attribute) and subject.attr == "object__type"
            and _is_name(subject.value, "r_obj")):
        return None
    if not (isinstance(pattern, ast.Constant) and type(pattern.value) is str):
        return None
    match = _LITERAL_OBJECT_TYPE_GATE.fullmatch(pattern.value)
    return match.group(1) if match else None


class _PolicyRoutes:
    """The policy lines to walk per request object type, built from one loaded policy list."""

    def __init__(self, source, by_type, shared, request_size, subject_index, object_index):
        self.source = source
        self.source_length = len(source)
        self.by_type = by_type
        self.shared = shared
        self.request_size = request_size
        self.subject_index = subject_index
        self.object_index = object_index


class ObjectTypeRoutingEnforcer(ExpressionCachingEnforcer):
    """An ``ExpressionCachingEnforcer`` that walks, for each request, only the policy lines that can
    match the request's object type.

    Every line ``_create_policy_text_helper`` emits opens its rule with the object-type gate
    ``regexMatch(r.obj.object__type, '^<type>\\Z')``. For a request of another type such a line
    evaluates the role lookup ``g(r.sub, p.sub)`` and at most that gate, neither of which raises,
    and comes out ``False``: it adds only ``INDETERMINATE`` to the effect set. pycasbin's effectors
    decide from the ALLOW and DENY effects alone, and ``enforce_ex`` stops the walk only on a line
    that produced one, so leaving such lines out -- the others kept in their original order -- does
    not change the decision, the line that ends the walk, or the line that raises.

    When the policy loads, the matcher expression of each line is built as ``enforce_ex`` builds it
    (``util.escape_assertion`` and ``util.replace_eval``, then pycasbin's own ``_get_expression``
    rewrite and parse) and its syntax tree is read by ``_literal_gate_object_type``. A line with a
    gate on a literal type name is listed for that type only. Every other line is SHARED and listed,
    in its original position, for every type and for the types no line names: a line whose type
    name carries regex syntax (and may match several types), the deny-all line, a line whose rule
    does not parse or whose field count is wrong. A deny line is therefore never dropped for a type
    it can match, and a request whose type no line names walks only the shared lines -- none in a
    generated policy -- and gets the effector's verdict on a walk in which no line matched, which
    under the VAMS policy effect is a deny.

    A request is routed only in the shape the service sends it -- a ``str`` subject and a plain
    ``dict`` object with a ``str`` ``object__type`` -- and only while the policy is the one the
    routes were built from; anything else walks every line. A policy list that was replaced or
    changed length, or that was edited through a policy method (``_POLICY_EDITS``) since it loaded,
    is walked in full until the next ``load_policy()``. Routing hands ``enforce_ex`` the routed
    list for the duration of one call, under the instance lock that already serializes
    ``enforce_ex``, and restores the full list afterwards. The role manager, matcher, effector and
    expression cache are the instance's own and are shared by every route, so each distinct line is
    still compiled once.
    """

    def __init__(self, model=None, adapter=None, enable_log=False, cache_key_order=None):
        # Assigned before the parent constructor, whose load_policy() builds the routes.
        self._policy_routes = None
        super().__init__(model=model, adapter=adapter, enable_log=enable_log, cache_key_order=cache_key_order)

    def load_policy(self):
        with self._enforce_lock:
            self._policy_routes = None
            super().load_policy()
            self._policy_routes = self._build_policy_routes()

    def enforce_ex(self, *rvals):
        with self._enforce_lock:
            route = self._route(rvals)
            if route is None:
                return super().enforce_ex(*rvals)
            policy_assertion, routed_policy = route
            if not routed_policy:
                # No line can match this object type: the full walk adds only INDETERMINATE.
                return effect_to_bool(self.eft.final_effect({Effector.INDETERMINATE})), []
            full_policy = policy_assertion.policy
            policy_assertion.policy = routed_policy
            try:
                return super().enforce_ex(*rvals)
            finally:
                policy_assertion.policy = full_policy

    def _route(self, rvals):
        """``(policy assertion, lines to walk)`` for this request, or None to walk every line."""
        routes = self._policy_routes
        if routes is None or not self.enabled or len(rvals) != routes.request_size:
            return None
        policy_assertion = (self.model["p"] or {}).get("p")
        if (policy_assertion is None or policy_assertion.policy is not routes.source
                or len(routes.source) != routes.source_length):
            return None
        subject, obj = rvals[routes.subject_index], rvals[routes.object_index]
        if type(subject) is not str or type(obj) is not dict:
            return None
        object_type = obj.get("object__type")
        if type(object_type) is not str:
            return None
        return policy_assertion, routes.by_type.get(object_type, routes.shared)

    def _build_policy_routes(self):
        try:
            matcher = self.model["m"]["m"].value
            policy_assertion = self.model["p"]["p"]
            request_tokens = self.model["r"]["r"].tokens
        except (KeyError, TypeError):
            return None
        rule_names = casbin_util.get_eval_value(matcher)
        if (len(rule_names) != 1 or "g" not in self.rm_map
                or "r_sub" not in request_tokens or "r_obj" not in request_tokens):
            return None

        policy = policy_assertion.policy
        line_types = [self._line_object_type(matcher, rule_names, policy_assertion.tokens, line) for line in policy]
        named_types = {line_type for line_type in line_types if line_type is not None}
        if not named_types:
            return None
        by_type = {
            object_type: [line for line, line_type in zip(policy, line_types)
                          if line_type is None or line_type == object_type]
            for object_type in named_types
        }
        shared = [line for line, line_type in zip(policy, line_types) if line_type is None]
        return _PolicyRoutes(policy, by_type, shared, len(request_tokens),
                             request_tokens.index("r_sub"), request_tokens.index("r_obj"))

    @staticmethod
    def _line_object_type(matcher, rule_names, policy_tokens, line):
        """The literal object type one policy line is gated on, or None when it must be shared."""
        if len(line) != len(policy_tokens):
            return None
        parameters = dict(zip(policy_tokens, line))
        try:
            rules = [casbin_util.escape_assertion(parameters[name]) for name in rule_names]
            expression = FastEnforcer._get_expression(casbin_util.replace_eval(matcher, rules))
        except Exception:
            return None
        return _literal_gate_object_type(getattr(expression, "ast_parsed_value", None))


# The pycasbin methods every policy edit goes through. An edit can keep the policy list and its
# length (``update_policy`` replaces a line in place), so each one drops the routes and the policy
# is walked in full until the next ``load_policy()``.
_POLICY_EDITS = (
    "_add_policy", "_add_policies", "_update_policy", "_update_policies", "_update_filtered_policies",
    "_remove_policy", "_remove_policies", "_remove_filtered_policy",
    "_remove_filtered_policy_returns_effects", "clear_policy",
)


def _drops_policy_routes(name):
    def edit(self, *args, **kwargs):
        self._policy_routes = None
        return getattr(super(ObjectTypeRoutingEnforcer, self), name)(*args, **kwargs)
    edit.__name__ = name
    return edit


for _edit_name in _POLICY_EDITS:
    if hasattr(ExpressionCachingEnforcer, _edit_name):
        setattr(ObjectTypeRoutingEnforcer, _edit_name, _drops_policy_routes(_edit_name))


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
        global casbin_user_policy_map
        self.service_object = None
        self.claims_and_roles = claims_and_roles  # Store for audit logging
        user_id = claims_and_roles["tokens"][0]
        mfaEnabled = is_mfa_enabled(claims_and_roles)
        
        if user_id in casbin_user_enforcer_map:
            # Previously cached user-specific enforcers - validate cache freshness
            cached_service = casbin_user_enforcer_map[user_id]
            
            # Check if cache is stale or MFA state changed
            cache_age = datetime.now() - cached_service._dateTime_Cached
            mfa_changed = cached_service._mfaEnabled != mfaEnabled
            
            if cache_age.total_seconds() > CASBIN_REFRESH_POLICY_SECONDS or mfa_changed:
                # Cache is stale or MFA changed - invalidate and recreate
                logger.info(f"Invalidating enforcer cache for user {user_id}. Age: {cache_age.total_seconds():.1f}s, MFA changed: {mfa_changed}")
                del casbin_user_enforcer_map[user_id]
                if user_id in casbin_user_policy_map:
                    del casbin_user_policy_map[user_id]
                # Fall through to create new enforcer below
            else:
                # Cache is fresh - reuse existing enforcer
                logger.debug(f"Reusing cached enforcer for user {user_id}. Age: {cache_age.total_seconds():.1f}s")
                self.service_object = cached_service
                return
        
        # Create new enforcer (either first time or after cache invalidation)
        self.service_object = CasbinEnforcerService(user_id, mfaEnabled)
        # Cache the user-specific enforcer for future calls
        casbin_user_enforcer_map[user_id] = self.service_object

    def enforce(self, obj, act):
        result = self.service_object.enforce(obj, act)
        
        #Send audit log on a data check if it fails ONLY, for performance reasons
        if result == False:
            # AUDIT LOG: Log authorization result with full obj details
            try:
                audit_data = {
                    "action": act,
                    "obj": obj  # Pass full obj with all details
                }
                log_authorization(self.claims_and_roles, result, audit_data)
            except Exception as audit_error:
                logger.exception(f"Failed to log failed authorization audit: {audit_error}")
        
        return result

    def enforceAPI(self, lambdaEvent, apiMethodOverrideValue = ''):
        """Enforce API authorization with audit logging"""
        claims_and_roles = request_to_claims(lambdaEvent)

        if 'lambdaCrossCall' in lambdaEvent:
            # This is a cross-call from another approved lambda.
            # Credentials logic in claims_and_roles should already account for this
            #

            #Auto approve if executing username in token
            if(len(claims_and_roles["tokens"]) > 0):
                return True
            else:
                return False
        elif 'requestContext' in lambdaEvent and 'http' in lambdaEvent['requestContext']:
            # This should be a regular API Gateway call
            #

            http_method = lambdaEvent['requestContext']['http']['method']
            if apiMethodOverrideValue != '':
                http_method = apiMethodOverrideValue

            request_object = {
                "object__type": "api",
                "route__path": lambdaEvent['requestContext']['http']['path'] #"/" + event['requestContext']['http']['path'].split("/")[1]
            }

            result = self.service_object.enforce(request_object, http_method)
            
            # AUDIT LOG: Log API authorization result with full request_object
            try:
                audit_data = {
                    "action": http_method,
                    "obj": request_object  # Pass full request_object with all details
                }
                log_authorization_api(lambdaEvent, result, audit_data)
            except Exception as audit_error:
                logger.exception(f"Failed to log API authorization audit: {audit_error}")
            
            return result
        else:
            #This is not a normal structered call so automatiacally fail
            return False

class CasbinEnforcerService:
    def __init__(self, user_id, mfa_enabled):
        # Handle user policy-specific caching and updates (globally)
        #
        global casbin_user_policy_map

        self._user_roles_table_name = ""
        self._roles_table_name = ""
        self._user_id = user_id
        self._mfaEnabled = mfa_enabled
        self._dateTime_Cached = datetime.now()
        self._enforcer = None

        try:
            self._user_roles_table_name = get_table_name(ResourceKeys.USER_ROLES_STORAGE_TABLE)
            self._roles_table_name = get_table_name(ResourceKeys.ROLES_STORAGE_TABLE)
            self._constraints_table_name = get_table_name(ResourceKeys.CONSTRAINTS_STORAGE_TABLE)
        except Exception as ex:
            logger.exception("Failed to resolve resource names")
            raise Exception("Failed to initialize Casbin Enforcer as required resource names could not be resolved")

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

    def _read_policies_batch_optimized(self, role_names):
        """Optimized batch read using GSI queries on denormalized ConstraintsStorageTable
        
        Args:
            role_names: List of role names to fetch policies for
            
        Returns:
            List of all policies for the given roles (deduplicated by base constraintId)
        """
        all_constraints = {}  # Use dict for deduplication by base constraintId
        
        # Query GroupPermissionsIndex for each role with pagination
        for role_name in role_names:
            query_kwargs = {
                'TableName': self._constraints_table_name,
                'IndexName': 'GroupPermissionsIndex',
                'KeyConditionExpression': 'groupId = :groupId',
                'ExpressionAttributeValues': {':groupId': {'S': role_name}}
            }
            
            # Paginate through all results
            while True:
                response = _dynamodb_client.query(**query_kwargs)
                
                for item in response.get('Items', []):
                    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                    
                    # Extract base constraintId (remove #group#{groupId} or #user#{userId} suffix)
                    full_constraint_id = deserialized['constraintId']
                    base_constraint_id = full_constraint_id.split('#group#')[0].split('#user#')[0]
                    
                    # Parse JSON strings back to objects
                    deserialized['groupPermissions'] = self._parse_json_field(deserialized.get('groupPermissions'), [])
                    deserialized['userPermissions'] = self._parse_json_field(deserialized.get('userPermissions'), [])
                    deserialized['criteriaAnd'] = self._parse_json_field(deserialized.get('criteriaAnd'), [])
                    deserialized['criteriaOr'] = self._parse_json_field(deserialized.get('criteriaOr'), [])
                    
                    # Store by base constraintId to deduplicate (same constraint may appear for multiple groups)
                    all_constraints[base_constraint_id] = deserialized
                
                # Check if there are more results
                if 'LastEvaluatedKey' not in response:
                    break
                query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        
        # Query UserPermissionsIndex for direct user permissions with pagination
        query_kwargs = {
            'TableName': self._constraints_table_name,
            'IndexName': 'UserPermissionsIndex',
            'KeyConditionExpression': 'userId = :userId',
            'ExpressionAttributeValues': {':userId': {'S': self._user_id}}
        }
        
        # Paginate through all results
        while True:
            response = _dynamodb_client.query(**query_kwargs)
            
            for item in response.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                
                # Extract base constraintId (remove #group#{groupId} or #user#{userId} suffix)
                full_constraint_id = deserialized['constraintId']
                base_constraint_id = full_constraint_id.split('#group#')[0].split('#user#')[0]
                
                # Parse JSON strings back to objects
                deserialized['groupPermissions'] = self._parse_json_field(deserialized.get('groupPermissions'), [])
                deserialized['userPermissions'] = self._parse_json_field(deserialized.get('userPermissions'), [])
                deserialized['criteriaAnd'] = self._parse_json_field(deserialized.get('criteriaAnd'), [])
                deserialized['criteriaOr'] = self._parse_json_field(deserialized.get('criteriaOr'), [])
                
                # Store by base constraintId to deduplicate
                all_constraints[base_constraint_id] = deserialized
            
            # Check if there are more results
            if 'LastEvaluatedKey' not in response:
                break
            query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        
        #logger.info(f"Retrieved {len(all_constraints)} unique constraints from denormalized table")
        
        return list(all_constraints.values())
    
    def _parse_json_field(self, value, default=[]):
        """Helper method to parse JSON string fields safely
        
        Args:
            value: The value to parse (can be string, list, or None)
            default: Default value to return if parsing fails
            
        Returns:
            Parsed value or default
        """
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return default
        elif isinstance(value, list):
            return value
        return default

    def _read_current_user_roles_from_table(self):
        """Every role assignment held by this user.

        userId is the partition key of the user-roles table (UserRolesStorageTable in
        infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts), so the assignments are
        read with a Query on that one partition. A Scan with a userId filter reads every row
        in the table on each policy build, and that cost grows with the total user count of
        the deployment rather than with the roles the caller holds.

        Paged on the presence of LastEvaluatedKey, matching _read_policies_batch_optimized.
        """
        query_kwargs = {
            "TableName": self._user_roles_table_name,
            "KeyConditionExpression": "userId = :userId",
            "ExpressionAttributeValues": {":userId": {"S": self._user_id}},
        }

        items = []
        while True:
            response = _dynamodb_client.query(**query_kwargs)

            for item in response.get("Items", []):
                items.append({k: deserializer.deserialize(v) for k, v in item.items()})

            if "LastEvaluatedKey" not in response:
                return items
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    @staticmethod
    def _role_is_mfa_not_required(role):
        """Whether a session that did not present MFA may use this role record.

        One answer for both places that ask: the assigned-role filter below and the
        default-role check in _resolve_default_role. Asking it in two spellings lets them
        disagree on a stored number, because `Decimal("0") == False` is true while
        `Decimal("0") is False` is not.

        The verdict is the one DynamoDB gives the filter
        `attribute_not_exists(mfaRequired) OR mfaRequired = :false` with `:false` typed BOOL:
        `=` compares the attribute TYPE as well as the value, so only an absent attribute or a
        stored boolean false satisfies it; BOOL true, NULL, a number and a string all fail the
        comparison and keep the role out of a non-MFA session.

        Two details carry that verdict across deserialization:

        * Presence is tested on the KEY. A stored JSON null (`{"NULL": true}`) deserializes to
          Python None exactly as an absent attribute does, so reading only the value would read
          a null as "not required" and activate the role.
        * `is False` rather than `== False`, so a stored number is excluded.
        """
        if "mfaRequired" not in role:
            return True
        return role["mfaRequired"] is False

    def _read_mfaNotRequired_roles_from_table(self, role_names):
        """Those of the named roles that a session without MFA may use.

        roleName is the partition key of the roles table, so each name is read by key, through
        the same helper the default-role check uses. Deciding about the handful of roles one
        caller holds by scanning the roles table with an mfaRequired filter reads every role in
        the deployment on every non-MFA policy build.

        A name with no role record is omitted: the caller holds an assignment to a role that
        does not exist. A failed read is not an omission: it propagates, so
        _create_policy_text retries and ultimately denies rather than building a policy that
        quietly drops a role the caller holds.
        """
        items = []
        for role_name in dict.fromkeys(role_names):
            role = self._read_role_from_table(role_name)
            if role is None or "roleName" not in role:
                continue

            if self._role_is_mfa_not_required(role):
                items.append(role)

        return items

    @staticmethod
    def _escape_rule_value(value):
        """Write a constraint value as the body of the Casbin obj_rule string literal it is
        interpolated into: every character as its Python source escape.

        Every operator wraps the value inside a single-quoted string literal (either a
        regex pattern argument to regexMatch(...) or the left side of an `in` test). The matcher
        expression is compiled from that text by simpleeval, and Python's parser turns each escape
        back into exactly the character it stands for, so the string Casbin compares -- and the
        pattern `regexMatch` compiles -- is the value as stored; only the source representation
        differs.

        Spelling out EVERY character, rather than escaping a chosen few, keeps the value out of
        reach of everything that reads or rewrites the rule text before it is compiled. The literal
        body is nothing but '\\x' / '\\u' / '\\U' escapes and lower-case hex digits, so:

        * it cannot terminate its string literal and inject additional expression syntax -- a
          quote or a backslash in the value is one more escape;
        * Casbin's policy reader finds nothing structural in it. `StringAdapter` splits the policy
          text on newlines, and `load_policy_line` splits each line on commas at bracket depth 0
          while tracking '()' and '[]' as depth, neither of them honouring quotes. A line whose
          field count does not match the policy definition makes the enforcer fail as a whole, so
          one such value would deny every check for every user holding the role;
        * pycasbin's rewrites of the rule text before it compiles a line find nothing to rewrite:
          `util.escape_assertion` turns every 'p.' / 'p<digits>.' / 'r.' at a word boundary into
          'p_' / 'r_', and `CoreEnforcer._get_expression` turns every '&&' / '||' / '!' into
          'and' / 'or' / 'not', string literals included. That holds where the value meets the
          operator's own pattern text too ('^', '.*', '(?s:.*)', '\\Z'), since an escape ends in
          a hex digit.

        Takes ONE value: a list-valued criterion is expanded by the caller into a clause per
        element, so each element is escaped separately and reaches its own string literal.

        Note: this is defense-in-depth on top of the REGEX validator applied at constraint
        write time (see models/roleConstraints.py). The regex operators legitimately treat
        the value as a regular expression (e.g. ".*" is allowed), but it must remain
        contained within the quoted regexMatch argument and cannot break out into the
        surrounding expression.
        """
        escaped = []
        for character in str(value):
            code = ord(character)
            if code <= 0xFF:
                escaped.append("\\x%02x" % code)
            elif code <= 0xFFFF:
                escaped.append("\\u%04x" % code)
            else:
                escaped.append("\\U%08x" % code)
        return "".join(escaped)

    def _generate_criteria_object_rules(self, policyCriteria, object_type=None):
        obj_rule = []
        valid_fields = get_constraint_fields_for_object_type(object_type) if object_type else None
        for criterion in policyCriteria:
            # Skip deprecated or unknown fields that are not in PERMISSION_CONSTRAINT_FIELDS
            if criterion['field'] not in PERMISSION_CONSTRAINT_FIELDS:
                logger.info(f"Skipping deprecated/unknown constraint field: {criterion['field']}")
                continue

            # Skip fields that are out of scope for this object type (defense-in-depth
            # for constraints stored before the field matrix was enforced).
            if valid_fields is not None and criterion['field'] not in valid_fields:
                logger.info(f"Skipping out-of-matrix field for objectType {object_type}: {criterion['field']}")
                continue

            field = criterion['field']
            operator = criterion['operator']

            # A criterion value is either a single value or a list of alternatives. Each element
            # gets its own comparison clause and its own escaping, so the elements stay separate
            # values instead of collapsing into one stringified container that matches nothing.
            # Escaping writes each value as source escapes, so it neither breaks out of its string
            # literal into the eval()'d matcher nor is altered by the rewrites Casbin applies to the
            # rule text before compiling it (see _escape_rule_value).
            raw_value = criterion['value']
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            values = [self._escape_rule_value(entry) for entry in values]

            # Two newline properties of Python's regex dialect decide the anchors and wildcards used
            # below. `regexMatch` is `re.match` (casbin.util.regex_match), so:
            #
            #   '$' also matches immediately BEFORE a trailing newline, so '^value$' matches both
            #   "value" and "value\n". On an allow rule that grants a second, distinct stored value;
            #   '\Z' is the true end of string. Names reach here through OBJECT_NAME, whose charset
            #   includes \s — so "value\n" is a storable name, not a hypothetical.
            #
            #   '.' does NOT cross a newline, so '.*value.*' fails to see "pre\nvalue". On a
            #   containment DENY that is a bypass: the inner match returns False, the negation
            #   returns True, and access is granted. The wildcards therefore use the SCOPED group
            #   '(?s:.*)' rather than a leading '(?s)': a global flag would apply to the caller's
            #   value too, so a value containing '.' would start spanning lines and the rule would
            #   match more than it says.
            #
            # The two treatments are per-operator on purpose. '\Z' narrows, so it belongs on the
            # operators that terminate a match; '(?s:.*)' widens the machine-generated wildcards, so
            # it belongs only where under-matching is the hazard. Applying '(?s)' to `equals` would
            # widen an allow rule instead of tightening it.
            #
            # Clauses stay None for an operator this generator does not know, so an unrecognized
            # operator is skipped rather than emitting a rule.
            clauses = None
            negated = False
            if operator == "equals":
                clauses = [f"""regexMatch(r.obj.{field}, '^{value}\\\\Z')""" for value in values]
            elif operator == "contains":
                clauses = [
                    f"""regexMatch(r.obj.{field}, '(?s:.*){value}(?s:.*)')""" for value in values
                ]
            elif operator == "does_not_contain":
                clauses = [
                    f"""regexMatch(r.obj.{field}, '(?s:.*){value}(?s:.*)')""" for value in values
                ]
                negated = True
            elif operator == "starts_with":
                # `re.match` already anchors at offset 0 and a trailing '.*' can
                # always match empty, so '^value.*' is boolean-identical to '^value' — there is no
                # forward over-match to close, and nothing a newline-crossing wildcard would alter.
                clauses = [f"""regexMatch(r.obj.{field}, '^{value}.*')""" for value in values]
            elif operator == "ends_with":
                # The leading wildcard must span newlines (an under-matching
                # deny is a bypass) and the terminator must be '\Z' rather than '$'.
                clauses = [
                    f"""regexMatch(r.obj.{field}, '(?s:.*){value}\\\\Z')""" for value in values
                ]
            elif operator == "is_one_of":
                clauses = [f"""'{value}' in r.obj.{field}""" for value in values]
            elif operator == "is_not_one_of":
                clauses = [f"""'{value}' in r.obj.{field}""" for value in values]
                negated = True

            if clauses is None:
                continue

            # The alternatives are OR-joined, so a multi-value criterion matches ANY of its values.
            # A negating operator wraps that same group in ONE '!', which is its complement: the
            # rule holds only when NONE of the values match, so no value can be slipped past by
            # pairing it with another. An empty value list contributes no alternative, and an
            # OR over no alternatives is False — never-match on the positive operators, and
            # vacuously true under the negation.
            #
            # The group is parenthesised unconditionally, a single clause included. Parentheses
            # around one complete boolean sub-expression cannot change what the matcher decides,
            # and they make every emitted clause a bracketed span for Casbin's policy reader,
            # which counts '()' as depth -- so a separator character that reaches the clause text
            # can no longer add a field to the policy line and take the whole policy down with it.
            # That is the structural half of the containment; the per-value escaping in
            # _escape_rule_value is the other half, and the one that covers what parentheses
            # cannot (a line terminator, an unbalanced bracket).
            group = " || ".join(clauses) if clauses else "False"
            if negated:
                obj_rule.append(f"!({group})")
            else:
                obj_rule.append(f"({group})")
        return obj_rule

    def _object_type_rule(self, object_type):
        """The clause every policy line opens with: the request object's type is ``object_type``.

        A type name of ASCII letters, digits and '_' only -- the shape of every registered object
        type -- is written as it is, giving the exact type check
        ``(regexMatch(r.obj.object__type, '^asset\\Z'))``: no such character can take part in the
        policy reader's structure or in the rewrites Casbin applies to the rule text. Any other
        stored type string (a regex such as ``asset|database`` on an older constraint) is an
        ``equals`` criterion like any other and is escaped by the criteria generator.
        """
        if isinstance(object_type, str) and _PLAIN_OBJECT_TYPE_NAME.fullmatch(object_type):
            return f"""(regexMatch(r.obj.object__type, '^{object_type}\\\\Z'))"""
        return self._generate_criteria_object_rules(
            [{"field": "object__type", "operator": "equals", "value": object_type}]
        )[0]

    # Returns a guaranteed valid policy statement.
    # Note: a deny all policy_text value (POLICY_TEXT_DENY_ALL) is returned if policy_text cannot be determined
    #
    def _create_policy_text(self):
        policy_text = None
        # Improves resiliency for obtaining policy text in case of API failures
        # when fetching policies from DynamoDB. Helps to prevent cascading failures
        # in case of authorization failure. Authorization is critical.
        #
        # Only a failed read (None) is retryable. An empty string is the builder's answer for a
        # caller with no effective role assignments: retrying re-runs the same reads and cannot
        # change it, so treating it as a failure would cost an unprovisioned identity a full
        # retry cycle - both table reads per attempt plus a sleep - on every cache miss.
        #
        for i in range(0, CASBIN_GET_POLICY_RETRY_ATTEMPTS):
            try:
                policy_text = self._create_policy_text_helper()
            except Exception as e:
                logger.exception(e)
                # Avoid assuming that policy_text was valid on failure
                #
                policy_text = None
            if policy_text is None:
                logger.info(f"Failed to retrieve policy_text. Retry count {str(i)}.")
                # No delay after the final attempt: nothing follows it to wait for.
                #
                if i < CASBIN_GET_POLICY_RETRY_ATTEMPTS - 1:
                    time.sleep(CASBIN_GET_POLICY_RETRY_DELAY_SECONDS) # nosemgrep: arbitrary-sleep
            else:
                # Successfully retrieved policy_text
                #
                break
        if policy_text is None:
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
            if not policy_text:
                # No role grants and no permissions: every check denies. Recorded as an
                # explicit deny-all so it caches like any other result.
                #
                logger.info("No effective role assignments for this user. Denying all access.")
                policy_text = POLICY_TEXT_DENY_ALL
            # Cache the user-specific policies for future calls (also done on enforce() calls)
            #
            casbin_user_policy_map[self._user_id] = policy_text
            self._dateTime_Cached = datetime.now()
        return policy_text
    def _read_role_from_table(self, role_name):
        """One role record by name, or None when no such role exists.

        A read failure propagates rather than becoming None: both callers treat None as "no
        such role" and drop the role, so swallowing the error here would let a throttled read
        look like a role that does not exist and silently change the policy that gets built.
        Propagating puts it on the _create_policy_text retry path, which denies if it persists.
        """
        response = _dynamodb_client.get_item(
            TableName=self._roles_table_name,
            Key={"roleName": {"S": role_name}},
        )

        item = response.get("Item")
        if not item:
            return None
        return {k: deserializer.deserialize(v) for k, v in item.items()}

    def _resolve_default_role(self, assigned_role_count):
        """The baseline role to grant this session, or None.

        Returns None — leaving the caller with a deny-by-default policy — in every case except a
        configured, existing, session-appropriate role held by a user with no assignments:

        * DEFAULT_ROLE_NAME unset: the feature is off.
        * The user already has role assignments: their own roles decide, filtered as usual. A
          default is never added alongside them, so this can only ever widen access for an
          unprovisioned identity.
        * The named role does not exist: dropped with a logged ERROR. Passing it through would put
          a `g, user::X, 'role::Y'` grouping into the Casbin policy with no matching policy lines,
          which is a silently misconfigured deployment rather than a working default.
        * The named role requires MFA and this session has none: dropped, mirroring the filter
          applied to assigned roles.
        """
        if not DEFAULT_ROLE_NAME:
            return None
        if assigned_role_count > 0:
            return None

        role = self._read_role_from_table(DEFAULT_ROLE_NAME)
        if role is None:
            logger.error(
                f"Configured default user role '{DEFAULT_ROLE_NAME}' does not exist in the roles "
                "table; no default role applied. Create the role (with constraints) or clear "
                "app.authProvider.authorizerOptions.defaultUserRoleName."
            )
            return None

        # The same verdict the assigned roles get, from the same predicate.
        if not self._mfaEnabled and not self._role_is_mfa_not_required(role):
            logger.warning(
                f"Default user role '{DEFAULT_ROLE_NAME}' requires MFA and this session has none; "
                "no default role applied."
            )
            return None

        logger.info(
            f"User has no assigned roles; applying default role '{DEFAULT_ROLE_NAME}'"
        )
        return {"userId": self._user_id, "roleName": DEFAULT_ROLE_NAME}

    def _create_policy_text_helper(self):
        # If the user is signed in with MFA, read all roles with actions and generate policy text
        # If not, get all related user roles with MFA attribute set to False and generate policy text
        #
        if self._mfaEnabled:
            all_user_roles_from_table = self._read_current_user_roles_from_table()
            user_roles_from_table = all_user_roles_from_table
        else:
            all_user_roles_from_table = self._read_current_user_roles_from_table()
            relevant_NonMFA_roles = self._read_mfaNotRequired_roles_from_table(
                [user_role["roleName"] for user_role in all_user_roles_from_table]
            )
            relevant_NonMFA_role_names = [role["roleName"] for role in relevant_NonMFA_roles]
            user_roles_from_table = [user_role for user_role in all_user_roles_from_table if user_role["roleName"] in relevant_NonMFA_role_names]

        # The optional baseline role applies only to a user with NO role assignments at all.
        # This is decided from the UNFILTERED assignment list on purpose: a user whose roles were
        # all removed by the MFA filter above IS provisioned, and handing them baseline access in a
        # non-MFA session would defeat the mfaRequired flag on the roles they actually hold.
        default_role = self._resolve_default_role(len(all_user_roles_from_table))
        if default_role is not None:
            user_roles_from_table = user_roles_from_table + [default_role]

        policies_from_table_by_roles = []

        policy_text = ""
        new_line = "\n"

        # Append roles
        for user_role in user_roles_from_table:
            policy_text = (
                f"{policy_text}{new_line if len(policy_text) > 0 else ''}"
                f"""g, user::{user_role["userId"]}, 'role::{user_role["roleName"]}'"""
            )
        
        # Use optimized batch read for all roles at once
        role_names = [user_role["roleName"] for user_role in user_roles_from_table]
        #logger.info(f"Fetching policies for roles: {role_names}")
        all_policies = self._read_policies_batch_optimized(role_names)
        #logger.info(f"Retrieved {len(all_policies)} policies from constraints table")

        # Append policies
        for policy in all_policies:

                if "criteriaAnd" not in policy:
                    policy["criteriaAnd"] = []

                #Backwards compatability - add criteria to criteriaAnd (field name change to make way for OR criteria)
                if "criteria" in policy:
                    policy["criteriaAnd"].extend(policy["criteria"])

                # Get the explicit criteria for the object to be of the type mentioned in "objectType"
                obj_rule_ObjectType = self._object_type_rule(policy["objectType"])

                #Generate object rules
                obj_rule_And = []
                obj_rule_Or = []
                if "criteriaAnd" in policy:
                    obj_rule_And = self._generate_criteria_object_rules(
                        policy["criteriaAnd"], object_type=policy.get("objectType")
                    )
                if "criteriaOr" in policy:
                    obj_rule_Or = self._generate_criteria_object_rules(
                        policy["criteriaOr"], object_type=policy.get("objectType")
                    )

                # Combine AND and OR criteria into a single rule expression:
                # all AND criteria must be true AND (when present) at least one
                # OR criterion must be true. Emitting one combined policy line
                # (instead of separate AND/OR lines, which Casbin's
                # some(where allow) effect would treat as alternatives) both
                # enforces the combined semantics and halves the rules the
                # matcher evaluates per request.
                rule_parts = [obj_rule_ObjectType]
                rule_parts.extend(obj_rule_And)
                if len(obj_rule_Or) > 0:
                    rule_parts.append(f"({' || '.join(obj_rule_Or)})")
                if len(obj_rule_And) == 0 and len(obj_rule_Or) == 0:
                    # No criteria at all: skip emitting a rule for this policy
                    continue
                combined_obj_rule = " && ".join(rule_parts)

                if "groupPermissions" in policy:
                    for group_permission in policy["groupPermissions"]:
                        policy_text = (
                            f"{policy_text}{new_line if len(policy_text) > 0 else ''}"
                            f"""p, 'role::{group_permission["groupId"]}', {combined_obj_rule}, {group_permission["permission"]}, {group_permission["permissionType"] or 'allow'}"""
                        )

                if "userPermissions" in policy:
                    for user_permission in policy["userPermissions"]:
                        policy_text = (
                            f"{policy_text}{new_line if len(policy_text) > 0 else ''}"
                            f"""p, user::{user_permission["userId"]}, {combined_obj_rule}, {user_permission["permission"]}, {user_permission["permissionType"] or 'allow'}"""
                        )
        
        #logger.info(f"Generated policy_text with {len(policy_text)} characters")
        if len(policy_text) < 100:
            logger.warning(f"Policy text seems too short: {len(policy_text)} characters")
        
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
        # Casbin's library logging is off: with it on, every enforce() writes the caller id and the
        # full checked object to the function log. API-level decisions and data-level denials are
        # recorded through log_authorization_api / log_authorization.
        #
        # ObjectTypeRoutingEnforcer walks only the policy lines that can match the request's object
        # type and compiles each policy line's matcher once per enforcer instance (see the
        # docstrings of it and ExpressionCachingEnforcer). The instance is owned by this service, so
        # the routes and compiled expressions are per user and go away with the service on TTL /
        # MFA invalidation.
        _enforcer = ObjectTypeRoutingEnforcer(model=new_model, adapter=new_string_adapter, enable_log=False)
        return _enforcer

    def _scrub_object_fields(self, obj):
        """Keep only the fields valid for the object's object__type (no-op for unknown types)."""
        valid_fields = get_constraint_fields_for_object_type(obj.get("object__type"))
        if not valid_fields:
            return obj
        allowed = set(valid_fields) | ALWAYS_ALLOWED_OBJECT_KEYS
        return {k: v for k, v in obj.items() if k in allowed}

    @staticmethod
    def _evaluated_database_id(obj):
        """The object with an archived record's databaseId replaced by the database it belongs to.

        Archiving an asset and deleting a database both rewrite the record under the partition key
        ``<databaseId>#deleted``, and the listings and single-record reads that include archived
        records hand that stored id to ``enforce()``. ``enforce()`` decides such a record in two
        forms: the copy returned here, and the record as stored. It is allowed only when both are
        allowed, so a rule on the database id (``equals dbX``) denies its archived records as it
        denies its live ones, and a rule naming the stored id (``equals dbX#deleted``) still applies
        to it. Granting archived records therefore takes an allow that covers both forms. Database
        ids cannot contain ``#`` (``id_pattern``), so the suffix is unambiguous; every trailing
        occurrence is removed, so a repeated suffix is decided on the same database. A copy is
        returned and the caller's object is not modified; an object without the suffix is returned
        unchanged (the same object).
        """
        database_id = obj.get("databaseId")
        if isinstance(database_id, str) and database_id.endswith(ARCHIVED_DATABASE_ID_SUFFIX):
            while database_id.endswith(ARCHIVED_DATABASE_ID_SUFFIX):
                database_id = database_id[: -len(ARCHIVED_DATABASE_ID_SUFFIX)]
            return {**obj, "databaseId": database_id}
        return obj

    def enforce(self, obj, act):
        """
        Enforce authorization (audit logging handled by wrapper).
        
        Args:
            obj: The object being accessed
            act: The action being performed
            
        Returns:
            Boolean indicating if access is authorized
        """
        sub = f"user::{self._user_id}"

        # If the internal Casbin module is not functioning, then immediately deny all access
        # Note: Cache validation now happens in CasbinEnforcer.__init__ to ensure cache is always
        # fresh when enforce() is called. This provides a single point of cache validation.
        #
        if self._enforcer is None:
            logger.warning(f"Enforcer is None for user {self._user_id}, denying access")
            return False

        # Ignore fields out of scope for this object's type so deprecated or foreign
        # fields cannot influence a match. Unknown/absent types are left unchanged.
        obj = self._scrub_object_fields(obj)
        # An archived record must also pass on the database it belongs to; the stored record is
        # then decided below as for any other object.
        evaluated = self._evaluated_database_id(obj)
        if evaluated is not obj and not self.enforce(evaluated, act):
            return False

        enhanced_object = PERMISSION_CONSTRAINT_FIELDS.copy()
        # Update with obj, but convert any None values to empty strings to prevent regex errors
        for key, value in obj.items():
            if value is None:
                enhanced_object[key] = ""
            else:
                enhanced_object[key] = value

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
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The ``bucketReadConstraints`` step of the v2.6 to v2.7 migration (issue #1978).

From v2.7 three calls need read on an asset bucket registration through a ``bucket`` constraint: each
``GET /buckets`` row, and the bucket chosen on ``POST /database`` (create) and on a ``PUT
/database/{databaseId}`` that moves the database to another bucket (update). Before v2.7 a role holding
those API routes listed every bucket and put a database on any of them, so a role stored before the
upgrade holds the routes and no ``bucket`` constraint, and after it lists no bucket and cannot choose one.

This step finds every role whose own constraints allow at least one of the three routes, decided the way
the authorizer decides it (the same operators, value lists, AND and OR criteria, and a deny line
overriding every allow line), and gives each such role that has no ``bucket`` constraint the one the
shipped role templates carry: ``GET`` on every registration (``bucketId`` contains ``.*``). That is the
bucket choice those roles had before the upgrade and nothing wider. The update route is decided for the
path of every database the deployment holds (``/database/<databaseId>``; archived databases are left
out), because the authorizer decides a concrete path. The step leaves a role that already has any
``bucket`` constraint as it is, changes no other constraint, and reports user-level grants of the routes
without changing them. A role whose constraints cannot be read or evaluated is reported and left alone.

It writes only the constraints table: one ``PutItem`` per role, conditional on the item not existing,
under an id derived from the role name. A repeat writes nothing for a role that still has a ``bucket``
constraint; it cannot tell a role whose constraint was removed after the first run from one that never had
one, and gives that role the bucket read again. Every role it looked at is one line of
``bucketReadConstraints-<dry-run|execute>-report-<UTC time>.jsonl`` naming the routes that qualified it.
Standard library and botocore only; it imports no backend code.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Iterator, List, Optional, Set, Tuple

from botocore.exceptions import ClientError

STEP = 'bucketReadConstraints'
OBJECT_TYPE_INDEX = 'ObjectTypeIndex'
BUCKET_OBJECT_TYPE = 'bucket'
API_OBJECT_TYPE = 'api'
# The request object and action the authorizer checks for GET /buckets (Tier 1).
BUCKETS_ROUTE_OBJECT = {'object__type': API_OBJECT_TYPE, 'route__path': '/buckets'}
BUCKETS_ROUTE_ACTION = 'GET'
# The three API routes whose call reads a bucket registration from v2.7 (Tier 1 route, action). The update
# route's path is the database's own: it is decided for each database the deployment holds.
ROUTE_LIST_BUCKETS = 'GET /buckets'
ROUTE_CREATE_DATABASE = 'POST /database'
ROUTE_UPDATE_DATABASE = 'PUT /database/{databaseId}'
QUALIFYING_ROUTES = (
    (ROUTE_LIST_BUCKETS, 'GET'),
    (ROUTE_CREATE_DATABASE, 'POST'),
    (ROUTE_UPDATE_DATABASE, 'PUT'),
)
ARCHIVED_DATABASE_MARKER = '#deleted'
# The one field an api constraint is evaluated on; a criterion on any other field builds no clause.
API_ROUTE_FIELDS = ('route__path',)
RULE_OPERATORS = ('equals', 'contains', 'does_not_contain', 'starts_with', 'ends_with', 'is_one_of',
                  'is_not_one_of')
NEGATED_OPERATORS = ('does_not_contain', 'is_not_one_of')
SYSTEM_USER = 'SYSTEM_USER'
ROLE_NAME_VARIABLE = '{{ROLE_NAME}}'

# The bucket constraint every shipped role template carries (documentation/permissionsTemplates/*.json,
# all but the deny overlay), with ROLE_NAME still unset. The step writes exactly this for a role.
TEMPLATE_BUCKET_CONSTRAINT = {
    'name': '{{ROLE_NAME}}-buckets',
    'description': ('Allow read of every asset bucket registration. Listing buckets also needs the /buckets '
                    'route; putting a database on a bucket needs read on that bucket'),
    'objectType': 'bucket',
    'criteriaAnd': [{'field': 'bucketId', 'operator': 'contains', 'value': '.*'}],
    'criteriaOr': [],
    'groupPermissions': [{'action': 'GET', 'type': 'allow'}],
}

# Fixed namespace of the ids this step writes: the same role always gets the same constraint id.
_ID_NAMESPACE = uuid.UUID('0eb5f894-4c1b-48c6-9198-5b5378be7a68')

OUTCOME_NO_ROUTE = 'noQualifyingRoute'
OUTCOME_HAS_BUCKET = 'alreadyHasBucket'
OUTCOME_WOULD_ADD = 'wouldAdd'
OUTCOME_ADDED = 'added'
OUTCOME_ALREADY_ADDED = 'alreadyAdded'
OUTCOME_UNREADABLE = 'unreadable'
OUTCOME_FAILED = 'failed'
OUTCOME_USER_GRANT = 'userGrantNotChanged'
ROLE_OUTCOMES = (OUTCOME_NO_ROUTE, OUTCOME_HAS_BUCKET, OUTCOME_WOULD_ADD, OUTCOME_ADDED, OUTCOME_ALREADY_ADDED,
                 OUTCOME_UNREADABLE, OUTCOME_FAILED)


class UnreadableConstraint(Exception):
    """A stored constraint whose criteria or permissions cannot be read, or whose criterion the authorizer
    could not evaluate either (an invalid regular expression)."""


def bucket_read_constraint_id(role: str) -> str:
    """The base constraint id the step writes for ``role``; the stored item adds ``#group#<role>``."""
    return str(uuid.uuid5(_ID_NAMESPACE, f'{STEP}:{role}'))


def _s(item: Dict, name: str) -> str:
    return ((item or {}).get(name) or {}).get('S', '')


def _json_list(item: Dict, name: str) -> list:
    """A JSON-string list attribute of a stored constraint item ([] when absent)."""
    raw = (item or {}).get(name)
    if raw is None:
        return []
    if 'S' not in raw:
        raise UnreadableConstraint(f'{name} is not a JSON string')
    try:
        value = json.loads(raw['S'])
    except ValueError as e:
        raise UnreadableConstraint(f'{name} is not valid JSON: {e}')
    if value is None:
        return []
    if not isinstance(value, list):
        raise UnreadableConstraint(f'{name} is not a list')
    return value


def list_constraint_items(dynamodb_client, table: str, object_type: str) -> Iterator[Dict]:
    """Every stored constraint item of ``object_type`` (group and user items), through ObjectTypeIndex."""
    kwargs = {
        'TableName': table,
        'IndexName': OBJECT_TYPE_INDEX,
        'KeyConditionExpression': 'objectType = :t',
        'ExpressionAttributeValues': {':t': {'S': object_type}},
    }
    while True:
        response = dynamodb_client.query(**kwargs)
        for item in response.get('Items', []):
            yield item
        if 'LastEvaluatedKey' not in response:
            return
        kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


# Compiled patterns (or the error compiling one), reused across the paths a run decides.
_PATTERNS: Dict[str, object] = {}


def _compiled(pattern: str):
    if pattern not in _PATTERNS:
        try:
            _PATTERNS[pattern] = re.compile(pattern)
        except re.error as e:
            _PATTERNS[pattern] = e
    return _PATTERNS[pattern]


def _clause_matches(operator: str, value: str, target: str) -> bool:
    """One clause as the authorizer builds it (``regexMatch`` is ``re.match``; ``in`` is substring)."""
    if operator == 'equals':
        pattern = '^' + value + r'\Z'
    elif operator in ('contains', 'does_not_contain'):
        pattern = '(?s:.*)' + value + '(?s:.*)'
    elif operator == 'starts_with':
        pattern = '^' + value + '.*'
    elif operator == 'ends_with':
        pattern = '(?s:.*)' + value + r'\Z'
    else:  # is_one_of, is_not_one_of
        return value in target
    compiled = _compiled(pattern)
    if isinstance(compiled, re.error):
        raise UnreadableConstraint(f'criterion value {value!r} is not a valid regular expression: {compiled}')
    return compiled.match(target) is not None


def criterion_matches(criterion, obj: Dict[str, str], fields: Tuple[str, ...]) -> Optional[bool]:
    """Whether one criterion holds for ``obj``; None when the authorizer builds no clause for it (a field
    outside ``fields`` or an operator it does not know). A list value is the OR of its alternatives, and a
    negating operator holds only when none of them matches."""
    # The authorizer reads field, operator and value by key, so a criterion missing one fails the whole
    # policy build of every caller holding the role; such a role is never read as allowed.
    if not isinstance(criterion, dict) or not all(key in criterion for key in ('field', 'operator', 'value')):
        raise UnreadableConstraint('a criterion is not an object with field, operator and value')
    field = criterion.get('field')
    operator = criterion.get('operator')
    if field not in fields or operator not in RULE_OPERATORS:
        return None
    raw = criterion.get('value')
    values = raw if isinstance(raw, list) else [raw]
    target = obj.get(field, '')
    matched = any(_clause_matches(operator, str(value), target) for value in values)
    return not matched if operator in NEGATED_OPERATORS else matched


def line_matches(criteria_and: list, criteria_or: list, obj: Dict[str, str],
                 fields: Tuple[str, ...]) -> Optional[bool]:
    """Whether a constraint's policy line matches ``obj``: every usable AND criterion holds and, when there
    is a usable OR criterion, at least one of them does. None when no criterion is usable (no line)."""
    and_results = [r for r in (criterion_matches(c, obj, fields) for c in criteria_and) if r is not None]
    or_results = [r for r in (criterion_matches(c, obj, fields) for c in criteria_or) if r is not None]
    if not and_results and not or_results:
        return None
    return all(and_results) and (not or_results or any(or_results))


class RouteDecisions:
    """Per principal (``('role', name)`` or ``('user', id)``): the allow and deny lines for one route and
    action that match, and the principals whose constraints could not be read."""

    def __init__(self):
        self.allows: Dict[Tuple[str, str], List[str]] = {}
        self.denies: Dict[Tuple[str, str], List[str]] = {}
        self.unreadable: Dict[Tuple[str, str], str] = {}
        self.principals: Set[Tuple[str, str]] = set()

    def allowed(self, principal: Tuple[str, str]) -> bool:
        return bool(self.allows.get(principal)) and not self.denies.get(principal)


def _item_principal(item: Dict) -> Optional[Tuple[str, str]]:
    if _s(item, 'groupId'):
        return ('role', _s(item, 'groupId'))
    if _s(item, 'userId'):
        return ('user', _s(item, 'userId'))
    return None


class ApiPolicy:
    """The stored api constraint items, read once, decided for any route path and action.

    An allow line counts for a principal only from that principal's own item (the item a caller holding
    just that role loads); a deny line counts from any item that names the principal, so a principal is
    never read as allowed while some stored line denies it. An entry's ``permissionType`` defaults to
    allow, as in the authorizer."""

    def __init__(self, items, fields: Tuple[str, ...] = API_ROUTE_FIELDS):
        self.fields = fields
        self.principals: Set[Tuple[str, str]] = set()
        self.unreadable: Dict[Tuple[str, str], str] = {}
        # (base id, owner, item, {action: [(principal, effect)]})
        self._constraints = []
        # base id -> (criteriaAnd, criteriaOr), or the error reading them; read on first use.
        self._criteria: Dict[str, object] = {}
        for item in items:
            owner = _item_principal(item)
            if owner is None:
                continue
            self.principals.add(owner)
            base_id = _s(item, 'constraintId').split('#group#')[0].split('#user#')[0]
            try:
                entries = ([('role', e) for e in _json_list(item, 'groupPermissions')]
                           + [('user', e) for e in _json_list(item, 'userPermissions')])
                if not all(isinstance(entry, dict) for _, entry in entries):
                    raise UnreadableConstraint('a permission entry is not an object')
            except UnreadableConstraint as e:
                self.unreadable.setdefault(owner, f'constraint {base_id}: {e}')
                continue
            by_action: Dict[str, List[Tuple[Tuple[str, str], str]]] = {}
            for kind, entry in entries:
                principal = (kind, str(entry.get('groupId' if kind == 'role' else 'userId') or ''))
                effect = entry.get('permissionType') or 'allow'
                if not principal[1] or effect not in ('allow', 'deny'):
                    continue
                if effect == 'allow' and principal != owner:
                    continue
                by_action.setdefault(entry.get('permission'), []).append((principal, effect))
            if by_action:
                self._constraints.append((base_id, owner, item, by_action))

    def decide(self, obj: Dict[str, str], action: str,
               only: Optional[Set[Tuple[str, str]]] = None) -> RouteDecisions:
        """The decisions for one request object and action; ``only`` limits the work to the constraints
        that name one of those principals."""
        decisions = RouteDecisions()
        decisions.principals = set(self.principals)
        decisions.unreadable = dict(self.unreadable)
        for base_id, owner, item, by_action in self._constraints:
            lines = by_action.get(action)
            if not lines or (only is not None and not any(principal in only for principal, _ in lines)):
                continue
            try:
                criteria_and, criteria_or = self._read_criteria(base_id, item)
                matched = line_matches(criteria_and, criteria_or, obj, self.fields)
            except UnreadableConstraint as e:
                # Not decidable: neither its owner nor a principal it would decide is read as allowed.
                for principal in {owner, *(principal for principal, _ in lines)}:
                    decisions.unreadable.setdefault(principal, f'constraint {base_id}: {e}')
                continue
            if not matched:
                continue
            for principal, effect in lines:
                target = decisions.allows if effect == 'allow' else decisions.denies
                target.setdefault(principal, []).append(base_id)
        return decisions

    def _read_criteria(self, base_id: str, item: Dict):
        key = _s(item, 'constraintId') or base_id
        if key not in self._criteria:
            try:
                self._criteria[key] = (_json_list(item, 'criteriaAnd'), _json_list(item, 'criteriaOr'))
            except UnreadableConstraint as e:
                self._criteria[key] = e
        cached = self._criteria[key]
        if isinstance(cached, UnreadableConstraint):
            raise cached
        return cached


def decide_route(items, obj: Dict[str, str], action: str,
                 fields: Tuple[str, ...] = API_ROUTE_FIELDS) -> RouteDecisions:
    """Evaluate stored api constraint items for one route path and action, per principal."""
    return ApiPolicy(items, fields).decide(obj, action)


def route_paths(route: str, database_ids: List[str]) -> List[str]:
    """The concrete request paths the authorizer would decide for one of the QUALIFYING_ROUTES."""
    if route == ROUTE_LIST_BUCKETS:
        return ['/buckets']
    if route == ROUTE_CREATE_DATABASE:
        return ['/database']
    return [f'/database/{database_id}' for database_id in database_ids]


class Qualification:
    """Per principal: the routes it is allowed (with the constraints that allow each, and for the update
    route the first database it is allowed on), the constraints that deny a route it is not allowed, and
    the principals whose constraints could not be read."""

    def __init__(self, principals):
        self.principals: Set[Tuple[str, str]] = set(principals)
        self.routes: Dict[Tuple[str, str], Dict[str, List[str]]] = {}
        self.update_database: Dict[Tuple[str, str], str] = {}
        self.denied: Dict[Tuple[str, str], Dict[str, Set[str]]] = {}
        self.unreadable: Dict[Tuple[str, str], str] = {}

    def qualified_by(self, principal: Tuple[str, str]) -> List[str]:
        allowed = self.routes.get(principal, {})
        return [route for route, _ in QUALIFYING_ROUTES if route in allowed]


def qualify(api_items, database_ids: List[str]) -> Qualification:
    """Which principals are allowed GET /buckets, POST /database or PUT /database/<id> (for some database
    the deployment holds), each decided the way the authorizer decides that request."""
    policy = ApiPolicy(api_items)
    result = Qualification(policy.principals)
    result.unreadable.update(policy.unreadable)
    for route, action in QUALIFYING_ROUTES:
        pending = set(policy.principals)
        for path in route_paths(route, database_ids):
            if not pending:
                break
            decisions = policy.decide({'object__type': API_OBJECT_TYPE, 'route__path': path}, action, pending)
            for principal, reason in decisions.unreadable.items():
                result.unreadable.setdefault(principal, reason)
            for principal in list(pending):
                if decisions.allowed(principal):
                    result.routes.setdefault(principal, {})[route] = sorted(set(decisions.allows[principal]))
                    if route == ROUTE_UPDATE_DATABASE:
                        result.update_database[principal] = path[len('/database/'):]
                    pending.discard(principal)
                elif decisions.denies.get(principal):
                    result.denied.setdefault(principal, {}).setdefault(route, set()).update(
                        decisions.denies[principal])
    for principal, routes in result.denied.items():
        for route in list(routes):
            if route in result.routes.get(principal, {}):
                del routes[route]
    return result


def list_database_ids(dynamodb_client, table: str) -> List[str]:
    """The id of every database the deployment holds, archived databases (``<id>#deleted``) left out."""
    kwargs = {
        'TableName': table,
        'ProjectionExpression': '#d',
        'ExpressionAttributeNames': {'#d': 'databaseId'},
    }
    database_ids = []
    while True:
        response = dynamodb_client.scan(**kwargs)
        for item in response.get('Items', []):
            database_id = _s(item, 'databaseId')
            if database_id and ARCHIVED_DATABASE_MARKER not in database_id:
                database_ids.append(database_id)
        if 'LastEvaluatedKey' not in response:
            return sorted(set(database_ids))
        kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def principals_with_object_type(items) -> Set[Tuple[str, str]]:
    """Every principal a stored constraint item of an object type belongs to or names."""
    principals: Set[Tuple[str, str]] = set()
    for item in items:
        owner = _item_principal(item)
        if owner is not None:
            principals.add(owner)
        try:
            for entry in _json_list(item, 'groupPermissions'):
                if isinstance(entry, dict) and entry.get('groupId'):
                    principals.add(('role', str(entry['groupId'])))
        except UnreadableConstraint:
            pass
    return principals


def build_bucket_read_item(role: str, now: str) -> Dict:
    """The stored (denormalized, wire-format) item of the template's bucket constraint for ``role``,
    shaped as the template import writes it."""
    template = TEMPLATE_BUCKET_CONSTRAINT
    base_id = bucket_read_constraint_id(role)
    permissions = [{
        'id': str(uuid.uuid5(_ID_NAMESPACE, f'{STEP}:{role}:{p["action"]}')),
        'groupId': role,
        'permission': p['action'],
        'permissionType': p['type'],
    } for p in template['groupPermissions']]
    return {
        'constraintId': {'S': f'{base_id}#group#{role}'},
        'groupId': {'S': role},
        'name': {'S': template['name'].replace(ROLE_NAME_VARIABLE, role)},
        'description': {'S': template['description']},
        'objectType': {'S': template['objectType']},
        'criteriaAnd': {'S': json.dumps(template['criteriaAnd'])},
        'criteriaOr': {'S': json.dumps(template['criteriaOr'])},
        'groupPermissions': {'S': json.dumps(permissions)},
        'userPermissions': {'S': json.dumps([])},
        'dateCreated': {'S': now},
        'dateModified': {'S': now},
        'createdBy': {'S': SYSTEM_USER},
        'modifiedBy': {'S': SYSTEM_USER},
    }


def report_path(output_dir: str, dry_run: bool, started: datetime) -> str:
    """One report per run, named by its mode and start time (UTC), so a later run keeps earlier lines."""
    mode = 'dry-run' if dry_run else 'execute'
    return os.path.join(output_dir, f"{STEP}-{mode}-report-{started.strftime('%Y%m%dT%H%M%SZ')}.jsonl")


def run_bucket_read_constraints(dynamodb_client, constraints_table: str, dry_run: bool, output_dir: str,
                                log: Optional[Callable[[str], None]] = None,
                                now: Optional[datetime] = None, database_table: Optional[str] = None) -> Dict:
    """Run the step. Returns the counts and the report path; writes nothing under ``dry_run``. Without
    ``database_table`` the update route is decided for no database."""
    log = log or (lambda message: None)
    started = now or datetime.now(timezone.utc)
    stamp = started.isoformat()
    mode = 'dry-run' if dry_run else 'execute'

    api_items = list(list_constraint_items(dynamodb_client, constraints_table, API_OBJECT_TYPE))
    with_bucket = principals_with_object_type(
        list_constraint_items(dynamodb_client, constraints_table, BUCKET_OBJECT_TYPE))
    database_ids = list_database_ids(dynamodb_client, database_table) if database_table else []
    found = qualify(api_items, database_ids)

    counts = {key: 0 for key in ('rolesExamined', 'rolesQualified', 'usersQualified', 'databasesChecked')
              + ROLE_OUTCOMES}
    counts['databasesChecked'] = len(database_ids)
    route_counts = {route: 0 for route, _ in QUALIFYING_ROUTES}
    lines: List[Dict] = []
    roles = sorted(name for kind, name in found.principals if kind == 'role')
    users = sorted(name for kind, name in found.principals if kind == 'user')

    def describe(principal, line):
        """The routes that qualified the principal, the constraints allowing each, and the database the
        update route was first found allowed on."""
        qualified_by = found.qualified_by(principal)
        line['qualifiedBy'] = qualified_by
        line['grantedBy'] = {route: found.routes[principal][route] for route in qualified_by}
        if principal in found.update_database:
            line['updateAllowedOnDatabase'] = found.update_database[principal]

    for role in roles:
        principal = ('role', role)
        counts['rolesExamined'] += 1
        line = {'step': STEP, 'mode': mode, 'role': role}
        if principal in found.unreadable:
            outcome = OUTCOME_UNREADABLE
            line['error'] = found.unreadable[principal]
        elif not found.qualified_by(principal):
            outcome = OUTCOME_NO_ROUTE
            if found.denied.get(principal):
                line['deniedBy'] = {route: sorted(ids) for route, ids in found.denied[principal].items()}
        else:
            describe(principal, line)
            counts['rolesQualified'] += 1
            for route in line['qualifiedBy']:
                route_counts[route] += 1
            if principal in with_bucket:
                outcome = OUTCOME_HAS_BUCKET
            else:
                item = build_bucket_read_item(role, stamp)
                line['constraintId'] = item['constraintId']['S']
                if dry_run:
                    outcome = OUTCOME_WOULD_ADD
                else:
                    try:
                        dynamodb_client.put_item(TableName=constraints_table, Item=item,
                                                 ConditionExpression='attribute_not_exists(constraintId)')
                        outcome = OUTCOME_ADDED
                    except ClientError as e:
                        code = e.response.get('Error', {}).get('Code', '')
                        if code == 'ConditionalCheckFailedException':
                            outcome = OUTCOME_ALREADY_ADDED
                        else:
                            outcome = OUTCOME_FAILED
                            line['error'] = code or str(e)
        counts[outcome] += 1
        line['outcome'] = outcome
        lines.append(line)
        routes = f" ({', '.join(line['qualifiedBy'])})" if line.get('qualifiedBy') else ''
        log(f"  {role}: {outcome}{routes}")

    for user in users:
        principal = ('user', user)
        if principal in found.unreadable or not found.qualified_by(principal):
            continue
        counts['usersQualified'] += 1
        line = {'step': STEP, 'mode': mode, 'user': user, 'outcome': OUTCOME_USER_GRANT}
        describe(principal, line)
        lines.append(line)

    os.makedirs(output_dir, exist_ok=True)
    path = report_path(output_dir, dry_run, started)
    # Appended, so two runs started in the same second keep each other's lines.
    with open(path, 'a', encoding='utf-8') as report:
        for line in lines:
            report.write(json.dumps(line, sort_keys=True) + '\n')
    return {'counts': counts, 'routes': route_counts, 'report': path}


def summary_lines(result: Dict, dry_run: bool) -> List[str]:
    """The step's closing summary, one count per line."""
    counts = result['counts']
    added = (('  would get the bucket read', OUTCOME_WOULD_ADD) if dry_run
             else ('  given the bucket read', OUTCOME_ADDED))
    rows = [
        ('Roles examined (holding api constraints)', counts['rolesExamined']),
        ('Databases the update route was checked on', counts['databasesChecked']),
        ('Roles allowed a bucket route', counts['rolesQualified']),
    ] + [(f'  allowed {route}', result['routes'][route]) for route, _ in QUALIFYING_ROUTES] + [
        (f'  already holding a bucket constraint ({OUTCOME_HAS_BUCKET})', counts[OUTCOME_HAS_BUCKET]),
        (f'{added[0]} ({added[1]})', counts[added[1]]),
        (f'  written by an earlier run ({OUTCOME_ALREADY_ADDED})', counts[OUTCOME_ALREADY_ADDED]),
        (f'  write failed ({OUTCOME_FAILED})', counts[OUTCOME_FAILED]),
        (f'Roles allowed none of them ({OUTCOME_NO_ROUTE})', counts[OUTCOME_NO_ROUTE]),
        (f'Roles left unchanged, constraints unreadable ({OUTCOME_UNREADABLE})', counts[OUTCOME_UNREADABLE]),
        (f'Users allowed a bucket route directly ({OUTCOME_USER_GRANT})', counts['usersQualified']),
    ]
    width = max(len(label) for label, _ in rows)
    return [f"{label}:{' ' * (width - len(label))} {value}" for label, value in rows] + [
        f"Report: {result['report']}"]


def exit_status(result: Dict) -> int:
    """Non-zero only when a write failed; an unreadable role is reported for the operator to review."""
    return 1 if result['counts'][OUTCOME_FAILED] else 0

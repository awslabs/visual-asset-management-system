# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Function logs keep request content out (#1552): a static scan of every backend module.

`safeLogger` redacts by key, so a value rendered into the message text is never redacted, and a
request carries text a user typed (search terms, filter patterns, comment text) and a stream image
carries every field of its row. A module logs `event_log_summary(event)`, ids and sizes instead.

The scan reads the AST of every module under `backend/backend` (handlers, common, add-ons, custom
resources, models). It looks at every call that logs: a method named like a log level on a receiver
whose dotted name contains "log" (`logger.info`, `self.logger.warning`, `log.info`,
`logging.getLogger().info`), and `print`. It walks every argument subtree, keyword arguments
(`extra=`) and f-strings included, so `%` formatting, `.format`, concatenation, `!r` and `json.dumps`
wrappers are all seen. It flags a request-content name (see CONTENT_NAME) anywhere in them, unless
the name only appears

* inside `event_log_summary(...)`, `query_clause_log_summary(...)`, `len`, `bool`, `type`,
  `sorted` or `isinstance`, or
* as `name['key']`, `name.get('key')` or `name.key` with a constant key listed in IDENTIFIER_KEYS
  (`event['assetId']`, `record['applyId']`, `request_model.assetLinkId`).

Every other key or attribute of a content name is flagged, including a variable key and a nested
one: `item['commentBody']`, `event['headers']`, `event['requestContext']`,
`record['dynamodb']['NewImage']`, `request_model.dict()`.

What the scan cannot catch: request content bound to a name that is not in CONTENT_NAME (for
example `typed = json.loads(event['body'])` followed by `logger.info(typed)`), a value reached
through a helper's return value or an attribute of another object, a key listed in IDENTIFIER_KEYS
that holds text in one module, and any log call whose receiver name does not contain "log". The live log search in the smoke plan covers those; review new log calls by hand.

An existing call that is acceptable stays in ALLOWED_SITES with its reason. The allow-list is
checked both ways: a new flagged call fails the scan, and an entry that no longer matches a
flagged call fails the stale-entry test, so the list cannot rot.
"""

import ast
import hashlib
import os
import re

import pytest

_BACKEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend"
)

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}

# Names a module gives request content, a whole event, a stream record or image, and the whole
# record it just read or built (`item` is a comment, `asset_object` an asset with its description).
CONTENT_NAME = re.compile(
    r"^(event|body|\w*_body|\w*Body|request_body|requestBody|request_model|query_params|queryParams|"
    r"queryParameters|query_parameters|payload|query_clause|queryClause|"
    r"query_string_parameters|parsed_body|item|asset_object|result|record|records|new_image|old_image|"
    r"stream_record|event_record)$"
)
# Keys whose value is an identifier, a count, a status or a fixed label, so `name['key']` and
# `name.get('key')` with one of them is safe to log. Any other constant key (and any non-constant
# key) of a content name is flagged: `item['commentBody']`, `event['headers']`,
# `event['requestContext']`, `record['dynamodb']['NewImage']`. Add a key only after reading what
# it holds; the reason sits next to it.
IDENTIFIER_KEYS = {
    "assetId", "databaseId", "executionId", "workflowId", "pipelineId", "commentId", "tagName",
    "tagTypeName", "fileKey", "assetName", "databaseName", "relationshipId", "jobId", "requestType",
    "eventName", "eventSource", "triggerSource", "httpMethod", "resource", "path",
    "status", "statusCode", "success", "count", "size", "total",
    # table keys and record ids of rows (item/record in the data and compliance modules)
    "pk", "key", "Key", "constraintId", "applyId", "bindingDatabaseId", "evaluationId", "verdict",
    "runs", "runLimit", "recipeId",
    # counters of an SNS publish result (snsQueuing)
    "success_count", "total_records", "skipped_count", "unpublished_count",
    # the CloudFormation custom-resource and re-index invocation fields (crReindexer): request type,
    # stack and resource ids, and the operation switches
    "RequestType", "StackId", "LogicalResourceId", "RequestId", "operation", "dry_run", "limit",
    "clear_indexes",
    # request-model fields that are ids, a name of a tag type or a metadata type, or a flag
    "assetLinkId", "fromAssetId", "toAssetId", "assetVersionId", "tagTypeName", "type", "basic",
    "revertMetadata",
}
SAFE_FUNCTIONS = {"event_log_summary", "query_clause_log_summary", "len", "bool", "type", "sorted", "isinstance"}


def _is_log_call(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    if not isinstance(func, ast.Attribute) or func.attr not in LOG_METHODS:
        return False
    return "log" in ast.unparse(func.value).lower()


def _content_names(argument):
    """The request-content names in one argument subtree that are not in a safe position."""
    found = []

    def walk(node):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in SAFE_FUNCTIONS:
                return
            if (name == "get" and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in IDENTIFIER_KEYS
                    and isinstance(func.value, ast.Name) and CONTENT_NAME.match(func.value.id)):
                return
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and CONTENT_NAME.match(node.value.id) and node.attr in IDENTIFIER_KEYS):
            return
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and CONTENT_NAME.match(node.value.id)
                and isinstance(node.slice, ast.Constant) and node.slice.value in IDENTIFIER_KEYS):
            return
        if isinstance(node, ast.Name) and CONTENT_NAME.match(node.id):
            found.append(node.id)
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(argument)
    return found


def _enclosing_functions(tree):
    """Map each call node to the name of the function it sits in ('<module>' outside any)."""
    owner = {}

    def visit(node, current):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else current
            if isinstance(child, ast.Call):
                owner[child] = current
            visit(child, name)

    visit(tree, "<module>")
    return owner


def _scan_source(source, relative_path):
    """Flagged log calls in one module, as (relative_path, function, digest, line, names)."""
    tree = ast.parse(source)
    owner = _enclosing_functions(tree)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_log_call(node)):
            continue
        names = []
        for argument in node.args + [keyword.value for keyword in node.keywords]:
            names += _content_names(argument)
        if names:
            digest = hashlib.sha1(ast.unparse(node).encode()).hexdigest()[:8]
            found.append((relative_path, owner.get(node, "<module>"), digest, node.lineno, sorted(set(names))))
    return found


def _scan_tree(root):
    found = []
    for directory, _dirs, files in os.walk(root):
        if "__pycache__" in directory:
            continue
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(directory, name)
                with open(path, encoding="utf-8") as handle:
                    source = handle.read()
                found += _scan_source(source, os.path.relpath(path, root))
    return found


# Reviewed sites the scan flags that are acceptable. Key: (module, function, digest of the call's
# source); changing the call changes the digest and sends it back through review.
ALLOWED_SITES = {
    ("common/compliance/quarantineGuard.py", "quarantine_refusal", "11648e92"):
        "passes the event to a route helper that returns the method and concrete path of a refused "
        "download, copy or workflow launch (identifiers); no request text is logged",
    ("handlers/indexing/sqsBucketSync.py", "parse_event", "c072afb1"):
        "the Message field of an SQS or SNS wrapper around an S3 event notification (bucket and key "
        "names), logged when its JSON cannot be parsed; not typed user text",
    ("customResources/getVpcEndpointIps.py", "lambda_handler", "93b15aea"):
        "the CloudFormation custom-resource event of the deployment (infrastructure values); "
        "not a user request",
    ("handlers/addon/edh/edhCatalogRegistration.py", "lambda_handler", "d4080df6"):
        "the registration result is lists of catalog entry names and warnings from the deployment "
        "custom resource; no request text",
    ("handlers/addon/edh/edhLaunchWorker.py", "_handle_start", "efd77022"):
        "logs only the whitelisted START_PAYLOAD_KEYS of the payload (ids and stamps)",
    ("handlers/addon/edh/edhLaunchWorker.py", "lambda_handler", "828ba671"):
        "logs only the whitelisted WORKER_PAYLOAD_KEYS of the payload (ids)",
    ("handlers/addon/garnetFramework/garnetDataIndexDatabase.py", "get_bucket_details", "b59f4bd2"):
        "`result` is a dict of bucket id, bucket name and prefix built in the function; not a request",
    ("handlers/addon/physna/physnaAssetSync.py", "_delete_by_item", "476f8c54"):
        "logs the key NAMES of a Physna listing item (a response from Physna), not its values",
    ("handlers/addon/physna/physnaAssetSync.py", "_sync_asset_metadata_to_physna", "1ea169f1"):
        "logs the key NAMES of a Physna listing item (a response from Physna), not its values",
    ("handlers/addon/physna/physnaViewer.py", "_mint_viewer_token", "3c99a9f2"):
        "logs the key names of the Physna token response (a service response, no secret value)",
    ("handlers/assets/assetExportService.py", "get_asset_tree_via_lambda", "98fb6bd6"):
        "the error body of the asset links service's own response (a downstream message), "
        "not the caller's request",
    ("handlers/compliance/complianceBindingApply.py", "stop_at_run_limit", "7358b9b5"):
        "ids and run counters of an apply record; no typed text",
    ("handlers/workflows/importGlobalPipelineWorkflow.py", "lambda_handler", "b5498b99"):
        "`_loggable_event` returns the event as a structure with the template content keys redacted "
        "by safeLogger; an import of a pipeline template bundle, not search or comment text",
    ("handlers/workflows/sfn/processWorkflowExecutionOutput.py", "process_metadata_file", "046a464f"):
        "`body.get('message')` reads the 200 response of the metadata service, whose model default is "
        "the fixed text 'Success'; it holds no request text",
    ("models/common.py", "authorization_error", "75e5a70a"):
        "`errorBody` is the generic response body the handler returns (a fixed message), "
        "not the request",
    ("models/common.py", "internal_error", "7fee03a8"):
        "`errorBody` is the generic response body the handler returns (a fixed message), "
        "not the request",
    ("models/common.py", "service_unavailable", "48e0dd45"):
        "`errorBody` is the 503 response body the handler returns; every caller passes a fixed "
        "message constant (search unavailable, index building, search busy), not the request",
    ("common/uploadStatus.py", "_settle", "af896e7c"):
        "logs the upload id from the stored upload record and the exception class; not request text",
    ("handlers/assets/largeObjectCopy.py", "sweep", "7c0e515d"):
        "`result` is the sweep outcome built by sweep_recorded_upload (swept, listed, remaining flags "
        "or a fixed reason); the input is a direct invoke from the sweep state machine, not a request",
    ("handlers/assets/zipDownloadService.py", "handle_sweep", "0f9689ec"):
        "logs only the event's `final` flag and `result`, the sweep outcome (swept flag, counts or a "
        "fixed reason); the event itself is not logged",
}


def _flagged_keys(found):
    return {(path, function, digest) for path, function, digest, _line, _names in found}


@pytest.mark.unit
def test_no_module_logs_request_content():
    """Every flagged log call under backend/backend is a reviewed, allow-listed site."""
    found = _scan_tree(_BACKEND_DIR)
    unreviewed = [
        f"{path}:{line} in {function} ({', '.join(names)})"
        for path, function, digest, line, names in found
        if (path, function, digest) not in ALLOWED_SITES
    ]
    assert not unreviewed, (
        "log calls pass the event, a request body, query parameters, a search clause or a whole record; "
        "log event_log_summary(event), ids and sizes instead (or, for a reviewed non-request case, add "
        "the site to ALLOWED_SITES with its reason): " + "; ".join(unreviewed)
    )


@pytest.mark.unit
def test_the_allow_list_has_no_stale_entry():
    """An entry whose call was changed or removed must leave the list."""
    stale = sorted(set(ALLOWED_SITES) - _flagged_keys(_scan_tree(_BACKEND_DIR)))
    assert not stale, f"ALLOWED_SITES entries no longer match a flagged call: {stale}"


@pytest.mark.unit
def test_every_allow_list_entry_has_a_reason():
    assert all(len(reason.strip()) > 20 for reason in ALLOWED_SITES.values())


# Mutation self-test: each bad form, placed in a scratch module in each scanned area, is flagged.
BAD_FORMS = [
    "logger.info(event)",
    "logger.info('x %s' % event)",
    "logger.info('x {}'.format(event))",
    "logger.info('x ' + str(event))",
    "logger.info('x', extra={'e': event})",
    "self.logger.info(event)",
    "log = logger\n    log.info(event)",
    "logger.info(f'{event!r}')",
    "logger.info(f'{event}')",
    "logging.getLogger().info(event)",
    "print(event)",
    "logger.info('b=' + event.get('body'))",
    "logger.info(f'{event[\"queryStringParameters\"]}')",
    "logger.debug(f\"q {event.get('queryStringParameters', {})}\")",
    "logger.info(json.dumps(event['body']))",
    "logger.info(f'Request body: {body}')",
    "logger.info(f'clause {json.dumps(query_clause)}')",
    "logger.info(item)",
    "logger.info(result)",
    "logger.info(asset_object)",
    "logger.warning(f'Bad record {record}')",
    "logger.debug(f'image {str(new_image)}')",
    "logger.info('x', comment_body, request_body)",
    "logger.info(item['commentBody'])",
    "logger.info(item['comment'])",
    "logger.info(result['message'])",
    "logger.info(body.get('message'))",
    "logger.info(item.get('commentBody'))",
    "logger.info(f\"c {item.get('commentBody', '')}\")",
    "logger.info(event['headers'])",
    "logger.info(event.get('headers'))",
    "logger.info(event['requestContext'])",
    "logger.info(event['pathParameters'])",
    "logger.info(event['Records'])",
    "logger.info(record['dynamodb']['NewImage'])",
    "logger.info(record['dynamodb'])",
    "logger.info(result['response'])",
    "logger.info(event[key])",
    "logger.info(event.get(key))",
    "logger.info(queryParameters)",
    "logger.info(query_parameters)",
    "logger.info(request_model)",
    "logger.info(request_model.dict())",
    "logger.info(request_model.json())",
    "logger.info(request_model.searchTerm)",
    "logger.info(f'p {requestBody}')",
    "logger.info(payload)",
]

SAFE_FORMS = [
    "logger.info(f'Event: {event_log_summary(event)}')",
    "logger.info(f\"Asset {event['assetId']} found\")",
    "logger.info(f\"Asset {event.get('assetId')} found\")",
    "logger.info(f'{len(event.get(\"Records\", []))} records')",
    "logger.info('body parsed')",
    "logger.info(f'clause {query_clause_log_summary(query_clause)}')",
    "logger.info(f'comment {len(item[\"commentBody\"])} chars')",
    "logger.info(f\"Asset {record['assetId']} of {item.get('databaseId')}\")",
    "logger.info(f'Created {request_model.assetLinkId}')",
    "logger.info(f'{type(event)} {sorted(event)}')",
]


def _scratch_function(form):
    return "def handler(event, self=None):\n    " + form + "\n"


@pytest.mark.unit
@pytest.mark.parametrize("area", ["handlers/x/handler.py", "common/helper.py", "handlers/addon/edh/worker.py"])
@pytest.mark.parametrize("form", BAD_FORMS)
def test_the_scan_flags_each_bad_form(area, form):
    assert _scan_source(_scratch_function(form), area), f"not flagged in {area}: {form}"


@pytest.mark.unit
@pytest.mark.parametrize("form", SAFE_FORMS)
def test_the_scan_passes_each_safe_form(form):
    assert not _scan_source(_scratch_function(form), "handlers/x/handler.py"), form


@pytest.mark.unit
def test_the_scan_walks_common_and_the_addons():
    """The scanned root holds the areas the first version of this test missed."""
    paths = {path for path, *_ in _scan_tree(_BACKEND_DIR)}
    assert any(p.startswith("common/") for p in paths)
    assert any(p.startswith("handlers/addon/") for p in paths)


@pytest.mark.unit
def test_the_known_limit_a_body_under_another_name_is_not_seen():
    """Documented limit: content bound to an unusual name passes the scan (see the module docstring)."""
    source = "def handler(event):\n    typed = event['body']\n    logger.info(typed)\n"
    assert not _scan_source(source, "handlers/x/handler.py")

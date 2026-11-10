# CLAUDE.md -- VAMS Python Lambda Backend

> Auto-loaded when Claude Code works within `backend/`. Authoritative guide for Lambda handler development, Pydantic model creation, authorization enforcement, DynamoDB access, and validators. Testing details: `backend/tests/CLAUDE.md`. Copy-paste skeletons for a new handler / model / test file: `backend/HANDLER_TEMPLATES.md`.

---

## Quick Reference

-   **Runtime**: Python 3.12 (AWS Lambda); Python 3.13+ (local dev/tests)
-   **Framework**: AWS Lambda + API Gateway REST API (v1)
-   **Validation**: Pydantic **1.10.13** (NOT v2) via aws-lambda-powertools
-   **Auth**: Casbin ABAC/RBAC with DynamoDB policy storage
-   **ORM**: boto3 DynamoDB resource + client APIs
-   **Search**: OpenSearch (opensearch-py 2.7.1)
-   **Logging**: aws-lambda-powertools Logger with custom redaction
-   **Tests**: pytest 9.0.3 + moto 5.1.0 (see `backend/tests/CLAUDE.md`)
-   **Gold Standard**: `backend/handlers/assets/assetService.py` (handler), `backend/models/assetsV3.py` (model)

---

## Directory Structure

> Update this tree when adding new handler domains, model files, or test directories (root `CLAUDE.md` Rule 11).

```
backend/
├── conftest.py, pytest.ini              # See backend/tests/CLAUDE.md
├── pyproject.toml, poetry.lock          # Poetry-managed deps
├── requirements.txt, requirements-dev.txt  # Exported from poetry.lock
├── backend/
│   ├── common/                                     # Shared utilities (no AWS bootstrap)
│   │   ├── auth/apiEvent.py                        #   normalize_event: REST→HTTP-API-v2 shape
│   │   ├── auth/authorizerCore.py                  #   JWT/API-key/IP validation
│   │   ├── auth/clientIp.py                        #   True client-IP resolution
│   │   ├── auth/originVerify.py                    #   CloudFront origin verification value (secret, cache)
│   │   ├── apiRoutes.py                            # MASTER API route registry: ApiRoute constants,
│   │   │                                           #   category group arrays, ALL_API_ROUTES
│   │   │                                           #   (core + ADDON_ROUTES + CUSTOM_ROUTES).
│   │   │                                           #   Handlers dispatch via ApiRoute.matches().
│   │   ├── apiRouteTypes.py                        # ApiRoute + GET/POST/... (re-exported by apiRoutes.py)
│   │   ├── addonApiRoutes.py                       # Add-on routes (Physna viewer) → ADDON_ROUTES
│   │   ├── addonConstants.py                       # Add-on object types, constraint fields, blocklist entries
│   │   ├── addonResourceNames.py                   # AddonResourceKeys (mirrors infra/common/addonResourceParamKeys.ts)
│   │   ├── constants.py                            # ABAC policy, allowed values, file blocklists:
│   │   │                                           #   CORE_* lists merged with the add-on + custom lanes
│   │   ├── downloadEligibility.py                  # Download eligibility (asset distributable, key containment +
│   │   │                                           #   length, asset-version pin, delete marker 410, blocklist,
│   │   │                                           #   whole-prefix refusal, is_file_archived); caller injects S3
│   │   │                                           #   client, getters, resolvers; signing stays in the caller
│   │   ├── dynamodb.py                             # query_all_items, query_has_match, batch_get_items,
│   │   │                                           #   to_update_expr, get_asset_object_from_id
│   │   ├── resourceNames.py                        # SSM resource-name resolver + ResourceKeys
│   │   ├── s3.py                                   # S3 file validation + paged list helpers; per-Region S3 client
│   │   │                                           #   factory and the Region-routing client the asset handlers hold
│   │   ├── largeObjectCopy.py                      # Large object copy engine: settings, part planner, job/batch rows, exactly-once batch report, cancel, ingestion hold
│   │   ├── zipDownloads.py                         # Download as Zip: job status machine (conditional terminal writes), keys under vams.preparedDownloads/, request ids, archive names, finalization rules, job-prefix cleanup
│   │   ├── s3MetadataKeys.py, s3PathPatterns.py    # Canonical S3 keys, .previewFile. patterns + select_newest_preview_file (mirror web/src/common/constants/fileFormats.ts)
│   │   ├── searchAuthzFilter.py                    # Asset GET policy lines -> OpenSearch upper/lower bound filters per search index (pure)
│   │   ├── dynamoDbMetadataKeys.py                 # Reserved DynamoDB metadata keys
│   │   ├── assetHistory.py, syncTracking.py        # Best-effort history / outbound-sync writers
│   │   ├── timestamps.py                           # utc_now(): naive UTC "now" (stored-string format); use instead of datetime.utcnow()
│   │   ├── validators.py                           # validate() dispatcher + regex patterns
│   │   └── workflows/                              # Execution/pipeline/workflow shared helpers (pure); incl. subExecutionStages.py (ASL frame + history → per-stage status), availableLogs.py (log-source identity, dedup, read planning) and executionLogCapture.py (the bounded, redacted execution log stored on an execution record)
│   │       ├── executionRecords.py                 #   storage record builders, keys, S3 prefixes
│   │       ├── executionOutputs.py                 #   output attribution + resolved manifest build; the shared
│   │       │                                       #   sub-process stop helper + ABORTABLE_RESOURCE_TYPES (SFN,
│   │       │                                       #   Batch, Deadline Cloud, SageMaker training job / pipeline
│   │       │                                       #   execution) used by both the abort API and the failure path
│   │       ├── systemRecords.py                    #   isSystem: importer cross-call check + read-only guard rules/messages
│   │       ├── systemPipelines.py                  #   system pipeline runs: server-only trigger (System-<Identifier> format)/context from a marked cross-call, re-run refusal
│   │       ├── executionLocks.py                   #   concurrency lock rows for perAsset / perInputFile / perInputFileVersion (sorted all-or-none acquire, holder-conditional release, TTL)
│   │       └── stepfunctions_builder.py            #   partition-aware ASL builder (Lambda/SQS/EventBridge/DeadlineCloud)
│   ├── customLogging/
│   │   ├── auditLogging.py                         # CloudWatch audit (9 event types, silent-fail)
│   │   └── logger.py                               # safeLogger with sensitive-data redaction
│   ├── customConfigCommon/                         # Override hooks for EXISTING behaviour (MFA check, login profile)
│   ├── custom/                                     # FORK EXTENSION POINT (shipped empty, never edited upstream):
│   │   ├── customApiRoutes.py                      #   CUSTOM_ROUTES, spliced into ALL_API_ROUTES
│   │   ├── customConstants.py                      #   object types, constraint fields, blocklist entries
│   │   └── customResourceNames.py                  #   CustomResourceKeys (mirrors infra/common/customResourceParamKeys.ts)
│   ├── handlers/                                   # Lambda handlers, one folder per domain
│   │   ├── assets/                                 # assetService.py (GOLD STANDARD),
│   │   │                                           #   assetVersions, assetHistory, etc.;
│   │   │                                           #   zipDownloadService (Download as Zip: the six
│   │   │                                           #   /download/zip routes, the read-time
│   │   │                                           #   reconcile and the delayed sweep)
│   │   ├── auth/                                   # apiGatewayAuthorizerRest, apiKeyService,
│   │   │                                           #   constraints, cognito, preTokenGen,
│   │   │                                           #   apiOriginVerifyRotation (secret rotation);
│   │   │                                           #   __init__: request_to_claims() → claims
│   │   ├── authz/__init__.py                       # CasbinEnforcer proxy (ABAC/RBAC)
│   │   ├── pipelines/                              # Pipeline CRUD (pipelineService = full CRUD;
│   │   │                                           #   pipelineTemplateService = templates + tagSchema)
│   │   ├── workflows/                              # Step Functions workflow mgmt. API-facing:
│   │   │                                           #   workflowService (CRUD), executeWorkflow
│   │   │                                           #   (asset-less multi-file execute), executionService
│   │   │                                           #   (list/abort/details/logs/rerun/permanent; two
│   │   │                                           #   functions: EXECUTION_SERVICE_ROUTES=service
│   │   │                                           #   serves all but permanent delete, =permanentDelete
│   │   │                                           #   only that route, on the one role that deletes rows),
│   │   │                                           #   workflowTriggerService, importGlobalPipelineWorkflow.
│   │   │                                           #   sfn/ (SFN/EventBridge-invoked): interimPipelineTracking,
│   │   │                                           #   handleExecutionError, processWorkflowExecutionOutput,
│   │   │                                           #   registerPipelineExecution, workflowTriggerDispatch,
│   │   │                                           #   deadlineCloudJobCallback
│   │   ├── addon/garnetFramework/                  # Garnet NGSI-LD indexer Lambdas
│   │   ├── addon/physna/                           # Physna Sync Lambdas (physnaCommon.py shared)
│   │   ├── custom/                                 # FORK EXTENSION POINT: fork handlers (handlers.custom.<name>)
│   │   └── assetLinks, comments, config, databases, indexing, metadata,
│   │       metadataschema, roles, search, sendEmail, subscription, tags,
│   │       tagTypes, userRoles                     # Domain handlers (folder per domain)
│   └── models/                                     # Pydantic v1 models, one file per domain
│       ├── assetsV3.py                             # GOLD STANDARD model file
│       ├── addon/                                  # Add-on models (physnaViewer.py)
│       ├── common.py                               # Response helpers, APIGatewayProxyResponseV2
│       ├── custom/                                 # FORK EXTENSION POINT: fork models
│       └── apiKeys, pipelines, workflows, executions, assetHistory, [domain].py
├── lambdaLayers/                                   # Lambda layer definitions
└── tests/                                          # See backend/tests/CLAUDE.md
```

---

## Critical Rules

1.  **ALWAYS use Pydantic v1 syntax.** This project uses `pydantic==1.10.13`. Never call
    v2 APIs (`model_validate`, `model_dump`, `ConfigDict`). Use `@root_validator`,
    `@validator`, `Field(...)`, `extra='ignore'`.

2.  **ALWAYS import BaseModel from aws_lambda_powertools**, not from pydantic directly:
    `from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError`.

3.  **ALWAYS use the `validate()` dispatcher** from `common.validators` for complex validation
    in `@root_validator` methods. Never write raw regex validation inline.

4.  **ALWAYS enforce two-level authorization**: `enforceAPI()` for route access, then
    `enforce()` for object-level access inside method handlers. **Fail closed on an empty
    token list** — empty `claims_and_roles["tokens"]` means no authenticated identity, so
    authorization cannot be evaluated and must deny.

    -   **Tier 1**: pre-set `method_allowed_on_api = False`, then only flip it inside
        `if len(tokens) > 0: ...enforceAPI()...`; deny if the flag is still `False`. The
        empty-token case naturally denies.
    -   **Tier 2, single resource**: guard with an explicit
        `if len(claims_and_roles["tokens"]) == 0: return authorization_error()` **before** the
        `enforce()` call. **Never** wrap a single-resource `enforce()` in `if len(tokens) > 0:`
        without an `else` that denies — that silently skips authorization and falls through to
        the response/mutation when tokens are empty.
    -   **Tier 2, list filtering**: the exception. Handlers that _append_ an item only when
        `enforce()` passes are fail-closed by construction (empty tokens → empty result).

5.  **ALWAYS use safeLogger** from `customLogging.logger`. Never `print()` or raw `logging.getLogger()`.

6.  **ALWAYS wrap AWS clients with retry config** at module level:
    `retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})`.

7.  **ALWAYS raise `VAMSGeneralErrorResponse`** for business logic errors. Never return raw dicts with status codes.

8.  **ALWAYS use `extra='ignore'`** on every Pydantic model class to silently drop unexpected fields.

9.  **NEVER log sensitive data.** `safeLogger` auto-redacts the credential keys `authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `apiKey`, `apiKeySecret`, `rawKey` and the caller-authored content keys `configBody`, `templateTags`, `tagValues`, `customTemplateOverride`, `webFormJson`, `inputInstructions` (also inside a JSON-string request `body`), at every nesting level in dicts and lists. Do not circumvent this. The redaction is key-driven, so an f-string that interpolates a payload value bypasses it — log identifiers and counts, never rendered bodies or tag values.

10. **ALWAYS resolve resource names at module level** via `get_table_name()`, `get_bucket_name()`, or `get_log_group_name()` from `common.resourceNames` inside a `try/except`. Never read `os.environ["TABLE_NAME"]` for resource names in non-pipeline handlers — SSM resolution provides centralized name management with env-var overrides for testing.

11. **NEVER echo request input into error messages returned to the client.** Keep response
    messages generic — no user-supplied values (IDs, names, paths, etc.) and no internal
    details (other databases' IDs, ARNs, stack traces). Log specifics via `logger` for
    debugging; the caller gets a generic message. Example: `logger.info(f"pipelineId {pipeline_id} conflicts with database {other_db}")` before
    `return validation_error(body={'message': "Pipeline ID is already in use by another database. Choose a different ID."}, event=event)`.

12. **ALWAYS dispatch API requests via the master route constants.** Handlers routing on
    `event['requestContext']['http']['path']` must match against the `ApiRoute` constants
    from `common/apiRoutes.py` (e.g. `API_LIST_FILES.matches(path)`), never against
    hard-coded path fragments (`path.endswith('/listFiles')`). When adding or renaming an
    API path, define the `ApiRoute` constant in `common/apiRoutes.py` AND add it to the
    matching category group array (`ASSET_FILE_ROUTES`, `AUTH_ROUTES`, …) so it is included
    in `ALL_API_ROUTES` and served by the `GET /auth/routes/api` listing. Keep route
    templates in sync with the routes attached in the CDK api builder stacks. An add-on's
    routes go in `common/addonApiRoutes.py` (`ADDON_ROUTES`); a fork's go in
    `custom/customApiRoutes.py` (`CUSTOM_ROUTES`) and never in `apiRoutes.py` (see "Fork
    extension points" below). Route lists import `ApiRoute` and the method constants from
    `common/apiRouteTypes.py`, never from `apiRoutes.py`, which imports them.

13. **ALWAYS use a leading `/` for normalized asset-relative file paths.** Normalized file
    paths (DynamoDB composite keys like `databaseId:assetId:filePath`, the `filePath`
    attribute, file-path provenance values) are asset-relative and begin with a single `/`
    (e.g. `/folder/file.txt`). Normalize inputs before storing or comparing:
    `file_path = "/" + raw_path.lstrip("/")`.

14. **NEVER read only the first page of an S3 or DynamoDB listing when the full set is
    needed.** S3 `list_object_versions` / `list_objects_v2` and DynamoDB queries cap a
    single call (`MaxKeys`, `Limit`, one page). When completeness matters, page to
    exhaustion via the shared helpers `common.s3.list_all_object_versions()` /
    `list_all_objects()` (page-size constants `S3_VERSIONS_PAGE_SIZE` /
    `S3_OBJECTS_PAGE_SIZE`; both accept an optional `max_keys` / `max_objects` cap for
    best-effort sampling). A bare `list_object_versions(..., MaxKeys=N)` silently drops
    versions beyond `N` (wrong archive status, truncated history). Existence-only checks
    (`MaxKeys=1`) are the allowed exception.

    **`MaxKeys=1` is sufficient for an existence check only because of three
    `ListObjectVersions` properties, and each one is load-bearing.** `max-keys` caps
    ENTRIES rather than distinct keys and will truncate mid-key; keys come back
    ascending, so a key precedes every longer sibling the same `Prefix` returns; and
    within one key the newest version comes first. Together those make the single entry
    the queried key's CURRENT version — which is why matching `entry['Key'] == key` (not
    merely "the page is non-empty") is what makes the answer conclusive, and why a
    sibling can only take the slot when the key itself has no entries, where a negative
    answer is correct anyway. Two ways to void this without touching the `MaxKeys`:
    passing a `Delimiter` (a `CommonPrefixes` entry can then occupy the slot), or passing
    `EncodingType='url'` (the returned `Key` is percent-encoded, so the equality test
    silently stops matching and **every** archived key reports not-archived). Neither is
    passed today; add either and the predicate needs rewriting, not adjusting.

    **A DynamoDB `FilterExpression` is applied AFTER the page is read, so a single
    filtered call is not a lookup and not an existence check.** DynamoDB reads up to
    1 MB (or `Limit` items), then discards the non-matching ones. Empty `Items`
    alongside a present `LastEvaluatedKey` is therefore the normal shape for "the match
    is on a later page" — it does not mean "no such item". Three consequences, each of
    which has occurred in this codebase:

    -   `len(response['Items']) > 0` as an existence test is a **false negative** once
        the table outgrows one page. The caller then accepts a duplicate, accepts a link
        that closes a cycle, or treats a node with children as a leaf.
    -   A filtered `scan` used to fetch **one** row by a non-key attribute returns `None`
        for a row that exists. Query a GSI on that attribute instead of scanning.
    -   `Limit=N` bounds items **evaluated**, not items returned, so a narrow filter over
        a wide window legitimately returns an empty page plus a `NextToken`. A caller that
        stops at the first empty page loses everything after it.

    This is the one place the S3 exception above does not carry over: S3 applies `Prefix`
    server-side before `MaxKeys`, so `MaxKeys=1` genuinely answers "does this exist".
    DynamoDB's filter does not work that way. See the paging patterns under
    [DynamoDB Patterns](#pagination-patterns) for the two correct shapes.

    What a `Prefix` + `MaxKeys=1` listing answers is "does anything under this prefix
    exist" — not "does this exact key exist". `Prefix` is a prefix match, so the listing
    also returns longer keys (`file.glb` matches `file.glb.previewFile.png`). Counting the
    returned entries therefore attributes a sibling's state to the requested key; match
    each entry's `Key` back to the key being asked about.

            **To check whether a single key or specific `versionId` is archived, do NOT list
            versions** — use `common.s3.is_object_version_archived(bucket, key, version_id,

        client=...)`. It issues one `HeadObject`(delete-marker → 405, live → 200, missing →

    404), O(1) regardless of version count. Handler-local`is_file_archived` helpers must
    delegate to it, never re-implement a version scan.

15. **Paginate large GET responses; never return an unbounded in-memory set.** A response
    that can exceed the AWS Lambda synchronous response limit (6 MB) must page externally:
    accept `maxItems`/`pageSize`/`startingToken` and return `NextToken`, defaulting sizes
    to named constants (mirror the asset-listing and metadata-listing handlers). Do not
    use DynamoDB `paginator.build_full_result()` to accumulate every record for a
    user-facing GET. When response ordering or enrichment requires the full set first
    (e.g. metadata-schema injection/ordering), enrich the full set, then offset-slice to
    the page. Limits that bound response size or protect Lambda runtime (e.g.
    `MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST`, worker-pool caps) stay as named constants with
    a rationale comment — keep them. A count cap does not bound a response whose items vary
    in size: upload initialization (`uploadFile.py` `initialize_upload`) also estimates its
    presigned-URL response from each file's part count and key
    (`MAX_UPLOAD_INITIALIZE_RESPONSE_BYTES` and the overhead constants in `models/assetsV3.py`)
    and refuses an over-budget request with a generic `400` before it creates any multipart
    upload.

16. **Normalize the event before reading `requestContext['http']`, `pathParameters`, or
    `queryStringParameters`.** The REST API (v1) proxy event differs from the HTTP API v2
    layout handlers are written against in two ways that `normalize_event(event)` (from
    `common.auth.apiEvent`) reconciles — it mutates the event in place, is idempotent, and
    is a no-op for internal `lambdaCrossCall` events:

    1.  **`requestContext.http` block.** Handlers read `event['requestContext']['http']['path']` / `['method']` / `['sourceIp']`, but the REST event exposes these as top-level `path` / `httpMethod` and `requestContext.identity.sourceIp`. `normalize_event` injects the v2-style block. The injected path is stage-relative on every host: through the optional REST API custom domain the event's `path` keeps the base path (`/api/database` where execute-api delivers `/database`), and `normalize_event` removes that one segment when `requestContext.domainName` is the configured domain (`API_CUSTOM_DOMAIN_NAME` / `API_CUSTOM_DOMAIN_BASE_PATH`, set by the CDK only when the domain is enabled). The top-level `path` is left as delivered, so dispatch on `requestContext.http.path`, never on `event['path']`.
    2.  **Null `pathParameters` / `queryStringParameters`.** The REST event sends explicit JSON `null` when there are none, so `event.get('pathParameters', {})` returns `None` (the default applies only when the key is **absent**, not when it is present-but-`null`). A handler that then does `path_params['id']` crashes with `TypeError` → 500. `normalize_event` coerces present-but-`null` to `{}`.

    `request_to_claims(event)` calls `normalize_event(event)` internally, so a handler whose **first** event access is `request_to_claims(event)` — the Gold Standard pattern — is already covered. A handler that reads `requestContext['http']`, `pathParameters`, or `queryStringParameters` **before** calling `request_to_claims` MUST call `normalize_event(event)` as the first statement of `lambda_handler`. Skipping it makes the handler 500 on a real REST request — a failure invisible to CDK synth and to unit tests that hand-build a v2-shaped event. Cover the REST-shaped event, including `null` params, in tests.

    ```python
    # ✅ Gold Standard — claims first; normalize is implicit
    def lambda_handler(event, context):
        claims_and_roles = request_to_claims(event)      # normalizes internally
        path = event['requestContext']['http']['path']   # safe — already normalized

    # ✅ When http MUST be read before claims — call normalize_event first
    from common.auth.apiEvent import normalize_event
    def lambda_handler(event, context):
        normalize_event(event)
        path = event['requestContext']['http']['path']
        claims_and_roles = request_to_claims(event)
    ```

---

## Gold Standard Handler Pattern

Reference: `backend/handlers/assets/assetService.py`

Every new Lambda handler MUST follow this exact structure. For a fill-in-the-blanks skeleton, see `backend/HANDLER_TEMPLATES.md`.

### 1. Module-Level Setup

Instantiate AWS clients (with `retry_config`), the logger, and every DynamoDB table at
import time — never inside the request path. Resolve resource names in one `try/except`
block, wrapping optional resources in inner `try/except KeyError`. Handler-specific env
vars read directly from `os.environ`. Full boilerplate lives in `backend/HANDLER_TEMPLATES.md`; the module-load contract is:

```python
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="YourServiceName")
claims_and_roles = {}

try:
    your_table_name = get_table_name(ResourceKeys.YOUR_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

your_table = dynamodb.Table(your_table_name)
```

### 2. Lambda Handler Entry Point

`lambda_handler` extracts claims (which also normalizes the event, Rule 16), runs Tier-1 auth, then dispatches by HTTP method inside a `try/except` that maps error types to the response functions in the table below. The Tier-1 block below is load-bearing: the pre-set `method_allowed_on_api = False` ensures the empty-token case denies (Rule 4).

```python
def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    try:
        method = event['requestContext']['http']['method']

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return authorization_error()

        if method == 'GET':    return handle_get_request(event)
        elif method == 'PUT':  return handle_put_request(event)
        elif method == 'DELETE': return handle_delete_request(event)
        else: return validation_error(body={'message': "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
```

### 3. Method Handlers

Route by path (via `ApiRoute.matches()`) and delegate to per-operation functions. Use `event.get('queryStringParameters', {}) or {}` to defensively coerce a REST `null` (Rule 16).

### 4. Business Logic Functions

Each per-operation function runs the same four steps: (1) validate params with the
`validate()` dispatcher; (2) query DynamoDB; (3) annotate `object__type`, fail-close on
empty tokens, then run Tier-2 `casbin_enforcer.enforce(event, item)`; (4)
`return success(body=...)`.

```python
def get_single_item(event, item_id):
    (valid, message) = validate({'itemId': {'value': item_id, 'validator': 'ID'}})
    if not valid:
        return validation_error(body={'message': message}, event=event)

    item = your_table.get_item(Key={'itemId': item_id}).get('Item')
    if not item:
        return general_error(body={'message': 'Item not found'}, event=event)

    item['object__type'] = 'yourObjectType'
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not CasbinEnforcer(claims_and_roles).enforce(event, item):
        return authorization_error()
    return success(body=item)
```

### 5. Error Handling Hierarchy

| Exception                    | Response Function       | Status Code |
| ---------------------------- | ----------------------- | ----------- |
| `ValidationError` (Pydantic) | `validation_error()`    | 400         |
| `VAMSGeneralErrorResponse`   | `general_error()`       | 400         |
| `Exception` (catch-all)      | `internal_error()`      | 500         |
| Authorization failure        | `authorization_error()` | 403         |

All response functions accept an optional `event=` parameter for audit logging.

`models/common.py` also defines two `VAMSGeneralErrorResponse` subclasses for single-asset routes that
decide Tier-2 before existence (the assetFiles, assetVersions and assetExportService
`get_asset_with_permissions` helpers): `VAMSAuthorizationDeniedResponse` (refused) and
`VAMSAssetNotFoundResponse` (authorized, asset missing). A read route answers them with
`authorization_error()` (403) and `general_error(status_code=404, body={'message': 'Asset not found'})`,
as the asset GET does, by catching them ahead of `VAMSGeneralErrorResponse`. Every assetFiles and
assetVersions route answers `VAMSAuthorizationDeniedResponse` with 403; the write routes catch only it
and keep their 400 for a missing asset. A route that does not catch them keeps its 400 with the same
message. The assetFiles and assetVersions helpers take `include_archived` (set only by read routes
called with `showArchived`): the record comes from `common.dynamodb.get_asset_record` and the Tier-2
object from `asset_authorization_object`, whose stand-in for a missing id carries the archived
`<databaseId>#deleted` id, so a caller refused that database's archived records gets the same 403
for an archived asset as for a missing one. Write routes never pass it.

---

## Pydantic v1 Model Patterns

Reference: `backend/models/assetsV3.py`

### CORRECT Model Definition

Import `BaseModel` from `aws_lambda_powertools.utilities.parser`; declare `extra='ignore'` on the class; use `Field(...)` with `regex=` (loaded from `common.validators`); attach a `@root_validator` for cross-field logic that calls the `validate()` dispatcher.

```python
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator, ValidationError
from pydantic import Field
from common.validators import validate, trim_name, id_pattern, object_name_pattern

class CreateItemRequestModel(BaseModel, extra='ignore'):
    databaseId: str = Field(min_length=4, max_length=63, regex=id_pattern)
    itemName:   str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    tags: Optional[list[str]] = []

    # Names and ids trim their surrounding whitespace; `strip_whitespace=` on Field() does nothing
    _trim_names = validator('databaseId', 'itemName', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'tags': {'value': values.get('tags'), 'validator': 'STRING_256_ARRAY', 'optional': True},
        })
        if not valid:
            raise ValueError(message)
        return values
```

### v1 vs v2 API Mapping

| v2 (WRONG)                               | v1 (CORRECT)                                                         |
| ---------------------------------------- | -------------------------------------------------------------------- |
| `from pydantic import BaseModel`         | `from aws_lambda_powertools.utilities.parser import BaseModel`       |
| `model_config = ConfigDict(extra='...')` | `class MyModel(BaseModel, extra='ignore'):`                          |
| `MyModel.model_validate(data)`           | `parse(body, model=MyModel)` (from `aws_lambda_powertools...parser`) |
| `@field_validator('name')`               | `@validator('name')`                                                 |
| `item.model_dump()`                      | `item.dict()`                                                        |
| `Field(pattern=...)`                     | `Field(regex=...)`                                                   |

Every model class must declare `extra='ignore'`.

**`Field()` silently swallows unknown kwargs.** Pydantic v1 collects any keyword it does not
recognize into `FieldInfo.extra` instead of raising, so a v2 spelling like `pattern=` becomes an
inert annotation that validates **nothing** — the model imports cleanly, tests pass, and the
field is unconstrained. `regex=` is the v1 spelling. `strip_whitespace=` is likewise inert on
`Field()` (it is a `class Config` / `constr` option, not a field constraint), so a padded value
reaches the length and regex checks — and storage — exactly as submitted. To confirm a constraint
is live, assert on the parsed field rather than reading the declaration:

```python
assert MyModel.__fields__['databaseId'].field_info.regex is not None   # live
assert not MyModel.__fields__['databaseId'].field_info.extra          # nothing swallowed
```

`tests/models/test_no_dead_field_kwargs.py` enforces this across every model.

### Field Validation Patterns

Common shapes:

-   String with regex: `Field(min_length=4, max_length=63, regex=id_pattern)`
-   Name, id, or free text: wire `common.validators.trim_name` as a `pre=True` validator — `_trim_names = validator('itemName', pre=True, allow_reuse=True)(trim_name)`. It removes the surrounding whitespace run and preserves interior whitespace, and runs before the length and regex checks. On a field that also carries a control-character rule (`pipelineName`, `category`, `workflowName`, `templateName`), declare `models.pipelines.reject_control_characters` as a `pre=True` validator BEFORE the trim — `.strip()` removes a trailing newline, tab or NEL, so a trim declared first turns that rejection into a silent normalization. Never `strip_whitespace=` on the field (inert), and not `anystr_strip_whitespace = True` on the model's `class Config` either — that strips every string on the model, including S3 keys and asset-relative paths, where a trailing space is a legitimate part of the key. A `description` or `comment` on a REQUEST model trims through the same validator, declared separately as `_trim_text` so the source keeps signalling which fields are ids and which are prose; a response or record model does not, because trimming there rewrites a stored row on the way out. `tests/models/test_no_dead_field_kwargs.py` asserts that partition as a rule — a new free-text field must either trim or be named in its `NO_TRIM_FREE_TEXT` map with the reason it keeps its whitespace
-   Optional with default: `Optional[list[str]] = []`, `Optional[str] = None`
-   Numeric constraints: `Field(None, ge=0)`, `Field(None, ge=0, le=10000)`
-   Nested models: `Optional[CurrentVersionModel] = None`

### Parsing Request Bodies

```python
from aws_lambda_powertools.utilities.parser import parse
body = json.loads(event.get('body', '{}'))
request = parse(body, model=CreateItemRequestModel)
```

---

## Authorization System (Casbin ABAC/RBAC)

Reference: `backend/handlers/authz/__init__.py`

### Two-Level Enforcement

Both tiers must deny when `claims_and_roles["tokens"]` is empty (Rule 4). An empty token list is
not "an anonymous user" — it means no identity could be established, so there is nothing to
evaluate policy against and `enforce()` must not be the only thing standing between the request
and the mutation. Copy these three shapes exactly; each makes the empty case deny structurally
rather than by remembering to check.

```python
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims

# ── Tier 1: API-level authorization (in lambda_handler) ────────────────────────
# The flag is pre-set False and only ever flipped INSIDE the token check, so the
# empty-token case cannot reach the dispatch below.
claims_and_roles = request_to_claims(event)

method_allowed_on_api = False
if len(claims_and_roles["tokens"]) > 0:
    if CasbinEnforcer(claims_and_roles).enforceAPI(event):
        method_allowed_on_api = True
if not method_allowed_on_api:
    return authorization_error()

# ── Tier 2: single resource (in method handlers) ───────────────────────────────
# The token guard is a SEPARATE statement before enforce(), never a wrapper around it.
item['object__type'] = 'asset'          # MUST annotate object type before enforce()
if len(claims_and_roles["tokens"]) == 0:
    return authorization_error()
if not CasbinEnforcer(claims_and_roles).enforce(event, item):
    return authorization_error()

# ── Tier 2: list filtering ────────────────────────────────────────────────────
# The one shape that may test tokens as a condition: it APPENDS on success, so empty
# tokens yield an empty result rather than an unfiltered one.
allowed = []
for item in items:
    item['object__type'] = 'asset'
    if len(claims_and_roles["tokens"]) > 0 and casbin_enforcer.enforce(event, item):
        allowed.append(item)
```

```python
# ❌ VIOLATION — the single-resource enforce() gated on tokens with no else that denies.
# When tokens is empty the whole block is skipped and execution falls through to the
# mutation/response below, so the request succeeds unauthorized. This reads as a guard
# and is the opposite of one; it is the exact defect Rule 4 exists to prevent.
if len(claims_and_roles["tokens"]) > 0:
    if not casbin_enforcer.enforce(event, item):
        return authorization_error()

your_table.put_item(Item=item)          # reached with NO authorization when tokens == []
return success(body=item)
```

### Key Concepts

-   **CasbinEnforcer** is a proxy with a **60-second policy cache TTL** per user; policy is stored in DynamoDB (`ConstraintsStorageTable`).
-   **A check walks only the policy lines that can match its object type.** `ObjectTypeRoutingEnforcer` (the enforcer `_create_casbin_enforcer_helper` builds) reads each line's compiled matcher expression when the policy loads and lists the line under the one object type its `regexMatch(r.obj.object__type, '^<type>\\Z')` gate names. A line that is not provably gated on one literal type -- a type name with regex syntax, a gate that is not the first conjunct, a top-level `||`, a rule that does not parse, a wrong field count, the deny-all line -- is shared by every type in its original position. A request of type T walks T's lines plus the shared ones in policy order, and a type no line names is denied without a walk. A skipped line would have evaluated `g()` and a false gate and added only `INDETERMINATE`, so no decision changes; `tests/handlers/authz/test_casbin_object_type_routing.py` compares it with the all-lines `ExpressionCachingEnforcer` over every shipped permission template, the seeded roles and synthetic and hand-written policy sets, and fails when a deny line is dropped or a type misrouted. Keep the object-type gate as the first criterion of every generated line: a line built otherwise is still correct but is walked for every type. A request that is not a `str` subject with a plain-`dict` object carrying a `str` `object__type` walks every line.
-   **Compiled matcher expressions are cached per user, never decisions.** The matcher contains `eval(p.obj_rule)`, so pycasbin re-parses every policy line's inlined rule on every `enforce()` (`core_enforcer.enforce_ex` -> `_get_expression` -> `SimpleEval.__init__` -> `ast.parse`). `ExpressionCachingEnforcer` (the parent of `ObjectTypeRoutingEnforcer`) overrides `_get_expression` with an **instance-level** dict keyed on the full inlined rule string, so each distinct line is parsed once per enforcer and every `enforce()` still evaluates every line it walks. The enforcer is owned by one `CasbinEnforcerService`, so the cache and the routes are per user and die with the service on TTL / MFA invalidation. Keep it that way: the compiled expression holds the enforcer's function map whose `g` is bound to that user's role manager, so a class-level or module-level cache would evaluate one user's role links for another (`tests/handlers/authz/test_casbin_expression_cache.py` pins the instance-level shape, decision parity against a plain `casbin.Enforcer`, cross-user isolation and TTL/MFA freshness). `enforce_ex` is serialized per instance because a compiled `SimpleEval` stores the request's parameters on itself while evaluating; routing swaps the walked list in under the same lock.
-   `request_to_claims(event)` returns `{"tokens": ["userId", ...], "roles": [...], "mfaEnabled": bool}`. `roles` comes from the `vams:roles` authorizer context value, which the authorizer (`common/auth/authorizerCore.py`) resolves from the user roles table with a 60-second per-user cache — so it is populated for every auth mode (Cognito, external OAuth IDP, API key), not only where a Cognito pre-token-generation trigger runs. It is informational for handlers and audit logs: `CasbinEnforcer` re-reads a user's roles from DynamoDB when building policy, so authorization does not depend on it.
-   **MFA-aware**: roles with `mfaRequired=True` are only active when `mfaEnabled=True` in claims.
-   **Object annotation**: set `item['object__type']` before every `enforce()` call.
-   **Policy lines as data.** `CasbinEnforcer.effective_object_policies(object_type, action)` returns the caller's applicable lines (`effect`, usable `criteriaAnd`/`criteriaOr`) built by the same `_usable_criteria` filter as the policy text, or `None` when the enforcer was not built from them (shared-cache hit, failed build, unloadable text). Search turns them into OpenSearch filters with `common/searchAuthzFilter.py`: an `upper` bound that fetches every readable document and a `lower` bound used for totals and aggregations that cannot be exact, so counts never include a record the caller cannot open. The per-hit `enforce()` stays authoritative. A new operator, constraint field, or change to a rule's regex shape must be mirrored in `searchAuthzFilter.py`; `tests/handlers/authz/test_search_authz_filter.py` compares both bounds with `enforce()` over every field, operator and value shape and fails otherwise. A future search mode (for example vector search) reuses `build_search_authz_filters` / `apply_search_authz_filter` in `handlers/search/search.py` instead of computing counts from unfiltered results.
-   **Search fills the page.** `_search_until_page_full` in `handlers/search/search.py` wraps every dual-index search (POST and simple search): while fewer than `from + size` hits pass the per-hit check and OpenSearch holds more candidates, it fetches a window up to `SEARCH_FILL_GROWTH_FACTOR` times larger, bounded by `OPENSEARCH_MAX_RESULT_WINDOW` per index and `SEARCH_FILL_MAX_SECONDS`; the window count is not a bound (each window is at least twice the last, so the loop ends within a few fetches), so small pages reach candidates ranked anywhere below the per-index cap. The time bound covers the fetches and every per-hit check (`DualIndexResponseProcessor.decide_in_order` stops at the deadline; `_affordable_window` sizes the next window from the measured fetch and check time per hit); a search stopped by a bound answers with the hits checked so far and reports `relation: gte`. For a search over both indexes only the merged prefix in true order is answered (`_ordered_prefix_length`: cut at the earliest last hit of an index whose window was full), so pages neither skip nor repeat records. Larger windows drop `aggs` and keep the first window's aggregations; per-hit decisions are cached per request in `DualIndexResponseProcessor._decide`. Both search handlers run inside `deferred_authorization_audit()`, so the refused-hit audit entries are written in batches when the search ends. A new search mode that post-filters hits routes through it instead of slicing one window.
-   Valid object types (`ALLOWED_CONSTRAINT_OBJECT_TYPES` in `common/constants.py`, and the only values `GET /auth/constraints/permissionObjects` offers): `database`, `asset`, `api`, `web`, `tag`, `tagType`, `role`, `userRole`, `pipeline`, `workflow`, `metadataSchema`, plus any type the add-on or fork constants lane registers (see "Fork Extension Points"). Anything else is rejected at constraint create/update with a `400`. There is no `apiKey` object type — API key access is governed at Tier 1 through `api` route constraints on `/auth/api-keys*` and `/auth/user/api-keys*`.

### System User (`SYSTEM_USER`)

`SYSTEM_USER` is the **only** valid user ID for system-process actions — never `SYSTEM`, `system`, or any other variant. It is seeded into the user and user-roles tables during CDK deployment and assigned to the `admin` role, so actions attributed to it pass Casbin authorization. Use it for: Lambda cross-calls (`{'lambdaCrossCall': {'userName': 'SYSTEM_USER'}}` — also the default in `request_to_claims()` when `userName` is omitted); username fallbacks (`claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]`); provenance/audit values (`createdBy`, `modifiedBy`, `changeUserId` fallbacks); and identity comparisons (`skip_schema_validation = (username == "SYSTEM_USER")` in `metadataService.py`, the pipeline-execution bypass in `processWorkflowExecutionOutput.py`). Off-schema keys a pipeline stores this way pass `restrictMetadataOutsideSchemas` on later user writes: every enforcing write path hands `validate_metadata_keys_against_schema` the record's keys as stored before the write (`stored_keys`), so only a key new to the record and absent from the schema is refused. Value type and controlled-list checks follow the same rule: every user write path passes `validate_metadata_against_schema` `value_check_keys=keys_set_by_write(incoming, stored)`, so a stored value the schema refuses blocks no write that leaves it unchanged, while required-field and dependsOn checks still read the merged record.

Handlers compare against this exact string, so a mismatched variant silently fails the comparison (or attributes records to a user ID with no admin role). IAM permissions on direct Lambda invocation are the security boundary for who can inject a `lambdaCrossCall` event.

### Internal Actions (Direct Invokes No API Route Reaches)

A handler that serves another VAMS Lambda an operation with no API route dispatches it from a top-level event key before any API handling: `workflowService` serves `vamsInternalAction` (`refreshPipelineReferences`, `redeployWorkflow`; names and the event builder in `common/workflows/workflowRecords.py`). The action runs only when the event is a `SYSTEM_USER` `lambdaCrossCall` with **no** `requestContext` — API Gateway builds every proxy event with a `requestContext` and cannot add top-level keys, so no route reaches it — and its payload ids pass the `ID` validator. Derive the targets server-side from the payload's own entity (the refresh takes a pipeline and finds its workflows) rather than accepting a target list, and grant callers `lambda:InvokeFunction` on the handler's function ARN only.

The same top-level-envelope rule carries the system-record exemption. `common/workflows/systemRecords.is_schema_import_call` is true only for a `lambdaCrossCall` whose `userName` is `SYSTEM_USER` **and** whose `source` is `vamsSchemaImport`, which only `importGlobalPipelineWorkflow` sends; that caller alone may set `isSystem` (a JSON boolean, read from the raw body because the request models ignore the key) or change a system record. The four guarded services (`pipelineService`, `pipelineTemplateService`, `workflowService`, `workflowTriggerService`) run the guard after Tier-2 authorization and before any write, refuse with a `400` that names the field or action, and refuse a request that mixes an allowed change with a refused one as a whole. Because the exemption rests on who can invoke these functions directly, a new `lambda:InvokeFunction` grant on any of them, or on the import function, widens who can register or change system records.

### System Pipeline Runs (Server-Only Trigger and Context; No Terminal Callback)

A system feature launches its pipeline through `executeWorkflow` with the same envelope rule, in `common/workflows/systemPipelines.py` (no import-time AWS dependency; the core never imports, names or calls the feature):

-   **Server-only trigger and context.** `resolve_system_execution` reads only a `lambdaCrossCall` with `userName` `SYSTEM_USER` **and** `source` `vamsSystemPipeline`, plus the top-level `systemExecution` object (`{"triggerType": "<system trigger value>", "context": {...}}`). It returns the trigger value unchanged and the serialized context (a JSON object, at most `MAX_SYSTEM_EXECUTION_CONTEXT_BYTES` = 4096), raises `SystemExecutionError` (answered `400` with a fixed message before any read) for a malformed marked call, and returns `None` for every other event, whatever its body says.
-   **Generic trigger value contract.** The launching feature picks its own value; core keeps no list of them. `is_system_trigger` accepts `SYSTEM_TRIGGER_PREFIX` (`System-`) followed by ASCII alphanumeric words joined by single hyphens, at most `MAX_SYSTEM_TRIGGER_LENGTH` (64) characters (for example `System-MyFeature`), and the value is stored as given (request and stored spellings are the same). `models.executions.WorkflowExecutionRecord` accepts `Manual`, `File-Upload` or a value in that format; `EXECUTE_TRIGGER_TYPES` never includes one, so a public request naming a system value is refused by the request model. Anything that must tell system runs apart (re-run refusal, a list filter) keys on `is_system_trigger`, never on a named value.
-   **Where the context goes.** Pipeline 1's manifest (`systemExecutionContext`) and the main row (`systemExecutionContext`, serialized text), only when set, so every other run's manifest and row are unchanged. Never `systemConfig` / `effectiveSystemConfig`, never the Step Functions input (the workflow Lambdas log it), never a list or details projection, never a re-run.
-   **No terminal callback.** A feature that runs a workflow tracks it by storing the `workflowExecutionId` on its own record and reading the run status from the existing execution tracking (execution rows, read-time reconcile, the status-change rule) when its own record is read; it does not register a callback with the workflow core. Core workflow code never names or calls a specific pipeline. Clean up a feature's leftover artefacts with S3 lifecycle rules (e.g. abort incomplete multipart uploads) rather than a terminal hook. Every terminal writer (end state, error handler, abort, group abort, list and details reconcile, the status change push) writes a system run's main row exactly as it writes an ordinary run's, and none of them invokes another Lambda for it.
-   **Re-run refused.** `rerun_execution` answers `400` (`SYSTEM_RERUN_REFUSED_MESSAGE`) for a system trigger, after the visibility check so a caller who cannot see the run gets `403`.
-   **Status change push.** `executionService.lambda_handler` first checks `is_sfn_status_change_event` (top-level `source` `aws.states` + `detail-type` `Step Functions Execution Status Change`, which no API Gateway event carries) and hands it to `handle_sfn_status_change` **before** the request log line, since the event detail carries the execution's input and output. The handler ignores a non-terminal status, a `detail.name` that is not an execution id, a missing main row, and a row whose `workflow_execution_arn` differs from `detail.executionArn` (the `vams-` state machine prefix is shared by every deployment in the account and Region). Otherwise it runs `_reconcile_main_status(force=True)`: the details reconcile with the poll interval skipped, so the status comes from its own `DescribeExecution` under the shared terminal guard. It applies to every run (same write the next read would make; releases locks of a run stopped outside VAMS at once). The permanent-delete route set ignores the event. Unlike the read paths, the push passes `raise_on_failure=True`: a failed main-row read, `DescribeExecution` or guarded write raises so Lambda's asynchronous retries redeliver the event; a lost guard (`ConditionalCheckFailed`) never raises. Do not add a broad `try/except` around the push.
-   **Trust boundary.** `lambda:InvokeFunction` on `executeWorkflowV2` is the right to launch system runs (any holder can send the marked cross-call with any well-formed system trigger value). Holders today: `executionService` (re-run) and `workflowTriggerDispatch`, neither of which sends the marker. Review any new invoke grant on it as a system-run grant.

Pinned by `tests/handlers/workflows/test_system_pipeline_hooks.py` (real guarded writes against moto for every writer and race, plus a source scan that fails when `common/workflows`, `handlers/workflows` or `infra/lib/lambdaBuilder/workflowFunctions.ts` names a feature-specific trigger or the removed callback registry).

### Workflow State Machine Deployment

A workflow's AWS Step Functions definition encodes each referenced pipeline's execution settings, so it must be rebuilt when any of them changes. The contract:

-   **One deploy routine.** Create, update, the pipeline-change refresh and the launch-time check all deploy through `workflowService._deploy_fields`, which calls `workflowAsl.deploy_state_machine` and returns the attributes written together: `workflow_arn`, `workflowVersionArn` (every deploy publishes a version; older ones past `MAX_RETAINED_STATE_MACHINE_VERSIONS` are deleted), `jobNames` (reused while the steps are unchanged, so output prefixes do not move), `pipelineFingerprints` (`{"pipelineDatabaseId:pipelineId": sha256}` of `workflowAsl.to_asl_pipeline_dict` minus `name`) and `deploymentRevision`. The refresh writes conditionally on the revision it read.
-   **Everything the ASL builders read from a pipeline record goes through `to_asl_pipeline_dict`.** That dict is what is fingerprinted; a setting read around it would change the definition without changing the fingerprint, and no workflow would be redeployed for it.
-   **A pipeline change refreshes its workflows.** `pipelineService.update_pipeline` compares fingerprints before and after the save and, on a difference, invokes `refreshPipelineReferences` asynchronously; the workflow service walks every non-archived referencing workflow to exhaustion. A failed redeploy sets `deploymentStale` + `deploymentStaleReason` on the workflow.
-   **The redeploy path reads strongly consistent.** `workflowService.redeploy_workflow` (behind both `refreshPipelineReferences` and `redeployWorkflow`) reads the workflow and each pipeline with `ConsistentRead=True` (the `consistent_read` parameter of `get_workflow_item`, `_get_pipeline_record` and `_resolve_snapshot_pipeline_records`). The refresh is invoked right after the pipeline write, and the re-read after a lost conditional write must see the other writer's deployment; an eventually consistent read can return the previous row in either place, so the walk finds every workflow "current" and redeploys nothing. API reads keep the default.
-   **A launch never runs a stale definition.** `executeWorkflow._ensure_current_deployment` compares the stored fingerprints with the live pipeline records (a record without a version or fingerprints never matches), invokes `redeployWorkflow` synchronously on a mismatch, and starts on `workflowVersionArn`; when the deployment cannot be made current it returns `409` and starts nothing. The main execution row records `workflowVersionArn`.
-   **Settings read at launch need no redeploy.** `systemConfig` (filters, arity, metadata gates, viewer suffix), templates, and enabled/archived state reach each run through the Step Functions input or the execute handler's own reads; keep it that way rather than baking them into the ASL.

### Large Object Copy Engine

Workflow outputs over 1 GiB (`common/largeObjectCopy.py` `LARGE_FILE_THRESHOLD_BYTES`, the one value `uploadFile.py` and the output step read) and API uploads over `API_UPLOAD_ENGINE_THRESHOLD_BYTES` (5 GiB; the engine values `MIN_PART_SIZE_BYTES`, `PARTS_PER_BATCH`, `THREADS_PER_BATCH`, `MAX_CONCURRENT_BATCHES` and `MAX_OUTPUT_INGESTION_WAIT_HOURS` are fixed constants in the same module, not deployment configuration or environment variables; `MAX_CONCURRENT_BATCHES` mirrors the construct's fixed Map `MaxConcurrency`, pinned by the infra test) are copied into their asset by the large object copy engine: the `LargeObjectCopy-<coreStackName>` Standard state machine (`infra/lib/lambdaBuilder/assetFunctions.ts`) running `handlers/assets/largeObjectCopy.py` (prepare / copyBatch / complete / fail / notify / reconcile). The batches run in child executions of `LargeObjectCopyParts-<coreStackName>` (`startExecution.sync:2`, `BATCHES_PER_CHUNK` = 50 per child, one child at a time) so no execution's history nears the 25,000-event quota; the arithmetic is in the construct and `infra/test/workflows/largeObjectCopyEngine.test.ts` recomputes it. Prepare returns `chunks` and still `batches` (an execution on the previous definition maps them inline). A refusal inside a child is recorded on the job row (`batchError`) so `fail` keeps the precise reason, and `fail` returns `refused`, which ends the execution in `LargeObjectCopyRefused` (Succeed) so the failed-copy alarm counts only breakage. A copy execution that ends outside its states (FAILED / TIMED_OUT / ABORTED other than the cancel path's `STOP_ERROR_CANCELLED` stop) is finalized by `reconcile`: a status change rule on the exact copy state machine ARN, and the execution details read (`_reconcile_ended_copies`, at most `MAX_RECONCILE_CHECKS_PER_READ` describes, async invoke) as the backstop, which a run claims at most once per `COPY_RECONCILE_CHECK_INTERVAL_SECONDS` with a conditional `lastCopyReconcileCheckDate` stamp on its main row; `notify` counts the job once. **The job status is monotonic:** every status writer conditions on the job being open (`OPEN_JOB_CONDITION`; `finalize_job` stamps `finalizedAt` once), because StopExecution does not stop a Lambda already running, so a Prepare or Complete of a stopped execution can outlive the reconcile. Prepare records its upload only while open (else aborts it and stops), Complete checks the job first and finalizes SUCCEEDED conditionally, `fail` keeps a stored outcome. One truth when the reconcile or the cancel path races Complete: a landed object wins. Both abort the multipart upload and then decide by the landed object WHATEVER the abort answered: S3 refuses a Complete that starts after an abort, but answers 204 (not NoSuchUpload) to an abort sent during or after a Complete that succeeds, and the object stays (#1123 D1; moto answers NoSuchUpload there, so tests use `_S3Abort204` in `tests/handlers/assets/test_largeObjectCopy_abort_204.py`). A Complete still in flight when a remover decided "not landed" supersedes the remover's FAILED / CANCELLED once with its own object (`loc.supersede_with_landed`: SUCCEEDED, `supersededStatus` keeps the overridden outcome, the rejection's `error` and `auditedAt` are removed so `_finish` writes the completion entry; the upload outcome is overwritten with `over_failed`). This is the one exception to the monotonic status. "Landed" is decided ONLY by the object this job's upload created, never by size or presence (an earlier object of the same size can sit at the key): Prepare records a fresh `destUploadToken` on the row in the same conditional write as the upload id and stamps it as object metadata (`s3MetadataKeys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY`, system-owned) on CreateMultipartUpload; Complete uses the VersionId CompleteMultipartUpload returned, and when it did not see that response (NoSuchUpload) it, the reconcile and the cancel path use `landed_object_version` (the key's current object carries the row's token; works on unversioned buckets; no token = not landed). The work after a terminal status (`_finish`) is safe to repeat: aborts and deletes are idempotent, the audit entry (`auditedAt`) and the asset type + email (`landedEffectsAt`) are claimed once on the job row, and the upload outcome (`uploadStatus.record_async_file_outcome`, monotonic PENDING -> one outcome, except `over_failed` for a superseded job) raises with `raise_errors=True` so a retry records it. The large-file queue consumer does the same with `finish_stored_file`: its audit entry is claimed on the file's upload-record entry (`claim_async_file_audit`), the outcome is written last and a failed write returns the message, and a redelivery that finds the file stored writes whatever is missing unless the engine may own the file (`copy_job_state`). Progress is the destination multipart upload (`ListParts` before each batch, so a retried task resumes); metadata, content headers and the `bucket-owner-full-control` ACL are set once on `CreateMultipartUpload`, so large outputs skip the provenance self-copy. State is `LargeObjectCopyJobsStorageTable` (job rows `{scope}#{hash}`, batch rows `batch#{executionId}`, index `WorkflowExecutionIdIndex`, TTL `expiresAt`). The output step (`processWorkflowExecutionOutput.py`) has three modes: `ingest` hands large files to jobs through `complete_external_upload` (`ingestionBatchId` = the execution id), defers file-level metadata targeting them, saves its state to the run bucket and leaves the row RUNNING with `executionPhase = INGESTING_OUTPUTS`; `await` (ASL v2 `AwaitOutputIngestion`, task token, timeout `MAX_OUTPUT_INGESTION_WAIT_HOURS`) registers the token; `finalize` records the outcome, naming each failed file. Exactly one caller reports a finished batch (`claim_notification`). A run on an ASL v1 definition is finalized by the engine (`legacy` batch) and the read-path reconcile holds it (`ingestion_hold`). Abort, group abort and the error handler call `cancel_execution_copy_jobs` (StopExecution + AbortMultipartUpload). **A stop leaves no part behind (#1123 D5):** StopExecution does not stop a running CopyBatch task, and S3 keeps a part whose UploadPartCopy was in flight during an abort, so every stopper (the cancel path, the reconcile of a console stop) first records `stopRequestedAt` on the open job (`loc.request_stop`, no status change), then aborts; each CopyBatch task reads the job before and after every part (`loc.copy_should_stop`), copies nothing further once stopped or finalized, and aborts again after its own last copy; `_finish` (FAILED / CANCELLED) and the reconcile of a CANCELLED job abort until ListParts answers NoSuchUpload (`loc.abort_until_gone`, at most `ABORT_SWEEP_ATTEMPTS`, waiting only while parts are still listed); never for a SUCCEEDED job. Tests use `_LateS3` (`tests/handlers/assets/test_largeObjectCopy_late_parts.py`), since moto removes the upload at once. **No immediate answer is final (#1123 D5, demo21 recheck):** S3 answered an abort, and ListParts right after it answered NoSuchUpload, yet the upload was listed again 2-8 minutes later with a part accepted before the stop. So the cancel path (every caller of `cancel_execution_copy_jobs`, the abort route included), `_finish` (FAILED / CANCELLED) and the reconcile of a CANCELLED job also start the job's **delayed sweep** (`loc.schedule_delayed_sweep`): an execution of the copy state machine itself whose input is `{"sweep": {"jobId"}}` (name `locsweep-<hash>`, same input from every scheduler, so one per job; `ExecutionAlreadyExists` counts as scheduled). Its sweep branch (`LargeObjectCopyCopyOrSweep` Choice -> Wait `SWEEP_FIRST_WAIT_SECONDS` 300 -> `sweep` -> Wait `SWEEP_SECOND_WAIT_SECONDS` 660 -> `sweep` final -> `LargeObjectCopySwept`) runs the engine's `sweep` action (`loc.sweep_recorded_upload`): reads the row, skips a SUCCEEDED job or an open one without `stopRequestedAt`, decides by the landed object first (raises when it cannot check, so the pass is retried), lists `ListMultipartUploads` with the exact key as prefix and aborts only the row's `uploadIdS3` when listed; never another writer's upload, never an object. The reconcile skips a sweep execution that ended unfinished. Tests: `tests/handlers/assets/test_largeObjectCopy_delayed_sweep.py` (`_GhostS3`). The job row carries `destRegion`, which the cancel path registers before its abort (`register_bucket_region`), and every stopper's coordination policy may query the asset buckets table's `bucketNameGSI` for a row without it (#1123 D6). The stoppers hold no s3:GetObject on the asset buckets, so a job whose upload is already gone and whose object they cannot read is left open, and the engine's reconcile of that stop (`_reconcile_cancelled_stop`: the cancel path's decision, plus the post-landing work for a landed job that the stopped Complete may never have done) decides it. That job is left open only when this call's StopExecution succeeded (`stopped`); with no stop made (no execution ARN, `ExecutionDoesNotExist`) no event comes, so it is written CANCELLED. **Every stop the cancel path left open, or finalized SUCCEEDED itself, is handed to the engine directly** (`request_reconcile`: async `{"action": "reconcile", "executionArn"}` invoke, best-effort, never fails the cancel) next to the stop's status change event, because every caller (abort, group abort, error handler, status change handler, output step of an ended run) acts on an ending run and the details read never checks an ended run's copies. Callers pass `lambda_client` + `loc.engine_function_name()` (env `LARGE_OBJECT_COPY_FUNCTION_NAME`). **The output step holds no copy grant (#1123, 2026-11-10):** it does its batch record, task-token registration, outcome read and cancel through the engine (`loc.call_engine`, a synchronous invoke of the engine's `outputStep` action, `largeObjectCopy.output_step`, each operation safe to repeat; a refused or failed call raises `EngineCallFailed`, so the step fails rather than report a run done; `AccessDeniedException` and an engine name not yet readable are retried for at most `ENGINE_CALL_DEPLOY_WAIT_SECONDS` while the deployment applies; a throttle (`TRANSIENT_ERROR_CODES` / 429, also as the engine's errorType) is retried with jittered backoff within the invocation's remaining time minus `INVOCATION_SAFETY_MARGIN_MS`, then raises `LargeObjectCopyEngineBusy`, which the ASL retries on process-output, the wait and finalize (`ENGINE_BUSY_RETRIER`), so a throttle never fails a run; `start_copy_job` and `cancel_execution_copy_jobs` take `max_wait_seconds` and wait out throttles and the deploy window via `call_with_transient_retry`, #1123 H1/L1). The task token is still answered only by `claim_notification` once every job of the batch is counted terminal (`tests/handlers/workflows/test_processOutput_engine_routed_completion.py`; output-step tests route through the real engine with `tests/copyEngineRouting.py`). It reads the engine name from the resource-name parameter `lambdaFunctions/largeObjectCopyEngine` named by `LARGE_OBJECT_COPY_FUNCTION_NAME_PARAMETER`, since an env reference would be a CloudFormation cycle with the engine. The reconcile is idempotent, so the hand-over and a late event finish the job once (`tests/handlers/assets/test_largeObjectCopy_cancel_handoff.py`). `ended_copy_executions` also returns an open job of a cancelled stop (`is_cancelled_stop`) for a run still RUNNING; `LargeObjectCopyReconcileDLQAlarm` watches the reconcile DLQ (remedy: re-invoke the reconcile with the message's execution ARN; the queue has no source queue to redrive to). A completion whose files are all accepted asynchronously answers `overallSuccess` true (`accepted_asynchronously`), and only files copied synchronously update the asset type (`landed_synchronously`). `ASL_SCHEMA_VERSION` is 2; `deployment_matches` also requires the stored `aslSchemaVersion`, so a launch redeploys an older definition first.

### Workflow Execution Locks

A workflow's `perAsset`, `perInputFile` and `perInputFileVersion` concurrency restrictions are lock rows in `WorkflowExecutionLocksStorageTable` (PK `lockKey`, TTL `expiresAt`, holder index `WorkflowExecutionLocksByHolderGSI` on `workflowExecutionId`), one row per distinct asset, file or file version a launch selects. `common/workflows/executionLocks.py` holds the contract:

-   **Acquire.** `executeWorkflow` alone takes locks (`acquire_locks`): conditional `PutItem`s in sorted key order, all or none, immediately before the launch's first side effect. A conflict answers `400` with the scope's fixed message; the contested key and its holder go to the service log only. A conflicting row whose holder has no main execution row and whose `acquiredAt` is older than the function's `EXECUTE_WORKFLOW_TIMEOUT_SECONDS` plus `LOCK_TAKEOVER_MARGIN_SECONDS` is taken over with a put conditional on that same holder.
-   **Release.** The end-state step, the error handler, the abort route and the executions read path (for a run stopped outside VAMS) call `release_locks_for_execution`, which deletes the union of the keys rebuilt from the execution's input rows and the rows the holder index lists, each delete conditional on the holder, and never raises. A launch that fails after acquiring releases from memory with `release_locks`.
-   **Only a run that has stopped releases.** The abort and a failed launch release only when `StopExecution` succeeded or `sfn_execution_has_ended` shows the execution ended; otherwise the rows wait for a terminal step or the TTL (the sum of the pipelines' timeouts, at least a day, plus 30 minutes).
-   **Grants** sit in each lock handler's own `ExecutionLockPolicy` (`attachExecutionLockPolicy` in `infra/lib/lambdaBuilder/workflowFunctions.ts`), never its role's default policy.
-   **Skip records.** A conflict on a `fileUpload` trigger launch (`triggerType` File-Upload, executing user `SYSTEM_USER`) also writes a `SKIPPED` execution (`executeWorkflow._record_skipped_launch`), best-effort and with the same `400`; a manual or API launch writes nothing. The id is `er.skip_record_execution_id(lockKey, holder)` (uuid5), so every refusal by one holder on one key lands on one record. Rows are written in this order, so the main row (which lists and details read) exists only once its authorization rows do: (1) the configuration row (`build_workflow_configuration_record` with the trigger's output target and `pipelineExecutionParameters`, the trigger's template parameters a re-run replays), a put conditional on `attribute_not_exists`, whose existing item supplies the first-skip date to later refusals; (2) the workflow-input rows (`er.build_workflow_execution_input_record`, never pinned, dated with that first-skip date) while the main row's `skippedLaunchCount` is below `MAX_SKIP_RECORD_INPUT_ROWS` (100); (3) the main row, `er.build_skip_record_update`: one `UpdateItem` that sets the identity fields `if_not_exists`, overwrites `lastSkippedDate` and `ADD`s one to `skippedLaunchCount`. A failure in (1) or (2) writes no main row. No pipeline or lock row is written, and a conflict with no known holder records nothing. `SKIPPED` is in both `TERMINAL_STATUSES` tuples, so no reconcile or terminal writer rewrites it. Reads: the lists add `er.skip_list_fields` (count, scope, reason; never the holder) to SKIPPED rows only; the details view adds `skip` (`skip_details_block`), whose `blockingExecutionId` is set only when `_caller_may_read_execution` passes the same Tier-2 GET check on the holder (missing and denied look the same); the logs route answers `SKIPPED_EXECUTION_LOG_MESSAGE` without reading CloudWatch; a single abort answers `400` `SKIPPED_EXECUTION_ABORT_MESSAGE` after authorization and writes nothing, and a group abort reports the member as `skipped-terminal`; re-run uses the configuration row's `pipelineExecutionParameters` when the record has no pipeline rows. Both list routes take `excludeStatus` (`models.executions.parse_list_status_filter`: known statuses only, no conflict with `status`, fixed 400s): the global list adds it to the FilterExpression (no signal beyond the `status` filter); the asset list drops a candidate only after it has passed the per-execution read check (`_authorize_candidate`, memoized and shared with the authorization pass), so a candidate the caller may not read is handled exactly as without the parameter; at most `MAX_EXECUTIONS_INSPECTED` drops per page (a spent budget stops with a cursor and a warning), re-applied after reconcile. The execute role's main-table `GetItem`/`UpdateItem` and configuration/input `PutItem` cover the writer (`test_execute_workflow_dynamodb_calls.py`).

### System Workflow Runs in the Execution Lists

`executeWorkflow._persist_execution_records` copies the workflow row's stored `isSystem` onto the execution main row (`build_workflow_execution_record(is_system=)`; only a boolean `True` marks a run). Both list routes in `executionService` hide those rows unless `includeSystem` applies (`INCLUDE_SYSTEM_PARAM`, the name `vamscli.constants.EXECUTION_LIST_INCLUDE_SYSTEM_PARAM` mirrors and a CLI contract test pins):

-   **Default.** `_resolve_include_system`: an explicit `true`/`false` wins (anything else is `400`); otherwise hidden, except a listing scoped to ONE workflow (both halves of its key) that is a system workflow the caller may GET. The definition read is the per-enforcer memo the row authorization uses. The applied value is echoed as `includeSystem`.
-   **Fail-visible.** `er.execution_row_is_system` is the one reader: a missing attribute (rows written before #1121, no migration) or any non-boolean reads as a user run. The global list's `FilterExpression` term is `attribute_not_exists(isSystem) OR isSystem <> true` for the same reason, and `_global_list_matches_filters` (`excludeSystem`) is the safety net.
-   **Paging.** A hidden row is a fully evaluated row: on the global list it advances the cursor like any filtered row, and on the asset list it is a post-filter after `build_execution_items`, so short or empty pages with a `NextToken` are possible there, as with the other filters. Token format unchanged.
-   **Not authorization.** It only chooses which rows a page includes; every row still passes `_execution_access_check`. Details, logs, abort, group abort and re-run do not read it.

### Download as Zip (Prepared Downloads)

`handlers/assets/zipDownloadService.py` serves `ZIP_DOWNLOAD_ROUTES` (create, per-asset and global list, get, cancel/delete, presigned URL), and in the same function the delayed sweep's passes. `lambda_handler` dispatches on the event envelope, never the body: an event with `requestContext` (every API Gateway event has one, and API Gateway cannot add top-level keys) always takes the API path (`handle_api_request`, Tier 1 then Tier 2); without one, a top-level `action` `sweep` goes to `handle_sweep`; any other shape (a terminal callback payload included) is logged and ignored with no read or write (`tests/handlers/zipDownloads/test_zipDownloadService_event_dispatch.py`). Only the sweep state machine invokes it directly. **No terminal callback:** the zip download service is not registered in the system-pipeline callback registry, and a run sends its job nothing when it ends. The job follows its run by the `workflowExecutionId` stored at launch, and the read-time reconcile (`_reconcile`, run by get, both lists, the URL route and the cap check) finalizes it from the run's main execution row on the next read (`tests/handlers/zipDownloads/test_zipDownloadService_run_end_reconcile.py`). An incomplete upload no reader or sweep aborted is removed by the `vams.preparedDownloads/` lifecycle rule (`AbortIncompleteMultipartUpload` after one day). The routes and `ZipDownloadRequestsStorageTable` deploy always; with `ZIPDOWNLOADS_ENABLED` false every route answers 404 `Zip downloads are not enabled for this deployment`, checked after Tier 1. The rules:

-   **Ownership.** Jobs are keyed `userId` (the authenticated caller, never a request value) + `requestId` (`common/zipDownloads.new_request_id`, time-sortable); `UserAssetIndex` (`userAssetKey` = `userId#databaseId#assetId`) serves the asset list. Another user's job, or one named under another asset's path, answers the same 404 as a missing one. Every such miss on get, URL and delete is audited as `zipDownloadNotFound` with the operation (`_owned_row`). Every route also re-checks Tier-2 asset `GET`; a `startingToken` must name the caller's own partition.
-   **Refuse the whole job.** Create refuses on any archived (`vams-status`, GLACIER, DEEP_ARCHIVE), delete-marker or blocklisted file in the resolved selection, through `common/downloadEligibility.py`. Folder and whole-asset entries are pre-scanned within a budget (`ZIP_DOWNLOAD_PRESCAN_MAX_OBJECTS` / `_SECONDS`); the zipArchive container checks every member again before writing, so run time enforces what the budget left. An archived file (its current version is a delete marker, which S3 answers `404` with `x-amz-delete-marker: true`, or `vams-status` archived) is refused with `zd.MSG_ARCHIVED_IN_SELECTION`, the web dialog's words, never `File not found` and never the caller's key.
-   **Version pinning.** Per-file `versionId`, or one `assetVersionId` / `assetVersionIdAlias` resolved through `handlers/assets/assetVersions.py` into the manifest's `pinnedFiles`. The resolvers never echo the requested id or alias (#1134).
-   **Launch.** A `SYSTEM_USER` cross-call to the execute function carrying the system-pipeline marker and `systemExecution` (trigger `zd.ZIP_SYSTEM_TRIGGER` `System-ZipDownload`, the zip's own value, which core checks for format only and stores as given; nothing in the zip feature reads it back, so runs recorded under the earlier `Zip-Download` still reconcile by their stored `workflowExecutionId`; context `{v, requestId, userId, databaseId, assetId, manifestKey}`); the request manifest at `vams.preparedDownloads/<db>/<asset>/<userHash>/<requestId>/manifest.json` (auxiliary bucket) is the container's authority. The execute handler answers `{"message": {"executionId": ...}}` (the envelope the re-run also reads), so `_launch` reads the id from `message`. Every cross-call here (launch, abort, details read) is a synchronous invoke through `lambda_client` built on `invoke_config` (no botocore retry, 910 s read timeout above the invoked functions' 900 s timeout) and `_invoke_request_response`, which retries only a `TooManyRequestsException`: a repeated launch would start a second run the job row never records (#1205 rule; `tests/handlers/zipDownloads/test_zip_cross_call_client.py`). Do not give this module a retrying Lambda client. The zipArchive workflow is `GLOBAL/utility-zip-archive`, so every cross-call is tested end to end for GLOBAL with the real receiving handler, never a hand-built answer: launch, abort and details read, each followed by the real zip download service's read-time reconcile (`tests/handlers/zipDownloads/test_zip_global_cross_calls_end_to_end.py`), and a mode 2 archive through the output step's write-back into the real `uploadFile` (`tests/handlers/workflows/test_zip_archive_write_back_end_to_end.py`). Those tests read the zipArchive pipeline and workflow records from the SHIPPED bundle through the real importer and create models (`tests/handlers/zipDownloads/zipBundle.registered_records`), never a hand-written systemConfig; `test_zip_bundle_launch_matrix.py` launches every selection shape through the real execute handler (#1125 D9: a bundle workflow without `systemConfig` got the defaults and refused every folder, whole-asset and multi-file job). Not every 4xx from the execute handler means no run exists: `lambda_handler` also maps exceptions raised AFTER `start_execution` (a throttle on the record writes, the item-size `ValidationException`, a pydantic or `VAMSGeneralErrorResponse` error) to 4xx. `_classify_launch_answer` therefore reads "no run exists" only from answers the handler gives before the start: 403, 404, the fixed early-return texts in `PRE_START_REFUSAL_MESSAGES` (workflow disabled/archived/undeployed, lock conflict, ...), the `executionValidationErrors`/`templateResolutionErrors` envelopes (`ZipLaunchRefused`: create removes the job, row and working objects, and answers `400` `zd.MSG_LAUNCH_REFUSED`, the refusal kept for the service log only), and 409 stale deployment or `ExecutionLimitExceeded:` (`ZipLaunchRetryable`: retried ONCE, then the job is removed and create answers `503` `zd.MSG_LAUNCH_BUSY`). Every other answer (5xx, timeout, any other 4xx) is `ZipLaunchMayHaveStarted`: never retried, the row stays `FAILED` `MSG_LAUNCH_FAILED`, and create answers 500, or 503 `MSG_LAUNCH_BUSY` for a `ThrottlingException:`. The lists are pinned to `executeWorkflow.py` by `tests/handlers/zipDownloads/test_zip_launch_answers.py`: reword an early-return refusal there and update the list in the same change.
-   **Status.** `QUEUED` -> `RUNNING` -> `SUCCEEDED` / `FAILED` / `CANCELLED`; every terminal write is `common/zipDownloads.finalize_row` (conditional on a non-terminal status, and on the run id where known). The container writes progress and its own refusal reasons (`ZIPDOWNLOAD:`-prefixed). The read-time reconcile finalizes the rest with `finalize_ended_run` on the job's next read (a run that SUCCEEDED without the archive -> FAILED `The archive was not produced`; FAILED / TIMED_OUT the container did not report -> FAILED with its documented message); a run aborted from the executions views ends FAILED `Cancelled from workflow executions`. Raw execution errors are never shown. **Neither the cancel nor the reconcile waits on a status event.** The user's cancel records CANCELLED itself before it aborts the run. It first records the cancel (`zd.request_cancel`: `cancelRequestedAt`, `cancelRequestedBy` and the stop marker, before anything stops the run); from then on no writer records FAILED (`finalize_row(unless_cancel_requested=True)` in `finalize_ended_run` and the launch-failed reconcile, and the container's `StatusWriter.failed`, condition on the marker being absent) and `decide_finalization` records CANCELLED by the user for a job with the marker and no landed archive, so a reconcile of the run the cancel's own stop ended still ends the job CANCELLED (#1125 D22). A cancel never deletes a job: one that finished before the cancel was recorded keeps its outcome and the DELETE answers `action: ended` with its `status`; only a DELETE of an already finished job deletes it (`tests/handlers/zipDownloads/test_zip_cancel_reconcile_race.py`). The DELETE takes an additive `intent=cancel` (`DeleteZipDownloadQueryModel`): with it a job that is already terminal at read is never deleted (`ended`, or `cancelled` for a CANCELLED job), so a cancel confirmed after the job finished (an open dialog, the CLI's check then DELETE) keeps it; web, CLI and MCP cancels always send it, and without it the route is unchanged (#1125 D23). The container never finalizes a job on SIGTERM: `StatusWriter.stopped` records `stopReason` only, and the read-time reconcile decides from the run's status (ABORTED -> `Cancelled from workflow executions` with `cancelledBy` execution, TIMED_OUT -> timed out, FAILED -> the recorded stop). **A stop outside VAMS ends the run (#1125 D26):** on a SIGTERM no VAMS stopper recorded (no `stopRequestedAt`/`cancelRequestedAt`, job still open: a Batch terminate outside VAMS) the container answers its own task token with `ZipArchiveContainerStopped` after recording `stopReason`, so the run ends FAILED and the reconcile shows `zd.MSG_JOB_STOPPED`; it also heartbeats that token every 2 minutes and the `ZipArchiveBatchJob` state has a 30-minute heartbeat timeout, so a container killed without a SIGTERM ends the run with `States.HeartbeatTimeout`, which `decide_finalization` maps to `zd.MSG_JOB_LOST` `The archive job stopped responding` (matched by the fixed error names only, never showing the raw error). With a VAMS stop recorded nothing is sent, as before, whichever acts first; `finalize_ended_run` removes the working objects whenever the job ended without its archive, also when another writer's final write won (#1125 D24, `tests/handlers/zipDownloads/test_zip_cancel_intent_and_abort_reason.py`). The read-time reconcile decides from the run's main row. A run that ended outside VAMS reaches that row only through its Step Functions status change event or the execution service's details read, so when a job's row has been idle `RUN_CHECK_IDLE_SECONDS` and the main row is not terminal, the reconcile claims `runCheckedAt` (once per `RUN_CHECK_INTERVAL_SECONDS` per job, at most `RUN_CHECKS_PER_CALL` per list) and makes that details read as SYSTEM_USER (`zipDownloadService._check_run_ended`, the abort's grant), then decides from the re-read row. That is the backstop for a lost event: without it the job would hold a cap slot until it expires. An abort from the executions views writes `ABORTED` on the main row, which the next read of the job finds; a create at the per-user cap reconciles the caller's active jobs before it refuses (`_cap_reached`, same run-check budget), so a job whose run ended unseen frees its slot on the next create. **The status is monotonic and a landed archive wins with one outcome**, the rule the large object copy engine follows: StopExecution and a Batch terminate do not stop a container still writing, so `finalize_ended_run` (the read-time reconcile) and `cancel_job` (the user's cancel) abort the job's open multipart uploads BEFORE they decide (S3 then refuses a CompleteMultipartUpload the container sends after the abort) and record SUCCEEDED whenever the key's current object is the one THIS job's upload created, whatever the run's status and **whatever the abort answered**: S3 answers 204, not NoSuchUpload, to an abort sent during or after a Complete that succeeds, and the object stays (#1123 D1). A Complete already in flight can still land after a "not landed" decision; the container then loses its SUCCEEDED write and deletes that archive, and if it was killed during the Complete the archive is never served (only a SUCCEEDED job gets a URL) and goes with the job's delete or the retention rule. `tests/handlers/zipDownloads/test_zip_abort_204.py` drives every finalizer through its real handler with an S3 double that answers 204. "Landed" is identity, never presence or size (an earlier same-size object can sit at the key): the container records a fresh `archiveUploadToken` on the row in its RUNNING write, before the upload exists, and stamps it as object metadata (`ARCHIVE_UPLOAD_TOKEN_METADATA`, the same `common/s3MetadataKeys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY` the large object copy engine stamps its `destUploadToken` under) on CreateMultipartUpload; `landed_archive` compares the two with the helper the engine's `landed_object_version` also uses (`s3MetadataKeys.is_own_upload_object`; `tests/handlers/assets/test_upload_token_shared.py`), and the finalizers re-read the row after the abort. A row without a token has no landed archive. A cancel that finds the archive landed records SUCCEEDED and keeps the job (`action: ended`). The container never writes FAILED over a completed upload and deletes an archive that landed after the job was finalized without it (`backendPipelines/CLAUDE.md`, archive). `backendPipelines/utility/zipArchive/container/tests/test_terminal_races.py` drives the real container and these finalizers through every ordering. **No part is left behind (#1125 D16, the #1123 D5 rule):** S3 keeps a part whose UploadPart was in flight during an abort, so every stopper first records `stopRequestedAt` on the open job (`zd.request_stop`, no status change; the container's sink reads the job before each part and uploads nothing further), then `abort_open_uploads` aborts each upload until ListParts answers NoSuchUpload (`zd.abort_until_gone`, at most `ABORT_SWEEP_ATTEMPTS`, waiting only while it is still listed; the zip download service's role holds `s3:ListMultipartUploadParts` on `vams.preparedDownloads/*`). `tests/handlers/zipDownloads/test_zip_late_parts.py`. **No immediate answer is final (#1125 D16 round 2, the #1123 D5 demo21 recheck):** an upload whose abort answered, and whose ListParts then answered NoSuchUpload, can be listed again minutes later. So `_abort_then_reread` (cancel_job, finalize_ended_run) and `delete_job` (before the row is removed) also start the job's **delayed sweep** (`zd.schedule_delayed_sweep`, a no-op unless `ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN` is set, which happens only with the feature on; a job stopped without it logs a warning): an execution of `ZipDownloadSweep-<coreStackName>` (`assetFunctions.buildZipDownloadSweep`). It is named `zipsweep-<hash(userId#requestId)>`, so there is one per job and `ExecutionAlreadyExists` counts as scheduled. Its input `{"zipSweep": zd.sweep_request(row)}` carries the identity plus a snapshot of `archiveKey` and the recorded upload ids for a row deleted before the passes run. It runs Wait 300 -> service `{"action": "sweep", "final": false}` -> Wait 600 -> final. Each pass (`zd.sweep_recorded_uploads`) reads the row when it exists: it skips SUCCEEDED and running jobs without `stopRequestedAt`, checks `landed_archive` first, then lists the uploads with the exact archive key as prefix and aborts only the ids in `archiveUploadIds`, which the container records right after CreateMultipartUpload (`StatusWriter.record_upload`). Without the row it uses the snapshot, confined to the job's prefix. Tests: `tests/handlers/zipDownloads/test_zip_delayed_sweep.py` (`GhostS3`). **Auxiliary bucket Region (#1125 D17):** the service registers the auxiliary bucket (the stack's own) for the deployment Region at load (`register_bucket_region`), so the region-routing client never queries `bucketNameGSI` for it on the reconcile and sweep paths.
-   **Cleanup.** Cancel aborts the run through the execution service's abort (cross-call) and the open multipart uploads; delete removes every version under the job prefix and the row. A job that ends without an archive (cancel, failure, refused or failed launch, an abort from the executions views) has its working objects (`manifest.json`, `resolved-manifest.jsonl`, every version) deleted at once by `zd.delete_working_objects`, from `cancel_job` and `finalize_ended_run`; a SUCCEEDED job keeps them until delete or retention, and a resolved manifest the container writes after the job ended stays (never served) until delete or retention. An incomplete upload left by a writer that finalized the job itself (the container's own FAILED) is aborted by the lifecycle rule after a day; a read of a finalized job writes nothing. `common/zipDownloads` refuses any cleanup prefix that is not exactly one job's. Retention is the table TTL plus the `vams.preparedDownloads/` lifecycle rule, and reads hide rows past `expiresAt`.
-   **No reserved database id.** Other auxiliary objects live under `{databaseId}/`; the prefix's first segment `vams.preparedDownloads` has a dot, which a database id (`common/validators.id_pattern`, `^[-_a-zA-Z0-9]{3,63}$`) never has, so the lifecycle rule can never match a database's data and no id is reserved (a database named `preparedDownloads` is ordinary; `tests/handlers/databases/test_databaseReservedKeyword.py`). Keep any new top-level auxiliary prefix dotted for the same reason.

Environment: `ZIPDOWNLOADS_ENABLED`, `ZIP_DOWNLOAD_RETENTION_DAYS`, `ZIP_DOWNLOAD_MAX_FILES_PER_REQUEST`, `ZIP_DOWNLOAD_MAX_TOTAL_BYTES_PER_REQUEST`, `ZIP_DOWNLOAD_MAX_ACTIVE_PER_USER`, `ZIP_ARCHIVE_WORKFLOW_DATABASE_ID`, `ZIP_ARCHIVE_WORKFLOW_ID`, `EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME`, `EXECUTION_SERVICE_FUNCTION_NAME`, `PRESIGNED_URL_TIMEOUT_SECONDS`; the zip table via `ResourceKeys.ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE`.

### Workflow Execution Input Metadata: Opt-in Sections

`metadataInputs` carries the four metadata types (default ON) and two opt-in keys (default OFF): `assetFileMetadata` and `metadataSchema`. `METADATA_INPUT_DEFAULTS` and `METADATA_INPUT_OPT_IN_KEYS` in `common/workflows/executionRecords.py` define what an omitted key means; `models/pipelines._METADATA_INPUT_KEYS` and `executionValidation._METADATA_KEYS` list all six, and a new key goes into all three in the same change.

-   **Off means unchanged.** With both keys absent or `false` the grouped envelope is byte-identical to the one built before they existed (`tests/handlers/workflows/golden/`). Never default an opt-in key ON.
-   **Gathered only when some step asks.** `executeWorkflow._intake_metadata_inputs` turns an opt-in key on at intake only when the workflow enables it AND a step's effective gate (systemConfig + chosen template overrides) does. `narrow_metadata_envelope` drops the section per step on the step's own gate, and returns the same object when the envelope carries neither section.
-   **`assetFileMetadata`** (`_expand_asset_selections`): a `/` or folder selection is listed with `ListObjectsV2` (current objects only, so archived files never appear), skipping folder markers and `PREVIEW_FILE_PATTERN`; at most `MAX_ASSET_FILE_EXPANSION_PER_SELECTION` (500) per selection, explicit + expanded file records within `MAX_INPUT_FILES_PER_EXECUTION`, and the listing stops counting at `MAX_ASSET_FILE_EXPANSION_SCAN_KEYS`. Each file's reads go through `_fetch_file_metadata` with the request's identity (`_metadata_service_payload`) in the shared worker pool. Records carry `"expanded": true`; the group carries `fileExpansion`. Expanded rows sit last in the total-budget order and are recorded for the step that received the covering selection (`pipeline_metadata_envelope_rows`). The persisted input-metadata row stores `expanded: True` only on such a row (`build_input_metadata_record(expanded=)`), and the details rows report `expanded` on every row (`_scrub_input_metadata`, false when absent), which the CLI table marks `(expanded)` and the web execution page badges.
-   **`metadataSchema`** (`_build_metadata_schemas`): Tier-1 on `GET /metadataschema` for the request's identity, Tier-2 on a provisional schema object (no name, no entity type) in the database and in `GLOBAL`, then the per-schema GET the list endpoint filters by. A database left out for any reason gets one generic warning whose text does not depend on whether a schema exists. The schema records come from `metadataSchemaValidation.load_enabled_schemas` (uncached, so a per-caller filtered aggregate is never cached) and `aggregate_schema_field_catalog`, which resolves each file field per file-extension reach the way `get_aggregated_schemas` filters a single file (one entry per distinct reach; the `[]` entry covers every file no other entry lists), so a conflict is reported only for files that two differing schemas both reach; asset metadata is resolved over every schema (`by_file_extension=False`). The execute role's reads sit in their own `MetadataSchemaReadPolicy` (Query on the schema index, GetItem on the database table).
-   Every cut or omission is an execute warning; neither section ever fails a launch.

### Casbin Policy Model

Defined in `common/constants.py`. Request: `(sub, obj, act)`; policy line: `(sub, obj_rule, act, eft)`; role mapping: `g = _, _`; effect: `some(allow) && !some(deny)`; matcher: `g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act`.

Each criterion value is written into its line's `obj_rule` as source escapes, one `\xNN` / `\uNNNN` / `\UNNNNNNNN` per character (`CasbinEnforcerService._escape_rule_value`), never as plain text: Casbin's policy reader splits lines on commas outside brackets, and pycasbin rewrites `p.` / `r.` and `&&` / `||` / `!` throughout the rule text, string literals included, before compiling it. Only the object-type check every line opens with, `regexMatch(r.obj.object__type, '^<type>\Z')`, carries a registered type name as plain text (`_object_type_rule`). `tests/handlers/authz/test_constraint_value_as_written.py` and `test_constraint_value_escape_parity.py` hold this.

### Constraint Fields (`PERMISSION_CONSTRAINT_FIELDS`)

Fields that can be referenced in ABAC policy rules: `databaseId`, `assetName`, `assetType`, `tags`, `tagName`, `tagTypeName`, `roleName`, `userId`, `pipelineId`, `pipelineExecutionType`, `workflowId`, `category`, `name`, `metadataSchemaName`, `metadataSchemaEntityType`, `object__type`, `route__path`.

### Authorization Decisions and Permission-Check Endpoints

Every allow-or-deny decision is made in the API, by the authorizer and `CasbinEnforcer`. The web app may only show or hide a control based on a decision the API returned, such as the route listing from `GET /auth/routes/api/allowed`. The API still enforces each call. Never derive a permission in the client: no capability flag computed from roles, no constraint matching in the browser, and no trust in a role, database or attribute the client sends.

An endpoint that answers "may this user do X" (a permission check, as opposed to the operation itself) follows these practices, from the [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) plus the VAMS rules:

-   **Deny by default.** An unknown object type, an unknown route or a failed lookup returns "not allowed", never an error that hints why.
-   **No existence leaks.** "Doesn't exist" and "not allowed" give the same answer, so the endpoint can't be used to enumerate assets or databases.
-   **Generic reasons only.** The UI may get a reason code for its tooltip ("not permitted"). The response never includes policy details or the constraint that matched.
-   **The result is advisory.** The real call is still enforced on the server, so a stale answer can't grant anything. Cache the answer only briefly in the page, never across users.
-   **Bounded cost.** Use a single-item read per object (no scan or query fan-out, Rules 14-15), validate the ids with the existing validators, and rely on API Gateway throttling.
-   **Audit.** Record denials through the existing batched probe audit (`_DenialAuditBatch` in `handlers/auth/routes.py`, with its probe marker).
-   **Reachability.** Put the endpoint under `/auth/routes/...` (for example `POST /auth/routes/api/evaluate`), behind the custom authorizer. Every shipped template, `database-readonly` included, already grants `POST` on `/auth/routes`, so no template changes are needed.

The check identifies the user from the token, loads the object itself from the identifiers it is given, and evaluates the same Tier 1 and Tier 2 checks as the endpoint it answers for.

---

## Validators (`common/validators.py`)

### `validate()` Dispatcher

The `validate()` function is the standard way to validate inputs in both `@root_validator`
methods and handler code. Per-field entries take `value`, `validator` (from the table
below), and optional `optional: True` (skip when `None`/empty) and
`allowGlobalKeyword: True` (accept the literal `"GLOBAL"`):

```python
from common.validators import validate

(valid, message) = validate({
    'databaseId': {'value': database_id, 'validator': 'ID', 'allowGlobalKeyword': True},
    'assetId':    {'value': asset_id,    'validator': 'ASSET_ID'},
    'tags':       {'value': tag_list,    'validator': 'STRING_256_ARRAY', 'optional': True},
})
if not valid:
    raise ValueError(message)                                    # In @root_validator
    # OR
    return validation_error(body={'message': message}, event=event)  # In handler
```

**Any field that carries a workflow's or pipeline's database id must pass `allowGlobalKeyword: True`**
(the execute route's `workflowDatabaseId`, the execution and pipeline models' `databaseId`, and
`CompleteExternalUploadRequestModel.workflowDatabaseId`): every shipped pipeline registers a GLOBAL
workflow, so refusing the keyword breaks every customer run (#1123 D2). A new field carried on a
cross-call needs a test that sends it through the receiving handler's own model with `GLOBAL`
(e.g. `tests/handlers/workflows/test_processOutput_write_back_end_to_end.py`), not a model built by
hand with a database-scoped fixture.

The `validator` name must be one the dispatcher implements. `validate()` resolves the check from
`_VALIDATOR_DISPATCH` in `common/validators.py`, so **that mapping is the list of valid names** —
there is no second set to keep in step with it. A name with no entry has no rule, so it raises, which
surfaces as a `500`: it is a code defect, not caller input. Adding a validator means adding one entry
to the mapping, and nothing else.

The name is resolved **after** the empty/optional short-circuits, so an optional field left empty is
skipped before its validator name is consulted — naming a validator is not required to say "there is
nothing here to check". Each entry receives the field name and the whole spec, not just the value,
because a few checks read another key off it (`ASSET_PATH` reads `isFolder`).

### Available Validator Types

Scalar validators: `ID` (`^[-_a-zA-Z0-9]{3,63}$` — databaseId, pipelineId, etc.),
`ASSET_ID` (filename pattern, max 256 chars), `UUID`, `FILE_NAME`,
`OBJECT_NAME` (`^[a-zA-Z0-9\-._\s]{1,256}$` — assetName, dbName, etc.),
`EMAIL`, `USERID` (`^[\w\-\.\+\@]{3,256}$`), `REGEX`, `NUMBER`, `BOOL`,
`RELATIVE_FILE_PATH` (`^\/.*$`), `ASSET_PATH` (`^.+\/.+$`),
`ASSET_PATH_PIPELINE` (`^pipelines\/.+\/.+\/output\/.+\/$`),
`STRING_30`, `STRING_256`, `STRING_16384` (free-form caller text, e.g. `commentBody`), `STRING_JSON`,
`FILE_EXTENSION` (`^[\\.]([a-zA-Z0-9]){1,7}$`).

The file-key validators — `RELATIVE_FILE_PATH`, `RELATIVE_FILE_PATH_ARRAY`, `DOWNLOAD_KEY_ARRAY`,
`ASSET_PATH`, `ASSET_AUXILIARYPREVIEW_PATH` — also refuse a key whose **UTF-8 encoding** is over
`MAX_S3_OBJECT_KEY_BYTES` (1024, the Amazon S3 object-key limit), measured in bytes rather than
characters so a 350-character key of 3-byte characters is refused as S3 would refuse it. The validator
only sees the asset-relative part; the handlers that resolve a key against the asset prefix re-check the
RESOLVED key before their first S3 call and answer a generic `400` ("File key exceeds the maximum S3
object key length", the key is not echoed): the stream and download handlers (`streamAsset.py`,
`streamAuxiliaryPreviewAsset.py`, `downloadAsset.py`), upload initialization (`uploadFile.py`
`initialize_upload`, which checks the final AND the `temp-uploads/` key in its pre-loop validation block
so no multipart upload is created for any file of a refused batch), and `create_folder`, `move_file` and
`copy_file` in `assetFiles.py` (destination key). Every guard calls the one predicate
`common/validators.exceeds_s3_object_key_limit(key)` — do not re-implement the byte comparison in a
handler. An over-long key is therefore a `400` and never an S3 `ClientError` reported as a `500`
(issue #407). `ASSET_ID` keeps its separate 256-character cap.

Partition-aware AWS-resource validators (used by pipeline sub-process registration):
`ARN` (any AWS resource ARN), `CLOUDWATCH_LOG_GROUP_ARN`, `CLOUDWATCH_LOG_GROUP_NAME`
(1-512 chars, `-_./#`+alnum), `LOG_STREAM_NAME` (1-512 chars, no `:`/`*`),
plus `EVENTBRIDGE_BUS_ARN`, `EVENTBRIDGE_SOURCE`, `EVENTBRIDGE_DETAIL_TYPE`, `SQS_QUEUE_URL`.
Registration descriptors: `SFN_STATE_NAME` (1-80 printable chars, no control characters — an ASL
state name), `DISPLAY_LABEL` (same class, 1-128), `LOG_SOURCE_TYPE` (closed enum `stateMachine`,
`lambda`, `batch`, `ecs`, `container`, `custom`; an unknown value is stored as `custom`). A new name
is one `_VALIDATOR_DISPATCH` entry **plus** one `LEGITIMATE_VALUES` row in
`tests/common/test_validator_name_dispatch.py` (its table-equality test fails otherwise) and a
negative sample in `tests/common/test_mock_validators_delegates.py`.

Array validators (each element runs the scalar rule): `ID_ARRAY`, `UUID_ARRAY`,
`STRING_256_ARRAY`, `EMAIL_ARRAY`, `USERID_ARRAY`, `OBJECT_NAME_ARRAY`,
`RELATIVE_FILE_PATH_ARRAY`, `DOWNLOAD_KEY_ARRAY`.

### User IDs Are Unicode, and Are Normalized Before Validation and Storage

`USERID` keeps the Unicode-aware `\w` class, so an external IDP's non-Latin username is accepted.
Call `normalize_userid()` (or `normalize_userid_array()`) from `common.validators` on a caller-supplied
user id **before** validating it and before storing or looking it up: NFKC folds two compatibility
spellings of one name together (a fullwidth character for its ASCII counterpart, a decomposed accent
for a composed one), and normalizing on only some paths is worse than not normalizing at all — the
mismatch makes a lookup miss a row that exists. `request_to_claims()` normalizes the caller's own
identity, so a handler working from `claims_and_roles["tokens"]` needs no further call. A new route
that accepts a user id normalizes it at the same point every existing one does;
`tests/common/test_userid_identity_normalization.py` walks the tree and fails on one that does not.

`confusable_skeleton()` / `find_confusable_userid()` compare how two ids **look** (Cyrillic `а` and
Greek `ο` fold onto `a` and `o`) and belong at **user creation only** — `create_cognito_user` refuses
an id that reads the same as one already in the pool. An id already stored keeps working, so the
comparison never runs on a read. The mapping covers the Cyrillic and Greek lookalikes and is
deliberately partial.

### A `REGEX` Value Is Checked for Complexity, and Only on Save

`validate_regex` rejects three things on top of the compile check: a repeating quantifier applied to a
group that itself repeats or alternates (`(a+)+`, `(a|a)*`), a backreference, and more quantifier
ambiguity than one evaluation can afford — the estimated worst-case backtracking search space against
a 256-character subject must stay under `MAX_REGEX_SEARCH_SPACE`, which admits at most three unbounded
quantifiers (`.*`, `+`, `{n,}`). The budget is calibrated by measurement: three unbounded quantifiers
separated by literals match in ~16 ms, four in ~1 s, and five do not finish in 25 s. A constraint criterion value
becomes a Casbin `regexMatch(...)` pattern that `re.match` re-evaluates for every policy line on every
authorization decision, so such a value hangs authorization for the affected role. General regex stays
accepted — literals, character classes, anchors, `.*`, `|` and un-quantified groups.

The check runs where a constraint is **saved**: `ConstraintCriteriaModel` (every request path), plus
`validate_substituted_criteria_values()` in the template importer, which re-checks the value produced
by variable substitution because that — not the submitted template body — is what gets stored. It
deliberately does **not** run on read: `ConstraintResponseModel` uses
`ConstraintCriteriaResponseModel`, which carries no criteria-value rules, because a constraint written
by an earlier release was never checked and a failing response model degrades the listing to the raw
DynamoDB item shape.

### Importing Regex Patterns for Pydantic Fields

```python
from common.validators import (
    id_pattern,              # r'^[-_a-zA-Z0-9]{3,63}$'
    filename_pattern,        # For asset IDs and file names
    object_name_pattern,     # r'^[a-zA-Z0-9\-._\s]{1,256}$'
    relative_file_path_pattern,  # r'^\/.*$'
)
```

---

## DynamoDB Patterns

### Table Initialization

Resource + client APIs at module level, table names resolved via SSM:

```python
from backend.common.resourceNames import get_table_name, ResourceKeys

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)   # for low-level scans/pagination
your_table = dynamodb.Table(get_table_name(ResourceKeys.YOUR_STORAGE_TABLE))
```

### Common Operations

Standard boto3 resource-API calls (`query` with `KeyConditionExpression`, `get_item`,
`put_item` with `ConditionExpression`, `update_item`). For updates, build the update
expression via `common.dynamodb.to_update_expr(update_dict)`:

```python
from common.dynamodb import to_update_expr
keys_map, values_map, expr = to_update_expr(update_dict)
your_table.update_item(
    Key={'itemId': item_id},
    UpdateExpression=expr,
    ExpressionAttributeNames=keys_map,
    ExpressionAttributeValues=values_map,
)
```

The helper names its placeholders `#u<i>` / `:u<i>`, so a boto3 condition object
(`ConditionExpression=Attr("rev").eq(n)`) is safe in the same call: boto3 renders it with
`#n<i>` / `:v<i>` and merges those into the maps, replacing any entry of the same name. A
hand-built update expression combined with a condition object must avoid `#n<i>` / `:v<i>`
for the same reason.

### Pagination Patterns

Two different jobs with two different shapes. Decide which by asking who needs the complete
set — the handler, or the caller. Never let a single un-looped `query`/`scan` stand in for
either (Rule 14): DynamoDB caps one call at 1 MB and applies any `FilterExpression` only to
what that call already read.

**A. Read to exhaustion — the handler needs every row** (cycle checks, cascade deletes,
existence tests, descendant walks). Two shared helpers in `common.dynamodb` already implement
this loop and are the first choice for a `query`: `query_all_items(table, **query_kwargs)`
returns every matching item, and `query_has_match(table, **query_kwargs)` answers an existence
check, stopping at the first page that yields one. Hand-roll the loop only for a `scan`, or where
the walk needs its own bound; the shape is: loop on `LastEvaluatedKey`, feeding it back as
`ExclusiveStartKey`:

```python
from boto3.dynamodb.conditions import Key

items = []
query_kwargs = {'KeyConditionExpression': Key('databaseId').eq(database_id)}
while True:
    response = your_table.query(**query_kwargs)
    items.extend(response.get('Items', []))
    if 'LastEvaluatedKey' not in response:       # presence, not truthiness — see below
        break
    query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
```

Three details that decide whether this loop is correct:

-   **Test for presence (`'LastEvaluatedKey' not in response`), not truthiness.** DynamoDB
    omits the key entirely when the walk is complete and never returns a falsy one, so the
    two forms agree in production but not under test: `response.get('LastEvaluatedKey')`
    against a bare `MagicMock` returns a truthy `Mock` on every iteration and the loop never
    terminates, while `in` resolves to `False` and exits. A hanging test is the good outcome
    here; the same shape in a `while` loop with a page cap silently returns one page.
-   **Assign any "drained"/"complete" flag on every exit path.** A flag set only on the
    `LastEvaluatedKey`-absent branch reports the opposite of the truth when an early `break`
    (a cap, a found-it short-circuit) leaves the loop first.
-   **Rule 15 still applies to the response.** Reading to exhaustion internally is fine;
    returning that set to the caller is not. Enrich the full set, then slice to the page.

**B. External paging — the caller needs every row.** Return one page plus an opaque
Base64 `NextToken` wrapping `LastEvaluatedKey`:

```python
import base64, json
from common.dynamodb import validate_pagination_info

max_items = int(query_params.get('maxItems', '100'))
starting_token = query_params.get('startingToken')

scan_kwargs = {'Limit': max_items}
if starting_token:
    scan_kwargs['ExclusiveStartKey'] = json.loads(base64.b64decode(starting_token).decode('utf-8'))

response = your_table.scan(**scan_kwargs)
result = {'Items': response.get('Items', [])}
if 'LastEvaluatedKey' in response:
    result['NextToken'] = base64.b64encode(
        json.dumps(response['LastEvaluatedKey']).encode('utf-8')
    ).decode('utf-8')
return success(body=result)
```

**The token must round-trip, and nothing checks that for you.** The value the handler emits
has to be something the request model accepts back on the parameter the handler actually
reads. Emitting the raw `LastEvaluatedKey` **dict** as `NextToken` while the request model
declares `startingToken: Optional[str]` yields a token no client can return: page one looks
perfect, every later page is unreachable, and no error is raised anywhere. The Base64 wrap
above exists for exactly that reason — it makes the key a string, and opaque, so clients
cannot come to depend on its interior. Pin it with a test that takes the token from page one,
feeds it back, and asserts page two begins where page one stopped; asserting only that
`NextToken` is present passes on a token that cannot be used.

### Batch Reads

Resolving many rows by full primary key (the assets a page of executions references, the
pipelines a page of workflows references, the assets in a link tree, the current-version rows
of a page of listed assets) goes through
`common.dynamodb.batch_get_items(dynamodb, table_name, keys, *, max_retries=3,
backoff_seconds=0.05)`, never a hand-rolled `batch_get_item` loop. It chunks at the 100-key
per-call limit, deduplicates keys (DynamoDB rejects a chunk carrying a duplicate), and
re-requests `UnprocessedKeys` with exponential backoff — the shape a partial throttle or a
16 MB response takes, which arrives as HTTP 200 and so sits outside the botocore retry config.

It returns `(rows, unresolved_keys)`. A key absent from both is an item that does not exist;
a key in `unresolved_keys` is one the read did not complete within the budget. Keep the two
apart in the caller — a throttled read must not be reported as a deleted asset or, after
authorization, as a denial — and decide what to do with the leftover: read it per-item
(`executionService`, `assetExportService`, `assetService` listings), report it as unresolved
(`assetLinksService`), or log and degrade with `max_retries=0` where the data is a display-only
hint (`workflowService`). A `ClientError` propagates; the caller owns its fallback.

### Archived Assets Pattern

Archived assets live under a `databaseId + "#deleted"` partition-key suffix. Archive =
rewrite items with the suffixed partition key; query archived items with
`KeyConditionExpression=Key('databaseId').eq(f"{database_id}#deleted")`.

An archived record (asset or deleted database) is authorized in two forms:
`CasbinEnforcerService.enforce` allows it only when it passes on its stored `databaseId`
(`<databaseId>#deleted`) AND on its database id (the suffix removed). A deny on the database
therefore covers its archived records, and an allow written only as `databaseId equals <id>`
does not reach them; to grant archived records, a rule must also name `<databaseId>#deleted`
(for example `criteriaOr`: `equals <id>` OR `equals <id>#deleted`). Pass the stored record to
`enforce()` unchanged; do not strip or add the suffix in a handler for authorization.

### TypeDeserializer for Low-Level Responses

Low-level client responses use DynamoDB's typed dict shape. Convert with
`boto3.dynamodb.types.TypeDeserializer`:

```python
from boto3.dynamodb.types import TypeDeserializer
deserializer = TypeDeserializer()
python_dict = {k: deserializer.deserialize(v) for k, v in item.items()}
```

---

## Logging

### safeLogger

```python
from customLogging.logger import safeLogger
logger = safeLogger(service_name="YourServiceName")
logger.info(...); logger.warning(...); logger.error(...); logger.exception(...)  # exception adds stack trace
```

**Service name.** Every logger in a Lambda process is one Powertools logger, so the name labels every line the process writes, shared-module lines included. The handler names it: each line carries `service_name` (the key to query in CloudWatch Logs Insights) and the Powertools `service` key, both set to the handler's name. Shared modules (`common/`, `models/`, `customLogging/`, `customConfigCommon/`) and handler package `__init__.py` files call `safeLogger()` with no name and log under the calling handler's. Each Lambda entrypoint module declares `logger = safeLogger(service_name="<Name>")` at module level with a string literal, never a raw Powertools `Logger()`: `safeLogger` reads that declaration from the entrypoint's source (the runtime's `_HANDLER`) on its first call, so lines logged while the handler's imports run already carry its name, and another handler module imported as a library does not relabel them. `tests/common/test_logger_handler_service_name.py` enforces all of this.

`safeLogger` auto-redacts two key families at every nesting level, walking dicts, lists, and tuples (`customLogging.logger.mask_sensitive_data`, case-insensitive on the key name):

-   **Credential keys** (`SENSITIVE_KEYS`) — `authorization`, `idJwtToken`, `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken`, `apiKey`, `apiKeySecret`, `rawKey`.
-   **Content keys** (`CONTENT_KEYS`) — `configBody`, `templateTags`, `tagValues`, `customTemplateOverride`, `webFormJson`, `inputInstructions`. A pipeline template body or tag value carries free-form caller content (prompts, model configuration). Every field that can carry a template body belongs here whichever request delivers it: an execute request supplies one as `customTemplateOverride`, a template record as `configBody`. The value is replaced with `<redacted>` and the key is kept, so the record still shows that the field was submitted.

A request `body` is masked whether it arrives as a dict or as a JSON string — the string is parsed, masked, and re-serialized. Redaction is key-driven, so an f-string that interpolates a payload value (`logger.info(f"template {body}")`) bypasses it entirely: log identifiers, counts, and flags instead.

### Audit Logging

`backend/customLogging/auditLogging.py` exposes `log_authentication`, `log_authorization`, `log_authorization_api`, `log_file_upload`, `log_file_download`, `log_errors`, and other event-type functions — writing to **9 CloudWatch log groups** whose names resolve from SSM via `get_log_group_name(ResourceKeys.*)`. All audit functions extract user context via `request_to_claims(event)`. Every entry carries a `--- [event: ...]` echo of the triggering API event, passed through `mask_sensitive_data` first. `deferred_authorization_audit()` collects the `log_authorization` entries of a block and writes them with `_write_batch_to_cloudwatch` when it ends (every entry kept, also on an exception; nested blocks join the outer one) -- for handlers that decide many objects one by one. **Silent failure**: a failed audit write is logged locally, and Lambda execution continues — `mask_sensitive_data` upholds that contract by returning `<redacted>` rather than raising on a structure it cannot walk.

**Every path that hands out asset file content** — a presigned GET URL or a streamed body, in `downloadAsset`, `streamAsset`, `streamAuxiliaryPreviewAsset`, and `assetExportService` — applies three controls on top of Casbin: it refuses (or, for the export's per-asset entries, withholds the URL) when the asset's `isDistributable` is false; it runs `validateUnallowedFileExtensionAndContentType` on the file's key and the `ContentType` a `head_object` of the version about to be served reports, and refuses (export: withholds the URL, keeps the file in the listing) when the file is blocklisted or the head fails; and it writes a file-download audit entry (`log_file_download` / `log_file_download_bulk` / `log_file_download_streamed`) for every URL or body it actually issued. A new download surface gets all three; `downloadAsset.py`'s `lambda_handler` is the reference call shape, and `assetExportService.py` `file_type_allowed_for_download` is the withhold-not-refuse form. A surface that checks many files in one request runs the heads through a bounded pool (`downloadAsset.py`'s bulk branch, `assetExportService.py` `mark_files_allowed_for_download`): serial per-file heads over an asset of thousands of files exceed the API Gateway integration timeout.

**The refuse-before-sign checks live in `common/downloadEligibility.py`.** `downloadAsset.py` delegates to it; a new surface that hands out whole files of an asset (presigned URLs, archives) calls it rather than copying the logic. `get_distributable_asset_context` reads the asset (live table only, so an archived asset is "not found"), refuses a non-distributable one or one without `assetLocation`, and returns `(bucketName, baseKey)`. `check_file_eligibility` runs the per-file checks in a fixed order and returns `(final_key, version_id)` for the caller to sign. The order is: key containment (`resolve_asset_file_key`: a bucket-root key must sit under `'<base>/'`) and S3 key length; asset-version pin resolution (`resolve_pinned_version_id`, `assetVersionId` or alias, the alias wins); then, for a prefix, `validate_prefix_content_types` over EVERY object (one blocked object refuses the whole prefix, and a failed head raises) plus the prefix's own existence; for a file, one `head_object` of the requested version (404 refuses with "File not found in S3", a delete marker refuses with 410, then the blocklist). `is_file_archived(head)` reads `vams-status` archived/deleted or GLACIER/DEEP_ARCHIVE. The module builds no client and reads no table: the caller passes its Region-routing S3 client, its asset and bucket getters, and the `assetVersions.py` resolvers. `downloadAsset.py` keeps thin wrappers (`get_distributable_asset_context`, `check_s3_object_exists`, `resolve_and_sign_file_key`) that pass its own module globals at call time, so tests that patch `downloadAsset.s3`, `check_s3_object_exists`, `list_all_objects` or the resolvers still reach the shared checks. `streamAsset.py` (its own 403/404 statuses, CORS headers, `get_object`) and `assetExportService.py` (it withholds the URL rather than refusing, and lists its own keys) keep their own checks. A change to a rule in the module must hold for every caller. Refusals carry fixed generic messages (Rule 11).

---

## Response Functions (`models/common.py`)

-   `success(body=...)` → 200
-   `validation_error(body={'message': ...}, event=event)` → 400
-   `general_error(body={'message': ...}, event=event)` → 400
-   `authorization_error()` → 403
-   `internal_error(event=event)` → 500
-   `VAMSGeneralErrorResponse(...)` — exception class raised by business logic; caught in the handler `try/except` and re-emitted via `general_error(body={'message': str(v)}, event=event)`.

Pass `event=event` where accepted so the audit-logging hook can capture the caller. All responses follow `APIGatewayProxyResponseV2`: `isBase64Encoded=False`, `statusCode`, `headers` (`Content-Type: application/json`, `Cache-Control: no-cache, no-store`), and a JSON-string `body`.

---

## Environment Variables

### Loading Pattern

Resolve resource names at module load inside one `try/except`; wrap optional resources in an inner `try/except KeyError`; read handler-specific env vars directly from `os.environ`:

```python
try:
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    auxiliary_bucket = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
    try:
        optional_table_name = get_table_name(ResourceKeys.OPTIONAL_TABLE)
    except KeyError:
        optional_table_name = None
    send_email_function = os.environ.get("SEND_EMAIL_FUNCTION_NAME")
except Exception as e:
    logger.exception("Failed loading environment variables and resource names")
    raise e

asset_table = dynamodb.Table(asset_table_name)
```

**Resolution order:** `get_table_name(ResourceKeys.*)` first checks for legacy env-var overrides (e.g. `ASSET_STORAGE_TABLE_NAME`), then a 60-minute in-module cache, then a short-lived negative record of keys a completed sweep did not carry, so an unpublished parameter costs one sweep per window rather than one per call, then fetches all resource-name parameters from SSM via one paginated `GetParametersByPath` call. Pytest and local utilities can inject names as env vars while deployed handlers use SSM. **Pipeline handlers** in `backendPipelines/` still use legacy env vars and do not call `get_table_name()`.

### Common Environment Variables

`VAMS_RESOURCE_PARAM_PREFIX` (required, non-pipeline handlers): SSM parameter prefix for resource-name resolution. `AWS_REGION` (auto, set by Lambda runtime). `COGNITO_AUTH_ENABLED` (authorizer Lambda only): whether the Cognito MFA-preference check is reachable. Handler-specific vars like `SEND_EMAIL_FUNCTION_NAME` are read directly from `os.environ`. `WORKFLOW_SERVICE_V2_FUNCTION_NAME` is set on `pipelineService`, `executeWorkflow` and `importGlobalPipelineWorkflow`; the first two read it with `os.environ.get` and degrade (the refresh is not scheduled; a stale launch returns `409`) when it is absent.

`PRESIGNED_URL_TIMEOUT_SECONDS` (S3 presigned URL TTL) is **not** set for every handler — only `infra/lib/lambdaBuilder/assetFunctions.ts` sets it, covering the five asset handlers that mint presigned URLs (`downloadAsset`, `streamAsset`, `streamAuxiliaryPreviewAsset`, `uploadFile`, `assetExportService`). All five index it (`os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]`) at module level, so a handler built by any other lambda builder that copies that idiom raises `KeyError` during module import and returns `500` on every request from cold start. Add the variable to the handler's own builder before reading it.

**Legacy env-var overrides** (for pipeline handlers and testing): `ASSET_STORAGE_TABLE_NAME`, `DATABASE_STORAGE_TABLE_NAME`, `S3_ASSET_AUXILIARY_BUCKET`, `AUDIT_LOG_*`, etc. Non-pipeline handlers resolve these via SSM unless the legacy env var is explicitly set.

---

## File Security

Uploads must be validated against **both** `UNALLOWED_FILE_EXTENSION_LIST` (`.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat`) and `UNALLOWED_MIME_LIST` (Java archives, MS-executable, shell/JS/PowerShell/VBScript, etc.). Both lists are built in `common/constants.py` from the core lists plus the add-on (`common/addonConstants.py`) and fork (`custom/customConstants.py`) lanes, append-only; extend the core list there or a lane list, never bypass or remove an entry. The one helper that applies them, `common/s3.py` `validateUnallowedFileExtensionAndContentType`, compares case-insensitively: the file extension and the media-type part of the content type (parameters such as `; charset=binary` stripped) are lower-cased before the membership test, so `.EXE` and `Application/X-Msdownload` are rejected like their lower-case forms. The extension is **everything from the last dot of the key's final non-empty segment, read after every trailing dot and whitespace character is removed** (`str.isspace`, so a non-ASCII space counts): `payload.exe.`, `payload.exe `, `.exe`, `..exe` and `payload.exe/` are all checked as `.exe`, while `.gitignore` is checked as `.gitignore` and stays allowed; because the trailing `/` is ignored, a folder whose own name ends in a blocked extension (`builds.exe/`) is refused for prefix download. The segment is split on `:` and every colon-separated piece of the name is checked against the list, each read after its own trailing dots and whitespace are removed and lower-cased (on NTFS a `:` opens a stream name, so `payload.exe::$DATA` and `payload.exe:evil` write `payload.exe`; a browser that cannot write `:` substitutes it and keeps the whole name, so `a:b.exe` lands as `a_b.exe` — both shapes, and `x:payload.exe:evil`, are checked as `.exe`), while a `:` in an ordinary name (`notes:v2.txt`) is still accepted by the helper for an object already in a bucket. The request models complement this on the paths that CREATE a key: `models/assetsV3.py` `validate_asset_file_path(..., new_object=True)` refuses a final path segment ending in a dot or whitespace for upload initialize/completion `relativeKey` and move/copy `destinationPath` (Windows drops that character on download, so `payload.exe.` would land as `payload.exe`), and `file_only=True` additionally refuses a `relativeKey` ending in `/` on the three upload paths (a file key is never a folder; `CreateFolderRequestModel` keeps its own trailing-slash rule); the read paths — download `key`, file info, archive, move/copy `sourcePath` — set neither flag so an object already stored under such a key stays reachable. `new_object=True` also refuses a `:` anywhere in the value, on every created-key path including `CreateFolderRequestModel` (Windows forbids it in any path segment, so no legitimate name is lost), while the read paths keep accepting it. Keep every list entry lower-case and listed once — `tests/common/test_s3_blocklist_case_insensitive.py` fails on an entry of the merged lists that is not (a lane entry that is not is refused at import first); `tests/common/test_s3_blocklist_trailing_dot.py` and `tests/models/test_assetsV3_trailing_dot_write_paths.py` pin the basename rule and the write-path refusal, `tests/common/test_s3_blocklist_ads_suffix.py` and `tests/models/test_assetsV3_ads_suffix_write_paths.py` pin the per-piece `:` check and the created-key `:` refusal, and `tests/models/test_validate_asset_file_path_call_sites.py` fails on a `validate_asset_file_path` call whose field neither passes `new_object=True` nor is declared a read path.

Move, rename and copy (`handlers/assets/assetFiles.py` `move_file` / `copy_file`) run the same helper on the resolved destination key, with an empty content type, right after the key-length check and before any S3 call, and refuse a blocked name with a `400`; `tests/handlers/assets/test_assetFiles_destination_extension_check.py` pins it.

Upload completion (`handlers/assets/uploadFile.py` `complete_upload` / `complete_external_upload`) runs the name-level blocklist (and the preview-extension rule) on every object it creates — zero-byte and regular multipart alike — before anything is written (a multipart file completed with no parts is a cancelled file: its multipart upload is aborted, nothing is stored, and its result is `success=False`; `sqsUploadFileLarge.py` `complete_multipart_upload_for_large_file` does the same and returns `False`), and accepts only the `relativeKey`s the initialize step recorded on the upload record (`AssetUploadTableModel.files`); a record without that attribute predates the binding and is completed with the blocklist checks but without the membership check, logged as a warning, until it expires: `complete_upload` refuses a record whose `expiresAt` (epoch seconds; 7 days from initialize, 1 day for workflow-output records) has passed, and refuses an external (`isExternalUpload`) record outright — the mirror of the check `complete_external_upload` makes — so the one record class designed without the list cannot be completed by the route that skips membership. `AssetUploadsStorageTable` has a DynamoDB TTL on `expiresAt`, which removes an expired record some time after it expires; until then this check is what refuses it.

The external (workflow-output) completion runs once per upload id. `claim_external_completion` moves the record from `initialized` to `processing` under a condition; the call that loses it runs nothing and `answer_repeated_external_completion` answers with the first call's stored result (`completionStatusCode` + zlib-compressed `completionResult`, at most 200 KB), polling while the first still runs and answering 409 when it does not finish within the invocation or stored no result. The record is therefore KEPT once completed (`completed` / `completed_with_errors` / `processing_async`, or `failed` when the completion raised) until the TTL removes it; `complete_upload` still deletes its own records. The caller, `processWorkflowExecutionOutput.py`, sends at most `MAX_FILES_PER_UPLOAD_REQUEST` (1,000) files per call (`_completion_chunks`, a `.previewFile.` in the same call as its base file, one upload record per call) and invokes the completion and the metadata service through `invoke_client`: no botocore retry and a 910 s read timeout, above the invoked functions' 900 s timeout; only a `TooManyRequestsException` is retried. Do not route a RequestResponse cross-call through the retrying `client` (Event invokes only).

The asynchronous >1GB completion (`sqsUploadFileLarge.py`, on `LargeFileProcessingQueue`: batchSize 1, maxReceiveCount 3, a dead-letter queue) splits failures in two. A rejection no retry can change (blocked type or preview extension, cancelled or S3-rejected multipart upload, upload ID mismatch, oversized preview) returns `False`: `process_large_file` aborts the multipart upload, deletes the staged object from its source bucket (`sourceBucketName`, else `bucketName`), writes a denied `log_file_upload` entry, and the message is consumed. Any other failure (S3 5xx, throttling, a failed copy or read) is raised, `lambda_handler` re-raises after its loop, and SQS redelivers and finally dead-letters the message; the multipart upload and staged object are kept so a later receive can finish the file (a `NoSuchUpload` with the staged object present means an earlier receive completed it), and only the final receive (`ApproximateReceiveCount >= LARGE_FILE_MAX_RECEIVE_COUNT`) aborts the multipart upload. Keep `LARGE_FILE_MAX_RECEIVE_COUNT` equal to the queue's maxReceiveCount in `apiBuilder-nestedStack.ts`. Helpers on this path must raise rather than return `False` for a transient error. A duplicate delivery (SQS is at-least-once) of a file an earlier receive stored finds the staged object gone; `is_large_file_already_stored` then checks the final object (`finalS3Key`) for this file's asset, database and change provenance and a `LastModified` no earlier than the message's `SentTimestamp`, and on a match `LargeFileAlreadyProcessed` is raised and `lambda_handler` consumes the message with no cleanup and no audit entry. An older version at the same key, other provenance, or a missing `SentTimestamp` is not a match, so a vanished upload is still rejected and audited as denied. What a dead-letter redrive can recover: a file whose multipart upload completed before the last receive (for example after a failed copy), from the kept staged object; it cannot finish a file whose multipart upload never completed, because the final receive aborted it, and that redrive is rejected (the file must be uploaded again).

### Choosing a file's `.previewFile.<ext>`

A file can hold several `<file>.previewFile.<ext>` objects (a pipeline re-run in another format writes a `.png` beside an existing `.gif`; nothing deletes the other). Every path that attaches a file's preview goes through `select_newest_preview_file((key, lastModified, isArchived) …)`: in `handlers/assets/assetFiles.py` the current listing, the version-snapshot listing in basic and detailed mode, and the single-file info that set `previewFile`, and in `handlers/assets/assetExportService.py` the export's per-file preview path. A live preview beats an archived one, then the latest `lastModified` wins, ties keep key order. Do not pick "the first in key order" in a new path. Tests: `tests/handlers/assets/test_assetFiles_newest_preview.py`, `tests/handlers/assets/test_assetExportService_newest_preview.py`.

---

## Partition Portability (Commercial / GovCloud / EU Sovereign)

Handlers run in `aws`, `aws-us-gov`, `aws-eusc` (EU Sovereign, region `eusc-de-east-1`), and potentially `aws-cn` / `aws-iso*`. A partition defect here is invisible in commercial tests and surfaces as a runtime 500 or a validation rejection that only reproduces in the affected partition. There is deliberately **no central partition helper** in the backend — the four rules below are the whole contract.

1. **Never build an ARN from a hardcoded partition.** Derive it, or take it as a parameter. Two patterns are already established, and new code should reuse one:

    - **Parameterize.** `common/workflows/stepfunctions_builder.py` threads a `partition` argument (defaulting to `"aws"`) through every state-machine integration ARN. Its value originates as the `AWS_PARTITION` Lambda env var, read once in `common/workflows/workflowAsl.py` and passed down.
    - **Parse an ARN you already hold.** `handlers/workflows/executionService.py` splits a resource ARN (falling back to the execution log-group ARN) to recover partition/region/account, so nothing is assumed. Prefer this when an ARN is already in hand — it needs no env var and cannot drift.

    `AWS_PARTITION` is currently set on the `workflowService` Lambda only (`infra/lib/lambdaBuilder/workflowFunctions.ts`). A handler that needs the partition either gets the env var added in its CDK builder or derives it from an ARN — do not read `os.environ["AWS_PARTITION"]` in a handler whose builder does not set it.

2. **Never hardcode an endpoint hostname or DNS suffix.** Suffixes differ per partition — `amazonaws.com`, `amazonaws.com.cn`, **`amazonaws.eu`** (EU Sovereign), `c2s.ic.gov`, `sc2s.sgov.gov`, `cloud.adc-e.uk`, `csp.hci.ic.gov`. Construct plain boto3 clients (`boto3.client("s3", config=retry_config)`) with **no `endpoint_url`** and let the SDK resolve per region; take service endpoints (e.g. the OpenSearch host) from SSM rather than composing them. Region comes from `os.environ["AWS_REGION"]`, which the Lambda runtime always sets — avoid a `"us-east-1"` default, which silently points at the wrong partition when the variable is missing.

    **Asset buckets may be in another Region, so every S3 call on an asset bucket goes through `common.s3`'s Region-routing client**, never a module-level `boto3.client('s3', ...)` of the handler's own. `region_routing_s3_client()` / `region_routing_s3_resource()` return the module-level client/resource a handler holds (`s3 = region_routing_s3_client()`); each call is signed for the Region of the `Bucket` it names (`Params['Bucket']` for a presigned URL), resolved from the bucket details the handler read (`bucket_region_fields(row)` spreads `bucketRegion`/`bucketAccountId` into every bucket-details dict and registers the name) or, for a name-only call, from the buckets table's `bucketNameGSI`. `copy_s3_object()` is the one copy path: a managed copy by the destination-Region client, or a streamed GET→PUT when `VAMS_LAMBDAS_IN_VPC` is `true` and the Regions differ, because an S3 interface endpoint does not serve `CopyObject` across Regions. The client config is `S3_ASSET_CLIENT_CONFIG` (SigV4, path style, adaptive retries, the regional `us-east-1` endpoint). `tests/handlers/databases/test_buckets_listing_region.py` holds the listed handlers at zero plain S3 clients, and the test mock `tests/mocks/common/s3.py` carries the same surface (`tests/common/test_s3_region_routing.py` asserts the parity).

3. **New ARN/URL validators must accept every partition.** `common/validators.py` defines `aws_partition_group` (`aws`, `-us-gov`, `-cn`, `-eusc`, `-iso[-x]`) and `aws_dns_suffix_group` (all seven suffixes). Compose new patterns from these groups — never inline `arn:aws:`. A pattern that hardcodes the commercial partition passes every commercial test and rejects legitimate input **only** in the affected partition, which is the hardest failure mode to attribute.

    ```python
    # ✅ CORRECT — composed from the shared groups
    my_arn_pattern = r'^arn:(' + aws_partition_group + r'):lambda:[a-z0-9\-]+:[0-9]{12}:function:.+$'

    # ❌ INCORRECT — rejects GovCloud and EU Sovereign at runtime
    my_arn_pattern = r'^arn:aws:lambda:.+$'
    ```

    `infra/lib/helper/const.ts` (`SERVICE_LOOKUP`) is the authoritative partition + suffix list; keep these groups in step with it.

4. **A service or model may not exist in every partition.** Bedrock model ids differ (both restricted config templates pin an older Sonnet), OpenSearch engine versions differ, and some services are absent entirely. A handler that hard-codes a model id or assumes a service is reachable fails only where it is not — prefer taking such values from configuration that the CDK layer supplies per partition.

Feature switches are the runtime signal for partition-conditional behavior: `GOVCLOUD` is present in `featuresEnabled` for **both** GovCloud and EU Sovereign (both templates set `app.govCloud.enabled: true`), so treat it as "restricted partition" rather than literally GovCloud. See `infra/CLAUDE.md` "Partition Portability" for the CDK-side rules, including the `AWS::Lambda::EventSourceMapping` tag restriction.

---

## Testing

Run `pytest` from `backend/` after `pip install -r requirements-dev.txt`; CI runs it in parallel with `pytest -n auto --dist loadfile` (`pytest-xdist`), so every test must pass in any file order (isolation rules: `backend/tests/CLAUDE.md`, "Running in parallel"). Tests live under `backend/tests/[domain]/`; markers are `unit`, `integration`, `slow`, `aws`, `temporary` (root CLAUDE.md Rule 13). Full configuration, mock module hierarchy, `conftest.py` layering, and event-shape conventions: see `backend/tests/CLAUDE.md`.

## Templates

New-handler / model / test skeletons: `backend/HANDLER_TEMPLATES.md`. Gold Standard: `backend/handlers/assets/assetService.py`.

## Fork Extension Points

Upstream VAMS ships these files empty and never edits them again, so a fork that adds net-new
endpoints writes only here and merges upstream releases without conflicts. Each opens with the same
"Fork extension point" header. Full walkthrough: `documentation/docusaurus-site/docs/developer/customizing-vams.md`.

| What a fork adds                                   | Fork-owned file                                                       | Spliced in by                                       |
| -------------------------------------------------- | --------------------------------------------------------------------- | --------------------------------------------------- |
| API routes                                         | `custom/customApiRoutes.py` (`CUSTOM_ROUTES`)                         | `common/apiRoutes.py` → `ALL_API_ROUTES`            |
| Resource-name keys                                 | `custom/customResourceNames.py` (`CustomResourceKeys`)                | resolved with `get_resource_name()` like a core key |
| Handlers                                           | `handlers/custom/<name>.py` (`handlers.custom.<name>.lambda_handler`) | the shared `backend/backend` code asset             |
| Models                                             | `models/custom/<name>.py`                                             | imported by the fork's handlers                     |
| Object types, constraint fields, deny-list entries | `custom/customConstants.py`                                           | `common/constants.py` (merged at import)            |

An add-on is optional, upstream-maintained code enabled by configuration: it either integrates VAMS with an external solution deployed and run separately (Garnet Framework, Physna), supplying the VAMS-side components, or is a stand-alone non-core component that VAMS deploys in full when enabled (the licensed VNTANA and VEERUM viewers). Custom is fork code, which upstream ships empty. Upstream adding an integration with an external solution uses the add-on lane; a fork uses the custom lane. Definition and current add-ons: `documentation/docusaurus-site/docs/developer/addons-overview.md`.

The add-on lane sits beside each core list and is upstream-owned: `common/addonApiRoutes.py`,
`common/addonResourceNames.py` (`AddonResourceKeys`), `common/addonConstants.py`, `handlers/addon/`
and `models/addon/` (the Physna viewer model). Every list merges in the order core, add-on, custom.

-   Fork handlers follow every rule above, including both Casbin tiers with the fail-closed
    pre-set flag (Rule 4) and dispatch on the `ApiRoute` constants from `custom/customApiRoutes.py`
    (Rule 12). Tier 2 uses a core entity type or a permission object type the fork registers in
    `custom/customConstants.py` (`CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS` + `CUSTOM_PERMISSION_CONSTRAINT_FIELDS`).
-   **Constants lanes.** `common/constants.py` keeps the core lists as `CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES`,
    `CORE_PERMISSION_CONSTRAINT_FIELDS`, `CORE_CONSTRAINT_OBJECT_TYPE_FIELDS`, `CORE_UNALLOWED_FILE_EXTENSION_LIST`
    and `CORE_UNALLOWED_MIME_LIST`, and builds the public names (no `CORE_` prefix) as core + add-on + custom at
    import. Consumers import the public names only (`tests/common/test_constants_extension_lanes.py` checks).
    A lane entry that breaks a rule raises `ConstantsExtensionError` naming the file and entry, which fails
    every Lambda's import (fail closed): names match `^[A-Za-z][A-Za-z0-9]{1,63}$` (no underscore, so no
    `x__y` shape and nothing that leaves the `r.obj.<field>` position in a rule), are not `object__type`,
    `route__path` or `method`, not a Python keyword or `EVALUATOR_RESERVED_ATTRIBUTES` name, and not a core or
    other-lane entry; a type's fields exist in the merged field set; samples are `""` or `[]` (list fields
    take only `is_one_of` / `is_not_one_of`). Type and field names are compared ignoring letter case when
    checking for a repeat, so `Asset` repeats `asset` and `DatabaseId` repeats `databaseId`; authorization
    still matches a name exactly. Deny-list entries are append-only, lower-case, `.ext` / `type/subtype`.
-   **No automatic grant.** An extension object type gets no seeded constraint: the
    `dynamodb-authdefaults-*` constructs are not extended, so every role, admin included, is denied on it until
    the fork adds a constraint (`tests/handlers/authz/test_extension_object_types.py`). Never add one.
-   Tests that pin constants must hold for any lane content: pin the `CORE_*` lists, or load a private copy of
    `constants.py` with stand-in lane modules; never assert that the merged lists equal core.
-   `custom/` modules import `ApiRoute` from `common/apiRouteTypes.py` and `ResourceParamKey` from
    `common/resourceNames.py`; nothing in core imports a fork handler or model.
-   `customConfigCommon/` is different: it holds override hooks for **existing** behaviour (the
    authorizer MFA check, the login profile), which a fork edits in place.
-   Recommended (not enforced) names: routes under `/custom/`, resource keys under `custom/`.
    An exact duplicate of a core route or key is rejected (`tests/common/test_custom_extension_points.py`,
    and at CDK synth). A resource-name key resolves a name, never a secret; a handler reads a secret
    from AWS Secrets Manager at runtime.

---

## Key Dependencies

Runtime: `aws-lambda-powertools` 2.36.0 (Logger, Parser, BaseModel, typing), `boto3` 1.43.45 / `botocore` 1.43.45 (botocore **≥1.36** is required for the `aws-eusc` EU Sovereign Cloud partition — older releases resolve `eusc-de-east-1` endpoints to the wrong `.amazonaws.com` suffix), `casbin` 1.36.0 (ABAC/RBAC), `pydantic` 1.10.13 (v1 ONLY), `opensearch-py` 2.7.1, `simpleeval` 1.0.7 (safe expression evaluation in Casbin matchers), `locked-dict` 2023.10.22 (thread-safe Casbin cache).

Dev only: `moto` 5.1.0 (AWS mocks), `pytest` 9.0.3, `mypy` 1.0.0, `flake8` 6.0.0.

---

## Updating Python Dependencies (Poetry-Managed)

Wherever a `pyproject.toml` sits next to a `requirements*.txt`, the requirements file is a **generated artifact** exported from `poetry.lock` — never edit it by hand. Poetry-managed projects: `backend/`, `backend/lambdaLayers/base/`, `backend/lambdaLayers/authorizer/`, and `backendPipelines/multi/rapidPipelineEKS/lambdaLayer/`.

To change a dependency version:

1. Edit the constraint in `pyproject.toml` only if the current constraint excludes the target version (exact pins like `urllib3 = "2.6.3"` must be edited; ranges like `^2.12.1` that already admit the target need no edit).
2. Re-resolve without installing: `poetry update --lock <package> [<package>...]`
3. Re-export the requirements file(s):

    ```bash
    # Lambda layers and pipeline layers (single requirements.txt):
    poetry export --without-hashes -f requirements.txt -o requirements.txt

    # backend/ (split main vs dev):
    poetry export --only main --without-hashes -f requirements.txt -o requirements.txt
    poetry export --with dev --without-hashes -f requirements.txt -o requirements-dev.txt
    ```

4. Commit `pyproject.toml`, `poetry.lock`, and the exported requirements file(s) together — a requirements file that drifts from its lock will be silently overwritten by the next export. The layer bundling build never reads the committed file: `layerBundlingCommand()` (`infra/lib/helper/lambda.ts`) re-exports the layer's `poetry.lock` inside the build container and installs that export, so a layer ships what its lock resolves to. The command installs pinned versions of pip, Poetry and `poetry-plugin-export`, with Poetry pinned to the version that writes the layer lock files; regenerate the locks with that Poetry version, or move the pin with them (`infra/test/security/layerBundlingToolPins.test.ts` asserts every tool is pinned).

`backend/` and `backend/lambdaLayers/base/` lock every package they share at the same version: handler Lambdas load their third-party packages from the base layer, while the backend suite installs `backend/requirements-dev.txt`. Change a shared package in both trees in the same change. To match a transitive package to a version other than its newest release, add a temporary exact constraint to `pyproject.toml`, run `poetry lock`, remove the constraint, and run `poetry lock` again — Poetry keeps the locked version. `backend/tests/test_base_layer_dependency_parity.py` fails on any difference. The authorizer layer (`backend/lambdaLayers/authorizer/`) is a separate dependency set and is not compared.

Requirements files with **no** side-by-side `pyproject.toml` (e.g. `backendPipelines/multi/rapidPipelineEKS/lambda/requirements.txt`) are hand-maintained pip files, edited directly.

---

## Anti-Patterns -- What NOT to Do

Most anti-patterns are the inverse of a Critical Rule above. The compact list cites the rule each violates:

-   Raw dict responses with status codes — Rule 7. Use `success` / `validation_error` / `general_error` / `internal_error`.
-   Skipping the `enforceAPI()` check in `lambda_handler` — Rule 4.
-   Missing `object__type` annotation before `enforce()` — Rule 4.
-   Gating a single-resource `enforce()` on `if len(tokens) > 0:` without an `else` that denies — Rule 4. Fails open on empty tokens; the list-filtering "append only when `enforce()` passes" shape is the one exception (fail-closed by construction).
-   Treating a single filtered `query`/`scan` as a lookup or an existence check — Rule 14. The `FilterExpression` runs after the 1 MB page read, so empty `Items` with a `LastEvaluatedKey` present means "the match is on a later page", not "absent". Loop to exhaustion, or query a GSI on the attribute.
-   Terminating a paging loop on `response.get('LastEvaluatedKey')` truthiness instead of key presence — see Pagination Patterns. Equivalent in production, divergent under test.
-   Emitting a pagination token the request model cannot accept back (raw `LastEvaluatedKey` dict vs a declared `str`) — see Pagination Patterns. Caps the listing at page one with no error.
-   Inline regex validation — Rule 3. Use the `validate()` dispatcher.
-   `print()` for logging — Rule 5.
-   Creating boto3 clients inside functions — Rule 6.
-   Pydantic v2 imports or syntax (`ConfigDict`, `field_validator`, `model_validate`, `model_dump`, or `from pydantic import BaseModel`) — Rules 1 and 2.
-   Resolving resource names inside handler functions — Rule 10.
-   Returning raw error strings (`{'statusCode': 400, 'body': 'bad request'}`) — Rules 7 and 11.
-   Echoing user input or internal details in client error messages — Rule 11.
-   Hardcoding `arn:aws:` in an ARN string or a validator regex — see Partition Portability. Compose from `aws_partition_group` / `aws_dns_suffix_group`, or derive the partition from an ARN already in hand; a commercial-only pattern fails only in GovCloud / EU Sovereign.
-   Passing `endpoint_url` or a hardcoded hostname to a boto3 client — see Partition Portability. Let the SDK resolve per region; read service endpoints from SSM.

### Swallowing exceptions silently

A bare `except Exception: pass` hides real failures — no log line, no metric, no re-raise. Log the error, then decide whether to raise, translate to a `VAMSGeneralErrorResponse`, or continue with a documented best-effort behavior:

```python
try:
    do_something()
except Exception as e:
    logger.exception(f"Error in do_something: {e}")
    raise VAMSGeneralErrorResponse("Operation failed")
```

The best-effort helpers `common.assetHistory` and `common.syncTracking` are the deliberate exceptions: they catch and log their own failures so a history-write failure never breaks the primary operation. New handlers should not replicate that shape without an equally explicit rationale.

---

## Development Checklist

When creating or modifying a handler:

-   [ ] Imports in standard order (stdlib, boto3, powertools, common, handlers, models)
-   [ ] Module-level: AWS clients with `retry_config`; `safeLogger(service_name=...)`; resource names via `get_table_name(ResourceKeys.*)` inside one `try/except`; DynamoDB tables initialized from resolved names
-   [ ] `lambda_handler` extracts claims via `request_to_claims(event)` first
-   [ ] Tier-1 auth via `casbin_enforcer.enforceAPI(event)` with the fail-closed pre-set flag pattern
-   [ ] Routes dispatch via `ApiRoute.matches()` (not hardcoded fragments)
-   [ ] Request bodies parsed with `parse(body, model=ModelClass)`; params validated with the `validate()` dispatcher
-   [ ] Tier-2 auth via `casbin_enforcer.enforce(event, item)` with `object__type` set; empty-token case fails closed with an explicit `authorization_error()` before any single-resource `enforce()`
-   [ ] Every `query`/`scan` either pages to exhaustion or returns a round-tripped `NextToken` — no single filtered call standing in for a lookup, existence check, or full listing (Rule 14, Pagination Patterns)
-   [ ] Business logic errors raise `VAMSGeneralErrorResponse`; error handling maps ValidationError→400, VAMSGeneralErrorResponse→400, Exception→500
-   [ ] Client error messages are generic (no echoed input or internal details); response functions pass `event=event` for audit logging
-   [ ] No `print()`, no Pydantic v2 syntax; models declare `extra='ignore'`
-   [ ] Tests exist with proper mocking via `conftest.py` (see `backend/tests/CLAUDE.md`)

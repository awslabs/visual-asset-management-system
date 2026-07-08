# VAMS Code Review — Findings (backend / CDK / web / pipelines)

Review method: multi-agent audit (13 parallel dimension finders → adversarial verification of every finding → independent spot-checks by the lead). 30 raw findings, 4 refuted on verification, 26 retained. Baseline static analysis: `ruff` security rules (10 low/medium leads), web `npm audit` clean. No code was changed.

Severities below are **post-verification adjusted** (several finders over-rated; verifiers corrected). Sorted by priority.

---

## HIGH

### H1. Command injection into RapidPipeline container via asset filename / S3 key

`backendPipelines/multi/rapidPipeline/lambda/constructPipeline.py:59` (also `:72`)
The container command is built as a shell string interpolating the S3 key and derived filename, wrapped only in double quotes, then executed via `["/bin/sh","-c",command]`. POSIX `sh` performs `$(...)` and backtick expansion **inside** double quotes, so double-quoting does not neutralize injection. `filename_pattern` (validators.py:17) permits many shell metacharacters.
**Impact:** A user who can upload an asset and trigger RapidPipeline gets arbitrary command execution inside the Batch/ECS container (its IAM task role, S3 access, VPC reach, IMDS) — data exfiltration, lateral movement, credential theft.
**Fix:** Pass S3 URIs/filenames as discrete argv elements (exec form, no `/bin/sh -c`), or `shlex.quote()` every interpolated value. Reject shell metacharacters (`` ` ``, `$`, `;`, `|`, `&`, `(`, `)`, newline, `'`) in filenames destined for shell pipelines.

### H2. Command injection into ModelOps container via `printf '<json>'` single-quote breakout

`backendPipelines/multi/modelOps/lambda/constructPipeline.py:60`
`command = "printf '" + json.dumps(config) + "' | .../index.js ..."` executed via `["/bin/bash","-c",command]`. `json.dumps` escapes `"` and `\` but **not** the single quote, so any `'` in a config value (e.g. `config["state"]["name"]`, which is derived from the asset filename) breaks out of the single-quoted literal into shell context. **`filename_pattern` explicitly allows `'`** (verified) — the injection is reachable.
**Impact:** Arbitrary command execution inside the ModelOps container with its task role and network position.
**Fix:** Write the config to a file and pass its path as argv, or pipe JSON to stdin without a shell, or `shlex.quote()` the serialized config; strip apostrophes/metacharacters from filenames.

---

## MEDIUM

### M1. Physna OAuth2 clientSecret embedded in CloudFormation template in plaintext

`infra/lib/nestedStacks/addon/physna/physnaSyncBuilder-nestedStack.ts:74`
The secret is materialized into a Secrets Manager secret via `unsafePlainText` from `config.app.addons.usePhysnaSync.clientSecret` (config.ts ~2065). `config.json:254` holds a real, non-placeholder secret and is git-tracked.
**Impact:** Anyone with `cloudformation:GetTemplate`, access to the CDK synth output, or the bootstrap assets bucket can recover the Physna tenant credential in cleartext — no `secretsmanager:GetSecretValue` needed, KMS-at-rest bypassed.
**Fix:** Provision an empty/generated secret in CDK and populate out-of-band, or import a pre-existing secret ARN via `Secret.fromSecretCompleteArn()` so the value never enters the template. Stop storing `clientSecret` in `config.json`. **Rotate the exposed secret.**

### M2. WAF WebACL provides zero protection even when enabled (COUNT-only)

`infra/lib/constructs/wafv2-basic-construct.ts:31`
The only managed rule (`AWSManagedRulesCommonRuleSet`) has `overrideAction: { count: {} }` (comment even says "change this back to none"), and the WebACL default action is `allow` (`:72`). No rate-based rule exists.
**Impact:** With `useWaf=true` (default), the WAF is purely observational — path traversal, LFI/RFI, bad UAs, oversized bodies all pass. No L7 DDoS/brute-force throttling. Security posture is far weaker than "WAF enabled" implies.
**Fix:** Set `overrideAction: { none: {} }` (validate legit large-upload traffic first, add scoped exclusions for false positives), add a rate-based rule. Make override mode configurable if a monitor-first rollout is wanted, but an "enabled" WAF should block by default.

### M3. Fail-open two-tier auth guard: empty token list skips ALL authorization

`createAsset.py:706`, `createDatabase.py:136`, `downloadAsset.py:563`, `ingestAsset.py:325`, `uploadFile.py:2400/2443/2473`
These handlers gate **both** auth tiers inside `if len(claims_and_roles["tokens"]) > 0:` and only deny inside that block — so when `request_to_claims()` returns `tokens=[]` (no/unrecognized authorizer context, `auth/__init__.py:18-24`), execution **falls through to the mutation/URL-signing with zero auth checks**.
**Impact:** Not a live bypass in the shipped config (the REST authorizer is always attached and populates `vams:tokens`). It is a latent fail-open / defense-in-depth deviation from the rest of the codebase — becomes a real bypass if any of these routes is ever wired to an anonymous/ignored-path authorizer, if the authorizer emits Allow with empty context, or via direct Lambda invoke lacking the authorizer block.
**Fix:** Fail closed: `if len(claims_and_roles["tokens"]) == 0: return authorization_error()` before the enforcer block (or the `method_allowed_on_api=False; if tokens: ...; if not allowed: deny` pattern).

### M4. MFA context reflects account-level enrollment, not the session's auth method

`backend/backend/customConfigCommon/customAuthClaimsCheck.py:49`
The default MFA hook uses `admin_get_user`'s `UserMFASettingList` (account-configured MFA methods) and treats non-empty as "logged in with MFA" — contradicting the session-level intent stated in its own docstring and `resolve_mfa_enabled`. Per-user cache keyed on `auth_time` reinforces the divergence.
**Impact:** A user who enrolls MFA _after_ a non-MFA sign-in (refresh-token sessions keep the original `auth_time`, never MFA-challenged) is reported `mfaEnabled=True`, so Casbin activates their `mfaRequired=True` roles without a step-up. Symmetrically, removing MFA config downgrades a live MFA session.
**Fix:** Derive MFA from the verified token's `amr` claim: `mfaLoginEnabled = any(m in ('mfa','software_token_mfa','sms_mfa') for m in claims.get('amr', []))`. Needs no Cognito call, reflects the actual session, removes `admin_get_user` latency.

### M5. `addComment` exception handler dereferences `.response` on any non-ClientError

`backend/backend/handlers/comments/addComment.py:189`
`except Exception as e:` immediately does `e.response["Error"]["Code"]`; only botocore `ClientError` has `.response`. A missing/malformed `assetVersionId:commentId` path param raises `KeyError`/`IndexError` at line 109 (only `assetId` is validated), then the handler raises `AttributeError` → uncaught → API Gateway **502**.
**Fix:** Validate the composite key before splitting (as `delete_handler` does), and separate `except ClientError` from `except Exception`; guard with `getattr(e, 'response', {})`.

### M6. `delete_comment` non-atomic delete-then-recreate loses the comment on partial failure

`backend/backend/handlers/comments/commentService.py:196`
Soft-delete is `delete_item(...)` then `put_item(...#deleted)` in two separate try/except blocks, delete-first (the riskier order). A transient failure on the put after a successful delete permanently loses the comment; retry returns 404.
**Fix:** Use `transact_write_items` (Delete + Put atomic), or mark deletion in place via `update_item` with an `isDeleted` flag filtered in reads. (Handler also lacks the standard `retry_config`.)

### M7. Stack-wide blanket `AwsSolutions-IAM5` suppression with match-all regex

`infra/lib/nestedStacks/searchAndIndexing/searchBuilder-nestedStack.ts:486`
`addResourceSuppressions(scope=whole nested stack, [...IAM5 with appliesTo {regex:"/.*$/g"}], applyToChildren=true)` with reason "Configured as intended." — violates Rule 4.
**Impact:** Any current/future wildcard IAM policy in the search/indexing stack (e.g. an accidental `es:*` on `*`) is auto-suppressed and never flagged by CDK Nag. Over-permissioning ships undetected.
**Fix:** Replace with per-construct, per-action suppressions naming the exact resource and wildcard action (e.g. only `Action::es:ESHttp*` on the four indexer roles scoped to `domainArn/*`), mirroring the narrow style in `security.ts`.

---

## LOW

### L1. `validate()` dispatcher skips all remaining fields when an optional field is empty

`backend/backend/common/validators.py:349-363`
The optional/empty branches `return (True, "")` for the **entire** call instead of `continue`. Since dict order is preserved, any field after an optional-and-empty one is never validated.
**Impact:** Real call site: `listExecutions.py` orders optional `workflowId` before `assetId`/`databaseId` — those are then unvalidated (they feed parameterized DynamoDB expressions, so bounded). Latent risk for any handler ordering an optional field before security-relevant ones.
**Fix:** Change the three short-circuits to `continue`; return `(True, "")` once after the loop.

### L2. `validate_bool` accepts any value (BOOL validator is a no-op)

`backend/backend/common/validators.py:302` — `bool(str(value))` never raises, always returns truthy.
**Fix:** Allow-list check: `if str(value).strip().lower() not in ('true','false'): return (False, ...)`.

### L3. Cognito JWT decoded without pinning allowed algorithms

`backend/backend/common/auth/authorizerCore.py:358` — `joserfc_jwt.decode(token, public_key)` with no `algorithms=`, while the external-IDP path correctly pins `['RS256']`.
**Impact:** No current bypass (joserfc key-type binding blocks alg=none / HS256 confusion at the pinned version). Latent — a library/registry change or refactor passing a permissive key could silently widen accepted algorithms.
**Fix:** `joserfc_jwt.decode(token, public_key, algorithms=['RS256'])`.

### L4. External-IDP OIDC discovery runs on every request, bypassing the JWKS cache

`backend/backend/common/auth/authorizerCore.py:641` — `get_external_keys` calls `get_jwks_uri_for_external_idp` (OIDC discovery, up to 10s timeout) **before** the cache check at :647.
**Impact:** External-IDP deployments put a synchronous external HTTP dependency on every authenticated call. On discovery outage it falls back to `{issuer}/.well-known/jwks.json` whose cache key differs → cache thrashing and repeated JWKS re-downloads when the IdP is already degraded.
**Fix:** Cache the resolved `jwks_uri` (key on issuer); do discovery at most once per `CACHE_TTL`.

### L5. Physna viewer token returned to browser is tenant-scoped (cross-asset reach)

`backend/backend/handlers/addon/physna/physnaViewer.py:404` — VAMS Tier-1/Tier-2 authz is correctly enforced on the requested asset, but `_mint_viewer_token` requests a tenant-wide `/viewer/token` (no asset scope) and returns it to the browser with `tenantId` and `physnaApiBase`.
**Impact:** A caller authorized for one synced asset gets a tenant-wide Physna token; with knowledge/enumeration of other Physna UUIDs they can view geometry for assets they're not authorized to in VAMS. Actual scope is defined by Physna's tenant authz mode; documented as an accepted tradeoff (docstring 33-37).
**Fix:** Request an asset-scoped token if Physna supports it; else proxy the viewer bootstrap server-side or minimize TTL and document the residual exposure in the permission model.

### L6. OAuth2 refresh/access tokens persisted in `localStorage` (XSS-exfiltratable)

`web/src/utils/authTokenUtils.ts:108` — the full OAuth2Token (incl. long-lived refreshToken) is serialized to `localStorage["oauth2_token"]`.
**Impact:** A single XSS or supply-chain compromise in any bundled viewer/dependency can steal the refresh token → persistent refreshable access to the victim's full role set. (This is the standard Amplify v6 / `@badgateway/oauth2-client` SPA default, not VAMS-specific — an accepted-risk decision to make explicitly.)
**Fix:** Prefer httpOnly/Secure/SameSite cookie for the refresh token (backend token endpoint), access token in memory only; else tighten CSP (eliminate `unsafe-eval`) and document the tradeoff.

### L7. OAuth2 access-token refresh is not coalesced (concurrent refresh races)

`web/src/utils/authTokenUtils.ts:139` — `getDualValidAccessToken()` calls `oauth2Client.refreshToken()` directly on the request hot path, bypassing `sessionManager`'s purpose-built `inFlight` coalescing (sessionManager.ts:35-43).
**Impact:** On parallel calls after access-token expiry, each fires its own refresh. IdPs that rotate refresh tokens (Okta, Auth0-w/rotation, Cognito hosted UI) invalidate after first use → all-but-one fail with forced re-login. (Verifier note: the "clobber the rotated token" mechanism doesn't hold — failed refreshes throw before writing — so impact is spurious re-logins, not token loss.)
**Fix:** Funnel token acquisition through the shared coalescing primitive so only one refresh is in flight.

### L8. Cognito token fetch returns empty string instead of throwing

`web/src/utils/authTokenUtils.ts:154` — `session.tokens?.idToken?.toString() || ""`, asymmetric with the OAuth2 branch which throws. `getAuthHeaders()` then sends `Authorization: Bearer ` (empty).
**Impact:** One extra failed round-trip (401/403) before recovery; no bypass. Minor.
**Fix:** `if (!token) throw new Error('No valid Cognito token');` to route through the pre-request recovery path.

### L9. `assetLinks` queries read only the first page (silent truncation)

`backend/backend/handlers/assetLinks/assetLinksService.py:453` (also 222-231, 354) — no `LastEvaluatedKey`/`ExclusiveStartKey` anywhere; each query reads one ≤1MB page. Violates backend/CLAUDE.md Rule 14.
**Impact:** For a parent/child pair with enough links to exceed a page, the alias-conflict check misses an existing alias → duplicate alias created (uniqueness invariant broken); tree/list views under-report. (Verifier: conflict query is scoped to one exact asset pair, so this needs an unusually high link count — real but low-likelihood.)
**Fix:** Page all three queries to exhaustion (shared paginated helper).

### L10. `createWorkflow`/`createPipeline` uniqueness check is a TOCTOU race

`backend/backend/handlers/workflows/createWorkflow.py:524` (createPipeline.py:321) — `find_conflicting_database` full-table scan then unconditional `put_item`/`update_item`, no `ConditionExpression`.
**Impact:** Concurrent creates of the same id in two databases both succeed; id-only lookups become ambiguous. Also same-key clobber possible without a condition.
**Fix:** At minimum add `ConditionExpression=attribute_not_exists(...)`; for cross-partition uniqueness use a dedicated uniqueness table written with `attribute_not_exists` in a `TransactWriteItems`.

### L11. `editComment` error path sets `response['body']` instead of `response['message']`

`backend/backend/handlers/comments/editComment.py:77` — the dict is initialized `{statusCode:404, message:'Record not found'}`; the 500 branch sets `statusCode=500` and `body={...}` but leaves `message` unchanged, so the caller reports "Record not found" on a 500.
**Fix:** Set `response["message"] = "Internal Server Error"`.

### L12. `checkSubscriptionService` parses the body outside the try block → 502 on bad JSON

`backend/backend/handlers/subscription/checkSubscriptionService.py:74` — `json.loads` at :75 runs before the `try:` at :77, so `JSONDecodeError` is uncaught → 502 instead of 400.
**Fix:** Move parsing inside a `try/except JSONDecodeError` returning 400 (mirror addComment.py:89-96).

### L13. RapidPipeline writes config to a fixed shared S3 key (concurrent collisions)

`backendPipelines/multi/rapidPipeline/lambda/constructPipeline.py:69` — writes `Key="rp_config.json"` at the auxiliary bucket root, discarding the per-execution prefix; the container downloads the same fixed key.
**Impact:** Concurrent RapidPipeline runs with different `inputParameters` can read the wrong config → incorrect conversion output. Correctness bug under concurrency.
**Fix:** Namespace the key per execution (jobName/task token/UUID) and pass it into the command.

### L14. User-controlled metadata field name interpolated unescaped into OpenSearch `query_string`

`backend/backend/handlers/search/search.py:1281` — in `_build_metadata_search_query` the value is escaped but the `field_name` from `_extract_metadata_field_name` is not.
**Impact:** An authenticated caller can alter the parsed clause (broaden matching, inject expensive leading-wildcard/regexp terms → query DoS, or parse errors → 500). No cross-database disclosure (the accessible-DB restriction is a separate ANDed filter and per-hit Casbin re-check remains).
**Fix:** Escape/allow-list the field name (`[A-Za-z0-9_.-]`); prefer `term`/`exists`/`match` over free-form `query_string`.

### L15. `escape_opensearch_query_string` double-escapes backslashes

`backend/backend/handlers/search/search.py:493` — the loop escapes each special char in order but `\` sits near the end of `special_chars`, so backslashes inserted for earlier chars get re-escaped. `(a+b)` → `\\(a\\+b\\)`.
**Impact:** Search values containing special chars are mis-escaped → wrong/empty results or parse errors in a security-relevant sanitizer.
**Fix:** Escape `\` first (or single-pass `re.sub` with one alternation).

### L16. `createPipeline` Lambda granted `iam:PassRole` with account+name wildcard ARN

`infra/lib/lambdaBuilder/pipelineFunctions.ts:123` — `IAMArn('*'+config.name+'*').role` → `arn:{partition}:iam::*:role/*{config.name}*` (account wildcard + name substring wildcard). A correctly-scoped PassRole also exists at :86-92 (this is an extra broad grant); `buildWorkflowRole` :462-471 duplicates it.
**Impact:** A pipeline-creator could pass any same-account role whose name contains the config name (cross-account passing is blocked by IAM regardless of the wildcard, so narrower than it looks). Bounded by Casbin API-tier auth.
**Fix:** Pin account to `config.env.account`; tighten name wildcard to the actual pipeline-role prefix (e.g. `{config.name}-pipeline-*`); remove the duplicate statement.

### L17. Vague "Configured as intended." CDK Nag justifications (Rule 4)

`infra/lib/nestedStacks/searchAndIndexing/constructs/opensearch-serverless.ts:374` (and others) — boilerplate reasons that don't let a reviewer confirm the suppression is deliberate/safe (e.g. non-latest Lambda runtime `AwsSolutions-L1`).
**Fix:** Replace each with a specific justification (see the good example at `opensearch-provisioned.ts:315`).

### L18. Sequential handlers alias & mutate the module-global `STANDARD_JSON_RESPONSE`

`backend/backend/handlers/subscription/*.py`, `comments/*.py` — `response = STANDARD_JSON_RESPONSE` (constants.py:322) aliases a shared mutable dict rather than copying it.
**Impact:** _Not_ a concurrency race (Lambda is single-threaded per invocation) — the finder's race framing was refuted. But across sequential warm invocations, `statusCode`/`body` set on one request persist into the initial state of the next, so a handler that doesn't overwrite every field can leak a stale status/body.
**Fix:** `response = copy.deepcopy(STANDARD_JSON_RESPONSE)` (or build a fresh dict) per invocation.

---

## Latent trap (design note)

`request_to_claims` wraps `customAuthClaimsCheckOverride` in a bare `try/except: pass` (`backend/backend/handlers/auth/__init__.py:80-83`). The default override is a passthrough, so harmless today — but the scaffold explicitly invites customers to add claim-filtering/role-restriction logic there, and any exception in that logic would silently fail _open_ (claims pass unmodified). Recommend logging the exception and failing closed (drop to empty roles) if a custom hook throws.

---

## Refuted findings (investigated, not real / not material)

-   **EventBridge ARN `:event:` typo** (`service-helper.ts:102`): genuinely a typo, but VAMS-generated Step Functions workflows use only lambda/sqs/eventbridge integrations with the `waitForTaskToken` callback pattern — never the `.sync` service integration whose IAM the broken ARN would grant. No functional impact. (Worth fixing cosmetically.)
-   **`apiClient.buildUrl` SSRF / bearer-to-foreign-origin** (`apiClient.ts:73`): the base URL is app-controlled config, not user input; no attacker-controlled path reaches `buildUrl`.
-   **`get_subscriptions` pagination-before-auth hiding subscriptions** (`subscriptionService.py:97`): the frontend pages to exhaustion on NextToken and Casbin filtering is applied; no authorized subscriptions are hidden.
-   **`STANDARD_JSON_RESPONSE` as a _race condition_**: refuted as a race (single-threaded invocation) — but retained above as L18, a real cross-invocation state-leak.

## Steering-doc drift (spot-checked by lead; workflow drift agents did not converge)

-   Minor: `backend/CLAUDE.md` Quick Reference says "Runtime Python 3.13+" while the actual Lambda runtime is `LAMBDA_PYTHON_RUNTIME = Runtime.PYTHON_3_12` (`infra/config/config.ts:21`); root CLAUDE.md correctly says 3.12. The 3.13 marker is the dev/requirements Python. Clarify that Lambda runs 3.12, dev is 3.13+.
-   Minor: root CLAUDE.md says "10 nested stacks"; there are 11 directories under `infra/lib/nestedStacks/` (`addon` added for physna/garnet). Update the count.
-   Minor: `backend/CLAUDE.md` addon tree lists `physnaFileSync, physnaAssetSync, physnaViewer` but omits `physnaCommon.py`.
-   The major recent additions (SSM resource resolution, addon/physna + garnet, assetHistory, syncTracking, authorizer consolidation, MFA-via-context) are otherwise well reflected in the docs.

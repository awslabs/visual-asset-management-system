---
inclusion: fileMatch
fileMatchPattern:
    [
        "backend/backend/handlers/addon/edh/**",
        "backend/backend/models/addon/edh*",
        "backend/tests/handlers/addon/edh/**",
        "infra/lib/nestedStacks/addon/edhWorkstations/**",
        "infra/config/addon/edh*",
        "infra/test/addon/edh/**",
        "web/src/addons/edh/**",
        "web/e2e/edh*",
        "web/e2e/support/edh*",
        "tools/VamsCLI/vamscli/commands/addon/edh*",
        "tools/VamsCLI/vamscli/utils/addon/edh*",
        "tools/VamsCLI/tests/addon/test_edh*",
        "tools/VamsMCP/vams_mcp/addon_tools/edh*",
        "tools/VamsMCP/tests/addon/test_edh*",
        "backendAddons/edh/**",
        "documentation/docusaurus-site/docs/developer/edh-workstations/**",
        "documentation/docusaurus-site/src/components/ConfigBuilder/addon/edh*",
        "documentation/permissionsTemplates/edh-*",
        "documentation/diagrams/source/drawio/addon_edhWorkstations_*",
    ]
---

# EDH Workstations Add-on Steering (Kiro)

This document covers the EDH Workstations add-on: GPU workstations (Amazon DCV desktops) on an operator's EDH cluster, containerized simulation jobs, and versioned AMI recipes built with EC2 Image Builder. The add-on is enabled with `app.addons.useEdhWorkstations` and deploys as `EdhWorkstationsBuilderNestedStack` under `AddonBuilder`. It loads when you edit the add-on's files; every rule in the core steering documents (`BACKEND_CDK_DEVELOPMENT_WORKFLOW.md`, `CDK_DEVELOPMENT_WORKFLOW.md`, `WEB_FRONTEND.md`, `WEB_DEVELOPMENT_WORKFLOW.md`, `CLI_DEVELOPMENT_WORKFLOW.md`, `DOCUMENTATION_WORKFLOW.md`) applies here as well, and this document adds what is specific to EDH.

> **Steering Document Sync (bidirectional):** This document mirrors the Claude Code steering in `backend/backend/handlers/addon/edh/CLAUDE.md` (backend), `infra/lib/nestedStacks/addon/edhWorkstations/CLAUDE.md` (CDK) and `web/src/addons/edh/CLAUDE.md` (web), plus the EDH subsections of `tools/VamsCLI/CLAUDE.md` ("Add-on Commands") and `tools/VamsMCP/CLAUDE.md` ("Add-on Tools"). Whenever you change a rule, pattern, or convention here, make the equivalent change in the matching `CLAUDE.md` file(s) in the same change — and whenever those `CLAUDE.md` files change, reflect it back here. Keep the two sets of documents saying the same thing.

The add-on reaches core only through the add-on lane files each core steering document describes; core files name no add-on (`infra/test/addon/addonCoreSeparation.test.ts`). Each invariant below names the test that pins it.

| Component     | Source                                                                                         | Tests                                                                         |
| ------------- | ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Backend       | `backend/backend/handlers/addon/edh/`, `backend/backend/models/addon/edh.py`                   | `backend/tests/handlers/addon/edh/`                                           |
| CDK           | `infra/lib/nestedStacks/addon/edhWorkstations/`, `infra/config/addon/edhWorkstationsConfig.ts` | `infra/test/addon/edh/` (synth helpers in `support/edhSynth.ts`)              |
| Build sources | `backendAddons/edh/` (`buildspec.yml`, `amiRecipes/<recipeId>/`)                               | `infra/test/addon/edh/edhAl2023Recipe.test.ts`, `edhIsaacSimRecipe.test.ts`   |
| Web           | `web/src/addons/edh/`                                                                          | beside their sources; `web/e2e/edh.workstations.spec.ts`, `edh.admin.spec.ts` |
| CLI           | `tools/VamsCLI/vamscli/commands/addon/edh.py`, `tools/VamsCLI/vamscli/utils/addon/edh_*.py`    | `tools/VamsCLI/tests/addon/`                                                  |
| MCP           | `tools/VamsMCP/vams_mcp/addon_tools/edh.py`                                                    | `tools/VamsMCP/tests/addon/test_edh_tools.py`                                 |
| Documentation | `documentation/docusaurus-site/docs/developer/edh-workstations/` (seven pages)                 | `infra/test/addon/edh/docs/`                                                  |

---

## Backend

### Layout

`backend/backend/handlers/addon/edh/` holds only the Lambda entry points; shared modules live in `common/`.

| Entry point              | What it is                                                                                                                                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `edhWorkstationService`  | Workstation and catalog routes (`/addon/edh/workstations*`, `/addon/edh/catalog/*`): launch, list, get, connect, Windows sign-in, stop, start, renew, delete                                                      |
| `edhLaunchWorker`        | Asynchronous half of a launch: creates the EDH desktop and binds the session row to it                                                                                                                            |
| `edhAdminService`        | Admin routes (`/addon/edh/admin/*` but the AMI recipes): images, instances, capacity, health, lease settings, all under the EDH root key                                                                          |
| `edhAmiRecipeService`    | AMI recipe routes (`/addon/edh/admin/ami-recipes*`)                                                                                                                                                               |
| `edhAmiBuildRegistrar`   | Completes AMI builds: invoked by the Image Builder state-change rule and a 30-minute sweep that closes missed builds, retries failed registrations, starts queued builds and removes pruned AMIs no instance uses |
| `edhContainerService`    | Container routes (`/addon/edh/containers/*`): the catalog, and submit, list and kill of the caller's jobs, scoped to the caller's EDH uid                                                                         |
| `edhSessionReconciler`   | Scheduled sweep (every 15 minutes whenever the add-on is deployed) that reconciles session rows with EDH                                                                                                          |
| `edhCatalogRegistration` | Deploy-time custom resource: registers the predefined images with EDH and the container job definitions with AWS Batch, seeds AMI recipes with build-on-deploy builds, and seeds the lease policy row             |

| `common/` module         | What it is                                                                                                                                      |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `edhClient.py`           | Budgeted root-key REST client for the EDH API                                                                                                   |
| `edhIdentity.py`         | VAMS user to EDH uid mapping                                                                                                                    |
| `edhSessionState.py`     | Session-row states, the EDH `session_state` mapping, `lastError` codes, the never-auto-start schedule, the credential claim, `session_row_live` |
| `edhCatalogFields.py`    | Model to EDH software-stack field mapping, and `stack_architecture`, which the launch catalog and the admin image listing share                 |
| `edhLeasePolicy.py`      | The lease settings row and the pure lease decisions (`resolve_launch_lease`, `lease_action`)                                                    |
| `edhSessionBootstrap.py` | Per-session API key and Secrets Manager secret: mint, re-issue, bind, revoke                                                                    |
| `edhAmiRecipes.py`       | AMI recipe build lifecycle                                                                                                                      |
| `edhAmiRecipeStore.py`   | Recipe and build table access                                                                                                                   |
| `edhImageBuilder.py`     | EC2 Image Builder calls and component-document substitution                                                                                     |

The models are in `backend/backend/models/addon/edh.py`.

### Add-on lane entries

EDH registers everything core sees through the add-on lane files, never through a core list:

-   **Routes**: the `# EDH Workstations` section of `common/addonApiRoutes.py` (`EDH_ROUTES`, part of `ADDON_ROUTES`). `test_edh_api_routes.py` asserts every path, method, group and category; handlers dispatch on these constants.
-   **Permission object types and fields**: `edhWorkstation` (EDH Image ID, EDH OS Family, EDH Instance Type), `edhImage` (EDH Image ID, EDH OS Family) and `edhContainer` (EDH Job Definition), with the fields `edhImageId`, `edhOsFamily`, `edhInstanceType`, `edhJobDefinition`, in `common/addonConstants.py` (`test_edh_permission_object_types.py`).
-   **Log redaction keys**: `X-EDH-TOKEN`, `rootKey`, `connection_string`, `authToken`, `session_token`, `authentication_token`, `session_local_admin_password`, `broker_session_id` and `password` in `ADDON_LOG_SENSITIVE_KEYS` — the root-key header and secret field, a desktop's DCV tokens, the connection string that embeds one, the Windows sign-in password, and the throw-away password the client sends when it creates a directory user. Both the structured logger and `common/logRedaction.py` redact them (`test_edh_log_redaction_keys.py`).
-   **Resource-name keys**: `AddonResourceKeys.EDH_SESSION_STORAGE_TABLE` (`addon/dynamoTables/edhSessionStorage`) and `AddonResourceKeys.EDH_AMI_RECIPE_STORAGE_TABLE` (`addon/dynamoTables/edhAmiRecipeStorage`) in `common/addonResourceNames.py`, mirrored by `infra/common/addonResourceParamKeys.ts`, resolved with `get_table_name` and the break-glass `EDH_SESSION_STORAGE_TABLE_NAME` / `EDH_AMI_RECIPE_STORAGE_TABLE_NAME` (`test_edh_resource_keys.py`).
-   **Permission template decisions**: `tests/handlers/addon/edh/addonRouteGrants.py` (`ROUTE_GRANTS`) records the methods `edh-user.json` and `edh-admin.json` allow on each EDH route; the core template test evaluates them. No shipped template grants the container routes. `test_edh_permission_templates.py` checks the two templates' shape.

### Module constants at the top

The EDH source (the handler folder, `common/` among it, and `models/addon/edh.py`) and the EDH tests (`backend/tests/handlers/addon/edh/`) declare each module-level `UPPER_CASE` constant, one directly in a module-level `if`/`try`/`with` included, before the module's first function or class. A constant whose value reads a function or class of its module at import (a name only in a lambda's body does not count), directly or through other module-level names, follows the last definition it needs instead, with no other definition between them. The rest of the backend is not held to this (`test_edh_module_constants_at_top.py`).

### Session keys

A desktop's VAMS credential is a temporary session key (`BACKEND_CDK_DEVELOPMENT_WORKFLOW.md`, "A temporary session key cannot mint or extend a key"):

-   `edhSessionBootstrap.SESSION_KEY_NAME_PREFIX` is the core `TEMPORARY_SESSION_KEY_NAME_PREFIX` followed by `edh-`. A key is named `temporary-session-edh-<bootstrapId>` at launch and start, and a renewal's re-issue adds `-r<YYYYmmddHHMMSS>`, so the key it replaces keeps its name; `bootstrap_id_from_key_name` maps either name back. apiKeyService therefore refuses key create and update to a desktop holding one, and lets only the bootstrap's Lambda cross-call create a key under the prefix (`test_edh_session_key.py`).
-   The key is minted through a `lambdaCrossCall` invoke of apiKeyService on the admin create route as the launching user, carrying the user's real MFA state; the invoke is single-attempt and bounded by the request's deadline. The value reaches the desktop only through the Secrets Manager secret `<EDH_SESSION_SECRET_PREFIX><bootstrapId>`, whose read grant is bound to the desktop's EC2 instance once EDH places it (`test_edhSessionBootstrap_mint.py`, `test_edhSessionBootstrap_secrets.py`).
-   `revoke_session` deletes the key the row names and then every other key of the bootstrap it lists as `SYSTEM_USER`; `every_key=False` deletes only the named key. `delete_other_session_keys` is the listing half on its own, under the caller's deadline (`test_edhSessionBootstrap_secrets.py`).
-   `bootstrap_applies(row)` is the mint gate: false for a Windows row, which every mint answers `disabled`, while revoke and cleanup keep `is_enabled()` (`test_edhSessionBootstrap_windows.py`). The module loads with the feature's environment absent, and aborts the import when the feature is on and a variable is missing (`test_edhSessionBootstrap_mint.py`).
-   Launch, start, connect and the Windows sign-in answer `403` to a caller that authenticated with an API key (compared with `handlers.auth.AUTH_METHOD_API_KEY`), so a session key cannot start another desktop, obtain a DCV connection or read a Windows password (`test_edhWorkstationService_create.py`, `test_edhWorkstationService_session_create.py`, `test_edhWorkstationService_session_lifecycle.py`, `test_edhWorkstationService_connect.py`, `test_edhWorkstationService_windows_sign_in.py`). A renewal from an API-key caller renews the lease and re-issues nothing (`test_edhWorkstationService_renew.py`).
-   A renewal of an `ACTIVE` row with no start in progress re-issues the key under a credential claim (`credentialClaimedAt`, fresh for `CREDENTIAL_CLAIM_SECONDS`): it mints through `mint_reissue_key`, names the key on the row conditioned on the claim, `ACTIVE` and the `apiKeyId` read after the claim, writes the secret through `write_reissued_secret`, and releases the claim. A start is refused while a claim is fresh; it deletes the key the row names before the mint and, once EDH has accepted the start, every other key of the bootstrap, best effort within the request's deadline less `KEY_CLEANUP_RESERVE_SECONDS_START`. A rollback takes back only the minted key (`test_edhWorkstationService_renew.py`, `test_edhWorkstationService_session_lifecycle.py`).

### Reads of EDH desktop state

-   List, get, the start pre-read and the read after a lost stop, start or delete answer read desktop state through `EdhClient.get_desktop_states`, EDH's token-free `session_state` route, for the session ids of the caller's own rows only. Connect and Windows sign-in read the per-user `list` narrowed to the row's session through `get_desktop_connection`, at most one connection token per request EDH answers, and match the answer to the row by session id. No other route or handler module reads the per-user list (`test_edhWorkstationService_token_reads.py`).
-   `GET /addon/edh/workstations/{sessionId}/windows-sign-in` returns the user name and EDH's `session_local_admin_password` of the owner's running Windows desktop, a null password with `directoryAccount` for a domain-qualified name, audited as `workstationWindowsSignIn` and never logged (`test_edhWorkstationService_windows_sign_in.py`).

### Launch checks

-   A launch whose instance type the image's own nested `profile` lists only under architectures of another family than the image's (`x86_64` and `i386` are one family) is refused after Tier 2 with `400` `instanceType does not match the image's architecture`, before any EDH call, row, mint or worker. It passes to EDH's own check when the stack has no `ami_arch`, no readable `profile`, or lists the type under no architecture. A launch whose `rootSizeGb` is below the image's registered root size is refused after Tier 2. `models/addon/edh.py` `ARCHITECTURE_FAMILIES` and `architecture_family` are the family table this check and the AMI recipe checks share (`test_edhWorkstationService_create.py`).
-   The launch catalog's images carry `architecture` and its instance types `architectures`; `GET /addon/edh/catalog/images` adds `category` `amiRecipe` (with `recipeId`, `recipeDisplayName`, `recipeVersion`) for a stack an AVAILABLE AMI recipe build registered, else `baseline`. The recipe table is not read while `EDH_AMI_BUILDS_ENABLED` is not `true`, and a failed read lists every image `baseline` with a warning. Recipe images come first, each group in numeric-aware name order (`test_edhWorkstationService_catalog_images.py`, `test_edhWorkstationService_catalog_instance_types.py`).
-   `edhLaunchWorker` sends `session_type` `console` for a Windows row and `virtual` for any other, reads the session id back through `EdhClient.find_desktop_by_name` (owner-strict on `session_owner` and `session_name`), then sets the never-auto-start schedule as the row's EDH uid and stamps `scheduleAppliedAt`; a schedule failure is logged and left to the sweep, never orphaning or retrying the launch (`test_edhLaunchWorker.py`).

### The reconciler sweep

`edhSessionReconciler` re-reads each row before deciding and writes conditionally (`test_edhSessionReconciler.py`):

-   It takes the lease decision for a row before the ordinary reconciliation of that row (see the next section).
-   It revokes the credentials of stopped, orphaned, deleted or expired sessions while the environment names the apiKeyService function; a credential it cannot revoke is counted `skipped`, and a revoke of a row under a fresh credential claim is held and counted `deferred`.
-   A row whose desktop EDH's listing leaves out is recorded `DELETED` only after EDH's token-free session-state read confirms the desktop is gone; a desktop that read still reports is reconciled from it, and when the read fails every unlisted row is left for the next sweep (counted `deferred`), so one empty or truncated listing cannot detach the workstations. EDH's Save and Resume continues a desktop under a new session uuid with the same session name and owner: a row whose session EDH no longer lists follows the desktop EDH lists under its owner and session name (`_resumed_desktop`), and a row the sweep recorded `DELETED` because EDH stopped listing its desktop carries `edhUnlistedAt`, so the resumed desktop re-adopts it; the sweep finds such a row, which it no longer scans, by the bootstrap id the listed desktop's session name encodes (`_resumed_rows`, at most `MAX_RESUMED_ROW_LOOKUPS_PER_SWEEP` reads). A row deleted through VAMS carries no stamp and is never re-adopted.
-   It removes the secrets of desktops that are gone and stamps `DELETED` rows that hold no credential with the `expiresAt` TTL.
-   It applies the never-auto-start schedule once to every non-`DELETED` row with a session id EDH lists and no `scheduleAppliedAt`, only on a pass with nothing else to write or revoke, at most 50 schedule calls per sweep and none after the first EDH error other than not-found or within the last 120 seconds of the run. A later schedule change made through EDH's API is left as it is.
-   While session bootstrap is on, it deletes expired or aged orphan `temporary-session-edh-*` keys, keeping every unexpired key of a live row's bootstrap, and audits an orphan deleted past its expiry as `expire` and any other as `revoke`.

### The lease settings row

The session table carries one row that is not a session: the workstation lease policy, under the reserved `bootstrapId` value `SETTINGS#lease` (`common/edhLeasePolicy.py` `LEASE_SETTINGS_KEY`). Two properties make that safe, and both must hold for any row of this kind: the literal cannot collide with a session row's partition key, which is the raw client-generated `bootstrapId`, always a 36-character UUID; and the row carries no `state`, so the state-filtered scans that read session rows never yield it. The catalog-registration custom resource writes it once at deploy (insert-if-absent) and the admin settings route thereafter (`test_edhLeasePolicy.py`).

The reconciler reads the policy once per sweep. It stops a desktop past `leaseExpiresAt` and stamps `leaseStoppedAt`, deletes one whose grace window has elapsed, and skips the step entirely when the policy is disabled or could not be read: a lease action stops or deletes someone's desktop, so inaction is the safe direction on every failure (`test_edhSessionReconciler.py`).

### AMI recipes

-   A recipe POST with `duplicateOf` runs Tier 2 `GET` on the source under its own OS family and copies its `assets` and `assetPrefix`; create and update refuse a `stackName` another recipe uses, compared trimmed and without case (`test_edhAmiRecipeService.py`).
-   A recipe PUT runs Tier 2 `PUT` on the stored recipe, and also on the OS family the body names when it differs, so an update cannot move a recipe into a family the caller's role does not cover (`test_edhAmiRecipeService.py`).
-   Create, update and duplicate refuse with `400` (`edhAmiRecipes.RecipeInvalid`) a recipe whose build instance types, or whose `ami-` or `ssm:` parent AMI, EC2 reports as another architecture family than its `architecture`, and pass a failed lookup, a type or image of no known family and an `edhStack:` parent. A build start refuses with `409`, before any Image Builder object, a parent EC2 does not find and a parent or build instance type of another family (`test_edhAmiRecipes.py`, `test_edhAmiRecipeService.py`).
-   `edhAmiRecipeStore.content_hash` folds the asset prefix, assets, parameters and the deployment values a recipe's documents name only when the recipe carries them, so a recipe without them keeps its hash. The table name resolves at import or, when that fails, on first use in `_table` (`test_edhAmiRecipeStore.py`).
-   `edhImageBuilder.substitute_component_data` resolves `${ASSET_<id>}`, `${PARAM_<key>}` and `${DEPLOY_<key>}` (from the closed `DEPLOYMENT_PARAMETERS` map) in one pass before `create_component`, and refuses a document with an unresolved placeholder; `component_documents` applies the 16000-character limit to the resolved documents (`test_edhImageBuilder.py`).
-   A recipe delete removes every build row, the sweep's only record of an AMI it still has to remove, so `delete_recipe` refuses with `409` `a build's AMI removal is pending` (`AMI_REMOVAL_PENDING`) while a build of its consistent listing holds an AMI not yet deregistered and is beyond `keepVersions` with a `PRUNABLE` status or `RETIRED` with `amiRemovalPending`, and releases its delete mark. `prune_amis` and `delete_recipe` pick those builds through the one helper `_ami_removal_candidates` (`test_edhAmiRecipes.py` `TestDeleteWhileAnAmiRemovalIsPending`, `test_edhAmiRecipeService.py` `TestDeleteRefusals`).

### Backend tests

-   `backend/tests/handlers/addon/edh/conftest.py` pins the real `common.constants` (`edhRealConstants.install_real_constants`) and seeds the environment the EDH modules read at import, including the break-glass table names, so collection makes no SSM call.
-   **Captured EDH exchanges are sanitizer fixed points.** Every file under `tests/handlers/addon/edh/fixtures/` is a request/response envelope emitted by `fixtureSanitizer.py` (`sanitize_document` redacts the keys in `SECRET_KEY_NAMES`, drops the token header and maps hostnames), and it must stay a byte-for-byte fixed point of that function: `test_fixtures_are_sanitized.py` re-runs the sanitizer over every file and fails on any difference. Run a new capture through `sanitize_document` before committing it. The directory is prettier-ignored by the root `.prettierignore` entry `backend/tests/handlers/addon/*/fixtures/`, since reformatting a fixture would break the fixed point.

---

## CDK

### Layout

| File                                        | What it is                                                                                                                                                                                                                         |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `edhWorkstationsBuilder-nestedStack.ts`     | `EdhWorkstationsBuilderNestedStack`, nested in `AddonBuilderNestedStack` while `app.addons.useEdhWorkstations.enabled`; builds every add-on resource below and registers the `/addon/edh/*` routes with `registry: props.registry` |
| `lambdaBuilder/edhWorkstationsFunctions.ts` | `buildCommonEdhLambda`, one `buildEdh*Function` per handler, `applySessionBootstrapEnvironmentAndPermissions` and `applyEdhAmiBuildEnvironmentAndPermissions`                                                                      |
| `constructs/edhImageBuild-construct.ts`     | One Amazon ECR repository and AWS CodeBuild project per `containers.buildAndPushImages` entry                                                                                                                                      |
| `constructs/edhAmiBuild-construct.ts`       | The AMI build instance profile and security group, and the recipe bundle and asset uploads                                                                                                                                         |
| `edhWafExclusions.ts`                       | `buildEdhWafRuleExclusions`, the regional web ACL exclusion for the AMI recipe routes                                                                                                                                              |

All paths are under `infra/lib/nestedStacks/addon/edhWorkstations/`. The build sources the stack reads ship in `backendAddons/edh/`: `buildspec.yml` for the container image builds, and one directory per shipped AMI recipe under `amiRecipes/` (`EDH_SHIPPED_AMI_RECIPE_DIRS`: `al2023-desktop`, `windows2022-desktop`, `isaacsim`). `amiRecipes/.gitattributes` holds the recipe documents at LF, because the deployment uploads them byte for byte (`edhIsaacSimRecipe.test.ts`).

### CDK add-on lane entries

EDH reaches the core CDK app only through the add-on lane files (the "Add-on lanes" table in `CDK_DEVELOPMENT_WORKFLOW.md`):

-   **Configuration**: `infra/config/addon/edhWorkstationsConfig.ts` holds `ConfigEdhWorkstations`, its defaults, `validateEdhWorkstationsConfig`, `edhVpcRequiringFeatures` and `warnEdhImageBuildsOutsideCodeBuildPartitions`, joined into `getConfig()` through `addonConfig.ts`. The ConfigBuilder counterpart is `documentation/docusaurus-site/src/components/ConfigBuilder/addon/edhWorkstations.ts`.
-   **Feature switches**: `EDH_WORKSTATIONS`, plus `EDH_CONTAINERS` with `containers.enabled` and `EDH_AMI_RECIPES` with `amiBuilds.enabled`, in `common/addonAppFeatures.ts`, pushed by `AddonBuilderNestedStack`.
-   **Resource-name keys**: `addon/dynamoTables/edhSessionStorage` and `addon/dynamoTables/edhAmiRecipeStorage` in `common/addonResourceParamKeys.ts`, mirrored by `AddonResourceKeys` in `backend/backend/common/addonResourceNames.py`.
-   **Default constraints**: `addonDefaultConstraints.ts` seeds the admin role GET/PUT/POST/DELETE on `edhWorkstation`, `edhImage` and `edhContainer`, and the read-only role GET on `edhWorkstation` and `edhImage`, while the add-on is enabled; the constructs append them after the core constraints, whose indices do not move (`edhSeededConstraints.test.ts`).
-   **Read-only role routes**: `addonDefaultRoutes.ts` grants the read-only role the `/addon/edh/workstations` and `/addon/edh/catalog` API prefixes and the `/edh/workstations` web route, GET only and no admin prefix (`edhSeededConstraints.test.ts`).
-   **VPC interface endpoints**: `addonVpcEndpoints.ts` adds `BatchEndpoint` with containers on, and `ImageBuilderEndpoint`, `EC2Endpoint`, `SSMMessagesEndpoint` and `EC2MessagesEndpoint` with AMI builds on; the VPC stack creates one Batch endpoint when a pipeline also needs it (`edhVpcEndpoints.test.ts`).
-   **AWS WAF rule exclusions**: `addonWafExclusions.ts` returns `buildEdhWafRuleExclusions(config)` (see [AWS WAF](#aws-waf)).
-   **CSP origins**: none. The DCV desktop opens in a new tab, not in a frame.
-   **Core-test hook**: `enableRouteContributingAddons()` in `infra/test/addon/support/addonTestHooks.ts` turns on the add-on, containers and AMI builds, so the route parity test sees every `/addon/edh/*` route (`edhWorkstations.test.ts`).

### Configuration

-   `app.addons.useEdhWorkstations`: `enabled`, `edhApiEndpoint`, `edhAdminApiKeySecretArn`, `edhApiCaBundleSecretArn`, `edhApiTlsServerName`, `edhClusterId`, `autoProvisionUsers`, `edhAllowedInstanceTypes`, `vdi` (with `vdi.lease`, whose four values seed the admin-owned workstation lease policy on the first deploy that finds no settings row), `sessionBootstrap`, `amiBuilds`, `predefinedImages` and `containers`. The add-on deploys only in the `aws` and `aws-us-gov` partitions and requires `app.useGlobalVpc.enabled` (`edhWorkstationsConfig.test.ts`).
-   `validateEdhWorkstationsConfig()` performs one normalization: `useEdhWorkstations.containers.enabled` is forced to `false`, with a warning, while the add-on itself is off. `applyEdhWorkstationsDerived()` in the ConfigBuilder module mirrors exactly that assignment, and `applyDerived()` runs it through `applyAddonDerived()`. A change to the normalization changes both in the same change (`infra/test/addon/edh/docs/edhWorkstationsDocs.test.ts` "ConfigBuilder normalization").
-   An image the add-on would build with AWS CodeBuild in a partition outside the in-account CodeBuild partitions raises a synth warning (`edhContainersCodeBuildPartition.test.ts`).

### Stack invariants

-   **No EDH resource in a stock synth.** The shipped templates leave the add-on off, and their synths carry no EDH resource, admin service or registration resource (`edhWorkstations.test.ts`, `edhAdminAndRegistration.test.ts`).
-   **Lambdas in the VPC.** `buildCommonEdhLambda` places every add-on Lambda in the isolated subnets behind `EdhAddonLambdaSecurityGroup` whenever the add-on is on, whatever `useForAllLambdas` says, because the handlers reach the EDH ALB over the VPC. The group's egress is 443, plus one rule for the endpoint's port when `edhApiEndpoint` names another; its id is the `EdhAddonLambdaSecurityGroupId` output and the `resourceNames/securityGroups/edhAddonLambda` parameter (`edhWorkstations.test.ts`).
-   **Handlers and routes.** `edhWorkstationService` backs `/addon/edh/workstations*` and `/addon/edh/catalog/*`, `edhAdminService` the `/addon/edh/admin/*` routes other than the AMI recipes, `edhContainerService` the container routes (containers on), and `edhAmiRecipeService` the five `/addon/edh/admin/ami-recipes*` routes (AMI builds on). `edhLaunchWorker` is invoked asynchronously by the workstation service with two retries within a 20-minute maximum event age (`EDH_LAUNCH_WORKER_MAX_EVENT_AGE`); `edhSessionReconciler` runs on the every-15-minute `EdhSessionReconcilerSchedule` rule whenever the add-on is deployed (`edhWorkstations.test.ts`, `edhContainers.test.ts`).
-   **Table grants.** The container service, the AMI recipe service and the AMI build registrar opt out of the session-table grant (`sessionTable: "none"`); the catalog registration Lambda holds `readWrite` only to insert the reserved `SETTINGS#lease` row once, through a conditional put. With `amiBuilds.enabled`, the recipe service and the registrar hold the recipe table `readWrite`, the catalog registration Lambda a direct read/write grant, and the workstation service `amiRecipeTable: "read"` for the launch catalog's recipe categories; with it off no add-on Lambda holds the table (`edhWorkstations.test.ts`).
-   **Table names.** The stack publishes both table names with `publishAddonResourceName()`, and every Lambda that opens a table is created after both parameters (`tableNameReaders`), since the handlers resolve them when they load and the catalog registration runs during the deploy (`edhWorkstations.test.ts` "EDH table names").
-   **Tables.** The stack creates both tables with `addonDynamoDbTableDefaults()` (`addonStackHelpers.ts`), the storage key imported by ARN, and passes them to the builders as `EdhTables`; the storage stack holds no EDH table. `EdhSessionStorageTable` is keyed on `bootstrapId`, with `sessionUuidIndex` (PK `sessionId`), `userIdIndex` (PK `userId`, SK `createdAt`) and the TTL attribute `expiresAt` on terminal rows, and carries no stream, which the Garnet indexers would route by table-name substring. `EdhAmiRecipeStorageTable` is keyed on `pk`/`sk`, with `imageBuildArnIndex` (PK `imageBuildVersionArn`), no stream and no TTL (`edhSessionStorageTable.test.ts`).
-   **Session bootstrap.** The `EdhSessionSecretsKey` CMK (DESTROY, no alias) exists only with `sessionBootstrap.enabled`. `applySessionBootstrapEnvironmentAndPermissions` gives the workstation service, launch worker, reconciler and admin Lambdas the bootstrap environment always, and with the flag on the apiKeyService cross-call invoke and the per-session Secrets Manager grants under the configured prefix (`PutResourcePolicy` conditioned on `secretsmanager:BlockPublicPolicy`). The reconciler keeps the revoke subset (the invoke, `PutSecretValue`, `DeleteSecret`) with the flag off through `revokeWithoutBootstrap`. The add-on stack depends on ApiBuilder for `apiKeyServiceFunction`, never the reverse (`edhSessionBootstrap.test.ts`).
-   **Catalog registration.** `edhCatalogRegistration` is the `onEventHandler` of the `EdhCatalogRegistrationProvider` provider. Its `EdhCatalogRegistration` custom resource carries the predefined image lists as JSON-string properties plus `contentHash`, `registrationRevision` and a per-synth `Timestamp`, and reports per-item failures through the `EdhCatalogRegistrationWarnings` output (`edhAdminAndRegistration.test.ts`).
-   **Containers** (`containers.enabled`). `EdhImageBuildConstruct` creates, per `buildAndPushImages` entry, the repository `{config.name}-{app.baseStackName}-edh-{name}` (explicitly named and lowercased so an AWS Batch image reference fits 255 characters, DESTROY with `emptyOnDelete`, tagged `edh:visibility:<edhClusterId>`, pull policy from `containers.imagePullRoleArns`), an out-of-VPC CodeBuild project using `backendAddons/edh/buildspec.yml`, a start-build trigger and an `EdhImageBuildProject<name>` output. The catalog registration Lambda registers the job definitions with AWS Batch and is the one add-on Lambda with Batch IAM, only with containers on (`edhContainers.test.ts`, `edhImageBuildConstruct.test.ts`).
-   **AMI builds** (`amiBuilds.enabled`). `EdhAmiBuildConstruct` creates the build instance profile (the two Image Builder managed policies) and an egress-only security group, and uploads one `RecipeBundle-<recipeId>` deployment per seeded recipe directory under `addon/edh/amiRecipes/<bundleHash>/<recipeId>` (`EDH_AMI_RECIPES_ARTEFACTS_PREFIX`; the root artefacts deployment excludes `addon/*` from its prune) and one `RecipeAsset_<recipeId>_<assetId>` deployment per declared asset under `addon/edh/amiRecipes/<bundleHash>/<recipeId>/assets/<assetId>`. The build subnet is `amiBuilds.buildSubnetId`, else the first private subnet, else the first isolated subnet. The registrar runs on the `EdhAmiBuildStateRule` Image Builder state rule, which matches only images named with the deployment prefix, and the every-30-minute `EdhAmiBuildSweepSchedule`. `applyEdhAmiBuildEnvironmentAndPermissions` gives the recipe service, the registrar and the catalog registration Lambda the build target, `EDH_SESSION_SECRET_PREFIX` and Image Builder and EC2 grants scoped to the deployment prefix: AMI and snapshot tagging and deletion are conditioned on the `vams:deployment` tag value, and a tag write on the keys VAMS writes. Its `cancelsBuilds` option, which only the registrar passes, adds `imagebuilder:CancelImageCreation` on the deployment's images, and `deletes: false`, which only the catalog registration passes, leaves out the Image Builder deletes, `ec2:DeregisterImage` and `ec2:DeleteSnapshot`. The Image Builder pipelines, recipes, components and configurations are created at runtime from recipe content, not by CloudFormation (`edhWorkstations.test.ts` "EDH AMI builds").
-   **Recipe bundle hash.** Each uploaded tree is folded into the bundle hash through `walkFilesSorted`, whose exclusions and the `Source.asset` globs come from one list (`ASSET_EXCLUDE_GLOBS`), so hash and upload agree, and `hashedBytes`, which folds CRLF to LF unless a NUL byte in a file's first 8 KiB marks it binary, the rule CDK applies to its asset hash, so a Windows and a Linux checkout hash alike. Credential files (`.env`, `.env.*`) are never uploaded, and the build instance role reads only the `addon/edh/amiRecipes/` prefix of the artefacts bucket, under an `AwsSolutions-IAM5` suppression (`edhAmiRecipeAssets.test.ts`, `edhWorkstations.test.ts`).
-   **Shipped recipes.** The Amazon Linux 2023, Windows Server 2022 and Isaac Sim recipes, and the session sign-in components the Linux recipes share, are pinned by `edhAl2023Recipe.test.ts` and `edhIsaacSimRecipe.test.ts`.
-   **CDK Nag.** No error-level finding lands under the add-on's construct path, and each suppression is scoped to the resource it names (`edhNag.test.ts`).

### AWS WAF

With the add-on and `amiBuilds` on, `buildEdhWafRuleExclusions` gives the regional ACL one scoped exclusion. It sets the Common Rule Set's `EC2MetaDataSSRF_BODY` and `GenericRFI_BODY` to `count` and adds the `VAMS-EdhAmiRecipeBodyRules` rule, which blocks on their labels unless the URI path, as received and URL-decoded and normalized, starts with `/api/addon/edh/admin/ami-recipes`. The recipe bodies are Image Builder component scripts that read the instance metadata address, and VAMS never dereferences a URL in them. The exclusion's `unrelaxedRules` name the Common Rule Set's `SizeRestrictions_BODY` and the Linux, Unix, Windows, SQLi and PHP groups; the shipped policy raises no warning for them. The REST API paths under the exemption prefix are exactly the AMI recipe routes (`edhWafExclusions.test.ts`, `edhWafVariations.test.ts`, `edhWorkstations.test.ts`). The resources page's "AWS WAF exemption for AMI recipes" section is held to the same exclusion by `infra/test/addon/edh/docs/edhWafExemptionDocs.test.ts`.

### Resources the add-on creates

`docs/developer/edh-workstations/resources.md` lists them and `docs/developer/edh-workstations/uninstall.md` their teardown; a change to any of them updates both pages:

-   the `EdhSessionStorageTable` and `EdhAmiRecipeStorageTable` tables (RETAIN like every VAMS table, auto-named)
-   `EdhAddonLambdaSecurityGroup` (destroyed with the stack; id under the explicitly named `resourceNames/securityGroups/edhAddonLambda` parameter) and the two `resourceNames/addon/dynamoTables/*` parameters
-   the `{config.name}-{app.baseStackName}-edh-{name}[-kms]` repositories (DESTROY with `emptyOnDelete`, explicitly named; KMS with the VAMS key imported by ARN when the CMK is on, AES-256 otherwise, and the `-kms` suffix follows the encryption because a flip replaces the repository)
-   the per-session secrets `<secretNamePrefix><bootstrapId>`, created at runtime and removed by the add-on, not by `cdk destroy`
-   the `EdhSessionSecretsKey` CMK, the `EdhSessionReconcilerSchedule`, `EdhAmiBuildStateRule` and `EdhAmiBuildSweepSchedule` rules
-   the AWS Batch job definitions the registration Lambda registers and tags `edh:visibility:<edhClusterId>` (not stack resources; deregistered by supersession or by the operator)
-   the EC2 Image Builder pipeline, image recipe, components, infrastructure configuration and distribution configuration the AMI recipe Lambdas create per recipe under `{config.name}-{app.baseStackName}-edh-{recipeId}`, and the AMIs and snapshots their builds produce (not stack resources; removed by deleting the recipe or by the operator)

### Deploy checks

`/deploy-check` runs these for a configuration with the add-on enabled:

-   `amiBuilds.enabled` with a `buildSubnetId` that has neither NAT egress nor the EC2 Image Builder, Amazon EC2, `ssmmessages` and `ec2messages` interface endpoints: the build instance never reports back and the build runs to its timeout. With `buildSubnetId` empty, the build lands in the first private subnet, or in an isolated subnet when the VPC has none, where only the endpoints reach those services.

---

## Web

### Module layout

The module is built with **Tailwind + Radix** and reuses the orchestration primitives: `features/orchestration/components/*` (DataTable, Dialog, ContextMenu, RefreshButton, SearchInput, StatusBadge, Stepper, ConfigEditor, controlStyles, ToastProvider), `permissions/useAllowedRoutes`, the `api/client` helpers `toTuple` and `unwrapMessage`, and `types`. The dependency runs one way: the module imports core, and core reaches the module only through the add-on lane files.

```
web/src/addons/edh/
  pages/          # Route shells: EdhWorkstationsPage.tsx (renders workstations/WorkstationsPage, title "Workstations") and
                  #   EdhAdminPage.tsx (renders admin/EdhAdminPage, title "Workstation Admin"); keep page logic out of them
  admin/          # EdhAdminPage.tsx (Sessions / Capacity / Images / AMI Recipes / Settings tabs, opening on the first one the
                  #   caller can read, each panel gated by useAllowedRoutes().can()) ImagesTab InstancesTab (the Sessions panel)
                  #   CapacityTab (each profile's allowed instance types grouped by architecture) RegisterImageDialog
                  #   ConfirmActionDialog AmiRecipesTab AmiRecipeFormDialog AmiBuildsPanel (the AMI Recipes tab, EDH_AMI_RECIPES;
                  #   Duplicate opens the form prefilled from any recipe) SettingsTab (the lease policy, GET/PUT /addon/edh/admin/settings)
  api/            # workstations.ts catalog.ts admin.ts containers.ts amiRecipes.ts (the apiClient callers)
                  #   queries.ts adminQueries.ts containerQueries.ts amiRecipeQueries.ts (TanStack hooks, module-local qk, polling cadence)
                  #   outcome.ts (EdhRequestError, toOutcomeTuple, unconfirmedNotice: a mutation EDH did not confirm, a start in progress)
  components/     # WorkstationStatusBadge.tsx DeleteWorkstationDialog.tsx TabStrip.tsx (roving-tabindex tablist shared by both pages)
                  #   EdhFeatureTabs.tsx NotEnabledPanel.tsx (both pages' "not enabled" notice) useTextFilter.ts (the toolbar search
                  #   the three EDH lists share) useInFlight.ts (the synchronous double-submit guard every EDH mutation handler claims)
  workstations/   # WorkstationsPage.tsx (one h1 above the Workstations / Simulations strip; the Simulations tab is selected from the
                  #   path; Remaining column and Renew lease action driven by useEdhCatalogLease and labels.ts leaseRemaining)
                  #   simulationsTab.ts (the page's route and the Simulations route beneath it)
                  #   LaunchWorkstationWizard.tsx (optional `Expires in (hours)` field while the lease policy is enabled and not
                  #   forced; offers only the instance types that run the chosen image's `architecture` by the catalog rule, clears
                  #   an incompatible type when the image changes, and shows the architecture on the image card and in Review)
                  #   ConnectLink.tsx (click-driven: opens the tab, then requests the URL) WindowsSignInDialog.tsx (the owner's
                  #   Windows user name and masked password, fetched on open through a non-cached mutation and reset on close;
                  #   opened from the row's Show Windows password action)
  containers/     # SimulationsTab.tsx: container catalog, job submit/list/kill per backend (EDH_CONTAINERS; eks experimental);
                  #   containerRoutes.ts (route templates)
  types.ts        # API shapes
  labels.ts       # The words shown for the OS family and EDH state tokens
  edhLanes.test.tsx  # The module's lane entries, read through the real route table and side navigation
```

### Web lane entries

| Lane             | File                        | EDH entries                                                                                                                                                                        |
| ---------------- | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Routes           | `addons/addonRoutes.tsx`    | `/edh/workstations`, `/edh/workstations/simulations` (the same lazy page, opened on its Simulations tab) and `/edh/admin`; each `active` is the href of the nav link it highlights |
| Navigation       | `addons/addonNavigation.ts` | "Virtual Workstations" (My Workstations, Simulations) and "Admin - Workstations" (Workstation Admin). Lane order places both after the core "User" section                         |
| Feature switches | `addons/addonFeatures.ts`   | `EDH_WORKSTATIONS`, `EDH_CONTAINERS`, `EDH_AMI_RECIPES`, each declared in `infra/common/addonAppFeatures.ts`                                                                       |
| Tailwind content | `web/tailwind.config.js`    | The one line `./src/addons/edh/**/*.{ts,tsx}` (the shells live in `pages/`, inside the glob)                                                                                       |

The web routes sit under `/edh/workstations` and `/edh/admin`, so the `web` grants in `edh-user.json`, `edh-admin.json` and the read-only default (`starts_with` `/edh/workstations`) cover the Simulations route too.

### Feature switches

The pages read the switches through `useFeaturesEnabled()` (`context/RuntimeConfigContext.ts`), and the navigation entries carry them in `featuresEnabledRestriction`, so both follow the secure config when it lands after a first login.

-   `EDH_WORKSTATIONS` -- the Workstations and Workstation Admin pages and their two navigation sections. The web route exists in every deployment and the seeded admin role allows every route, while the API routes exist only where the add-on stack is deployed, so both pages render the "not enabled" panel without the switch rather than failing every request
-   `EDH_AMI_RECIPES` -- the **AMI Recipes** tab of the Workstation Admin page (`admin/AmiRecipesTab.tsx`); without it the tab is not in the strip
-   `EDH_CONTAINERS` -- the Simulations tab of the Workstations page and the "Simulations" nav link (`#/edh/workstations/simulations/`, restricted to `EDH_WORKSTATIONS` and `EDH_CONTAINERS`); the tab is shown to every caller and its panel is gated (`WEB_DEVELOPMENT_WORKFLOW.md` Rule 12): a caller who may not list jobs sees a no-access notice inside it

### Simulations route

With containers on, the Workstations page shows the Workstations / Simulations strip under one heading. The Simulations tab has its own route beneath the page's (`EDH_SIMULATIONS_PATH` in `workstations/simulationsTab.ts`): `WorkstationsAndSimulations` selects the tab with `useMatch(EDH_SIMULATIONS_PATH)` and switches it with `navigate(path, { replace: true })`, so the side navigation can link to and highlight either tab and a tab can be deep-linked. Both routes render the same lazy page component, so a tab switch keeps the page mounted. Keep the route paths in `addonRoutes.tsx` and the constants in `simulationsTab.ts` equal; `edhLanes.test.tsx` checks them.

### API layer

The module keeps its own `api/` directory (`WEB_FRONTEND.md` Rule 3 lists an add-on module's `api/` as a service directory): the services import the module's `types.ts` and the orchestration client's `toTuple`, which `services/addons/` could not do without the services layer depending on a feature module. Components never import `apiClient`.

-   The callers return `[boolean, data]` tuples through `toTuple`; the EDH mutations use `toOutcomeTuple` from `outcome.ts`, which keeps the status and body a page reads for an unconfirmed outcome (`EdhRequestError`).
-   The query hooks use the module-local `qk` factory (keys under `"edh"`, never colliding with the orchestration module's). `computeWorkstationRefetchInterval` polls every `WORKSTATION_POLL_TRANSITIONAL_MS` while a listed workstation is still changing state, every `WORKSTATION_POLL_STABLE_MS` once all are settled (EDH can still stop one on idle), and not at all for an empty list.

### Content Security Policy

The add-on contributes no CSP source: the DCV desktop opens in a new tab, not in a frame.

### Web tests

-   Unit tests sit beside their sources. `edhLanes.test.tsx` covers the lane entries end to end: each route is lazy, parameterless and highlights its link; the Simulations route renders the Workstations page and stays behind the web-route check; both sections sit after "User", follow the switches (also when they arrive after mount) and the web-route check. `addons/addonFeatures.test.ts` checks the switch names against `ADDON_APP_FEATURES`.
-   `jest.mock` paths into the orchestration module are `../../../features/orchestration/...` from a subdirectory of this module.
-   Playwright: `web/e2e/edh.workstations.spec.ts` and `web/e2e/edh.admin.spec.ts` are core specs that skip, naming the feature list, without `EDH_WORKSTATIONS`; `gotoEdh(page, route, heading)` in `web/e2e/support/edhFixtures.ts` opens a page and waits on its heading. Launching, stopping or deleting a desktop belongs in an untracked ad-hoc spec (`web/e2e/CLAUDE.md` Rule 2).
-   Some infra docs tests read files of this module by path (`grep -rn "web/src/addons/edh" infra/test`); moving a file means updating them.

---

## CLI and MCP

The lane files and their guards are in `CLI_DEVELOPMENT_WORKFLOW.md` "Add-on Commands and MCP Tools"; this section adds the EDH entries.

### Command group and client

-   `edh` (`commands/addon/edh.py`) has the `workstation`, `catalog`, `admin` (`images`, `ami-recipe`, `instances`, `capacity`, `health`, `settings`) and `container` sub-groups. Every command stacks `@requires_feature(FEATURE_EDH_WORKSTATIONS, ...)` after `@requires_setup_and_auth` (`container` uses `FEATURE_EDH_CONTAINERS`, `admin ami-recipe` uses `FEATURE_EDH_AMI_RECIPES`); the switch is a login-time snapshot. The three refusal sentences (`EDH_WORKSTATIONS_DISABLED_MESSAGE`, `EDH_CONTAINERS_DISABLED_MESSAGE`, `EDH_AMI_RECIPES_DISABLED_MESSAGE` in `addon_constants.py`) are the ones the MCP server's EDH pre-flight reports.
-   `EdhAPIClientMixin` (`utils/addon/edh_api_client.py`): every method passes `raise_http_errors=True`, because with the default `_make_request` converts a 404/400/5xx into a generic `APIError` before the method's own handler runs. `_edh_error` maps a 403 the methods read themselves to `PermissionDeniedError("Access forbidden. " + message)` and a 401 to `AuthenticationError`, and `_edh_session_id` checks a workstation id (one path segment, the service's `id_pattern`) before any request. The list routes return every row in one `{"Items": [...]}` page, so the commands take no paging options.
-   `utils/addon/edh_exceptions.py` holds the `EdhError` family; the class names are the `error_type` that `--json-output` and the MCP tools report. `EdhOutcomeUnknownError` carries `json_fields` `{"outcomeUnknown": True}` and `EdhStartInProgressError` `{"startInProgress": True}` on the class, the flags the API's own error body sets.

### Command semantics

-   `edh workstation list|create|get|start|stop|delete|connect|renew` -- the caller's own GPU desktops. `create` takes `--lease-hours` (1 up to the lease policy's `leaseHours`, honoured only while the policy is enabled and not forced, and overridden when it is forced; a value above `leaseHours` on an unforced policy, or any value while leases are disabled, answers 400) and answers 202 with a CREATING row: the launch is asynchronous, so follow it in `list` by `bootstrapId`, since `get` accepts a `bootstrapId` only until a `sessionId` exists, and an ORPHANED row carries `lastError`. An `--instance-type` the image's profile lists only under architectures of another family than the image's answers 400 `instanceType does not match the image's architecture`, whose help names `edh catalog instance-types --image-id`. `renew` resets a workstation's lease to the policy's current window (409 when leases are disabled, when the row carries no lease, or when it is deleted). A SESSION_ID may be the bootstrap id of a session-less launch; `delete` requires `--confirm`.
-   `edh workstation connect` prints only the DCV host in CLI mode; the `connectionUrl`, which carries a session auth token, is returned only with `--json-output`, and the log redactor treats `connectionUrl` as a credential key. `--show-windows-password` adds `windowsSignIn` (the Windows user name and password) through `get_edh_workstation_windows_sign_in`, which no MCP tool reads.
-   `edh catalog images|instance-types|lease` -- `lease` is the launch-side view of the lease policy (`enabled`, `leaseHours`, `forced`, `graceHours`, no audit fields). `images` prints one row per image with a Category column (`Custom AMI Recipe`, with the recipe and build version, listed first; `Baseline Image` otherwise) and an `ARCH` column (the image's `architecture`, `-` when null), while `--json-output` returns the API's `category`. `instance-types` prints `INSTANCE TYPE` / `ARCH` rows in the API's order, and `--image-id ID` keeps on the client only the types that run that catalog image by the launch wizard's rule (`instance_type_runs_image`: the image's `architecture` is null, the type's `architectures` is empty, or it contains the image's). An id the catalog does not list is `EdhImageNotFoundError` ("Image Not Found") naming `edh catalog images`, and a filter that empties a non-empty list prints the wizard's `No instance type in this deployment runs this image's architecture (<arch>). ...` with exit 0 and `{"Items": []}`.
-   `edh admin images list|register-vdi|register-simulation|delete-vdi|delete-simulation`, `edh admin ami-recipe list|get|create|update|duplicate|delete|build|builds|register|retire`, `edh admin instances list|stale|stop|deactivate`, `edh admin capacity|health`, `edh admin settings get|set` -- `delete-*`, `ami-recipe delete`, `ami-recipe retire` and `deactivate` require `--confirm`, and `settings set` refuses an empty patch as a `UsageError`, so it cannot rewrite `updatedAt` without changing anything.
-   `ami-recipe duplicate SOURCE_ID --recipe-id NEW` (optional `--display-name` and `--stack-name`, by default the source's plus ` (copy)` and ` Copy`) calls `duplicate_edh_ami_recipe`, which reads the source and posts its request fields with `duplicateOf` (the service copies the bundled `assets`); the MCP tool of the same name calls that method, so the copied field list (`_EDH_AMI_RECIPE_COPIED_FIELDS`, held to `AmiRecipeRequestModel` by `tests/addon/test_edh_client.py`) lives in one place.
-   A 404 for the recipe an `ami-recipe` command names (every command but `list` and `create`; the source for `duplicate`) is `EdhAmiRecipeNotFoundError` ("Recipe Not Found") naming `edh admin ami-recipe list`; `register` and `retire` tell it from `EdhAmiRecipeBuildNotFoundError` ("Build Not Found", naming `edh admin ami-recipe builds RECIPE_ID`) by the 404 body's message, exactly `Recipe not found` or `Build not found` (`_edh_ami_build_not_found`); any other 404 stays `APIError`.
-   `edh container catalog`, `edh container job submit|list|kill` -- `submit` requires `--job-queue` and takes `--memory` in MiB (at least 512); `list` and `kill` scope the experimental eks backend with `--cluster` (required) and `--namespace`; `kill JOB_ID` takes the listing's `jobId` and requires `--confirm`.
-   Option semantics, output and error texts are documented in `documentation/docusaurus-site/docs/developer/edh-workstations/cli-commands.md` and `cli-troubleshooting.md`; a change to a command updates those pages and is checked against the external connectors (`CLI_DEVELOPMENT_WORKFLOW.md`, MCP propagation).

### MCP tools

-   `addon_tools/edh.py` holds 16 read, 15 write and 7 destructive tools. Each opens with the `client.require_feature(...)` pre-flight: the container tools on `FEATURE_EDH_CONTAINERS`, the AMI recipe tools on `FEATURE_EDH_AMI_RECIPES`, and the rest on `FEATURE_EDH_WORKSTATIONS`, with the matching `EDH_*_DISABLED_MESSAGE` from `vamscli.addon_constants`. The add-on's list routes return every row in one `{"Items": [...]}` page and no `message` envelope, so the tools return the body as it comes, without `paginate()` or `unwrap_message()`. `EdhOutcomeUnknownError` and `EdhStartInProgressError` carry `outcomeUnknown` / `startInProgress` in their `json_fields`, which `@tool_result` passes through.
-   Compute: `create_edh_workstation`, `renew_edh_workstation`, `start_edh_workstation`, `stop_edh_workstation` and `stop_edh_admin_instance` (write tier) start, resume, extend the lease of, or interrupt a GPU desktop, `submit_edh_container_job` (write tier) starts a Batch/EKS job, `start_edh_ami_recipe_build` (write tier) starts an EC2 Image Builder build instance, and `deactivate_edh_admin_instance` and `kill_edh_container_job` (destructive tier) terminate one — all nine stay out of `autoApprove`.
-   `get_edh_workstation_connect` changes no stored data but issues a DCV `connectionUrl` carrying a session auth token — the same class as the presigned-URL tools — so it is the one read placed in the write tier (behind `VAMS_ENABLE_WRITES`, not always available), it never enters `autoApprove`, and its docstring and the README security note both say so.
-   No tool calls `get_edh_workstation_windows_sign_in`: the Windows password of a desktop is shown to its owner in the web app and behind `vamscli edh workstation connect --show-windows-password` only, never returned to an agent. The workstation tools reach the CLI methods that refuse an id which is not one path segment (`_edh_session_id`), and beneath every client method `_make_request` refuses a request path with an empty, `.` or `..` segment (raw or percent-encoded), so an id cannot address the sign-in route either.
-   The README lists the tools in the EDH subsection of "Add-on tools", with the safe reads in that subsection's `autoApprove` addition, never in the core sample.

### CLI and MCP tests

-   `tools/VamsCLI/tests/addon/test_edh.py` (commands) and `tests/addon/test_edh_client.py`, which holds the client to the backend: the route constants, the copied fields of `ami-recipe duplicate` (`_EDH_AMI_RECIPE_COPIED_FIELDS` against `AmiRecipeRequestModel`) and the 404 messages that tell a missing recipe from a missing build. A command test patches `generic_command_mocks('addon.edh')`.
-   `tools/VamsMCP/tests/addon/test_edh_tools.py` registers the module on a recording fake `mcp`, one `Config` per gate setting, and holds the tools to the absent Windows sign-in call, to `_edh_session_id` and to the README's EDH section; `tools/VamsCLI/tests/test_api_request_path.py` holds the `_make_request` path check.

---

## Documentation

-   The add-on's pages are the seven in `documentation/docusaurus-site/docs/developer/edh-workstations/`: `overview.md`, `enable.md`, `user-guide.md`, `resources.md`, `uninstall.md`, `cli-commands.md` and `cli-troubleshooting.md`, each listed in the sidebar's Add-ons category. Everything about the add-on is documented on these pages; core pages carry it only in the registries and rule-mandated entries `DOCUMENTATION_WORKFLOW.md` lists for an add-on (the `addons-overview.md` row, the `cli/command-reference.md` group row, `api/addon.md` and `VAMS_API.yaml`, `deployment/configuration-reference.md` "EDH Workstations (`app.addons.useEdhWorkstations`)" with its `:::note[Implemented by]` admonition, `additional/notices.md`, `additional/partner-integrations.md`, `additional/revisions.md`).
-   `infra/test/addon/edh/docs/placement.ts` names the identifiers that mark the add-on's internals and the core pages and sections allowed to name them; `infra/test/docs/coreDocsAddonPlacement.test.ts` applies it to every core page. A core page that needs an EDH identifier outside those sections gets an entry there with its reason, or the text moves to the add-on's pages.
-   `infra/test/addon/edh/docs/edhWorkstationsDocs.test.ts` holds the pages to the code: the feature switches, the configuration reference table against the shipped templates, the steering names (`get_edh_workstation_connect`, `useEdhWorkstations.containers.enabled`, the recipe deployment construct ids), the ConfigBuilder normalization, the page set and its sidebar place, the diagrams and their embeds, and the AMI recipe manifest keys and asset exclusions. `edhWafExemptionDocs.test.ts` holds the resources page's AWS WAF exemption section to `buildEdhWafRuleExclusions()`.
-   Diagrams follow the draw.io convention in `DOCUMENTATION_WORKFLOW.md` Rule 10, keyed `addon_edhWorkstations_<view>`: the source is `documentation/diagrams/source/drawio/<key>.drawio`, exported as one PNG with identical bytes in `documentation/diagrams/<key>.png`, `documentation/docusaurus-site/static/img/<key>.png` and `documentation/docusaurus-site/docs/assets/images/diagrams/<key>.png`. The keys are `addon_edhWorkstations_overview` (embedded in `overview.md` "How VAMS integrates with EDH", `enable.md` at the top and `user-guide.md` "How workstations work"), its black-background variant `addon_edhWorkstations_overview_dark` (embedded nowhere), `addon_edhWorkstations_network` (`enable.md` "Step 4: Connect the networks", `resources.md` "Network"), `addon_edhWorkstations_sessionBootstrap` (`overview.md` "Session bootstrap") and `addon_edhWorkstations_amiRecipes` (`overview.md` "AMI recipes"). No page outside the add-on's folder embeds an EDH diagram, and a new EDH diagram source joins the test's list.

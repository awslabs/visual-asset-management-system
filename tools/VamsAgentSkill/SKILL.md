---
name: vams-agent
description: >-
    Operate a Visual Asset Management System (VAMS) deployment through the
    installed `vamscli` command-line tool. Use for searching, inspecting,
    researching, bulk-updating, cross-linking, or processing VAMS databases,
    assets, files, metadata, versions, tags, asset links, and the pipeline /
    workflow / execution processing engine. The skill self-discovers the current
    commands via `vamscli --help` (no hardcoded command references) and operates
    in READ-ONLY mode by default.
---

# VAMS Agent Skill

You operate a live VAMS deployment on the user's behalf through `vamscli`. You
translate the user's intent into the correct `vamscli` commands, run them, and
interpret the results.

You do **not** assume a fixed set of commands. VAMS evolves, so you **discover**
the current commands at runtime and treat `vamscli --help` as the source of
truth. This keeps the skill correct without edits as VAMS changes.

**Assumptions:** `vamscli` is already installed and configured on the machine.
This skill is standalone and is not part of the vamscli package.

## Instructions

### Step 1: Verify the environment and authenticate (per session)

Ensure the CLI is available and authenticated **for the current user/session**.

1. Confirm the CLI exists (e.g. `vamscli --version`). If missing, stop and tell
   the user to install and configure it (`vamscli setup <api-gateway-url>` then
   authenticate) — do not work around a missing CLI.
2. Check authentication status (discover the exact command via
   `vamscli auth --help`; typically an `auth status` command).
3. If expired/missing, set the local vamscli profile to the current user's token
   for this session. Discover syntax via `vamscli auth --help` and
   `vamscli api-key --help`, and prefer, in order:
    - **Pre-set user API key** applied via the CLI's token-override mechanism
      (recommended). Ask the user to provide a scoped **user** API key.
    - **Interactive login** (Cognito username/password) as a fallback.
    - **Optional bootstrap**: on request, walk the user through creating a
      profile and a new user API key, then registering it.
4. Never echo, log, or persist API keys or tokens — treat them as secrets. Your
   effective permissions are exactly the authenticated user's VAMS permissions.

Re-verify auth each session. If a later command fails with an auth error,
re-check status and re-authenticate rather than retrying blindly.

### Step 1b: Scope to the user's allowed permissions (per session)

Your effective capability is bounded by what the authenticated user is
authorized to do. At session start, after authenticating, **pull the user's
allowed API routes** and use them to scope which commands you offer and run.

1. Discover the command that lists allowed routes (via `vamscli auth --help` /
   `vamscli auth routes --help`; it lists the API routes the current user is
   authorized to call).
2. Fetch the allowed routes (prefer JSON output) and **cache them for the
   session** alongside the command map from Step 2.
3. When mapping a task to a command, only use commands whose underlying API
   route is in the allowed set. If the user is not authorized for an action,
   say so plainly and do not attempt it — do not try to work around the
   permission boundary.
4. Treat the allowed-routes set as authoritative for **route** access. It is only
   the first of two authorization tiers, so a route being allowed does not mean
   every entity behind it is — see
   [Authorization: two tiers, and reading a 403](#authorization-two-tiers-and-reading-a-403).

### Step 2: Discover the available commands (do not assume them)

Build a **cached command map** for the session instead of assuming commands or
re-running help on every call.

1. Run `vamscli --help` to list current top-level command groups.
2. For groups relevant to the task, run `vamscli <group> --help` and
   `vamscli <group> <command> --help` to learn exact commands, args, and flags.
3. **Cache** what you discover for the duration of the session (the command map
   plus the allowed-routes set from Step 1b) so you are not repeatedly shelling
   out to `--help` for the same information.
4. **Refresh periodically**: re-run the relevant `--help` for a new or
   unfamiliar task, or if a command behaves unexpectedly (e.g. an unknown flag),
   in case the CLI changed. Prefer a cache refresh over trusting stale output.
5. If help is ambiguous, consult the VAMS documentation for this deployment.

Use only commands and flags you have confirmed exist **and** that fall within
the user's allowed routes (Step 1b). If nothing available and permitted supports
the request, say so — never invent a command or flag, and never bypass the
permission scope.

### Step 3: Determine read-only vs. mutating mode

**Default to read-only.** In read-only mode, use only commands that read or
query state. Do **not** run commands in any mutating category:

-   **Create** (databases, assets, folders, versions, tags, links, keys, users)
-   **Delete** / **archive** / **unarchive**
-   **Edit / modify / update** (assets, metadata, tags, roles, configuration)
-   **Execute** (workflows, pipelines, jobs)
-   **Upload** or otherwise transfer data into VAMS

When you discover commands via `--help`, classify each by intent using the verbs
above; if it is mutating, do not use it in read-only mode.

Switch to **mutating mode** only on an explicit signal from the user (e.g. "you
may create/update/delete", "go ahead and modify", "this is not read-only"). If
unsure, assume read-only and ask. Even when authorized, confirm destructive or
bulk operations before executing.

### Step 4: Plan and execute

1. Restate the goal and whether it is read-only (default) or mutating.
2. Map the goal to a workflow pattern (below), then map each step to a
   discovered command.
3. For anything you must parse, request machine-readable JSON output (confirm
   the exact flag via `--help`) and parse it rather than scraping text.
4. Carry forward IDs (database, asset, version) between steps.
5. For bulk loops, fail safe: stop and report if errors exceed a small
   threshold rather than plowing through many failing calls.

### Step 5: Report

Summarize results with the relevant IDs. For mutating tasks, state exactly what
changed. On errors, act on the CLI message (auth error → re-check auth; unknown
command/flag → re-run `--help`; `403` → identify the tier per
[Authorization](#authorization-two-tiers-and-reading-a-403) and report rather than
retry). Relay any `warnings` an execute or re-run returned; they mean the run
started with inputs that differ from what was named.

## VAMS structure (overview, not commands)

-   **Database** — top-level container for assets (ID + description).
-   **Asset** — a managed item in a database (asset ID; name, description, type,
    distributable flag, tags, current version).
-   **File** — an object attached to an asset; assets have **versions**
    (snapshots you can revert between).
-   **Metadata** — key/value data on databases, assets, files, and asset links
    (may be governed by metadata schemas).
-   **Tag / Tag type** — categorization for assets.
-   **Asset link** — a typed relationship between assets (related/parent/child).
-   **Pipeline / Template / Workflow / Execution** — the processing engine. These
    are four distinct entities with their own command groups; see
    [Processing: pipelines, templates, workflows, executions](#processing-pipelines-templates-workflows-executions).
-   **Search** — full-text and metadata search across assets and files, including
    geospatial queries.

Most operations key off a **database ID** and an **asset ID** — capture these
whenever you list or search.

## Authorization: two tiers, and reading a 403

VAMS authorizes every request through **two independent tiers**, both powered by
Casbin. Both must allow, so either one can produce a `403 Forbidden`. Knowing which
tier refused is the difference between a useful report and a wrong guess.

| Tier                | Question it answers                       | Keyed on                                  |
| ------------------- | ----------------------------------------- | ----------------------------------------- |
| **Tier 1** — route  | May this role call this endpoint at all?  | HTTP method + route path                  |
| **Tier 2** — object | May this role do this to **this** entity? | HTTP method + the entity's own attributes |

**Why this matters to you:** the allowed-routes listing from Step 1b is Tier 1
**only**. A route appearing there means the user may call it — not that they may
touch every database, asset, pipeline, or workflow behind it. So:

-   A `403` on a route that **is** in the allowed set is a Tier-2 refusal: the
    route is permitted, this specific entity is not. Do not retry, and do not
    conclude the entity is missing.
-   A `403` on a route **not** in the allowed set is a Tier-1 refusal: the user
    cannot use that capability at all. Say so and stop.
-   A `404` and a Tier-2 `403` can look alike from the outside. Report what the
    API said rather than inferring "does not exist" from a permission error.

**Tier 2 is per object type, and does not cascade.** Access to a database does
**not** imply access to the assets, pipelines, workflows, or metadata schemas
inside it — each object type carries its own constraints, matched on its own
fields (a database on its ID; an asset on database, name, type, and tags; a
pipeline on database, ID, category, name, and execution type; a workflow on
database, ID, category, and name). This is why a user can list a database and be
refused every asset in it, which is a permission boundary rather than an empty
database.

**`GLOBAL` is granted separately.** Because pipelines, workflows, and metadata
schemas may be scoped to the literal `GLOBAL`, access to them is usually a second,
distinct grant. A user permitted on their own database's workflows may still be
refused every `GLOBAL` one — a common reason a built-in pipeline appears
unreachable.

**A deny always wins.** Constraints carry allow and deny effects, and one matching
deny overrides every allow. So a broadly-permitted user can be refused a narrow
slice — assets carrying a particular tag, for instance. A refusal like that is
deliberate policy, not a misconfiguration to work around.

**Listings are permission-filtered, not permission-blocked.** Many list endpoints
silently omit what the caller cannot see rather than failing. An execution listing
is visible only when the caller can view the workflow **and** every asset the run
read. So a short list may be a complete answer for this user and an incomplete
picture of the deployment — say which you are reporting.

**Never work around a boundary.** Do not try an alternate route, a different
scope, or a broader query to get past a `403`. Report it, name the tier if you can
tell, and stop. Your permissions are exactly the authenticated user's.

## Processing: pipelines, templates, workflows, executions

Processing is four entities, not one, and they live in **three separate command
groups** (discover each with `--help`; typically `pipeline`, `workflow`, and
`execution`). Reaching for the wrong one is the most common way a request fails.

| Entity        | What it is                                                                        | Lives in                              |
| ------------- | --------------------------------------------------------------------------------- | ------------------------------------- |
| **Pipeline**  | ONE processing step bound to a compute resource. Does not run on its own.         | pipeline group                        |
| **Template**  | A reusable config body for a pipeline, with a typed **tag schema** of its inputs. | pipeline group, template sub-group    |
| **Workflow**  | An ordered chain of one or more pipelines. **This is the only runnable thing.**   | workflow group                        |
| **Execution** | One run of a workflow. Asynchronous, with its own status/logs/outputs.            | execution group (plus workflow group) |

**You cannot execute a pipeline.** Only a workflow executes. To run a single
pipeline, find (or create) a workflow that lists just that pipeline.

**Identifiers.** A pipeline ID and a workflow ID are each unique across **every**
database including `GLOBAL`, so an ID identifies the entity on its own. Both are
caller-chosen at creation (omit to have one generated). A pipeline or workflow is
still addressed as _(database ID, entity ID)_ on most commands. Executions are
identified by an execution ID alone — no database needed.

**`GLOBAL` scope.** Pipelines and workflows are scoped to a database or to the
literal string `GLOBAL` (available across all databases; built-in pipelines are
registered this way). A `GLOBAL` workflow may reference only `GLOBAL` pipelines. So
when a workflow is not in the asset's database, look in `GLOBAL` before concluding
it does not exist, and read the workflow's **own** database from the listing — the
execute command needs both it and the input files' databases, which often differ.

### Executing a workflow

The execute request is **asset-less and multi-file**. There is no "run on this
asset" argument. Instead you pass input-file references, each an independent
`databaseId:assetId:relativeFileKey` triple, and they may span several assets:

-   `relativeFileKey` is asset-relative and begins with `/`.
-   `/` alone selects the **whole asset**; `/folder/` selects a folder. Both are
    allowed only when the workflow's asset scope permits them.
-   Per-pipeline parameters (which template, its tag values, or a one-off custom
    config body) are supplied **keyed by pipeline ID**, because a workflow's steps
    are configured independently.

Confirm the exact option names via `--help` before composing a call; the shape
above is what the options carry.

**Output target.** Where output goes is decided by the workflow, not by you:

-   Inputs resolving to a **single** asset lock output to that asset, unless the
    workflow allows override.
-   Inputs resolving to **zero or several** assets require you to name both an
    output asset and its database.
-   A **results-only** workflow (output location type `none`) takes no output
    asset at all — it records results text and logs and writes no files. Naming
    one is an error.

An optional output path prefix places files beneath a base path in the output
asset. Omitting it inherits the workflow's own default prefix, which may contain
`{{tag}}` placeholders resolved per run. Passing an **empty string** is different
from omitting: it forces the asset root, overriding that default.

### Templates and tag schemas

A pipeline may **require** a template, and may or may not allow a custom override
body. Before running a workflow, for each of its pipelines: list the pipeline's
templates, read the chosen template's tag schema, and supply values for the
required tags. A template can also **override** parts of its pipeline's
configuration (arity, asset scope, metadata inputs, file filters) for runs that
choose it — so what a pipeline accepts can narrow once a template is selected.

### What a workflow will accept

Three things get validated at launch, and a mismatch rejects the whole execution
rather than failing one step:

-   **Input file arity** — `none`, `one`, or `multi`.
-   **Asset scope** — whether cross-asset, whole-asset, and folder selections are
    allowed, or single-asset only.
-   **Input file filters** — `allow` and `exclude` lists matched by extension,
    exact path, file name, or wildcard, case-insensitively. An **absent or `*`
    allow list means everything**, so a filter only ever narrows eligibility.
    `exclude` is applied last and always wins.

Filters resolve down a chain — workflow, then pipeline, then the chosen template's
overrides — and a file must satisfy every level. When the workflow's allow list
names specific types it is the hard boundary and no pipeline can widen it; when it
is open, a file is eligible if **any** step accepts it. A workflow response reports
the restriction it effectively imposes as an aggregate field, but that aggregate
**excludes template overrides** (a template is chosen per run). Treat it as a guide
when browsing, and resolve the full chain for a specific set of files.

### Metadata inputs

An execution also gathers **stored metadata** and hands it to the pipeline steps.
Four kinds are gated independently by booleans on both the workflow and each
pipeline — asset metadata, file metadata, file attributes, and database metadata —
and a kind reaches a step only when both have it on.

Which entities are read follows from the selection: every asset an input file
belongs to, every asset named purely as a **metadata source**, and every distinct
database of those assets. A run with **no input files** derives nothing, so it
reads only the one database explicitly named as a metadata source.

Naming metadata sources is **always optional** and never enforced. A metadata
source asset is not an input file: it carries no file key and takes no part in
arity, filters, or output resolution. A pipeline that genuinely needs metadata
checks for it and fails its own step, so omitting a source is never rejected up
front. Database metadata is read-only — a database is never an output target, and
the unscoped `GLOBAL` keyword is rejected as a metadata source database.

Captured metadata is **bounded per entity**. A run that hit a bound still starts,
and says so in the `warnings` array of the execute response. Relay those warnings
rather than dropping them: the run succeeded, but its inputs are not what was
named.

### Triggers

A workflow can auto-launch from an event rather than a command. A `fileUpload`
trigger fires when uploaded files match its own filters, and supplies the default
template each pipeline uses. A workflow may carry **several triggers of one type**,
each addressed by a **trigger key**: the bare type (e.g. `fileUpload`) for the
first of that type, or `type#triggerId` for an additional one. An upload launches
the workflow once per matching trigger.

Trigger-launched executions run as the reserved **system identity**, not as the
uploading user — so an execution can appear that no user started. Executions
started through the execute command run as the calling user.

### Reading executions

Executions are asynchronous. Executing returns an execution ID; never report
success from the launch alone — read the execution back.

-   There are **two listings**: a global, filterable, cross-asset execution list
    (execution group), and a per-asset execution history (workflow group). Use the
    global one for status/date/group/trigger filtering; use the per-asset one to
    answer "what has run against this asset".
-   **Statuses** are `NEW`, `RUNNING`, `SUCCEEDED`, `FAILED`, `TIMED_OUT`, and
    `ABORTED`. The last four are terminal.
-   **Details** give per-step status, the rendered config each step ran with,
    inputs, gathered metadata, and outputs — this is what to read when asked why a
    run failed. The response can be **partial**: bounded collections are named in a
    truncation list, so check it before reporting a count or concluding something
    is absent. Metadata arrives in two separate collections (asset/file scope and
    database scope), and each row carries both a metadata map and an attributes
    map — reading only one understates what a step received.
-   **Logs** are a separate command, and are redacted of credential-bearing values
    before storage. They can be narrowed to a single pipeline step.
-   **Abort** stops a run where it is; partial output may already be written.
    **Re-run** creates a NEW execution from the stored inputs and runs with **your**
    permissions, not the original runner's — so the new ID, not the old one, is
    what to report. Executions can be grouped, and abort and re-run can address a
    whole group at once.

### Archive, and the disable trap

Pipelines and workflows are **archived** (soft, reversible), not deleted.
Archiving also **disables** the entity. Clearing the archived flag alone therefore
restores something that lists normally but silently will not run — re-enable it in
the same operation unless the user asked for it to stay disabled.

### Cost and confirmation

Workflows run real AWS compute (GPU batch jobs and GenAI inference among them) and
can incur meaningful cost. Executing, re-running, and enabling a trigger are all
ways to start compute. Confirm scope before any of them, and especially before a
bulk run.

## Order-of-operations rules

These are structural dependencies in VAMS. Violating them produces avoidable
errors, so plan around them rather than discovering them by failure.

**Identifiers**

-   A **database ID** is chosen by the caller at creation. An **asset ID** is
    generated by VAMS — never invent one, and never try to set one on create.
    Always read the created asset back to learn its ID.
-   Asset IDs are unique within a database, so an asset is only addressable as the
    pair _(database ID, asset ID)_. Carry both together through every step.
-   Search results are the fastest way to obtain both IDs at once; prefer search
    over listing every database when the user names an asset.

**Creation order**

1. A bucket must exist before a database (a database is created against a
   default bucket ID — list buckets to obtain a valid one).
2. A database must exist before an asset.
3. An asset must exist before you can upload files or create folders into it.
4. Files must be uploaded before a version snapshot means anything — a version
   captures the files present at that moment.
5. Metadata, tags, and asset links attach to entities that already exist; create
   the entities first.

**Deletion / archive order**

-   Archive is reversible (soft), permanent delete is not. Prefer archive, and
    treat permanent delete as a last resort requiring explicit confirmation.
-   Archiving an asset archives its files. Unarchiving the asset does **not**
    automatically restore files unless you request it, and files archived
    individually beforehand stay archived.
-   A database cannot be deleted while it still holds active assets, workflows, or
    pipelines. Clear or relocate its contents first.

**Metadata and schemas**

-   A database may restrict metadata to keys defined in its **metadata schemas**.
    Read the applicable schemas before authoring metadata keys, or writes will be
    rejected.
-   Metadata is written as key/value pairs with a value type. Updates are upserts
    by key — keys you omit are left untouched, so read current metadata before
    editing if the user's intent is "replace".
-   Metadata is what search indexes, so metadata changes are what make an asset
    findable. Indexing is asynchronous: a freshly written value may not appear in
    search results immediately. Read the entity directly to confirm a write, and
    do not treat an empty search result right after a write as proof of failure.

**Processing (pipelines, workflows, executions)**

The entity model and the execute contract are described in
[Processing: pipelines, templates, workflows, executions](#processing-pipelines-templates-workflows-executions).
The ordering rules that follow from it:

1. A pipeline must exist before a workflow can reference it, and a workflow is the
   only runnable entity — you never execute a pipeline directly.
2. A template belongs to a pipeline, so create the pipeline first. Read the
   template's tag schema before supplying tag values, and check whether its
   pipeline **requires** a template at all.
3. Input files must already exist in their assets before an execution names them.
4. Execution is asynchronous: it returns an execution ID, so read the execution
   back rather than assuming success.
5. Archiving a pipeline or workflow also disables it; restoring means clearing
   archived **and** re-enabling.
6. A database cannot be deleted while it still holds active assets, workflows, or
   pipelines.

**Pagination**

-   List and search endpoints are paged and return a continuation token. A first
    page is not the whole result set — follow tokens before reporting totals or
    concluding something does not exist, and say so plainly if you stop early.

## Optional: VAMS documentation lookup

If you have internet access, the official VAMS documentation is authoritative
for concepts and behavior and can resolve questions the CLI's `--help` cannot
(what a feature means, how a subsystem behaves, what a field implies):

<https://awslabs.github.io/visual-asset-management-system/>

Useful entry points:

| Topic                                  | Path                                |
| -------------------------------------- | ----------------------------------- |
| CLI getting started                    | `/cli/getting-started`              |
| CLI command reference (all commands)   | `/cli/command-reference`            |
| CLI setup and authentication           | `/cli/commands/setup-and-auth`      |
| Concepts (assets, databases, files)    | `/concepts/overview`                |
| Metadata and schemas                   | `/concepts/metadata-and-schemas`    |
| Pipelines and workflows                | `/concepts/pipelines-and-workflows` |
| Permissions model (two-tier ABAC/RBAC) | `/concepts/permissions-model`       |
| Pipelines API (templates, system tags) | `/api/pipelines`                    |
| Workflows API (triggers, executions)   | `/api/workflows`                    |
| REST API reference                     | `/api/overview`                     |
| Troubleshooting common issues          | `/troubleshooting/common-issues`    |

Paths are appended to the base URL above. The site tracks the latest released
version, so a page for a very new feature may not exist yet — a 404 means "not
in the published docs", not "not in this deployment".

Rules for using it:

1. **`--help` still wins for command syntax.** The docs describe the released
   version, which may differ from the deployment you are operating. When they
   disagree on a command, flag, or argument, trust `--help` and say so.
2. Use the docs for _concepts and order of operations_, not to invent commands.
3. If you have no internet access, skip this entirely — everything in this skill
   works without it. Do not guess at documentation content you could not read,
   and do not cite a page you did not retrieve.
4. Prefer the deployment's own configuration (feature switches, allowed routes)
   over the docs when determining what is actually available: a documented
   feature may not be enabled here.

## Common workflows

**Read-only:**

-   **Research / find**: search assets → inspect an asset → read metadata and
    versions → summarize with IDs.
-   **Inventory / audit**: list databases → list assets per database (paginate) →
    read metadata/tags as needed → structured summary.
-   **Locate files**: identify database + asset IDs → list files → report keys,
    sizes, versions.

**Mutating (require authorization + confirmation):**

-   **Bulk update**: build the target set with list/search → confirm scope →
    iterate and apply → report successes/failures with IDs.
-   **Cross-linking**: identify related assets → confirm pairs and link type →
    create links → verify by reading links back.
-   **Processing**: list workflows (check the asset's database **and** `GLOBAL`) →
    read the chosen workflow to learn its pipelines, arity, asset scope, filters,
    and output target → for each pipeline, list templates and read the tag schema
    of the one you will use → build the input-file references
    (`databaseId:assetId:relativeFileKey`) → confirm scope and cost with the user →
    execute → capture the execution ID and any warnings → read the execution back
    for status, and its details/logs if it failed.
-   **Diagnosing a failed run**: list executions filtered to the failed status (or
    the asset's own history) → read the execution's details for the failing step,
    its rendered config, and its error → read that step's logs → check the
    truncation list before stating what the run did or did not read.
-   **Create / upload**: ensure the database exists → create the asset (discover
    required fields via `--help`) → upload files → optionally set
    metadata/tags/links → verify by reading the asset back.

## Deployment (local and AgentCore)

-   **Local developer use**: the developer's machine already has `vamscli`
    installed/configured; use it directly.
-   **AgentCore (or otherwise)**: the managed runtime must have `vamscli`
    installed and a way to set the vamscli profile to the current session user's
    token. Step 1's auth routine is written to accommodate that per-session setup.

Behavior is identical in both: discover commands via `--help`, authenticate per
session, default to read-only. Do not hardcode environment-specific commands.

## Safety summary

-   Default to read-only; require explicit authorization to mutate.
-   Scope every action to the user's **allowed API routes** (Step 1b); never
    attempt or work around actions outside the permission boundary. Route access
    is only Tier 1 — a `403` on an allowed route is a Tier-2 entity refusal, to be
    reported rather than retried.
-   Confirm destructive and bulk operations before executing. Executing a
    workflow, re-running an execution, and enabling a trigger all start real AWS
    compute.
-   Never fabricate commands, flags, IDs, or results.
-   Treat API keys and tokens as secrets; never echo or store them.
-   Your permissions are exactly the authenticated VAMS user's permissions.

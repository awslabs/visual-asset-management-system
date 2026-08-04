---
name: vams-agent
description: >-
    Operate a Visual Asset Management System (VAMS) deployment through the
    installed `vamscli` command-line tool. Use for searching, inspecting,
    researching, bulk-updating, cross-linking, or processing VAMS databases,
    assets, files, metadata, versions, tags, asset links, and workflows. The
    skill self-discovers the current commands via `vamscli --help` (no hardcoded
    command references) and operates in READ-ONLY mode by default.
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
4. Treat the allowed-routes set as authoritative for access control; the user's
   token/role determines it, and it can differ per user and per session.

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
command/flag → re-run `--help`).

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
-   **Workflow / Pipeline** — configured processing (conversion, previews,
    extraction, GenAI) run against assets.
-   **Search** — full-text and metadata search across assets and files, including
    geospatial queries.

Most operations key off a **database ID** and an **asset ID** — capture these
whenever you list or search.

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

**Workflows**

-   A workflow belongs to a database, or is **global**. Executing one requires
    both the asset's database and the workflow's own database — they are not
    always the same, so read the workflow's database from the workflow listing
    rather than assuming the asset's.
-   Execution is asynchronous. It returns an execution ID; poll the executions
    list for status instead of assuming success.
-   Workflows run real AWS compute and can incur meaningful cost, especially GenAI
    and 3D processing pipelines. Confirm scope (how many assets) before bulk runs.

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
-   **Processing**: identify targets + workflow → confirm (may incur AWS cost) →
    execute per asset → monitor executions.
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
    attempt or work around actions outside the permission boundary.
-   Confirm destructive and bulk operations before executing.
-   Never fabricate commands, flags, IDs, or results.
-   Treat API keys and tokens as secrets; never echo or store them.
-   Your permissions are exactly the authenticated VAMS user's permissions.

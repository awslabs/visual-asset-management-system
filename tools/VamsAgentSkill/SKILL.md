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

- **Create** (databases, assets, folders, versions, tags, links, keys, users)
- **Delete** / **archive** / **unarchive**
- **Edit / modify / update** (assets, metadata, tags, roles, configuration)
- **Execute** (workflows, pipelines, jobs)
- **Upload** or otherwise transfer data into VAMS

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

- **Database** — top-level container for assets (ID + description).
- **Asset** — a managed item in a database (asset ID; name, description, type,
  distributable flag, tags, current version).
- **File** — an object attached to an asset; assets have **versions**
  (snapshots you can revert between).
- **Metadata** — key/value data on databases, assets, files, and asset links
  (may be governed by metadata schemas).
- **Tag / Tag type** — categorization for assets.
- **Asset link** — a typed relationship between assets (related/parent/child).
- **Workflow / Pipeline** — configured processing (conversion, previews,
  extraction, GenAI) run against assets.
- **Search** — full-text and metadata search across assets and files, including
  geospatial queries.

Most operations key off a **database ID** and an **asset ID** — capture these
whenever you list or search.

## Common workflows

**Read-only:**
- **Research / find**: search assets → inspect an asset → read metadata and
  versions → summarize with IDs.
- **Inventory / audit**: list databases → list assets per database (paginate) →
  read metadata/tags as needed → structured summary.
- **Locate files**: identify database + asset IDs → list files → report keys,
  sizes, versions.

**Mutating (require authorization + confirmation):**
- **Bulk update**: build the target set with list/search → confirm scope →
  iterate and apply → report successes/failures with IDs.
- **Cross-linking**: identify related assets → confirm pairs and link type →
  create links → verify by reading links back.
- **Processing**: identify targets + workflow → confirm (may incur AWS cost) →
  execute per asset → monitor executions.
- **Create / upload**: ensure the database exists → create the asset (discover
  required fields via `--help`) → upload files → optionally set
  metadata/tags/links → verify by reading the asset back.

## Deployment (local and AgentCore)

- **Local developer use**: the developer's machine already has `vamscli`
  installed/configured; use it directly.
- **AgentCore (or otherwise)**: the managed runtime must have `vamscli`
  installed and a way to set the vamscli profile to the current session user's
  token. Step 1's auth routine is written to accommodate that per-session setup.

Behavior is identical in both: discover commands via `--help`, authenticate per
session, default to read-only. Do not hardcode environment-specific commands.

## Safety summary

- Default to read-only; require explicit authorization to mutate.
- Scope every action to the user's **allowed API routes** (Step 1b); never
  attempt or work around actions outside the permission boundary.
- Confirm destructive and bulk operations before executing.
- Never fabricate commands, flags, IDs, or results.
- Treat API keys and tokens as secrets; never echo or store them.
- Your permissions are exactly the authenticated VAMS user's permissions.

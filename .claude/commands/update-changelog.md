# Update CHANGELOG

Generate CHANGELOG.md entries by analyzing recent git commits. Categorizes changes and formats them to match the existing VAMS CHANGELOG style (standard-version format).

## Instructions

You are generating CHANGELOG entries for the VAMS project. The CHANGELOG follows a specific format that must be preserved exactly.

### Step 1: Determine Version

If $ARGUMENTS contains a version number (e.g., `2.5.1`, `2.6.0`), use that version. Otherwise:

1. Read the current CHANGELOG.md to find the latest version
2. Determine the next logical version:
    - If changes include BREAKING CHANGES -> bump major version
    - If changes include new Features -> bump minor version
    - If changes are only Bug Fixes/Chores -> bump patch version

### Step 2: Analyze Git Commits

Run these git commands to gather commit information:

1. Find the last version tag or identify the commit range:

    ```
    git log --oneline --since="YYYY-MM-DD" (date of last version)
    ```

    Or if version tags exist:

    ```
    git log --oneline v{lastVersion}..HEAD
    ```

2. Get detailed commit messages:

    ```
    git log --format="%H %s%n%b" v{lastVersion}..HEAD
    ```

3. Get changed files to determine component prefixes:
    ```
    git log --name-only --format="%H %s" v{lastVersion}..HEAD
    ```

### Step 3: Categorize Commits

Group commits into these categories (in this order):

1. **Major Change Summary:** - Brief 1-2 sentence overview (only for minor/major versions)
2. **BREAKING CHANGES** - Prefixed with warning emoji: `### BREAKING CHANGES`
3. **Features** - New functionality: `### Features`
4. **Bug Fixes** - Defect corrections: `### Bug Fixes`
5. **Chores** - Maintenance, refactoring, tooling: `### Chores`

### Step 4: Determine Component Prefixes

Based on which files were changed, prefix entries with the appropriate component tag:

| File Path Pattern   | Prefix                       |
| ------------------- | ---------------------------- |
| `web/`              | **Web**                      |
| `backend/backend/`  | **Backend**                  |
| `infra/`            | **CDK**                      |
| `tools/VamsCLI/`    | **CLI**                      |
| `backendPipelines/` | **Pipeline**                 |
| `documentation/`    | **Docs**                     |
| Multiple areas      | No prefix (or list multiple) |

### Step 5: Format Entries

Follow this exact formatting from the existing CHANGELOG:

```markdown
## [{version}] (YYYY-MM-DD)

### Major Change Summary:

Brief description of the major themes in this release.

### BREAKING CHANGES

-   Description of breaking change

### Features

-   **Web** Description of web feature
    -   Note: Additional details or caveats as sub-bullets
-   **Backend** Description of backend feature
-   **CDK** Description of infrastructure change
-   Description without prefix for cross-cutting changes
    -   **Web** Web-specific part of the feature
    -   **CLI** CLI-specific part of the feature

### Bug Fixes

-   **Web** Fixed description of what was fixed
-   **Backend** Fixed description of backend fix
-   **CLI** Continued fixes to various CLI commands...

### Chores

-   **Web** Description of web maintenance task
-   Description of general maintenance task

### Known Outstanding Issues

-   Description of known issue (only add new ones, keep existing ones)
```

**Formatting rules:**

-   Use `-   ` (dash + 3 spaces) for top-level bullets
-   Use ` -` (4 spaces + dash + 3 spaces) for sub-bullets
-   Bold component prefixes: `**Web**`, `**Backend**`, `**CDK**`, `**CLI**`, `**Pipeline**`
-   Write in past tense for bug fixes ("Fixed..."), present/past for features ("Added...", "Updated...")
-   Group related changes into single entries with sub-bullets rather than separate entries
-   Include `Note:` sub-bullets for caveats, limitations, or deployment considerations
-   Do NOT include commit hashes in the CHANGELOG
-   Keep entries concise but informative -- focus on what changed for the user, not implementation details

### Step 6: Insert into CHANGELOG

1. Read the current `CHANGELOG.md`
2. Insert the new version section after the header line ("All notable changes...") and before the previous version
3. Preserve all existing content below

### Step 7: Review

Before writing:

1. Show the generated entries to the user for review
2. Ask if any entries should be modified, removed, or added
3. Write the final version to CHANGELOG.md

### Commit Classification Guide

When analyzing commit messages, use these heuristics:

| Commit Message Pattern                                     | Category                                         |
| ---------------------------------------------------------- | ------------------------------------------------ |
| `Add`, `Added`, `New`, `Implement`, `Create`               | Feature                                          |
| `Fix`, `Fixed`, `Bug`, `Resolve`, `Patch`, `Correct`       | Bug Fix                                          |
| `Update`, `Upgrade`, `Refactor`, `Clean`, `Lint`, `Format` | Chore (unless it adds user-facing functionality) |
| `Remove`, `Delete`, `Deprecate`                            | Chore or Breaking Change                         |
| `BREAKING`, `Breaking`                                     | Breaking Change                                  |
| `Merge branch`                                             | Skip (merge commits)                             |
| `docs:`, `readme`, `changelog`                             | Chore                                            |
| `test:`, `tests`                                           | Chore                                            |
| `ci:`, `cd:`, `pipeline:`                                  | Chore                                            |

If a commit touches both `web/` and `backend/` files, decide if it is a single cross-cutting feature (no prefix) or separate changes (list with sub-bullets).

### Handling Ambiguous Commits

For vague commit messages like "Fix bugs" or "Updates":

1. Look at the actual files changed using `git show --stat {hash}`
2. Look at the diff to understand what changed: `git show {hash}`
3. Write a descriptive entry based on the actual code changes, not the commit message
4. Group related small commits into a single meaningful entry

## Workflow

1. Read current CHANGELOG.md to understand format and find latest version
2. Analyze git commits since last version
3. Categorize and format entries
4. Present draft to user for review
5. Write finalized CHANGELOG.md

## User Request

$ARGUMENTS

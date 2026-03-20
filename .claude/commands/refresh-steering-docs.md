# Refresh CLAUDE.md Steering Documents

Update the directory structure trees and key file references in all CLAUDE.md files to reflect the current state of the codebase.

## Instructions

You are refreshing the CLAUDE.md steering documents to keep them in sync with the codebase. Do NOT rewrite the documents -- only update the parts that may have become stale.

### What to Update

For each CLAUDE.md file, update these specific sections:

1. **Directory structure trees** -- Regenerate the ASCII directory tree to reflect current files and folders. Preserve the annotation comments explaining each entry.
2. **Key files tables** -- Verify all listed files still exist at the stated paths. Add any significant new files. Remove entries for deleted files.
3. **Command/handler/component lists** -- Update enumerated lists of handlers, commands, components, viewers, nested stacks, etc. to reflect what actually exists.
4. **Version numbers** -- Check if VAMS_VERSION, CLI version, or dependency versions have changed.
5. **Feature switch enums** -- Check if new VAMS_APP_FEATURES values have been added.
6. **Skills table** (root CLAUDE.md only) -- Verify all `.claude/commands/*.md` files are listed.

### What NOT to Change

-   Do NOT rewrite rules, patterns, anti-patterns, or code examples
-   Do NOT change the document structure or section ordering
-   Do NOT remove maintenance notes or cross-references
-   Do NOT modify the tone or formatting conventions
-   Only touch content that is factually stale (file paths, lists, version numbers)

### Process

1. **Root `CLAUDE.md`**: Scan top-level directories. Update the directory tree, version table, skills table, and technology stack tables.

2. **`backend/CLAUDE.md`**: Scan `backend/backend/handlers/` for handler domains, `backend/backend/models/` for model files, and `backend/tests/` for test directories. Update the directory tree, handler list, and key files table.

3. **`web/CLAUDE.md`**: Scan `web/src/components/`, `web/src/pages/`, `web/src/services/`, and `web/src/visualizerPlugin/viewers/` for components, pages, services, and viewer plugins. Update the directory tree, viewer table, and key files table.

4. **`infra/CLAUDE.md`**: Scan `infra/lib/nestedStacks/` for nested stacks, `infra/lib/lambdaBuilder/` for lambda builders, and `infra/lib/nestedStacks/pipelines/` for pipeline types. Update the directory tree, nested stack table, and key files table.

5. **`tools/VamsCLI/CLAUDE.md`**: Scan `tools/VamsCLI/vamscli/commands/` for command files and `tools/VamsCLI/vamscli/utils/` for utilities. Update the directory tree, command list, and key files table.

### Verification

After updating, briefly confirm:

-   All listed file paths exist
-   No major new directories are missing from trees
-   Version numbers match source files (`infra/config/config.ts` for VAMS_VERSION, `tools/VamsCLI/vamscli/version.py` for CLI version)

Report a summary of what changed in each file.

## User Request

$ARGUMENTS

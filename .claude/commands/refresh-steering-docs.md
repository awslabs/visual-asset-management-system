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
7. **Skills content** -- Spot-check each `.claude/commands/*.md` skill against the current CLAUDE.md rules and patterns it scaffolds or references (e.g., the endpoint checklist in `add-api-endpoint.md`, the pipeline checklist in `add-pipeline.md`, source-to-doc mappings in `update-docs.md`/`verify-docs.md`). Flag or update skills whose steps, file paths, code templates, or checklists have drifted from the steering documents (see root `CLAUDE.md` Rule 12).

### What NOT to Change

-   Do NOT rewrite rules, patterns, anti-patterns, or code examples
-   Do NOT change the document structure or section ordering
-   Do NOT remove maintenance notes or cross-references
-   Do NOT modify the tone or formatting conventions
-   Only touch content that is factually stale (file paths, lists, version numbers)

### Process

**Discover the document set first — do not work from a hardcoded list.** Glob `**/CLAUDE.md` (excluding `node_modules`) plus `tools/VamsAgentSkill/SKILL.md`, and walk every result. Cross-check the glob against the Rule 11 Kiro mapping table in the root `CLAUDE.md`: any document on disk with no row there, or any row naming a document that no longer exists, is itself a defect to fix in this pass. The directory-scoped documents (`backend/tests/`, `backendPipelines/`, `infra/lib/nestedStacks/pipelines/`, `web/e2e/`, `web/src/visualizerPlugin/`) auto-load only when the working context is inside their directory, so they drift the most quietly.

Per-document scan targets:

1. **Root `CLAUDE.md`**: Scan top-level directories. Update the directory tree (including the pipeline directories under `backendPipelines/genAi/nvidia/`, and the box-drawing glyphs, which assert which entry is a parent's last child), the Project Overview pipeline list, the version table, the skills table, the Rule 11 and Rule 12 tables, and the technology stack tables.

2. **`backend/CLAUDE.md`**: Scan `backend/backend/handlers/` for handler domains and `backend/backend/models/` for model files. Update the directory tree, handler list, and key files table.

3. **`backend/tests/CLAUDE.md`**: Scan `backend/tests/` for test directories, fixtures, and conftest layout.

4. **`web/CLAUDE.md`**: Scan `web/src/components/`, `web/src/pages/`, and `web/src/services/` for components, pages, and services. Update the directory tree and key files table.

5. **`web/src/visualizerPlugin/CLAUDE.md`**: Scan `web/src/visualizerPlugin/viewers/` and `config/viewerConfig.json`. Update the "Current Viewers" table (id, name, category, extensions, enabled status) and the plugin config field list.

6. **`web/e2e/CLAUDE.md`**: Scan `web/e2e/` for spec files, the shared harness, and Playwright project configuration.

7. **`infra/CLAUDE.md`**: Scan `infra/lib/nestedStacks/` for nested stacks and `infra/lib/lambdaBuilder/` for lambda builders. Update the directory tree, nested stack table, and key files table.

8. **`infra/lib/nestedStacks/pipelines/CLAUDE.md`**: Scan `infra/lib/nestedStacks/pipelines/` for pipeline nested stacks. Update the pipeline stack list and the VPC builder condition-block line references.

9. **`backendPipelines/CLAUDE.md`**: Scan `backendPipelines/*/*/` (and `backendPipelines/genAi/nvidia/*/*/`) for directories carrying a `vamsSchema/`. Update the pipeline inventory, the execution-type list against `PIPELINE_EXECUTION_TYPES` in `backend/backend/models/pipelines.py`, and the new-pipeline checklist.

10. **`tools/VamsCLI/CLAUDE.md`**: Scan `tools/VamsCLI/vamscli/commands/` for command files and `tools/VamsCLI/vamscli/utils/` for utilities. Update the directory tree, command list, and key files table.

11. **`tools/VamsMCP/CLAUDE.md`**: Scan `tools/VamsMCP/vams_mcp/` for modules and `server.py` for the tools in each gate section. Update the directory tree and tool sections, and check the version pair (`pyproject.toml` and `vams_mcp/__init__.py`).

12. **`documentation/CLAUDE.md`**: Scan `documentation/docusaurus-site/docs/` for page counts per section and `src/components/` for custom components. Update the structure tree and the key-files cross-reference table.

13. **`tools/VamsAgentSkill/SKILL.md`**: Verify only the structural rules — entity creation/deletion ordering, identifier semantics, permission scoping, and mutating categories. It self-discovers commands via `vamscli --help`, so command additions need no edit here.

14. **Kiro steering mirrors (`.kiro/steering/`)**: If any factual updates above changed a rule, pattern, or convention (not just a file list), make the equivalent change in the corresponding Kiro steering document per the mapping in root `CLAUDE.md` Rule 11.

### Verification

After updating, briefly confirm:

-   All listed file paths exist
-   No major new directories are missing from trees
-   Every `CLAUDE.md` found by the glob has a row in the root `CLAUDE.md` Rule 11 mapping table, and every row names a document that exists
-   Every `<file>` + quoted-"Section Title" pointer in `.claude/commands/*.md` and `.kiro/steering/*.md` resolves to a Markdown heading that exists in that file — a moved section leaves a pointer to nothing
-   Version numbers match source files (`infra/config/config.ts` for VAMS_VERSION, `tools/VamsCLI/vamscli/version.py` for CLI version, `tools/VamsMCP/pyproject.toml` + `vams_mcp/__init__.py` for the MCP version)

Report a summary of what changed in each file.

## User Request

$ARGUMENTS

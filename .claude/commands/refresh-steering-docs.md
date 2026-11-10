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
5. **Feature switch enums** -- Check if new VAMS_APP_FEATURES values have been added, and new `ADDON_APP_FEATURES` values (`infra/common/addonAppFeatures.ts`).
6. **Skills table** (root CLAUDE.md only) -- Verify all `.claude/commands/*.md` files are listed.
7. **Skills content** -- Spot-check each `.claude/commands/*.md` skill against the current CLAUDE.md rules and patterns it scaffolds or references (e.g., the endpoint checklist in `add-api-endpoint.md`, the pipeline checklist in `add-pipeline.md`, source-to-doc mappings in `update-docs.md`/`verify-docs.md`). Flag or update skills whose steps, file paths, code templates, or checklists have drifted from the steering documents (see root `CLAUDE.md` Rule 12).

### What NOT to Change

-   Do NOT rewrite rules, patterns, anti-patterns, or code examples
-   Do NOT change the document structure or section ordering
-   Do NOT remove maintenance notes or cross-references
-   Do NOT modify the tone or formatting conventions
-   Only touch content that is factually stale (file paths, lists, version numbers)

### Process

**Discover the document set first — do not work from a hardcoded list.** Glob `**/CLAUDE.md` (excluding `node_modules`) plus `tools/VamsAgentSkill/SKILL.md` and `tools/VamsReleaseAutomation/skill/SKILL.md`, and walk every result. Cross-check the glob against the Rule 11 Kiro mapping table in the root `CLAUDE.md`: any document on disk with no row there, or any row naming a document that no longer exists, is itself a defect to fix in this pass. The directory-scoped documents (`backend/tests/`, `backendPipelines/`, `infra/lib/nestedStacks/pipelines/`, `web/e2e/`, `web/src/visualizerPlugin/`) auto-load only when the working context is inside their directory, so they drift the most quietly.

Per-document scan targets:

1. **Root `CLAUDE.md`**: Scan top-level directories. Update the directory tree (including the pipeline directories under `backendPipelines/genAi/nvidia/`, and the box-drawing glyphs, which assert which entry is a parent's last child), the Project Overview pipeline list, the version table, the skills table, the Rule 11 and Rule 12 tables, and the technology stack tables.

2. **`backend/CLAUDE.md`**: Scan `backend/backend/handlers/` for handler domains and `backend/backend/models/` for model files (including the `addon/` and `custom/` subpackages), and the add-on and custom lane files (`backend/backend/common/addon*.py`, `backend/backend/custom/`). Update the directory tree, handler list, and key files table.

3. **`backend/tests/CLAUDE.md`**: Scan `backend/tests/` for test directories, fixtures, and conftest layout.

4. **`web/CLAUDE.md`**: Scan `web/src/components/`, `web/src/pages/`, and `web/src/services/` for components, pages, and services, and the add-on and custom lanes (`web/src/addons/`, `web/src/services/addons/`, `web/src/custom/`). Update the directory tree and key files table.

5. **`web/src/visualizerPlugin/CLAUDE.md`**: Scan `web/src/visualizerPlugin/viewers/` and `config/viewerConfig.json` plus `config/addonViewerConfig.json` (the catalog lists core and add-on viewers). Update the "Current Viewers" table (id, name, category, extensions, enabled status) and the plugin config field list.

6. **`web/e2e/CLAUDE.md`**: Scan `web/e2e/` for spec files, the shared harness, and Playwright project configuration.

7. **`infra/CLAUDE.md`**: Scan `infra/lib/nestedStacks/` for nested stacks (including `addon/` and `custom/`), `infra/lib/lambdaBuilder/` for lambda builders, and `infra/common/` for the core, add-on and custom key and feature files. Update the directory tree, nested stack table, and key files table.

8. **`infra/lib/nestedStacks/pipelines/CLAUDE.md`**: Scan `infra/lib/nestedStacks/pipelines/` for pipeline nested stacks. Update the pipeline stack list and the VPC builder condition-block line references.

9. **`backendPipelines/CLAUDE.md`**: Scan `backendPipelines/*/*/` (and `backendPipelines/genAi/nvidia/*/*/`) for directories carrying a `vamsSchema/`. Update the pipeline inventory, the execution-type list against `PIPELINE_EXECUTION_TYPES` in `backend/backend/models/pipelines.py`, and the new-pipeline checklist.

10. **`tools/VamsCLI/CLAUDE.md`**: Scan `tools/VamsCLI/vamscli/commands/` for command files (including `commands/custom/`), `tools/VamsCLI/vamscli/utils/` for utilities, and the constants lanes (`constants.py`, `addon_constants.py`, `custom_constants.py`). Update the directory tree, command list, and key files table. The `Command Groups (N top-level)` section is restated in `.kiro/steering/CLI_DEVELOPMENT_WORKFLOW.md`; update both copies together against the `cli.add_command()` calls in `tools/VamsCLI/vamscli/main.py`, and write no test-file count into either.

11. **`tools/VamsMCP/CLAUDE.md`**: Scan `tools/VamsMCP/vams_mcp/` for modules and `server.py` for the tools in each gate section. Update the directory tree and tool sections, and check the version pair (`pyproject.toml` and `vams_mcp/__init__.py`).

12. **`documentation/CLAUDE.md`**: Scan `documentation/docusaurus-site/docs/` for page counts per section and `src/components/` for custom components. Update the structure tree and the key-files cross-reference table. Check the lane table in `docs/developer/customizing-vams.md` ("How the Extension Points Work") against the core, add-on and custom files that exist, and the add-on table in `docs/developer/addons-overview.md` against `infra/lib/nestedStacks/addon/` and `addonViewerConfig.json`.

13. **`tools/VamsReleaseAutomation/CLAUDE.md`**: Scan `tools/VamsReleaseAutomation/` (the `engine/vra/` modules, `skill/workflows/`, `adapters/`, `cicd/`, `iam/`) against its directory structure, and `engine/vra/mcp_server.py` and `engine/vra/human.py` against the MCP tool tiers and the human-only list. Update the directory structure and those lists, check the tool's own version (`pyproject.toml`, `engine/vra/__init__.py` and `cicd/pipeline/package.json`, which move together and not with the VAMS version), and run `make check` there (`make index` first when files were added or removed: it rewrites `FILES.md`). Mirror changes in `.kiro/steering/RELEASE_AUTOMATION_WORKFLOW.md`.

14. **`tools/VamsReleaseAutomation/skill/SKILL.md`**: Verify the MCP tier table and the human-only list against `engine/vra/mcp_server.py` and `engine/vra/human.py`, and that every workflow it names exists in `skill/workflows/`. It has no Kiro mirror; the Kiro adapter loads it directly.

15. **`tools/VamsAgentSkill/SKILL.md`**: Verify only the structural rules — entity creation/deletion ordering, identifier semantics, permission scoping, and mutating categories. It self-discovers commands via `vamscli --help`, so command additions need no edit here.

16. **Kiro steering mirrors (`.kiro/steering/`)**: If any factual updates above changed a rule, pattern, or convention (not just a file list), make the equivalent change in the corresponding Kiro steering document per the mapping in root `CLAUDE.md` Rule 11.

### Verification

After updating, briefly confirm:

-   All listed file paths exist
-   No major new directories are missing from trees
-   Every `CLAUDE.md` found by the glob has a row in the root `CLAUDE.md` Rule 11 mapping table, and every row names a document that exists
-   Every `<file>` + quoted-"Section Title" pointer in `.claude/commands/*.md` and `.kiro/steering/*.md` resolves to a Markdown heading that exists in that file — a moved section leaves a pointer to nothing
-   Version numbers match source files (`infra/config/config.ts` for VAMS_VERSION, `tools/VamsCLI/vamscli/version.py` for CLI version, `tools/VamsMCP/pyproject.toml` + `vams_mcp/__init__.py` for the MCP version, `tools/VamsReleaseAutomation/pyproject.toml` + `engine/vra/__init__.py` + `cicd/pipeline/package.json` for the release automation version)

Report a summary of what changed in each file.

## User Request

$ARGUMENTS

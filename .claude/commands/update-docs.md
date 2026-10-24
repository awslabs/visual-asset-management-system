Update VAMS documentation based on recent code changes.

## Instructions

1. Run `git diff --name-only HEAD~5` (or appropriate range) to identify recently changed source files
2. For each changed file, determine which documentation pages need updating using the mapping in `documentation/CLAUDE.md`
3. Read the changed source files to understand what changed
4. Update the corresponding documentation pages in `documentation/docusaurus-site/docs/` following the writing style in `documentation/CLAUDE.md`
5. If a new feature was added, update `documentation/docusaurus-site/docs/overview/features.md`
6. If a new API endpoint was added, update **both** the relevant `documentation/docusaurus-site/docs/api/` page **and** `documentation/VAMS_API.yaml` — these are two independent sources of truth that must stay in sync
7. If a new CLI command was added, update `documentation/docusaurus-site/docs/cli/commands/<group>.md` (and `cli/command-reference.md` / `sidebars.ts` if it is a new command group)
8. If configuration options changed, update `documentation/docusaurus-site/docs/deployment/configuration-reference.md`
9. If a new page was added, update `documentation/docusaurus-site/sidebars.ts`
10. Build and verify: `cd documentation/docusaurus-site && npm run build`
11. Report what was updated and any pages that may need manual review (e.g., screenshots)

## Important writing-style notes:

-   Follow the writing style in `documentation/CLAUDE.md`: AWS-doc tone, fully qualified AWS service names, present-tense framing (describe current behavior — no "previously"/"now defaults to"/version-relative phrasing outside `deployment/update-the-solution.md` and `additional/revisions.md`)
-   Never hardcode version numbers; never reference other AWS solutions by name

## Important Docusaurus syntax notes:

-   Admonitions use `:::note` / `:::warning` / `:::tip` / `:::danger` / `:::info` (NOT MkDocs `!!!`)
-   Curly braces in text must be escaped as `\{variable\}` outside code blocks
-   Images reference `/img/` path (maps to `static/img/`)
-   New pages must be added to `sidebars.ts`

## Key source-to-doc mappings:

-   `backend/backend/handlers/` → `docs/api/` pages
-   `backend/backend/models/` → `docs/api/` pages (request/response models)
-   `infra/config/config.ts` → `docs/deployment/configuration-reference.md`
-   `infra/lib/lambdaBuilder/` → `docs/architecture/aws-resources.md`
-   `infra/lib/nestedStacks/storage/` → `docs/architecture/data-model.md`
-   `web/src/visualizerPlugin/config/viewerConfig.json`, `addonViewerConfig.json` → `docs/additional/viewer-plugins.md`, `docs/developer/viewer-plugins.md`, `docs/concepts/viewers.md`
-   Add-on and custom lane files (`backend/backend/common/addon*.py`, `backend/backend/custom/`, `backend/backend/models/addon/`, `infra/common/addon*.ts`, `infra/common/custom*.ts`, `infra/lib/nestedStacks/addon/`, `infra/lib/nestedStacks/custom/`, `web/src/addons/`, `web/src/custom/`, `tools/VamsCLI/vamscli/addon_constants.py`, `custom_constants.py`) → `docs/developer/customizing-vams.md` (the lane table) and `docs/developer/addons-overview.md` (the add-on definition and table)
-   `backend/backend/common/constants.py` object types and constraint fields → `docs/concepts/permissions-model.md`, `docs/api/auth.md`, the `objectType` schemas in `documentation/VAMS_API.yaml`, `docs/architecture/security.md` (Tier 2 list and Constraint Fields table), `docs/developer/backend.md` and `.claude/commands/generate-permissions.md` (the full list is the "New permission object type or constraint field" row in `documentation/CLAUDE.md`)
-   `documentation/permissionsTemplates/` → the pages in the "New or changed permission template" row in `documentation/CLAUDE.md`
-   `tools/VamsCLI/vamscli/commands/` → `docs/cli/commands/<group>.md`, `docs/cli/command-reference.md`
-   `CHANGELOG.md` → `docs/additional/revisions.md`
-   `backendPipelines/`, `infra/lib/nestedStacks/pipelines/` → `docs/pipelines/` pages (+ `docs/deployment/configuration-reference.md` for pipeline config options)
-   `infra/lib/nestedStacks/storage/` (S3 buckets, DynamoDB tables, log groups) → also `docs/deployment/uninstall.md` (removal policy + custom-name collision flags)

Update VAMS documentation based on recent code changes.

## Instructions

1. Run `git diff --name-only HEAD~5` (or appropriate range) to identify recently changed source files
2. For each changed file, determine which documentation pages need updating using the mapping in `documentation/CLAUDE.md`
3. Read the changed source files to understand what changed
4. Update the corresponding documentation pages in `documentation/docusaurus-site/docs/` following the writing style in `documentation/CLAUDE.md`
5. If a new feature was added, update `documentation/docusaurus-site/docs/overview/features.md`
6. If a new API endpoint was added, update the relevant `documentation/docusaurus-site/docs/api/` page and `documentation/VAMS_API.yaml`
7. If a new CLI command was added, update `documentation/docusaurus-site/docs/cli/command-reference.md`
8. If configuration options changed, update `documentation/docusaurus-site/docs/deployment/configuration-reference.md`
9. If a new page was added, update `documentation/docusaurus-site/sidebars.ts`
10. Build and verify: `cd documentation/docusaurus-site && npm run build`
11. Report what was updated and any pages that may need manual review (e.g., screenshots)

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
-   `web/src/visualizerPlugin/config/viewerConfig.json` → `docs/additional/viewer-plugins.md`, `docs/developer/viewer-plugins.md`
-   `tools/VamsCLI/vamscli/commands/` → `docs/cli/command-reference.md`
-   `CHANGELOG.md` → `docs/additional/revisions.md`
-   `backendPipelines/` → `docs/pipelines/` pages

# CLAUDE.md — VAMS Documentation

> Steering document for Claude Code when working within the `documentation/` directory.
> Auto-loaded when the working context is within `documentation/`.

---

## Project Overview

VAMS documentation is built with **Docusaurus** (React-based static site generator) and lives in `documentation/docusaurus-site/`. The source Markdown files are in `documentation/docusaurus-site/docs/`.

-   **Docusaurus config**: `documentation/docusaurus-site/docusaurus.config.ts`
-   **Sidebar config**: `documentation/docusaurus-site/sidebars.ts`
-   **Source pages**: `documentation/docusaurus-site/docs/` (78 Markdown files)
-   **Custom CSS**: `documentation/docusaurus-site/src/css/custom.css`
-   **Static images**: `documentation/docusaurus-site/static/img/`
-   **Architecture diagrams**: `documentation/diagrams/` (source PNGs, JPEGs, draw.io files)
-   **OpenAPI spec**: `documentation/VAMS_API.yaml`
-   **Build output**: `documentation/docusaurus-site/build/`

---

## Documentation Structure

```
docusaurus-site/docs/
├── index.md                    # Landing page
├── overview/                   # Solution overview, benefits, use cases, features, costs
├── concepts/                   # Core concepts: databases, assets, files, pipelines, metadata, permissions
├── architecture/               # Architecture overview, details, AWS resources, security, networking, data model
├── deployment/                 # Prerequisites, deploy, config reference, external S3, update, uninstall
├── user-guide/                 # Getting started, web UI, upload tutorial, asset mgmt, search, metadata, permissions
├── cli/                        # CLI getting started, installation, command reference, automation
├── pipelines/                  # Pipeline overview + 10 individual pipeline docs + custom pipeline guide
├── developer/                  # Dev setup, backend, frontend, CDK, viewer plugins, audit logging
├── api/                        # API overview, auth, assets, files, metadata, search, pipelines, workflows, tags
├── troubleshooting/            # Common issues, known limitations, FAQ
└── additional/                 # Quotas, partner integrations, viewer plugins ref, notices, revisions
```

---

## Writing Style

Follow AWS documentation standards:

1. **Tone**: Professional, formal, solution-focused
2. **AWS service names**: Always fully qualified (e.g., "Amazon DynamoDB" not "DynamoDB")
3. **Paragraphs**: 2-4 sentences, concise
4. **Headings**: `##` for main sections, `###` for subsections
5. **Admonitions**: Use Docusaurus admonition syntax:
    - `:::note` — General information
    - `:::tip` — Helpful suggestions
    - `:::warning` — Caution needed
    - `:::danger` — Critical warnings
    - `:::info` — Supplementary information
    - With title: `:::warning[Custom Title]`
6. **Code blocks**: Always include language tags (`bash, `python, `typescript, `json)
7. **Tables**: For comparisons, feature lists, field references
8. **Mermaid diagrams**: Use ```mermaid code blocks (supported via @docusaurus/theme-mermaid)
9. **Cross-references**: Use relative links `[Page Title](../section/page.md)`
10. **Images**: Reference from `/img/` (maps to `static/img/`)
11. **Curly braces**: Escape `{variable}` as `\{variable\}` outside code blocks (MDX parses them as JSX)
12. **Never reference other AWS solutions** by name — VAMS documentation is standalone
13. **Never hardcode version numbers** — reference source of truth (`config.ts`)

---

## Build Commands

```bash
# Install dependencies
cd documentation/docusaurus-site
npm install

# Local preview (live reload)
npm run start
# Opens http://localhost:3000

# Build static site
npm run build
# Output in documentation/docusaurus-site/build/
```

---

## When to Update Documentation

| Change Type             | Documentation to Update                                                                        |
| ----------------------- | ---------------------------------------------------------------------------------------------- |
| New API endpoint        | `api/` relevant page, `VAMS_API.yaml`, `cli/command-reference.md` (if CLI updated)             |
| New config option       | `deployment/configuration-reference.md`                                                        |
| New pipeline            | `pipelines/` new page + `pipelines/overview.md` table + `overview/features.md` + `sidebars.ts` |
| New viewer plugin       | `developer/viewer-plugins.md`, `additional/viewer-plugins.md`, `overview/features.md`          |
| New DynamoDB table      | `architecture/aws-resources.md`, `architecture/data-model.md`                                  |
| Permission model change | `concepts/permissions-model.md`, `user-guide/permissions.md`                                   |
| New CLI command         | `cli/command-reference.md`, `cli/automation.md` (if new patterns)                              |
| UI navigation change    | `user-guide/web-interface.md`, `user-guide/getting-started.md`                                 |
| Breaking change         | `additional/revisions.md`, `deployment/update-the-solution.md`                                 |
| New feature             | `overview/features.md`, relevant user guide page                                               |
| New sidebar page        | `sidebars.ts` — add the page to the appropriate category                                       |

---

## Key Files to Cross-Reference

| Documentation Topic | Source Files                                                                  |
| ------------------- | ----------------------------------------------------------------------------- |
| Config options      | `infra/config/config.ts` (ConfigPublic interface)                             |
| API endpoints       | `infra/lib/nestedStacks/apiLambda/apiBuilder-nestedStack.ts`, `VAMS_API.yaml` |
| DynamoDB tables     | `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts`                |
| Feature flags       | `infra/common/vamsAppFeatures.ts`                                             |
| Backend handlers    | `backend/backend/handlers/`                                                   |
| Pydantic models     | `backend/backend/models/`                                                     |
| CLI commands        | `tools/VamsCLI/vamscli/commands/`                                             |
| Viewer plugins      | `web/src/visualizerPlugin/config/viewerConfig.json`                           |
| Lambda builders     | `infra/lib/lambdaBuilder/`                                                    |
| Pipeline configs    | `infra/lib/nestedStacks/pipelines/`                                           |

---

## Documentation Framework

| Component             | Technology                                                                         | Purpose                                                     |
| --------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Static site generator | [Docusaurus 3.x](https://docusaurus.io/)                                           | React-based SSG with MDX support                            |
| Language              | TypeScript                                                                         | Config files and custom components                          |
| Markdown format       | CommonMark (`.md`) with `format: 'detect'`                                         | Standard Markdown (not MDX) for doc pages                   |
| Diagrams              | [@docusaurus/theme-mermaid](https://docusaurus.io/docs/markdown-features/diagrams) | Mermaid diagrams in code blocks                             |
| Theme                 | GitHub Docs inspired                                                               | Custom CSS in `src/css/custom.css`                          |
| Deployment            | GitLab Pages + GitHub Pages                                                        | CI/CD via `.gitlab-ci.yml` and `.github/workflows/docs.yml` |

### Navigation Structure (sidebars.ts)

The sidebar uses a hierarchical tree with collapsible categories:

```
Home (index.md)
├── Overview (5 pages)
├── Core Concepts (8 pages)
├── Architecture (6 pages)
├── Deployment (7 pages)
├── User Guide (11 pages)
└── Developer Guide
    ├── Setup, Backend, Frontend, CDK, Viewer Plugins, Audit Logging
    ├── CLI Reference (4+ pages with commands/ subcategory)
    ├── Pipelines (11 pages)
    ├── API Reference (11 pages)
    └── Troubleshooting (3 pages)
Additional (5 pages)
```

When adding new pages, always update `sidebars.ts` to include the page in the correct category.

### Deployment

Documentation is deployed automatically via CI/CD when changes are pushed to `main` or `release/*` branches:

-   **GitLab**: `.gitlab-ci.yml` → builds with `node:20-slim`, outputs to `public/` for GitLab Pages
-   **GitHub**: `.github/workflows/docs.yml` → builds with `actions/setup-node@v4`, deploys via `actions/deploy-pages@v4`

Both pipelines only trigger when files under `documentation/docusaurus-site/` change.

---

## Anti-Patterns

1. **Don't use MkDocs syntax** — use Docusaurus admonitions (`:::note` not `!!! note`)
2. **Don't use unescaped curly braces** outside code blocks — MDX interprets them as JSX
3. **Don't hardcode version numbers** — reference the source of truth
4. **Don't duplicate content** across pages — link to the authoritative page
5. **Don't leave placeholder pages** — every sidebar entry must have real content
6. **Don't reference other AWS solutions** by name
7. **Don't forget to update `sidebars.ts`** when adding new pages
8. **Don't use HTML directly** in Markdown — use standard Markdown or Docusaurus components
9. **Don't use barrel imports** in any custom React components — import individually

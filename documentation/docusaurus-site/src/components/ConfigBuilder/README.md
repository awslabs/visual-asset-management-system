# ConfigBuilder

Interactive, validation-aware builder for the VAMS deployment config
(`infra/config/config.json`). Rendered on the
`docs/deployment/config-builder.mdx` page. Fully client-side (wrapped in
`@docusaurus/BrowserOnly`), zero runtime dependencies beyond what the docs site
already ships (React, `clsx`, `prism-react-renderer`).

## Files

| File                | Responsibility                                                                                  |
| ------------------- | ----------------------------------------------------------------------------------------------- |
| `types.ts`          | Shared types (`ConfigShape`, `FieldMeta`, `Section`, `Rule`, `Profile`).                        |
| `pathUtils.ts`      | `getByPath` / immutable `setByPath` / `cloneConfig`. No dependencies.                           |
| `defaults.ts`       | Commercial, GovCloud & EU Sovereign Cloud presets — literal copies of the three template JSONs. |
| `serialize.ts`      | `toConfigJson()` — strict JSON (4-space), normalizes `"UNDEFINED"` → `null`.                    |
| `validation.ts`     | `RULES` ported line-by-line from `getConfig()` + `evaluateRules()`.                             |
| `derived.ts`        | Auto-toggle engine (force VPC on) + GovCloud-safe defaults helper.                              |
| `schema.ts`         | `SECTIONS` + `FIELDS` declarative metadata; `makeModelFields()` GPU factory.                    |
| `fields/`           | Input primitives mapped to `FieldMeta.input`.                                                   |
| `panels/`           | Section, validation summary, output (preview/download/copy), profile switcher.                  |
| `ConfigBuilder.tsx` | Orchestrator: state, memoized validation/serialize, derived pipeline.                           |
| `index.tsx`         | `<BrowserOnly>` SSR boundary.                                                                   |
| `styles.module.css` | Scoped styles using Infima `--ifm-*` theme variables.                                           |

## Keeping in sync with `infra/config/config.ts`

This component mirrors the deploy-time source of truth. When `config.ts`
changes, update the corresponding files here:

1. **New/changed field** in the `ConfigPublic` interface → add it to
   `schema.ts` (`FIELDS`) and, if it has a new default, to `defaults.ts`.
2. **New/changed default value** in a template → update `defaults.ts` (and keep
   all three of `config.template.commercial.json` /
   `config.template.govcloud.json` / `config.template.eusovereign.json` as the
   authority).
3. **New/changed `throw new Error(...)`** (or meaningful `console.warn`) in
   `getConfig()` → add/adjust the corresponding `Rule` in `validation.ts`. Each
   rule carries an approximate `config.ts` line reference in a comment for
   diffing.

The presets in `defaults.ts` are intentionally structured so they should remain
deep-equal to the three template JSON files (GovCloud is Commercial plus the
documented GovCloud overrides; EU Sovereign Cloud is GovCloud plus its region,
FIPS, certificate ARN, and ECR URI differences). If you change the templates,
re-check `defaults.ts`.

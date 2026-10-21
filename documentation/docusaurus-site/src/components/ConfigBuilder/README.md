# ConfigBuilder

Interactive, validation-aware builder for the VAMS deployment config
(`infra/config/config.json`). Rendered on the
`docs/deployment/config-builder.mdx` page. Fully client-side (wrapped in
`@docusaurus/BrowserOnly`), zero runtime dependencies beyond what the docs site
already ships (React, `clsx`, `prism-react-renderer`).

## Files

| File                | Responsibility                                                                                                        |
| ------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `types.ts`          | Shared types (`ConfigShape`, `FieldMeta`, `Section`, `Rule`, `Profile`).                                              |
| `pathUtils.ts`      | `getByPath` / immutable `setByPath` / `cloneConfig`. No dependencies.                                                 |
| `defaults.ts`       | Commercial, GovCloud & EU Sovereign Cloud presets — literal copies of the three template JSONs.                       |
| `serialize.ts`      | `toConfigJson()` — strict JSON (4-space), normalizes `"UNDEFINED"` → `null`.                                          |
| `validation.ts`     | `RULES` ported rule-by-rule from `getConfig()` + `evaluateRules()`.                                                   |
| `ruleHelpers.ts`    | Helpers shared by the core rules and the add-on rule modules.                                                         |
| `addon/`            | Add-on modules: each add-on's fields, rules, defaults and normalizations; `index.ts` joins them into the core tables. |
| `derived.ts`        | `applyDerived()` — the `getConfig()` normalizations + GovCloud-safe defaults helper.                                  |
| `schema.ts`         | `SECTIONS` + `FIELDS` declarative metadata; `makeModelFields()` GPU factory.                                          |
| `fields/`           | Input primitives mapped to `FieldMeta.input`.                                                                         |
| `panels/`           | Section, validation summary, output (preview/download/copy), profile switcher.                                        |
| `ConfigBuilder.tsx` | Orchestrator: state, memoized validation/serialize, derived pipeline.                                                 |
| `index.tsx`         | `<BrowserOnly>` SSR boundary.                                                                                         |
| `styles.module.css` | Scoped styles using Infima `--ifm-*` theme variables.                                                                 |

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
   rule section names the `getConfig()` block it mirrors by quoting that block's
   leading comment or error-message text; search `config.ts` for the quoted
   string when diffing the two.
4. **New/changed/removed auto-mutation** in `getConfig()` — an assignment that
   rewrites the operator's config rather than rejecting it → mirror it in
   `derived.ts`, or in the add-on's module under `addon/` for an add-on block.
   These are the only code here that can change the operator's config, so they
   must contain nothing `getConfig()` does not itself do: a mutation
   `getConfig()` **stops** performing has to be deleted, or the builder keeps
   silently rewriting the downloaded `config.json`. Each mirrored assignment
   quotes the `getConfig()` warning it mirrors. Where `getConfig()` rejects a
   feature combination instead, the mirror belongs in `validation.ts` as an
   error rule (step 3), not here.
5. **New/changed feature in a `getConfig()` constraint list** — for example a
   pipeline added to the VPC-requiring set → extend the matching table in
   `validation.ts` (`VPC_REQUIRING_FEATURES` for that one). Omitting it means
   the builder approves a config that then fails at `cdk synth`.
6. **Run the drift check** — `infra/test/config/configBuilderSync.test.ts` (part of
   `npm test` in `infra/`). It covers `schema.ts` field coverage and the
   `defaults.ts` presets only. **`validation.ts` and `derived.ts` are outside
   it**, so steps 3 to 5 are held by review discipline alone.

The presets in `defaults.ts` are intentionally structured so they should remain
deep-equal to the three template JSON files (GovCloud is Commercial plus the
documented GovCloud overrides; EU Sovereign Cloud is GovCloud plus its region,
FIPS, certificate ARN, and ECR URI differences). If you change the templates,
re-check `defaults.ts`.

## Add-on modules

An add-on whose configuration block lives under `infra/config/addon/` is mirrored
here by one module under `addon/`, holding the add-on's `FIELDS` entries, its
rules (anchored on the add-on's configuration module rather than `config.ts`), its
entry in the VPC-requiring features, its template defaults, its `getConfig()`
normalizations and the paths edited only in the downloaded `config.json`.
`addon/index.ts` joins the modules into `ADDON_FIELDS`, `ADDON_RULES`,
`ADDON_VPC_REQUIRING_FEATURES`, `ADDON_DEFAULTS`, `applyAddonDerived()` and
`ADDON_JSON_ONLY_PATHS`, which `schema.ts`, `validation.ts`, `defaults.ts`,
`derived.ts` and the drift check splice in. The sync steps above apply to an
add-on module the same way, with the add-on's configuration module in place of
`config.ts`.

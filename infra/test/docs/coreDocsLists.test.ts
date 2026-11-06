/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Hand-maintained lists in the core documentation and steering that nothing else ties to the code:
 * the feature-flag tables, the Casbin object-type lists and the OpenAPI constraint objectType schemas,
 * the permission-template tables, the resource-count sentences, the ConfigBuilder normalization note,
 * the sidebar coverage of every page and the release version. Each assertion derives the expected
 * value from the source of truth and reads it back out of the page, so a list that drifts fails here
 * instead of shipping. The lists are the core ones, and the user documentation pages that carry a
 * feature-flag table name no add-on switch; an add-on's own entries are held by the guard tests of
 * its pages under infra/test/addon/<addon>/docs/.
 *
 * Durable (root CLAUDE.md Rule 13): every list below can drift again with the next flag, object type,
 * template, table, normalization, page or release, and an add-on switch can be added to a user
 * table again.
 */

import * as fs from "fs";
import * as path from "path";
import { ADDON_APP_FEATURES } from "../../common/addonAppFeatures";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../common/addonResourceParamKeys";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import { VAMS_APP_FEATURES } from "../../common/vamsAppFeatures";
import { VAMS_VERSION } from "../../config/config";
import { addonClaimedTemplates } from "./support/addonDocsPlacement";
import {
    OPENAPI,
    REPO_ROOT,
    SIDEBARS,
    capture,
    docsPages,
    page,
    pythonCode,
    read,
    yamlBlock,
} from "./support/docsText";

const CONSTANTS = "backend/backend/common/constants.py";
const STORAGE_STACK = "infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts";
const DERIVED = "documentation/docusaurus-site/src/components/ConfigBuilder/derived.ts";
const TEMPLATES_DIR = path.join(REPO_ROOT, "documentation", "permissionsTemplates");

/** The feature-flag tables of the user documentation, which list the core switches only. */
const USER_FEATURE_TABLES = [
    "documentation/docusaurus-site/docs/architecture/details.md",
    "documentation/docusaurus-site/docs/overview/features.md",
    "documentation/docusaurus-site/docs/cli/commands/setup-and-auth.md",
];

/** Every core feature-flag list; the developer and steering ones may also name add-on switches. */
const FEATURE_TABLES = [
    ...USER_FEATURE_TABLES,
    "documentation/docusaurus-site/docs/developer/cdk.md",
    "infra/CLAUDE.md",
    "tools/VamsCLI/CLAUDE.md",
];

const OBJECT_TYPE_LISTS = [
    "documentation/docusaurus-site/docs/concepts/permissions-model.md",
    "documentation/docusaurus-site/docs/api/auth.md",
    "documentation/docusaurus-site/docs/developer/backend.md",
    "documentation/docusaurus-site/docs/architecture/security.md",
    "backend/CLAUDE.md",
    ".claude/commands/generate-permissions.md",
];

/** The steering documents that say what the core part of `applyDerived()` mirrors (root CLAUDE.md Rule 11). */
const NORMALIZATION_MIRRORS = [
    "infra/CLAUDE.md",
    ".kiro/steering/CDK_DEVELOPMENT_WORKFLOW.md",
    ".kiro/steering/BACKEND_CDK_DEVELOPMENT_WORKFLOW.md",
];

/** The count words a steering document states the normalizations with. */
const COUNT_WORDS = ["no", "one", "two", "three", "four"];

/** Each line of `text` that names one of `names` as a whole word, as `<line number>: <line>`. */
const linesNaming = (text: string, names: string[]): string[] =>
    text
        .split("\n")
        .map((l, i) => ({ l, n: i + 1 }))
        .filter(({ l }) => names.some((name) => new RegExp(`\\b${name}\\b`).test(l)))
        .map(({ l, n }) => `${n}: ${l.trim()}`);

/** The Python list literal `CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES = [...]`. */
function coreObjectTypes(): string[] {
    const code = pythonCode(read(CONSTANTS));
    const m = /^CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES\s*=\s*\[([\s\S]*?)\]/m.exec(code);
    if (!m) throw new Error(`CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES not found in ${CONSTANTS}`);
    return Array.from(m[1].matchAll(/'([A-Za-z]+)'/g), (x) => x[1]);
}

/** The name pattern every permission object type follows. */
const extensionNamePattern = (): string =>
    capture(pythonCode(read(CONSTANTS)), /^EXTENSION_NAME_PATTERN\s*=\s*r"([^"]+)"/m);

/** Each `objectType` string schema of VAMS_API.yaml that constrains the value with a pattern. */
function openApiObjectTypeSchemas(): Array<{ pattern: string; description: string }> {
    const lines = read(OPENAPI).split("\n");
    const out: Array<{ pattern: string; description: string }> = [];
    lines.forEach((line, i) => {
        if (line.trim() !== "objectType:" || lines[i + 1]?.trim() !== "type: string") return;
        const block = yamlBlock(lines, i).join("\n");
        const pattern = capture(block, /^\s*pattern: "([^"]+)"$/m);
        if (pattern === "") return;
        const description = capture(block, /^\s*description: >-\n([\s\S]*)$/m)
            .split("\n")
            .map((l) => l.trim())
            .filter(Boolean)
            .join(" ");
        out.push({ pattern, description });
    });
    return out;
}

/** The names of the `dynamo` members of the storage stack's `storageResources` interface. */
function storageInterfaceTables(): string[] {
    const source = read(STORAGE_STACK);
    const start = source.indexOf("dynamo: {");
    if (start === -1) throw new Error(`no dynamo interface block in ${STORAGE_STACK}`);
    const block = source.slice(start, source.indexOf("};", start));
    return Array.from(block.matchAll(/^\s*(\w+)\??\s*:\s*dynamodb\.I?Table\b/gm), (m) => m[1]);
}

/** The config paths the core part of `applyDerived()` assigns on the operator's config. */
const coreNormalizations = (): string[] =>
    Array.from(read(DERIVED).matchAll(/setByPath\(config, "([^"]+)"/g), (m) => m[1]);

describe("core documentation lists match the code they describe", () => {
    describe("feature flags", () => {
        const members = Object.values(VAMS_APP_FEATURES) as string[];

        test("the enum has members (count control)", () => {
            expect(members.length).toBeGreaterThan(10);
            expect(members).toContain("GOVCLOUD");
        });

        test.each(FEATURE_TABLES)("%s lists every VAMS_APP_FEATURES member", (rel) => {
            const text = read(rel);
            const missing = members.filter((m) => !new RegExp(`\\b${m}\\b`).test(text));
            expect(missing).toEqual([]);
        });

        // An add-on's switches are listed on its own pages, which the user tables point to through
        // the Add-ons Overview.
        const addonMembers = Object.values(ADDON_APP_FEATURES) as string[];

        test("the add-on enum has members (count control)", () => {
            expect(addonMembers.length).toBeGreaterThan(0);
            expect(addonMembers.filter((m) => !/^[A-Z][A-Z0-9_]*$/.test(m))).toEqual([]);
        });

        test.each(addonMembers)(
            "the matcher finds a table row that carries %s (positive control)",
            (flag) => {
                const table = [
                    "| Feature Flag | Description |",
                    "| ------------ | ----------- |",
                    "| `GOVCLOUD`   | AWS GovCloud deployment mode |",
                    `| \`${flag}\` | An add-on switch |`,
                ].join("\n");
                expect(linesNaming(table, addonMembers)).toEqual([
                    `4: | \`${flag}\` | An add-on switch |`,
                ]);
                expect(linesNaming(table.replace(flag, `FEATURE_${flag}`), addonMembers)).toEqual(
                    []
                );
            }
        );

        test.each(USER_FEATURE_TABLES)("%s names no ADDON_APP_FEATURES member", (rel) => {
            expect(linesNaming(read(rel), addonMembers).map((l) => `${rel}:${l}`)).toEqual([]);
        });
    });

    describe("Casbin object types", () => {
        test("the core list parsed (count control)", () => {
            const types = coreObjectTypes();
            expect(types).toContain("metadataSchema");
            expect(types.length).toBeGreaterThan(10);
        });

        test.each(OBJECT_TYPE_LISTS)(
            "%s names every CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES entry",
            (rel) => {
                const text = read(rel);
                expect(coreObjectTypes().filter((t) => !text.includes(`\`${t}\``))).toEqual([]);
            }
        );

        // `apiKey` is not a Casbin object type; API-key access is Tier-1 route access.
        const apiKeyAfterMetadataSchema = /`metadataSchema`\s*,\s*`apiKey`/;

        test("the apiKey pattern matches a list that carries it (positive control)", () => {
            expect(
                apiKeyAfterMetadataSchema.test(
                    "constraints (`database`, `asset`, `role`, `userRole`, `metadataSchema`, `apiKey`) with"
                )
            ).toBe(true);
            expect(apiKeyAfterMetadataSchema.test("`metadataSchema`, `widget`, `apiKey`")).toBe(
                false
            );
        });

        test.each(OBJECT_TYPE_LISTS)("%s does not list apiKey beside the object types", (rel) => {
            const offending = read(rel)
                .split("\n")
                .map((l, i) => ({ l, n: i + 1 }))
                .filter(({ l }) => apiKeyAfterMetadataSchema.test(l));
            expect(offending.map((o) => `${rel}:${o.n}`)).toEqual([]);
        });

        test("the OpenAPI objectType reader finds the constraint schemas (count control)", () => {
            expect(extensionNamePattern()).toMatch(/^\^.+\$$/);
            expect(openApiObjectTypeSchemas()).toHaveLength(2);
        });

        // The accepted set is the core types plus whatever the add-on and fork lanes register, so the
        // schemas constrain the value by the name pattern every type follows and name the core types.
        test("VAMS_API.yaml constraint objectType schemas carry the type-name pattern and name exactly the core types", () => {
            const pattern = extensionNamePattern();
            const core = coreObjectTypes();
            expect(core.filter((t) => !new RegExp(pattern).test(t))).toEqual([]);
            for (const schema of openApiObjectTypeSchemas()) {
                expect(schema.pattern).toBe(pattern);
                const named = capture(schema.description, /The core types are ([^;.]+)[;.]/)
                    .split(/,\s*|\s+and\s+/)
                    .map((t) => t.trim())
                    .filter(Boolean);
                expect(named.sort()).toEqual([...core].sort());
                expect(schema.description).toContain("/auth/constraints/permissionObjects");
            }
        });
    });

    describe("permission templates", () => {
        const templateFiles = (): string[] =>
            fs.readdirSync(TEMPLATES_DIR).filter((f) => f.endsWith(".json"));
        const count = (f: string): number =>
            JSON.parse(fs.readFileSync(path.join(TEMPLATES_DIR, f), "utf8")).constraints.length;
        /** The templates of the core: every one no add-on's documentation descriptor claims. */
        const coreTemplates = (): string[] => {
            const claimed = addonClaimedTemplates();
            return templateFiles().filter((f) => !claimed.has(f));
        };

        test("core templates exist (count control)", () => {
            expect(coreTemplates().length).toBeGreaterThanOrEqual(6);
            expect(coreTemplates()).toContain("database-admin.json");
        });

        test("developer/permissions.md states each core template's constraint count, and any other template's correctly", () => {
            const text = page("developer/permissions.md");
            const wrong: string[] = [];
            for (const f of templateFiles()) {
                const row = text
                    .split("\n")
                    .find((l) => l.includes(`\`${f}\``) && l.includes("constraint"));
                if (!row && !coreTemplates().includes(f)) continue;
                if (!row || !row.includes(`(${count(f)} constraint`)) {
                    wrong.push(`${f}: expected (${count(f)} constraints) in: ${row ?? "<no row>"}`);
                }
            }
            expect(wrong).toEqual([]);
        });

        test("generate-permissions.md Existing Templates table states each core template's constraint count, and any other template's correctly", () => {
            const text = read(".claude/commands/generate-permissions.md");
            const wrong: string[] = [];
            for (const f of templateFiles()) {
                const row = text.split("\n").find((l) => l.startsWith(`| \`${f}\``));
                if (!row && !coreTemplates().includes(f)) continue;
                const cell = row ? row.split("|")[2].trim() : "<no row>";
                if (cell !== String(count(f))) {
                    wrong.push(`${f}: Constraints cell ${cell}, template has ${count(f)}`);
                }
            }
            expect(wrong).toEqual([]);
        });
    });

    describe("resource counts", () => {
        const tables = Object.keys(RESOURCE_PARAM_KEYS.dynamoTables).length;
        const legacy = Object.keys(RESOURCE_PARAM_KEYS.dynamoTablesLegacy).length;
        const buckets = Object.keys(RESOURCE_PARAM_KEYS.s3Buckets).length;
        const logGroups = Object.keys(RESOURCE_PARAM_KEYS.cloudwatchLogGroups).length;
        const lambdas = Object.keys(RESOURCE_PARAM_KEYS.lambdaFunctions).length;
        const integration = Object.keys(RESOURCE_PARAM_KEYS.integration).length;
        // ResourceNamesBuilder publishes every key but three: SearchBuilder publishes the reindexer
        // function name, and the secondary API stack the workflow role ARN and the import function
        // name, so the total details.md quotes is three higher than the materialized set.
        const allParams = tables + legacy + buckets + logGroups + lambdas + integration;
        const registryParams = allParams - lambdas - 1;
        /** The storage interface's tables that are not an add-on's: an add-on table is named after its add-on key. */
        const coreInterfaceTables = (): string[] => {
            const addonTables = new Set(
                Object.keys(ADDON_RESOURCE_PARAM_KEYS.dynamoTables ?? {}).map((k) => `${k}Table`)
            );
            return storageInterfaceTables().filter((t) => !addonTables.has(t));
        };

        test("the key and interface readers find the tables (count control)", () => {
            expect(tables).toBeGreaterThan(40);
            expect(storageInterfaceTables()).toContain("assetStorageTable");
            expect(coreInterfaceTables().length).toBeGreaterThanOrEqual(tables);
        });

        test("aws-resources.md DynamoDB sentence", () => {
            expect(page("architecture/aws-resources.md")).toContain(
                `VAMS deploys ${
                    tables + legacy
                } Amazon DynamoDB tables for persistent data storage — ${tables} read by Lambda handlers and ${legacy} migration source tables.`
            );
        });

        test("aws-resources.md SSM parameter rows", () => {
            const text = page("architecture/aws-resources.md");
            const cell = (suffix: string): number => {
                const row = text.split("\n").find((l) => l.includes(`/resourceNames/${suffix}\``));
                return row ? Number(row.split("|")[2].trim()) : NaN;
            };
            expect(cell("dynamoTables/*")).toBe(tables);
            expect(cell("dynamoTables/legacy/*")).toBe(legacy);
            expect(cell("s3Buckets/*")).toBe(buckets);
            expect(cell("cloudwatchLogGroups/*")).toBe(logGroups);
            expect(cell("lambdaFunctions/*")).toBe(lambdas);
        });

        test("details.md resource-name sentence", () => {
            const text = page("architecture/details.md");
            expect(text).toContain(
                `— ${allParams} in the shipped configuration (${
                    tables + legacy
                } DynamoDB tables, of which ${legacy} are deprecated`
            );
            expect(text).toContain(`materializes ${registryParams} of them`);
            expect(text).toContain(`${integration} integration values`);
            expect(text).toContain(`${lambdas} Lambda function names`);
        });

        test("quotas.md table-count row", () => {
            expect(page("additional/quotas.md")).toContain(
                `| ${tables} tables (plus ${legacy} retained for migration) |`
            );
        });

        // infra/CLAUDE.md writes "N resource-name parameters" and "N core DynamoDB tables"; the Kiro
        // mirror writes "N SSM resource-name parameters" and "N DynamoDB tables". The table figure is
        // the storage interface's `dynamo` members, the parameter figure the materialized set.
        test.each(["infra/CLAUDE.md", ".kiro/steering/CDK_DEVELOPMENT_WORKFLOW.md"])(
            "%s counts",
            (rel) => {
                const text = read(rel);
                expect(text).toMatch(
                    new RegExp(`\\b${coreInterfaceTables().length} (core )?DynamoDB tables`)
                );
                expect(text).toMatch(
                    new RegExp(`\\b${registryParams} (SSM )?resource-name parameters`)
                );
            }
        );
    });

    describe("ConfigBuilder normalization note", () => {
        test("derived.ts runs the add-on normalizations after its own (control)", () => {
            const source = read(DERIVED);
            expect(source).toContain("applyAddonDerived(");
            expect(coreNormalizations().length).toBeLessThan(COUNT_WORDS.length);
        });

        test.each(NORMALIZATION_MIRRORS)(
            "%s states how many normalizations the core part of applyDerived() mirrors",
            (rel) => {
                const stated = Array.from(
                    read(rel).matchAll(/performs (\w+) (?:normalization|auto-mutation)s? today/g),
                    (m) => m[1]
                );
                expect(stated.length).toBeGreaterThan(0);
                expect(
                    stated.filter((w) => w !== COUNT_WORDS[coreNormalizations().length])
                ).toEqual([]);
            }
        );
    });

    describe("sidebar coverage", () => {
        // Both steering families describe the source pages as each listed in sidebars.ts.
        test("every Markdown and MDX page is listed in sidebars.ts", () => {
            const source = read(SIDEBARS);
            const ids = new Set(Array.from(source.matchAll(/"([^"]+)"/g), (m) => m[1]));
            const pages = docsPages().map((p) => p.replace(/\.mdx?$/, ""));
            expect(pages.length).toBeGreaterThan(100);
            expect(pages).toContain("developer/addons-overview");
            expect(pages.filter((id) => !ids.has(id))).toEqual([]);
            expect(read("documentation/CLAUDE.md")).toContain("each listed in `sidebars.ts`");
            expect(read(".kiro/steering/DOCUMENTATION_WORKFLOW.md")).toContain(
                "each listed in sidebars.ts"
            );
        });
    });

    describe("release version", () => {
        const pkg = (): string => JSON.parse(read("package.json")).version as string;

        test("the version files agree", () => {
            expect(VAMS_VERSION).toBe(pkg());
            const py = read("tools/VamsCLI/vamscli/version.py");
            expect(py).toContain(`__version__ = "${pkg()}"`);
            expect(py).toContain(`CLI_VERSION = "${pkg()}"`);
            expect(read("tools/VamsMCP/pyproject.toml")).toContain(`version = "${pkg()}"`);
            expect(read("tools/VamsMCP/vams_mcp/__init__.py")).toContain(
                `__version__ = "${pkg()}"`
            );
        });

        // The leading section may be the release in preparation, ahead of the package version.
        test("CHANGELOG.md and revisions.md record the package version, none older above it", () => {
            const changelog = read("CHANGELOG.md");
            const first = /^## \[([^\]]+)\]/m.exec(changelog);
            const parts = (v: string): number[] => v.split(".").map(Number);
            const [a, b] = [parts(first ? first[1] : "0"), parts(pkg())];
            const notOlder = a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
            expect(notOlder).toBeGreaterThanOrEqual(0);
            expect(changelog).toContain(`## [${pkg()}]`);
            const revisions = page("additional/revisions.md");
            expect(revisions).toContain(`| [${pkg()}](#${pkg().replace(/\./g, "")})`);
            expect(revisions).toContain(`### ${pkg()}`);
        });
    });
});

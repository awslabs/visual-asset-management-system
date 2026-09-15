/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Steering and skill files restate facts that live in source: the VPC condition blocks a pipeline flag
 * appears in, the boto3 pin, the registration construct's prop names, the changelog's heading
 * conventions, the documentation page count. Each is re-derived from its source here so that a
 * steering sentence cannot silently outlive the code it describes.
 *
 * Kiro steering documents mirror the Claude Code CLAUDE.md files (root CLAUDE.md Rule 11); the mirror
 * assertions compare the two copies of each load-bearing sentence.
 */

import * as fs from "fs";
import * as path from "path";

const REPO = path.join(__dirname, "..", "..", "..");
const readRepo = (rel: string): string => fs.readFileSync(path.join(REPO, rel), "utf8");

/**
 * Config flags referenced in a slice of vpcBuilder-nestedStack.ts, normalised to `useX` or `useX.sub`.
 * A block that guards a sub-flag with its parent's `enabled` (`useX.enabled && useX.sub`) is keyed on the
 * sub-flag, so the bare parent is dropped when one of its sub-flags is also present.
 */
function flagsIn(block: string): Set<string> {
    const out = new Set<string>();
    for (const m of block.matchAll(/pipelines\.([A-Za-z0-9_]+(?:\??\.[A-Za-z0-9_]+)*)/g)) {
        out.add(m[1].replace(/\?\./g, ".").replace(/\.enabled$/, ""));
    }
    for (const flag of [...out]) {
        if ([...out].some((other) => other.startsWith(flag + "."))) out.delete(flag);
    }
    return out;
}

/** The three VPC condition blocks, keyed by the anchors the security test also uses. */
function vpcBlocks(): { subnetBlock: string; batchBlock: string; ecsBlock: string } {
    const source = readRepo(
        path.join("infra", "lib", "nestedStacks", "vpc", "vpcBuilder-nestedStack.ts")
    );
    const subnetStart = source.indexOf("subnetConfigurations.push(subnetPublicConfig)");
    const subnetBlock = source.slice(source.lastIndexOf("if (", subnetStart), subnetStart);
    const batchStart = source.indexOf('"BatchEndpoint"');
    const batchBlock = source.slice(source.lastIndexOf("if (", batchStart), batchStart);
    const ecsStart = source.indexOf("const needsEcsIsolated");
    const ecsBlock = source.slice(source.indexOf("const needsEcsPrivate"), ecsStart);
    return { subnetBlock, batchBlock, ecsBlock };
}

/** Backticked tokens on the first line of `text` that starts with `marker`. */
function backtickedAfter(text: string, marker: string): string[] {
    const line = text.split("\n").find((l) => l.trim().startsWith(marker));
    if (!line) return [];
    return [...line.matchAll(/`([^`]+)`/g)].map((m) => m[1]);
}

const ISOLATED_MARKER = "Isolated-subnet flags (block 2 only):";
const PRIVATE_MARKER = "Private-subnet flags (all three blocks):";
const PIPELINES_STEERING = path.join("infra", "lib", "nestedStacks", "pipelines", "CLAUDE.md");
const KIRO_CDK = path.join(".kiro", "steering", "CDK_DEVELOPMENT_WORKFLOW.md");
const DOCS = path.join(REPO, "documentation", "docusaurus-site", "docs");

/**
 * The retired labeling pipeline's identifiers, assembled from parts: `retiredPipelineIdentifiers.test.ts`
 * forbids their literal spelling anywhere under infra/, this file included, so the negative assertions
 * below build them.
 */
const RETIRED_LABELING_STEM = ["Metadata", "3d", "Labeling"].join("");
const RETIRED_LABELING_DIR = "metadata" + RETIRED_LABELING_STEM.slice("Metadata".length);

describe("pipelines/CLAUDE.md VPC block lists match vpcBuilder-nestedStack.ts", () => {
    const { subnetBlock, batchBlock, ecsBlock } = vpcBlocks();

    test("control: the three blocks were extracted and name at least one flag each", () => {
        expect(subnetBlock.length).toBeGreaterThan(0);
        expect(batchBlock.length).toBeGreaterThan(0);
        expect(ecsBlock.length).toBeGreaterThan(0);
        expect(flagsIn(subnetBlock).size).toBeGreaterThan(0);
        expect(flagsIn(batchBlock).size).toBeGreaterThan(0);
        expect(flagsIn(ecsBlock).size).toBeGreaterThan(0);
    });

    test("the isolated-subnet list equals the flags in block 2 and in no other block", () => {
        const derived = [...flagsIn(batchBlock)]
            .filter((f) => !flagsIn(subnetBlock).has(f) && !flagsIn(ecsBlock).has(f))
            .sort();
        const documented = backtickedAfter(readRepo(PIPELINES_STEERING), ISOLATED_MARKER).sort();
        expect(documented).toEqual(derived);
    });

    test("the private-subnet list equals the flags present in all three blocks", () => {
        const derived = [...flagsIn(batchBlock)]
            .filter((f) => flagsIn(subnetBlock).has(f) && flagsIn(ecsBlock).has(f))
            .sort();
        const documented = backtickedAfter(readRepo(PIPELINES_STEERING), PRIVATE_MARKER).sort();
        expect(documented.length).toBeGreaterThan(0);
        expect(documented).toEqual(derived);
    });

    test("the Isaac Lab exception the steering states is real: blocks 1 and 2, ECS through needsEcsIsolated", () => {
        expect(flagsIn(subnetBlock).has("useIsaacLabTraining")).toBe(true);
        expect(flagsIn(batchBlock).has("useIsaacLabTraining")).toBe(true);
        expect(flagsIn(ecsBlock).has("useIsaacLabTraining")).toBe(false);
        const source = readRepo(
            path.join("infra", "lib", "nestedStacks", "vpc", "vpcBuilder-nestedStack.ts")
        );
        expect(source).toMatch(
            /const needsEcsIsolated = props\.config\.app\.pipelines\.useIsaacLabTraining/
        );
        expect(readRepo(PIPELINES_STEERING)).toContain(
            "`useIsaacLabTraining` places its compute in private subnets"
        );
    });

    test("the steering no longer conflates Lambda-only pipelines with isolated-subnet pipelines", () => {
        const text = readRepo(PIPELINES_STEERING);
        expect(text).toContain("A Lambda-only pipeline appears in no block");
        expect(text).not.toMatch(/Six pipelines run in isolated subnets/);
        expect(text).not.toContain("CAD/mesh metadata extraction");
        expect(text).not.toContain("GenAI metadata labeling");
    });
});

describe("root, backend, and documentation steering", () => {
    test("root CLAUDE.md names the system pipeline directory and the vector search domain", () => {
        const root = readRepo("CLAUDE.md");
        expect(fs.existsSync(path.join(REPO, "backendPipelines", "system", "genAiMetadata"))).toBe(
            true
        );
        expect(root).toContain("system/");
        expect(root).toContain("genAiMetadata/");
        expect(root).toMatch(/Processing pipelines[^\n]*SYSTEM GenAI metadata/);
        expect(root).toContain("`VECTORSEARCH`");
        expect(root).toContain("`SearchVectors`");
        expect(root).toContain("systemRecords.py");
        expect(root).toContain("executionLocks.py");
    });

    // TEMPORARY-TEST — pins the removal of the retired labeling pipeline's tree entry from root CLAUDE.md; the
    // durable guard for the tree is the directory listing itself, which review compares by hand.
    test("root CLAUDE.md no longer lists the retired pipeline directory", () => {
        expect(
            fs.existsSync(path.join(REPO, "backendPipelines", "genAi", RETIRED_LABELING_DIR))
        ).toBe(false);
        expect(readRepo("CLAUDE.md")).not.toContain(RETIRED_LABELING_DIR);
    });

    test("backend/CLAUDE.md's boto3 pin equals backend/pyproject.toml's", () => {
        const pyproject = readRepo(path.join("backend", "pyproject.toml"));
        const pin = pyproject.match(/^boto3\s*=\s*"[^\d"]*(\d+\.\d+\.\d+)"/m);
        expect(pin).not.toBeNull();
        const steering = readRepo(path.join("backend", "CLAUDE.md"));
        expect(steering).toContain("`boto3` " + pin![1]);
        expect(steering).toContain("`botocore` " + pin![1]);
    });

    test("backend/CLAUDE.md lists the vectorsearch handler domain and the shared modules, and each module exists", () => {
        const steering = readRepo(path.join("backend", "CLAUDE.md"));
        const modules: Array<[string, string]> = [
            ["vectorsearch/", path.join("backend", "backend", "handlers", "vectorsearch")],
            ["databaseAccess.py", path.join("backend", "backend", "common", "databaseAccess.py")],
            [
                "indexing/documentIds.py",
                path.join("backend", "backend", "common", "indexing", "documentIds.py"),
            ],
            [
                "indexing/fileEnumeration.py",
                path.join("backend", "backend", "common", "indexing", "fileEnumeration.py"),
            ],
            [
                "vectorsearch/embeddings.py",
                path.join("backend", "backend", "common", "vectorsearch", "embeddings.py"),
            ],
            [
                "vectorsearch/vectorStore.py",
                path.join("backend", "backend", "common", "vectorsearch", "vectorStore.py"),
            ],
            [
                "executionLocks.py",
                path.join("backend", "backend", "common", "workflows", "executionLocks.py"),
            ],
            [
                "systemRecords.py",
                path.join("backend", "backend", "common", "workflows", "systemRecords.py"),
            ],
        ];
        for (const [mention, rel] of modules) {
            expect(steering).toContain(mention);
            expect({ rel, exists: fs.existsSync(path.join(REPO, rel)) }).toEqual({
                rel,
                exists: true,
            });
        }
    });

    test("backend/tests/CLAUDE.md documents the Stubber helper, the moto prohibition, and the floor guard", () => {
        const steering = readRepo(path.join("backend", "tests", "CLAUDE.md"));
        expect(steering).toContain("#### Use the shared Stubber helper in `tests/vectorStub.py`");
        expect(steering).toContain("`stubbed_dynamodb(");
        expect(steering).toContain("`stubbed_bedrock_runtime(");
        expect(steering).toContain("VectorIndexes=");
        expect(steering).toContain("tests/common/test_vector_api_floor.py");
        const helper = readRepo(path.join("backend", "tests", "vectorStub.py"));
        expect(helper).toContain("def stubbed_dynamodb");
        expect(helper).toContain("def stubbed_bedrock_runtime");
        expect(
            fs.existsSync(path.join(REPO, "backend", "tests", "common", "test_vector_api_floor.py"))
        ).toBe(true);
    });

    test("documentation/CLAUDE.md's page count equals the number of pages on disk", () => {
        const walk = (dir: string): number =>
            fs.readdirSync(dir, { withFileTypes: true }).reduce((n, e) => {
                const full = path.join(dir, e.name);
                return n + (e.isDirectory() ? walk(full) : /\.mdx?$/.test(e.name) ? 1 : 0);
            }, 0);
        const steering = readRepo(path.join("documentation", "CLAUDE.md"));
        const stated = steering.match(/\((\d+) Markdown and MDX pages\)/);
        expect(stated).not.toBeNull();
        expect(Number(stated![1])).toBe(walk(DOCS));
    });

    test("documentation/CLAUDE.md's navigation counts equal the sidebar's leaf counts", () => {
        const sidebar = readRepo(path.join("documentation", "docusaurus-site", "sidebars.ts"));
        const leafCount = (label: string): number => {
            const at = sidebar.indexOf(`label: "${label}"`);
            expect(at).toBeGreaterThan(-1);
            const itemsAt = sidebar.indexOf("items: [", at);
            let depth = 0;
            let end = -1;
            for (let i = itemsAt + "items: ".length; i < sidebar.length; i++) {
                if (sidebar[i] === "[") depth++;
                else if (sidebar[i] === "]" && --depth === 0) {
                    end = i;
                    break;
                }
            }
            return [...sidebar.slice(itemsAt, end).matchAll(/"([a-z0-9-]+(?:\/[a-z0-9-]+)+)"/g)]
                .length;
        };
        const steering = readRepo(path.join("documentation", "CLAUDE.md"));
        for (const label of [
            "Overview",
            "Core Concepts",
            "Architecture",
            "Deployment",
            "User Guide",
            "Pipelines",
            "API Reference",
            "Additional",
        ]) {
            const m = steering.match(new RegExp(`${label.replace(/ /g, "\\s")} \\((\\d+) pages`));
            expect({ label, stated: m && m[1] }).toEqual({
                label,
                stated: String(leafCount(label)),
            });
        }
    });

    test("documentation/CLAUDE.md's spelled-out pipeline count is the one overview/features.md states", () => {
        const steering = readRepo(path.join("documentation", "CLAUDE.md"));
        const m = steering.match(/VAMS includes _([a-z-]+)_ built-in processing pipelines/);
        expect(m).not.toBeNull();
        const features = fs.readFileSync(path.join(DOCS, "overview", "features.md"), "utf8");
        expect(features).toContain(`VAMS includes ${m![1]} built-in processing pipelines`);
    });
});

describe("Kiro steering mirrors", () => {
    test("CDK_DEVELOPMENT_WORKFLOW.md carries the same VPC block lists as pipelines/CLAUDE.md", () => {
        const claude = readRepo(PIPELINES_STEERING);
        const kiro = readRepo(KIRO_CDK);
        expect(backtickedAfter(claude, ISOLATED_MARKER).length).toBeGreaterThan(0);
        expect(backtickedAfter(kiro, ISOLATED_MARKER)).toEqual(
            backtickedAfter(claude, ISOLATED_MARKER)
        );
        expect(backtickedAfter(kiro, PRIVATE_MARKER)).toEqual(
            backtickedAfter(claude, PRIVATE_MARKER)
        );
        expect(kiro).not.toMatch(/Six pipelines run in isolated subnets/);
        expect(kiro).toContain("system/genAiMetadata/");
        expect(kiro).not.toContain(RETIRED_LABELING_DIR);
        expect(kiro).not.toContain("useGenAi" + RETIRED_LABELING_STEM);
        expect(kiro).toMatch(
            /SearchBuilder \(OpenSearch, vector indexing, POST \/search\/nlp\)\s+-> storage, resourceNames, ApiBuilder2/
        );
    });

    test("backendPipelines/CLAUDE.md carries the same VPC block lists as pipelines/CLAUDE.md", () => {
        const claude = readRepo(PIPELINES_STEERING);
        const pipelines = readRepo(path.join("backendPipelines", "CLAUDE.md"));
        const line = pipelines.split("\n").find((l) => l.trim().startsWith(ISOLATED_MARKER));
        expect(line).toBeDefined();
        // Both lists sit on one line here: each is a run of backticked flags, comma-separated, closed by a period.
        const listAfter = (marker: string): string[] => {
            const m = line!.match(
                new RegExp(`${marker.replace(/[()]/g, "\\$&")} ((?:\`[^\`]+\`(?:, )?)+)\\.`)
            );
            return m ? [...m[1].matchAll(/`([^`]+)`/g)].map((x) => x[1]) : [];
        };
        expect(listAfter(ISOLATED_MARKER)).toEqual(backtickedAfter(claude, ISOLATED_MARKER));
        expect(listAfter(PRIVATE_MARKER)).toEqual(backtickedAfter(claude, PRIVATE_MARKER));
        expect(pipelines).not.toMatch(/Six pipelines run in isolated subnets/);
    });

    test("BACKEND_CDK_DEVELOPMENT_WORKFLOW.md lists the vectorsearch handler domain", () => {
        expect(
            readRepo(path.join(".kiro", "steering", "BACKEND_CDK_DEVELOPMENT_WORKFLOW.md"))
        ).toContain("`vectorsearch/`");
    });

    test("DOCUMENTATION_WORKFLOW.md carries the same page and category counts as documentation/CLAUDE.md", () => {
        const claude = readRepo(path.join("documentation", "CLAUDE.md"));
        const kiro = readRepo(path.join(".kiro", "steering", "DOCUMENTATION_WORKFLOW.md"));
        const pages = claude.match(/\((\d+) Markdown and MDX pages\)/)![1];
        expect(kiro).toContain(`(${pages} pages)`);
        for (const label of ["Core Concepts", "User Guide", "Pipelines", "API Reference"]) {
            const c = claude.match(new RegExp(`${label} \\((\\d+) pages`))![1];
            expect(kiro).toMatch(new RegExp(`${label} \\(${c} pages`));
        }
        expect(kiro).not.toMatch(/user-guide\/getting-started\.md(?!x)/);
        expect(kiro).toContain("user-guide/getting-started.mdx");
    });

    test("WEB_FRONTEND.md mirrors web/CLAUDE.md's search provider table and VECTORSEARCH flag", () => {
        const claude = readRepo(path.join("web", "CLAUDE.md"));
        const kiro = readRepo(path.join(".kiro", "steering", "WEB_FRONTEND.md"));
        const catalog = readRepo(
            path.join("web", "src", "searchPlugin", "config", "searchProviderConfig.json")
        );
        for (const text of [claude, kiro]) {
            expect(text).toContain("| `asset-list`");
            expect(text).toContain("| `unified-search`");
            expect(text).toContain("`VECTORSEARCH`");
        }
        for (const id of ["asset-list", "unified-search"])
            expect(catalog).toContain('"' + id + '"');
    });

    test("WEB_DEVELOPMENT_WORKFLOW.md points at pages that exist", () => {
        const kiro = readRepo(path.join(".kiro", "steering", "WEB_DEVELOPMENT_WORKFLOW.md"));
        const table = kiro.slice(kiro.indexOf("### **Rule 16"), kiro.indexOf("### **Rule 17"));
        const refs = [
            ...table.matchAll(
                /`((?:user-guide|overview|developer|additional|deployment|concepts|architecture)\/[a-z0-9-]+\.mdx?)`/g
            ),
        ].map((m) => m[1]);
        expect(refs.length).toBeGreaterThan(3);
        const missing = refs.filter((r) => !fs.existsSync(path.join(DOCS, r)));
        expect(missing).toEqual([]);
        expect(table).toContain("concepts/vector-search.md");
    });
});

describe("skills restate the steering they scaffold", () => {
    test("/add-pipeline uses the registration construct's real prop names", () => {
        const construct = readRepo(
            path.join(
                "infra",
                "lib",
                "nestedStacks",
                "pipelines",
                "constructs",
                "vamsSchemaRegistration-construct.ts"
            )
        );
        const props = construct.slice(
            construct.indexOf("interface"),
            construct.indexOf("export class")
        );
        const required = [...props.matchAll(/^\s+([a-zA-Z]+):\s/gm)].map((m) => m[1]);
        expect(required).toEqual(
            expect.arrayContaining(["importFunctionName", "artefactsBucket", "vamsSchemaDir"])
        );
        const skill = readRepo(path.join(".claude", "commands", "add-pipeline.md"));
        const start = skill.indexOf("new VamsSchemaRegistration(");
        expect(start).toBeGreaterThan(-1);
        const snippet = skill.slice(start, skill.indexOf("});", start));
        for (const prop of required) expect(snippet).toContain(prop + ":");
        expect(skill).not.toContain("schemaPath:");
    });

    test("/add-pipeline describes placement-dependent VPC blocks, system pipelines, and the NOTICE step", () => {
        const skill = readRepo(path.join(".claude", "commands", "add-pipeline.md"));
        expect(skill).not.toMatch(/MUST be added to \*\*all three\*\* condition blocks/);
        expect(skill).toContain("Isolated-subnet pipeline");
        expect(skill).toContain('`"isSystem": true`');
        expect(skill).toContain("`NOTICE.md`");
        expect(skill).toContain("additional/notices.md");
        expect(skill).toContain("pipelines/system-pipelines.md");
    });

    test("/add-api-endpoint cites the /search/nlp route as its worked example, spelled as apiRoutes.py spells it", () => {
        const routes = readRepo(path.join("backend", "backend", "common", "apiRoutes.py"));
        const declaration = 'API_SEARCH_NLP = ApiRoute("/search/nlp", (POST,), "search")';
        expect(routes).toContain(declaration);
        const skill = readRepo(path.join(".claude", "commands", "add-api-endpoint.md"));
        expect(skill).toContain(declaration);
        expect(skill).toContain("SEARCH_ROUTES");
    });

    test("/update-changelog matches the CHANGELOG's actual conventions", () => {
        const skill = readRepo(path.join(".claude", "commands", "update-changelog.md"));
        const changelog = readRepo("CHANGELOG.md");
        expect(changelog).toContain("### ⚠ BREAKING CHANGES");
        expect(skill).toContain("### ⚠ BREAKING CHANGES");
        expect(skill).toContain("| **Documentation**");
        expect(skill).not.toMatch(/\|\s*\*\*Docs\*\*\s*\|/); // the component table maps documentation/ to **Documentation**, never **Docs**
    });

    test("/update-docs maps the vector search and system pipeline sources to their pages, and each source exists", () => {
        const skill = readRepo(path.join(".claude", "commands", "update-docs.md"));
        for (const source of [
            "backend/backend/handlers/vectorsearch/",
            "backend/backend/common/vectorsearch/",
            "backendPipelines/system/",
            "infra/lib/nestedStacks/pipelines/system/",
            "infra/common/vamsAppFeatures.ts",
            "backend/backend/common/workflows/systemRecords.py",
        ]) {
            expect(skill).toContain(source);
            expect({ source, exists: fs.existsSync(path.join(REPO, source)) }).toEqual({
                source,
                exists: true,
            });
        }
        for (const page of [
            "docs/concepts/vector-search.md",
            "docs/pipelines/system-pipelines.md",
            "docs/pipelines/system-genai-metadata.md",
        ]) {
            expect(skill).toContain(page);
            expect(fs.existsSync(path.join(REPO, "documentation", "docusaurus-site", page))).toBe(
                true
            );
        }
    });

    test("/deploy-check checks the vector search prerequisites", () => {
        const skill = readRepo(path.join(".claude", "commands", "deploy-check.md"));
        expect(skill).toContain("#### 9. Vector Search and Amazon Bedrock Prerequisites");
        const s = skill.slice(skill.indexOf("#### 9."), skill.indexOf("### Report Format"));
        expect(s).toContain("app.vectorSearch.enabled");
        expect(s).toContain("useSystemGenAiMetadata");
        expect(s).toContain("bedrockGuardrail");
        expect(s).toContain("aws-eusc");
        expect(s).toContain("get-foundation-model-availability");
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The EDH Workstations add-on is described on its seven pages in `docs/developer/edh-workstations/`,
 * in its rule-mandated places (the add-on API reference, the OpenAPI document, the configuration
 * reference) and in hand-maintained steering, and nothing else ties those to the code: the feature
 * switches, object types and constraint fields, the add-on routes in both API sources, the web routes,
 * the permission templates, the resource inventory, the configuration defaults and fields, the MCP
 * steering names, the ConfigBuilder normalization, the page set and its sidebar place, the links into
 * and out of the pages, the EDH revision and minimal configuration block, the services EDH deploys a
 * desktop with, the diagram sources, exports and embeds, the navigation and admin tab labels, the
 * reconciler's deployment condition, the instance-type pattern, the stale reasons and launch failure
 * codes, the recipe deployments' construct ids, the names a recipe asset tree is uploaded without,
 * what starts the session sign-in on a shipped image, how the Isaac Sim recipe nests the connector,
 * the launch catalog and admin image fields, the capacity reservation answers, registry fields, view
 * fields, key, ceiling and audit actions, and the add-on's CLI JSON error fields. Each assertion
 * derives the expected value from the source of truth and reads it back out of the page, so a page
 * that drifts fails here instead of shipping.
 *
 * Durable (root CLAUDE.md Rule 13): every table below can drift again with the next switch, object
 * type, route, template, resource, configuration field, normalization, tool, page, diagram, EDH
 * revision, construct id, asset exclusion, recipe component, field or message.
 */

import * as fs from "fs";
import * as path from "path";
import { ADDON_APP_FEATURES } from "../../../../common/addonAppFeatures";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../../../common/addonResourceParamKeys";
import { EDH_SHIPPED_AMI_RECIPE_DIRS } from "../../../../config/addon/edhWorkstationsConfig";
import commercialTemplate from "../../../../config/config.template.commercial.json";
import { ASSET_EXCLUDE_GLOBS } from "../../../../lib/nestedStacks/addon/edhWorkstations/constructs/edhAmiBuild-construct";
import { matches } from "../../../docs/support/addonDocsPlacement";
import {
    DOCS,
    OPENAPI,
    REPO_ROOT,
    SIDEBARS,
    capture,
    claudeSteeringFiles,
    docLinks,
    docsPages,
    exists,
    headingAnchors,
    kiroSteeringFiles,
    page,
    paragraphsWith,
    pythonCode,
    read,
    sectionOf,
    sidebarCategoryItems,
    unresolvedLinks,
    withoutFences,
    yamlEntries,
    yamlKeys,
    yamlReplace,
} from "../../../docs/support/docsText";
import { identifiers as EDH_IDENTIFIERS, pageDir as PAGE_DIR } from "./placement";

/** The seven add-on pages, in their sidebar order. */
const PAGE_NAMES = [
    "overview",
    "enable",
    "user-guide",
    "resources",
    "uninstall",
    "cli-commands",
    "cli-troubleshooting",
];
const OVERVIEW = `${PAGE_DIR}/overview.md`;
const ENABLE = `${PAGE_DIR}/enable.md`;
const USER_GUIDE = `${PAGE_DIR}/user-guide.md`;
const RESOURCES = `${PAGE_DIR}/resources.md`;
const UNINSTALL = `${PAGE_DIR}/uninstall.md`;
const CLI_COMMANDS = `${PAGE_DIR}/cli-commands.md`;
const CLI_TROUBLESHOOTING = `${PAGE_DIR}/cli-troubleshooting.md`;
const API_ADDON = "api/addon.md";
const CONFIG_REFERENCE = "deployment/configuration-reference.md";

const STACK = "infra/lib/nestedStacks/addon/edhWorkstations/edhWorkstationsBuilder-nestedStack.ts";
const ADDON_ROUTES = "backend/backend/common/addonApiRoutes.py";
const ADDON_CONSTANTS = "backend/backend/common/addonConstants.py";
const EDH_SERVICE = "backend/backend/handlers/addon/edh/edhWorkstationService.py";
const EDH_ADMIN_SERVICE = "backend/backend/handlers/addon/edh/edhAdminService.py";
const EDH_SESSION_STATE = "backend/backend/handlers/addon/edh/common/edhSessionState.py";
const EDH_MODEL = "backend/backend/models/addon/edh.py";
const EDH_CONFIG = "infra/config/addon/edhWorkstationsConfig.ts";
const EDH_CONFIG_BUILDER =
    "documentation/docusaurus-site/src/components/ConfigBuilder/addon/edhWorkstations.ts";
const WEB_ROUTES = "web/src/addons/addonRoutes.tsx";
const WEB_NAVIGATION = "web/src/addons/addonNavigation.ts";
const ADMIN_PAGE = "web/src/addons/edh/admin/EdhAdminPage.tsx";
const IMAGES_TAB = "web/src/addons/edh/admin/ImagesTab.tsx";
const STATUS_BADGE = "web/src/addons/edh/components/WorkstationStatusBadge.tsx";
const WIZARD = "web/src/addons/edh/workstations/LaunchWorkstationWizard.tsx";
const CLI_EDH = "tools/VamsCLI/vamscli/commands/addon/edh.py";
const CLI_EDH_EXCEPTIONS = "tools/VamsCLI/vamscli/utils/addon/edh_exceptions.py";
const MCP_EDH = "tools/VamsMCP/vams_mcp/addon_tools/edh.py";
const AMI_BUILD_CONSTRUCT =
    "infra/lib/nestedStacks/addon/edhWorkstations/constructs/edhAmiBuild-construct.ts";
const RECIPES = "backendAddons/edh/amiRecipes";
const TEMPLATES_DIR = path.join(REPO_ROOT, "documentation", "permissionsTemplates");

const DEVELOPER_PAGE_SECTIONS = [
    "## How VAMS integrates with EDH",
    "## Configuration",
    "## Credentials",
    "## Identity mapping",
    "## Architecture",
    "## Permissions",
    "## Session bootstrap",
    "## Leases",
    "## Administration",
    "## AMI recipes",
    "## Containers",
    "## API reference",
    "## Current limitations",
    "## Troubleshooting",
];

/** Services EDH deploys a desktop with; EDH is external, so nothing in this repository derives them. */
const DESKTOP_DEPLOYMENT_SERVICES = [
    "Amazon EC2",
    "AWS CloudFormation",
    "Amazon EBS",
    "Elastic Load Balancing",
    "Amazon DCV",
];

const EDH_REPOSITORY = "https://github.com/awslabs/engineering-development-hub";

/** The fields of the enablement guide's minimal block; the TLS pair serves EDH's automatic certificate. */
const ENABLEMENT_FIELDS = [
    "enabled",
    "edhApiEndpoint",
    "edhAdminApiKeySecretArn",
    "edhApiCaBundleSecretArn",
    "edhApiTlsServerName",
    "edhClusterId",
    "vdi.defaultProject",
    "vdi.virtualDesktopProfileId",
];

/** The core and rule-mandated pages that name the add-on, each with the add-on pages it links to. */
const INBOUND_LINKS: Array<[string, string[]]> = [
    ["developer/addons-overview.md", [OVERVIEW]],
    ["additional/partner-integrations.md", [OVERVIEW]],
    [API_ADDON, [OVERVIEW, CLI_COMMANDS]],
    [CONFIG_REFERENCE, [OVERVIEW, ENABLE, RESOURCES]],
    ["cli/command-reference.md", [CLI_COMMANDS]],
];

/** The template's `app.addons.useEdhWorkstations` block: the add-on's shipped defaults. */
interface EdhTemplateBlock extends Record<string, unknown> {
    sessionBootstrap: { secretNamePrefix: string };
}
const templateEdhBlock = (): EdhTemplateBlock => commercialTemplate.app.addons.useEdhWorkstations;

/** Whether a name is one of the add-on's internals, by its documentation descriptor. */
const isEdh = (name: string): boolean => EDH_IDENTIFIERS.some((id) => matches(id, name));

interface EdhRoute {
    path: string;
    methods: string[];
}

/** Every `ApiRoute("/addon/edh/...", (METHODS,), "addons")` declaration in the add-on route lane. */
function edhRoutes(): EdhRoute[] {
    const code = pythonCode(read(ADDON_ROUTES));
    const re = /ApiRoute\(\s*"(\/addon\/edh\/[^"]+)"\s*,\s*\(([^)]*)\)/g;
    const out: EdhRoute[] = [];
    for (let m = re.exec(code); m; m = re.exec(code)) {
        out.push({
            path: m[1],
            methods: m[2]
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
        });
    }
    return out;
}

/** Every `path: "/edh/..."` route entry in the add-on web route table. */
function edhWebRoutes(): string[] {
    return Array.from(read(WEB_ROUTES).matchAll(/path:\s*"(\/edh\/[^"]*)"/g), (m) => m[1]);
}

/** The add-on's feature switches: the add-on lane values its descriptor claims. */
const edhFeatures = (): string[] => (Object.values(ADDON_APP_FEATURES) as string[]).filter(isEdh);

/** The add-on's permission object types, each with its constraint fields, from the add-on lane. */
function edhObjectTypes(): Array<{ type: string; fields: string[] }> {
    const code = pythonCode(read(ADDON_CONSTANTS));
    const start = code.indexOf("ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS");
    const block = code.slice(start, code.indexOf("\n}", start));
    return Array.from(
        block.matchAll(/"(\w+)":\s*\{"label":\s*"[^"]*",\s*"fields":\s*\[([\s\S]*?)\]\}/g),
        (m) => ({
            type: m[1],
            fields: Array.from(m[2].matchAll(/"value":\s*"(\w+)"/g), (f) => f[1]),
        })
    ).filter((t) => isEdh(t.type));
}

/** The `### METHOD \`/addon/edh/...\`` headings of the add-on API page. */
function documentedEdhHeadings(text: string): Array<{ method: string; path: string }> {
    return Array.from(
        text.matchAll(/^### (GET|POST|PUT|DELETE) `(\/addon\/edh\/[^`]+)`/gm),
        (m) => ({ method: m[1], path: m[2] })
    );
}

/** The cells of the table rows of a section whose first cell is a code span, by that code span. */
function tableRows(section: string): Map<string, string[]> {
    const out = new Map<string, string[]>();
    for (const line of section.split("\n")) {
        const m = /^\| `([^`]+)`/.exec(line);
        if (m)
            out.set(
                m[1],
                line
                    .split("|")
                    .slice(1, -1)
                    .map((c) => c.trim())
            );
    }
    return out;
}

/** Relative leaf paths of a configuration object (`vdi.defaultProject`). */
function leafKeysOf(o: Record<string, unknown>, prefix = ""): string[] {
    return Object.entries(o).flatMap(([k, v]) => {
        const key = prefix ? `${prefix}.${k}` : k;
        return v !== null && typeof v === "object" && !Array.isArray(v)
            ? leafKeysOf(v as Record<string, unknown>, key)
            : [key];
    });
}

/**
 * The distinct EDH revisions (`edhVersion` and `provenance.edhCommit`) recorded by the EDH fixtures
 * the backend tests replay: the revision the add-on's EDH client is verified against.
 */
function edhFixtureRevisions(): Array<{ version: string; commit: string }> {
    const dir = path.join(REPO_ROOT, "backend", "tests", "handlers", "addon", "edh", "fixtures");
    const seen = new Map<string, { version: string; commit: string }>();
    for (const f of fs.readdirSync(dir).filter((n) => n.endsWith(".json"))) {
        const doc = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
        const commit = doc.provenance?.edhCommit;
        if (typeof commit === "string") {
            seen.set(`${doc.edhVersion} ${commit}`, { version: doc.edhVersion, commit });
        }
    }
    return [...seen.values()];
}

/** The files of each steering family that name a needle. */
function steeringFamiliesNaming(needle: string): { claude: string[]; kiro: string[] } {
    return {
        claude: claudeSteeringFiles().filter((rel) => read(rel).includes(needle)),
        kiro: kiroSteeringFiles().filter((rel) => read(rel).includes(needle)),
    };
}

describe("EDH Workstations documentation matches the code it describes", () => {
    describe("feature switches", () => {
        test("the add-on lane declares the switches (count control)", () => {
            expect(edhFeatures()).toContain("EDH_WORKSTATIONS");
            expect(edhFeatures().length).toBeGreaterThanOrEqual(3);
        });

        test.each([
            [OVERVIEW, "### Feature flags"],
            [CLI_COMMANDS, "## Feature switches"],
        ])("%s %s lists every switch", (rel, heading) => {
            const text = sectionOf(page(rel), heading);
            expect(text).not.toBe("");
            expect(edhFeatures().filter((f) => !text.includes(`\`${f}\``))).toEqual([]);
        });
    });

    describe("permission object types", () => {
        test("the add-on lane declares the object types and their fields (count control)", () => {
            const types = edhObjectTypes();
            expect(types.map((t) => t.type)).toContain("edhWorkstation");
            expect(types.length).toBeGreaterThanOrEqual(3);
            expect(types.filter((t) => t.fields.length === 0)).toEqual([]);
        });

        test("the overview's Permissions table gives each object type its constraint fields", () => {
            const rows = tableRows(sectionOf(page(OVERVIEW), "## Permissions"));
            const wrong = edhObjectTypes().flatMap((t) => {
                const row = rows.get(t.type);
                const fields = row ? Array.from(row[1].matchAll(/`(\w+)`/g), (m) => m[1]) : [];
                return [...fields].sort().join() === [...t.fields].sort().join()
                    ? []
                    : [`${t.type}: ${row ? fields.join(", ") : "<no row>"}`];
            });
            expect(wrong).toEqual([]);
        });
    });

    describe("add-on routes", () => {
        test("the add-on route lane declares the EDH routes (count control)", () => {
            const routes = edhRoutes();
            expect(routes.length).toBeGreaterThanOrEqual(7);
            expect(routes.map((r) => r.path)).toContain("/addon/edh/workstations");
            expect(routes.filter((r) => r.methods.length === 0)).toEqual([]);
        });

        test("api/addon.md carries a heading per EDH route method", () => {
            const text = page(API_ADDON);
            const missing: string[] = [];
            for (const r of edhRoutes()) {
                for (const m of r.methods) {
                    if (!text.includes(`### ${m} \`${r.path}\``)) missing.push(`${m} ${r.path}`);
                }
            }
            expect(missing).toEqual([]);
        });

        test("api/addon.md documents no EDH route that the route lane does not declare", () => {
            const declared = new Set(
                edhRoutes().flatMap((r) => r.methods.map((m) => `${m} ${r.path}`))
            );
            const undeclared = documentedEdhHeadings(page(API_ADDON))
                .map((h) => `${h.method} ${h.path}`)
                .filter((k) => !declared.has(k));
            expect(undeclared).toEqual([]);
        });

        test("VAMS_API.yaml has a path item per EDH route", () => {
            const yaml = read(OPENAPI);
            expect(
                edhRoutes()
                    .filter((r) => !yaml.includes(`\n    ${r.path}:\n`))
                    .map((r) => r.path)
            ).toEqual([]);
        });

        test("every EDH path item names the gating key and secures every operation", () => {
            const yaml = read(OPENAPI);
            const problems: string[] = [];
            for (const r of edhRoutes()) {
                const start = yaml.indexOf(`\n    ${r.path}:\n`);
                if (start === -1) {
                    problems.push(`${r.path}: no path item`);
                    continue;
                }
                const rest = yaml.slice(start + 1);
                const end = rest.search(/\n {4}\/|\ncomponents:/);
                const block = rest.slice(0, end === -1 ? rest.length : end);
                if (!block.includes("app.addons.useEdhWorkstations.enabled")) {
                    problems.push(`${r.path}: description does not name the gating key`);
                }
                const secured = (block.match(/DefaultCognitoAuthorizer: \[\]/g) || []).length;
                if (secured !== r.methods.length) {
                    problems.push(
                        `${r.path}: ${secured} secured operations for ${r.methods.length} methods`
                    );
                }
            }
            expect(problems).toEqual([]);
        });

        test("every EDH operation declares a 403 response like the rest of the spec", () => {
            const yaml = read(OPENAPI);
            const problems: string[] = [];
            for (const r of edhRoutes()) {
                const start = yaml.indexOf(`\n    ${r.path}:\n`);
                if (start === -1) continue; // reported by the path-item test above
                const rest = yaml.slice(start + 1);
                const end = rest.search(/\n {4}\/|\ncomponents:/);
                const block = rest.slice(0, end === -1 ? rest.length : end);
                const declared = (block.match(/^ {16}"403":$/gm) || []).length;
                if (declared !== r.methods.length) {
                    problems.push(
                        `${r.path}: ${declared} 403 responses for ${r.methods.length} methods`
                    );
                }
            }
            expect(problems).toEqual([]);
        });

        test("the overview's API routes table has a row per EDH route with its methods", () => {
            const rows = tableRows(sectionOf(page(OVERVIEW), "### API routes"));
            const wrong = edhRoutes().flatMap((r) => {
                const row = rows.get(r.path);
                const methods = row ? row[1].split(/,\s*/).sort().join(", ") : "<no row>";
                return methods === [...r.methods].sort().join(", ")
                    ? []
                    : [`${r.path}: ${methods} vs ${r.methods.join(", ")}`];
            });
            expect(wrong).toEqual([]);
        });
    });

    describe("web routes", () => {
        test("the add-on web route table declares the EDH pages (count control)", () => {
            expect(edhWebRoutes()).toEqual(
                expect.arrayContaining(["/edh/workstations", "/edh/workstations/simulations"])
            );
        });

        test("the overview's Web routes table has a row per EDH page", () => {
            const rows = tableRows(sectionOf(page(OVERVIEW), "### Web routes"));
            expect(edhWebRoutes().filter((p) => !rows.has(p))).toEqual([]);
        });

        test("the user guide's Direct URLs table gives each EDH page's hash URL", () => {
            const text = sectionOf(page(USER_GUIDE), "## Direct URLs");
            expect(edhWebRoutes().filter((p) => !text.includes(`/#${p}\``))).toEqual([]);
        });
    });

    describe("permission templates", () => {
        const templateFiles = (): string[] =>
            fs.readdirSync(TEMPLATES_DIR).filter((f) => f.endsWith(".json") && isEdh(f));
        const template = (f: string) =>
            JSON.parse(fs.readFileSync(path.join(TEMPLATES_DIR, f), "utf8"));

        test("the add-on's templates exist (count control)", () => {
            expect(templateFiles()).toEqual(
                expect.arrayContaining(["edh-user.json", "edh-admin.json"])
            );
        });

        test("the overview's Templates table states each template's constraint count", () => {
            const lines = sectionOf(page(OVERVIEW), "### Templates").split("\n");
            const wrong = templateFiles().flatMap((f) => {
                const row = lines.find((l) => l.startsWith("| ") && l.includes(`| \`${f}\``));
                const cell = row ? row.split("|")[3].trim() : "<no row>";
                const count = String(template(f).constraints.length);
                return cell === count
                    ? []
                    : [`${f}: Constraints cell ${cell}, template has ${count}`];
            });
            expect(wrong).toEqual([]);
        });

        test.each([OVERVIEW, USER_GUIDE])("%s names each template by its metadata name", (rel) => {
            const text = page(rel);
            expect(
                templateFiles()
                    .map((f) => template(f).metadata.name as string)
                    .filter((n) => !text.includes(n))
            ).toEqual([]);
        });
    });

    describe("resource inventory", () => {
        const stackSource = (): string => (exists(STACK) ? read(STACK) : "");
        const sgKey = (): string => capture(stackSource(), /"(securityGroups\/[A-Za-z]+)"/);
        /** The add-on's resource-name keys in the add-on lane. */
        const resourceKeys = (): string[] =>
            Object.values(ADDON_RESOURCE_PARAM_KEYS).flatMap((c) => Object.values(c).filter(isEdh));

        test("the add-on stack publishes a security-group parameter key and the lane its table keys (count control)", () => {
            expect(sgKey()).toBe("securityGroups/edhAddonLambda");
            expect(stackSource()).toContain("EdhAddonLambdaSecurityGroupId");
            expect(resourceKeys().length).toBeGreaterThanOrEqual(2);
            expect(resourceKeys().filter((k) => !k.startsWith("addon/"))).toEqual([]);
        });

        test("the resources page lists every parameter the add-on publishes", () => {
            const rows = sectionOf(page(RESOURCES), "### AWS Systems Manager parameters");
            expect(
                [...resourceKeys(), sgKey()].filter(
                    (k) => !rows.includes(`/<name>-<baseStackName>/resourceNames/${k}\``)
                )
            ).toEqual([]);
        });

        test("no page names a table parameter outside the add-on prefix", () => {
            const outside = resourceKeys().map((k) => `resourceNames/${k.replace(/^addon\//, "")}`);
            const hits = docsPages().flatMap((rel) =>
                outside.filter((o) => page(rel).includes(o)).map((o) => `${rel}: ${o}`)
            );
            expect(hits).toEqual([]);
        });

        test("the resources page records the security group, its output, the secrets and the repositories", () => {
            const text = page(RESOURCES);
            expect(text).toContain("### EDH add-on Lambda security group");
            expect(text).toContain("`EdhAddonLambdaSecurityGroupId`");
            expect(text).toContain("### AWS Secrets Manager");
            expect(text).toContain("`{config.name}-{app.baseStackName}-edh-{name}`");
        });

        test("the uninstall page covers the EDH repositories and the session secrets", () => {
            const text = page(UNINSTALL);
            const prefix = templateEdhBlock().sessionBootstrap.secretNamePrefix;
            expect(text).toContain("-edh-<image name>");
            expect(text).toContain(`Values=${prefix}`);
        });
    });

    describe("configuration reference", () => {
        /** Every leaf of the template's `app.addons.useEdhWorkstations` block with its value. */
        const leaves = (): Array<[string, unknown]> => {
            const out: Array<[string, unknown]> = [];
            const walk = (o: Record<string, unknown>, prefix: string): void => {
                for (const [k, v] of Object.entries(o)) {
                    const key = `${prefix}.${k}`;
                    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
                        walk(v as Record<string, unknown>, key);
                    } else {
                        out.push([key, v]);
                    }
                }
            };
            walk(templateEdhBlock(), "app.addons.useEdhWorkstations");
            return out;
        };

        test("the commercial template carries the block (count control)", () => {
            expect(templateEdhBlock()).toBeDefined();
            expect(leaves().length).toBeGreaterThanOrEqual(20);
        });

        test("every leaf has exactly one row whose Default cell is the template value or the required marker", () => {
            const text = page(CONFIG_REFERENCE);
            const problems: string[] = [];
            for (const [key, value] of leaves()) {
                const rows = text.split("\n").filter((l) => l.startsWith(`| \`${key}\``));
                if (rows.length !== 1) {
                    problems.push(`${key}: ${rows.length} rows`);
                    continue;
                }
                const cell = rows[0].split("|")[3].trim();
                const rendered =
                    value === ""
                        ? '`""`'
                        : typeof value === "string"
                        ? `\`${value}\``
                        : `\`${JSON.stringify(value)}\``;
                const emptyish =
                    value === "" || value === 0 || (Array.isArray(value) && value.length === 0);
                const ok = cell === rendered || (cell === "_(required when enabled)_" && emptyish);
                if (!ok) problems.push(`${key}: default cell ${cell} vs template ${rendered}`);
            }
            expect(problems).toEqual([]);
        });

        test("the section carries an Implemented-by admonition whose paths exist", () => {
            const section = sectionOf(
                page(CONFIG_REFERENCE),
                "### EDH Workstations (`app.addons.useEdhWorkstations`)"
            );
            expect(section).not.toBe("");
            const admonition = /:::note\[Implemented by\]([\s\S]*?):::/.exec(section);
            expect(admonition).not.toBeNull();
            const paths = Array.from(
                (admonition as RegExpExecArray)[1].matchAll(/`(infra\/[^`]+\.ts)`/g),
                (m) => m[1]
            );
            expect(paths).toContain(STACK);
            expect(paths.filter((p) => !exists(p))).toEqual([]);
        });
    });

    describe("CLI and MCP steering", () => {
        test("the connect tool is named as a credential-returning read in both steering families", () => {
            expect(read(MCP_EDH)).toContain("def get_edh_workstation_connect");
            const named = steeringFamiliesNaming("`get_edh_workstation_connect`");
            expect(named.claude.length).toBeGreaterThan(0);
            expect(named.kiro.length).toBeGreaterThan(0);
        });
    });

    describe("ConfigBuilder normalization", () => {
        /** The config paths the add-on's ConfigBuilder module assigns on the operator's config. */
        const normalizations = (): string[] =>
            Array.from(
                read(EDH_CONFIG_BUILDER).matchAll(/setByPath\(config, "([^"]+)"/g),
                (m) => m[1]
            );

        test("the add-on's ConfigBuilder module performs the containers normalization (count control)", () => {
            expect(normalizations()).toEqual(["app.addons.useEdhWorkstations.containers.enabled"]);
        });

        test("the add-on's configuration lane makes the same assignment while the add-on is off", () => {
            const source = read(EDH_CONFIG);
            const fn = capture(
                source,
                /(export function validateEdhWorkstationsConfig[\s\S]*?\n\})/
            );
            expect(fn).toMatch(
                /if \(!edh\.enabled && edh\.containers\.enabled\) \{[\s\S]*?edh\.containers\.enabled = false;/
            );
        });

        test("a steering document of each family names the normalization the module mirrors", () => {
            const named = steeringFamiliesNaming("`useEdhWorkstations.containers.enabled`");
            expect(named.claude.length).toBeGreaterThan(0);
            expect(named.kiro.length).toBeGreaterThan(0);
        });
    });

    describe("page set and sidebar", () => {
        test("the sidebar reader returns a category's doc ids, nested ones included (control)", () => {
            const source = [
                "items: [",
                '    { type: "category", label: "Outer", items: [',
                '        "a/one",',
                '        { type: "category", label: "Inner", items: ["a/b/two"] },',
                '        "a/three",',
                "    ] },",
                "]",
            ].join("\n");
            expect(sidebarCategoryItems("Outer", source)).toEqual(["a/one", "a/b/two", "a/three"]);
            expect(sidebarCategoryItems("Inner", source)).toEqual(["a/b/two"]);
        });

        test("the add-on folder holds exactly the seven pages", () => {
            expect(
                docsPages(PAGE_DIR)
                    .map((p) => path.posix.basename(p, ".md"))
                    .sort()
            ).toEqual([...PAGE_NAMES].sort());
        });

        test("the EDH Workstations category lists the seven pages in order, after the Physna page in Add-ons", () => {
            const ids = PAGE_NAMES.map((n) => `${PAGE_DIR}/${n}`);
            expect(sidebarCategoryItems("EDH Workstations")).toEqual(ids);
            const addons = sidebarCategoryItems("Add-ons");
            const physna = addons.indexOf("developer/physna-integration");
            expect(physna).toBeGreaterThan(-1);
            expect(addons.slice(physna + 1, physna + 1 + ids.length)).toEqual(ids);
            expect(read(SIDEBARS).split(`"${ids[0]}"`)).toHaveLength(2);
        });
    });

    describe("overview page", () => {
        test("the page carries every section", () => {
            const text = page(OVERVIEW);
            expect(DEVELOPER_PAGE_SECTIONS.filter((h) => !text.includes(`\n${h}\n`))).toEqual([]);
        });

        // The diagrams draw only some of these services; the table is where the full list lives.
        test("How VAMS integrates with EDH lists the services EDH deploys a desktop with", () => {
            const lines = withoutFences(page(OVERVIEW)).split("\n");
            const section = lines.indexOf("## How VAMS integrates with EDH");
            const next = (from: number, re: RegExp): number => {
                const i = lines.findIndex((l, n) => n > from && re.test(l));
                return i === -1 ? lines.length : i;
            };
            const start = lines.indexOf("### How EDH deploys a desktop");
            expect(section).toBeGreaterThan(-1);
            expect(start).toBeGreaterThan(section);
            expect(start).toBeLessThan(next(section, /^#{1,2}\s/));
            const services = lines
                .slice(start, next(start, /^#{1,3}\s/))
                .filter((l) => l.startsWith("| "))
                .map((l) => l.split("|")[1].trim());
            expect(DESKTOP_DEPLOYMENT_SERVICES.filter((s) => !services.includes(s))).toEqual([]);
        });

        test("the page links the API reference, the CLI page, the configuration reference and each other page", () => {
            const text = page(OVERVIEW);
            expect(text).toContain("](../../api/addon.md#edh-workstations-add-on)");
            expect(text).toContain("](./cli-commands.md)");
            expect(text).toContain(
                "](../../deployment/configuration-reference.md#edh-workstations-appaddonsuseedhworkstations)"
            );
            const intro = sectionOf(text, "# EDH Workstations Add-on").split("\n## ")[0];
            expect(
                PAGE_NAMES.filter((n) => n !== "overview" && !intro.includes(`](./${n}.md)`))
            ).toEqual([]);
        });
    });

    describe("links", () => {
        test("the anchor extractor reads a page's headings (control)", () => {
            const anchors = headingAnchors(
                "# T\n\n## Before you begin\n\n```bash\n# not a heading\n```\n\n### EDH (`app.x`)\n\n## Before you begin\n"
            );
            expect([...anchors]).toEqual([
                "t",
                "before-you-begin",
                "edh-appx",
                "before-you-begin-1",
            ]);
        });

        test("a line that opens with inline code does not open a fence (control)", () => {
            const anchors = headingAnchors(
                "```x``` is inline code\n\n## After inline code\n\n```bash\n# fenced\n```\n\n## After the fence\n"
            );
            expect([...anchors]).toEqual(["after-inline-code", "after-the-fence"]);
        });

        test("the registries and rule-mandated pages that name the add-on link to its pages", () => {
            const links = docLinks();
            const missing = INBOUND_LINKS.flatMap(([from, targets]) =>
                targets
                    .filter((to) => !links.some((l) => l.from === from && l.to === to))
                    .map((to) => `${from} -> ${to}`)
            );
            expect(missing).toEqual([]);
        });

        test.each(PAGE_NAMES)(
            "every link to the %s page, and every link from it to a page, resolves to the page and heading it names",
            (name) => {
                const rel = `${PAGE_DIR}/${name}.md`;
                const links = docLinks();
                const inbound = links.filter((l) => l.to === rel && l.from !== rel);
                expect(inbound.length).toBeGreaterThan(0);
                const outbound = links.filter((l) => l.from === rel);
                expect(outbound.length).toBeGreaterThanOrEqual(10);
                expect(unresolvedLinks([...inbound, ...outbound])).toEqual([]);
            }
        );
    });

    describe("enablement guide", () => {
        test("the EDH fixtures record one EDH revision (count control)", () => {
            const revisions = edhFixtureRevisions();
            expect(revisions).toHaveLength(1);
            expect(revisions[0].version).toMatch(/^\d+\.\d+\.\d+$/);
            expect(revisions[0].commit).toMatch(/^[0-9a-f]{40}$/);
        });

        test("the page links the EDH repository and names the revision the fixtures record", () => {
            const [{ version, commit }] = edhFixtureRevisions();
            const text = page(ENABLE);
            expect(text).toContain(`](${EDH_REPOSITORY})`);
            expect(text).toContain(`EDH ${version}`);
            expect(text).toContain(`\`${commit.slice(0, 8)}\``);
            expect(text).toContain(`](${EDH_REPOSITORY}/commit/${commit})`);
            // The commit link and the checkout command spell the hash in full, and no other hash.
            const hashes = Array.from(text.matchAll(/\b[0-9a-f]{40}\b/g), (m) => m[0]);
            expect(hashes.length).toBeGreaterThanOrEqual(2);
            expect(hashes.filter((h) => h !== commit)).toEqual([]);
        });

        test("the minimal-block fields are template fields and hold every field an enabled add-on requires", () => {
            const leaves = leafKeysOf(templateEdhBlock());
            expect(ENABLEMENT_FIELDS.filter((f) => !leaves.includes(f))).toEqual([]);
            // A field required only under a sub-feature's own `enabled` switch is not one of them.
            const subFeatures = leaves
                .filter((k) => k.endsWith(".enabled"))
                .map((k) => k.slice(0, -"enabled".length));
            const required = page(CONFIG_REFERENCE)
                .split("\n")
                .filter(
                    (l) =>
                        l.startsWith("| `app.addons.useEdhWorkstations.") &&
                        l.split("|")[3].trim() === "_(required when enabled)_"
                )
                .map((l) => l.split("`")[1].replace("app.addons.useEdhWorkstations.", ""))
                .filter((k) => !subFeatures.some((p) => k.startsWith(p)));
            expect(required).toContain("edhApiEndpoint");
            expect(required.filter((k) => !ENABLEMENT_FIELDS.includes(k))).toEqual([]);
        });

        test("Configure VAMS sets exactly the minimal-block fields in its example and describes each in its table", () => {
            const lines = page(ENABLE).split("\n");
            const markdown = withoutFences(lines.join("\n")).split("\n");
            const start = markdown.findIndex((l) => /^## (Step \d+: )?Configure VAMS$/.test(l));
            expect(start).toBeGreaterThan(-1);
            const end = markdown.findIndex((l, n) => n > start && /^#{1,2}\s/.test(l));
            const section = lines.slice(start, end === -1 ? lines.length : end).join("\n");
            const example = /^```json\n([\s\S]*?)\n```$/m.exec(section);
            expect(example).not.toBeNull();
            const block = JSON.parse((example as RegExpExecArray)[1]).app.addons.useEdhWorkstations;
            expect(leafKeysOf(block).sort()).toEqual([...ENABLEMENT_FIELDS].sort());
            expect(ENABLEMENT_FIELDS.filter((f) => !section.includes(`| \`${f}\``))).toEqual([]);
        });
    });

    describe("diagrams", () => {
        const DRAWIO_DIR = "documentation/diagrams/source/drawio";
        /** The directories that each hold a byte-identical copy of every PNG export. */
        const EXPORT_DIRS = [
            "documentation/diagrams",
            "documentation/docusaurus-site/static/img",
            "documentation/docusaurus-site/docs/assets/images/diagrams",
        ];
        const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
        /** Each EDH diagram, the pages that embed it and the section heading it sits under. */
        const EDH_DIAGRAMS: Array<{
            key: string;
            embeds: Array<{ page: string; section: string }>;
        }> = [
            {
                key: "addon_edhWorkstations_overview",
                embeds: [
                    { page: OVERVIEW, section: "## How VAMS integrates with EDH" },
                    { page: ENABLE, section: "# Enable the EDH Workstations Add-on" },
                    { page: USER_GUIDE, section: "## How workstations work" },
                ],
            },
            // The black-background variant of the overview, embedded nowhere.
            { key: "addon_edhWorkstations_overview_dark", embeds: [] },
            {
                key: "addon_edhWorkstations_network",
                embeds: [
                    { page: ENABLE, section: "## Step 4: Connect the networks" },
                    { page: RESOURCES, section: "## Network" },
                ],
            },
            {
                key: "addon_edhWorkstations_sessionBootstrap",
                embeds: [{ page: OVERVIEW, section: "## Session bootstrap" }],
            },
            {
                key: "addon_edhWorkstations_amiRecipes",
                embeds: [{ page: OVERVIEW, section: "## AMI recipes" }],
            },
        ];
        const keys = EDH_DIAGRAMS.map((d) => d.key);

        // A new EDH diagram source must join the list, so its exports and embeds are held too.
        test("every EDH draw.io source is listed here (count control)", () => {
            const sources = fs
                .readdirSync(path.join(REPO_ROOT, DRAWIO_DIR))
                .filter((f) => f.startsWith("addon_edhWorkstations_") && f.endsWith(".drawio"))
                .map((f) => f.replace(/\.drawio$/, ""));
            expect(sources.sort()).toEqual([...keys].sort());
        });

        test.each(keys)("%s: the draw.io source is an uncompressed mxfile", (key) => {
            const rel = `${DRAWIO_DIR}/${key}.drawio`;
            expect(exists(rel)).toBe(true);
            const source = read(rel);
            expect(source.trimStart().startsWith("<mxfile")).toBe(true);
            // Every page carries its graph as XML; a compressed page holds base64 text instead.
            const pages = (source.match(/<diagram\b/g) ?? []).length;
            expect(pages).toBeGreaterThan(0);
            expect((source.match(/<mxGraphModel\b/g) ?? []).length).toBe(pages);
        });

        test("the dark overview variant draws the overview's cells on a black canvas", () => {
            const cellsOf = (source: string): string[] =>
                [
                    ...source.matchAll(
                        /<mxCell\b[^>]*?\bid="([^"]*)"(?:[^>]*?\bvalue="([^"]*)")?/g
                    ),
                ].map((m) => `${m[1]}=${m[2] ?? ""}`);
            const light = read(`${DRAWIO_DIR}/addon_edhWorkstations_overview.drawio`);
            const dark = read(`${DRAWIO_DIR}/addon_edhWorkstations_overview_dark.drawio`);
            expect(cellsOf(light).length).toBeGreaterThan(0);
            expect(cellsOf(dark)).toEqual(cellsOf(light));
            expect(dark).toMatch(/<mxGraphModel\b[^>]*\bbackground="#000000"/);
            expect(light).not.toMatch(/<mxGraphModel\b[^>]*\bbackground="#000000"/);
        });

        test.each(keys)(
            "%s: the PNG export has identical bytes in every image directory",
            (key) => {
                const files = EXPORT_DIRS.map((d) => `${d}/${key}.png`);
                expect(files.filter((f) => !exists(f))).toEqual([]);
                const [first, ...rest] = files.map((f) => fs.readFileSync(path.join(REPO_ROOT, f)));
                expect(first.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)).toBe(true);
                expect(rest.filter((b) => !b.equals(first)).length).toBe(0);
            }
        );

        /**
         * "" when the embed sits inside the section it illustrates, ahead of the section's
         * subsections and of any Mermaid block in it; otherwise the problem. The section runs to the
         * next heading at its own level or above.
         */
        const embedPlacement = (text: string, key: string, section: string): string => {
            const lines = text.split("\n");
            const markdown = withoutFences(text).split("\n");
            const start = markdown.indexOf(section);
            if (start === -1) return "no such heading";
            const nextLine = (re: RegExp): number => {
                const i = markdown.findIndex((l, n) => n > start && re.test(l));
                return i === -1 ? lines.length : i;
            };
            const end = nextLine(new RegExp(`^#{1,${section.indexOf(" ")}}\\s`));
            const embed = new RegExp(`!\\[[^\\]]+\\]\\(/img/${key}\\.png\\)`);
            const at = markdown.findIndex((l, n) => n > start && n < end && embed.test(l));
            if (at === -1) return "not embedded in the section";
            if (at > nextLine(/^#{1,6}\s/)) return "embedded below a subsection";
            const mermaid = lines.findIndex(
                (l, n) => n > start && n < end && /^\s*`{3,}mermaid/.test(l)
            );
            if (mermaid !== -1 && mermaid < at) return "embedded below a Mermaid block";
            return "";
        };

        test("the embed placement check reads a section (control)", () => {
            const img = "![A](/img/k.png)\n";
            const mermaid = "```mermaid\nflowchart LR\n```\n\n";
            expect(embedPlacement(`## S\n\nIntro.\n\n${img}\n### Sub\n`, "k", "## S")).toBe("");
            expect(embedPlacement(`## S\n\n### Sub\n\n${img}`, "k", "## S")).toBe(
                "embedded below a subsection"
            );
            expect(embedPlacement(`## S\n\n${mermaid}${img}`, "k", "## S")).toBe(
                "embedded below a Mermaid block"
            );
            expect(embedPlacement(`### S\n\nText.\n\n### T\n\n${img}`, "k", "### S")).toBe(
                "not embedded in the section"
            );
        });

        test.each(
            EDH_DIAGRAMS.flatMap((d) =>
                d.embeds.map((e): [string, string, string] => [e.page, d.key, e.section])
            )
        )("%s embeds %s under %s", (rel, key, section) => {
            expect(embedPlacement(page(rel), key, section)).toBe("");
        });

        test("no page outside the add-on's folder embeds an EDH diagram", () => {
            const embed = /\]\(\/img\/addon_edhWorkstations_\w+\.png\)/;
            expect(
                docsPages().filter(
                    (rel) => !rel.startsWith(`${PAGE_DIR}/`) && embed.test(page(rel))
                )
            ).toEqual([]);
        });

        // The steering says where the diagram files live (root CLAUDE.md Rule 11).
        test("a steering document of each family names the diagram source and export locations", () => {
            const locations = [
                `${DRAWIO_DIR}/<key>.drawio`,
                ...EXPORT_DIRS.map((d) => `${d}/<key>.png`),
            ];
            const naming = (files: string[]): string[] =>
                files.filter((rel) => locations.every((l) => read(rel).includes(l)));
            expect(naming(claudeSteeringFiles()).length).toBeGreaterThan(0);
            expect(naming(kiroSteeringFiles()).length).toBeGreaterThan(0);
        });
    });

    describe("AMI recipes", () => {
        const ROW_KEY = "app.addons.useEdhWorkstations.predefinedImages.amiRecipes";
        const spellings = ["`assets[]`", "`${ASSET_<id>}`", "`parameters`", "`${PARAM_<key>}`"];

        test("the overview names every shipped recipe directory", () => {
            const text = page(OVERVIEW);
            expect(EDH_SHIPPED_AMI_RECIPE_DIRS.length).toBeGreaterThanOrEqual(2);
            expect(
                EDH_SHIPPED_AMI_RECIPE_DIRS.filter((dir) => !text.includes(path.basename(dir)))
            ).toEqual([]);
        });

        test("the amiRecipes row explains the asset and parameter manifest keys", () => {
            const row = page(CONFIG_REFERENCE)
                .split("\n")
                .find((l) => l.startsWith(`| \`${ROW_KEY}\``));
            expect(row).toBeDefined();
            expect(spellings.filter((s) => !row?.includes(s))).toEqual([]);
        });
    });

    describe("configuration fields", () => {
        /** Relative leaf paths of the template's `useEdhWorkstations` block (`vdi.defaultProject`). */
        const leafKeys = (): string[] => leafKeysOf(templateEdhBlock());

        test("the template block has leaves (count control)", () => {
            expect(leafKeys()).toContain("vdi.defaultProject");
            expect(leafKeys().length).toBeGreaterThanOrEqual(20);
        });

        // A field removed from the configuration must leave both tables with it: a row that outlives
        // its field describes a switch the deployment no longer reads.
        test("configuration-reference.md documents no useEdhWorkstations field the template lacks", () => {
            const keys = page(CONFIG_REFERENCE)
                .split("\n")
                .filter((l) => l.startsWith("| `app.addons.useEdhWorkstations."))
                .map((l) => l.split("`")[1].replace("app.addons.useEdhWorkstations.", ""));
            expect(keys.length).toBeGreaterThanOrEqual(20);
            const leaves = leafKeys();
            expect(keys.filter((k) => !leaves.includes(k))).toEqual([]);
        });

        test("the overview's field table documents no useEdhWorkstations field the template lacks", () => {
            const text = page(OVERVIEW);
            const start = text.indexOf("| Field ");
            expect(start).toBeGreaterThan(-1);
            const keys = text
                .slice(start, text.indexOf("\n\n", start))
                .split("\n")
                .filter((l) => l.startsWith("| `"))
                .map((l) => l.split("`")[1]);
            expect(keys.length).toBeGreaterThanOrEqual(20);
            const leaves = leafKeys();
            expect(keys.filter((k) => !leaves.includes(k))).toEqual([]);
        });

        test("the instance-type pattern is one spelling, up to its anchor, in the configuration lane, the backend model and the configuration reference", () => {
            const cdk = /EDH_INSTANCE_TYPE_PATTERN = \/(.+?)\/;/.exec(read(EDH_CONFIG));
            const py = /edh_instance_type_pattern = r'(.+?)'/.exec(read(EDH_MODEL));
            expect(cdk).not.toBeNull();
            expect(py).not.toBeNull();
            const pattern = (cdk as RegExpExecArray)[1];
            // Pydantic v1 matches with re.match, where a terminal `$` also matches immediately
            // before a trailing newline, so the Python spelling anchors with `\Z`. JavaScript has
            // no `\Z`, and its `$` does not match there. Everything ahead of the anchor is shared.
            expect(pattern.endsWith("$")).toBe(true);
            expect((py as RegExpExecArray)[1]).toBe(`${pattern.slice(0, -1)}\\Z`);
            const row = page(CONFIG_REFERENCE)
                .split("\n")
                .find((l) =>
                    l.startsWith("| `app.addons.useEdhWorkstations.edhAllowedInstanceTypes`")
                );
            expect(row).toContain(`\`${pattern}\``);
        });
    });

    describe("navigation and tab labels", () => {
        /** The add-on navigation lists (main menu, admin area) the navigation file exports. */
        const NAV_EXPORTS = ["addonNavMainSections", "addonNavAdminSections"];
        /** The top-level entries of every add-on navigation array, as source text. */
        const navSections = (): string[] => {
            const source = read(WEB_NAVIGATION);
            const out: string[] = [];
            for (const name of NAV_EXPORTS) {
                const declared = source.indexOf(`export const ${name}`);
                if (declared === -1) throw new Error(`${WEB_NAVIGATION} does not export ${name}`);
                // The array literal opens after the `=`; the `[]` of the declared type is not it.
                const open = source.indexOf("= [", declared) + 2;
                let depth = 0;
                let from = -1;
                for (let i = open + 1; i < source.length; i++) {
                    const ch = source[i];
                    if (ch === '"') {
                        const close = source.indexOf('"', i + 1);
                        if (close === -1)
                            throw new Error(`unterminated string literal from offset ${i}`);
                        i = close;
                    } else if (ch === "{") {
                        if (depth === 0) from = i;
                        depth += 1;
                    } else if (ch === "}") {
                        depth -= 1;
                        if (depth === 0) out.push(source.slice(from, i + 1));
                    } else if (ch === "]" && depth === 0) {
                        break;
                    }
                }
            }
            return out;
        };
        /** The `text:` labels of the navigation sections gated on an EDH switch. */
        const navLabels = (): string[] =>
            navSections()
                .filter((s) =>
                    edhFeatures().some((f) =>
                        s.includes(`featuresEnabledRestriction: [addonFeaturesEnabled.${f}`)
                    )
                )
                .flatMap((s) => Array.from(s.matchAll(/text: "([^"]+)"/g), (m) => m[1]));
        /** The `label:` values of the admin page's `TABS` table. */
        const adminTabs = (): string[] => {
            const source = read(ADMIN_PAGE);
            const start = source.indexOf("const TABS");
            const block = source.slice(start, source.indexOf("];", start));
            return Array.from(block.matchAll(/label: "([^"]+)"/g), (m) => m[1]);
        };
        const named = (text: string, label: string): boolean =>
            text.includes(`**${label}**`) ||
            text.split("\n").some((l) => new RegExp(`^#{2,4} ${label}$`).test(l));

        test("the sources declare the labels (count control)", () => {
            expect(navSections().length).toBeGreaterThanOrEqual(2);
            expect(navLabels()).toContain("Virtual Workstations");
            expect(navLabels()).toContain("Workstation Admin");
            // The Simulations link carries its own containers restriction inside the first section.
            expect(navLabels()).toContain("Simulations");
            expect(navLabels().length).toBeGreaterThanOrEqual(5);
            expect(adminTabs()).toContain("Images");
            // Sessions, Capacity, Images, AMI Recipes, Settings. The extractor reads the TABS array
            // literal, so the count is the declared set and not what a feature flag renders; the
            // order is pinned by EdhAdminPage.test.tsx, not here.
            expect(adminTabs().length).toBe(5);
        });

        test.each([USER_GUIDE, OVERVIEW])("%s names every EDH navigation label", (rel) => {
            const text = page(rel);
            expect(navLabels().filter((l) => !named(text, l))).toEqual([]);
        });

        test.each([USER_GUIDE, OVERVIEW])("%s names every Workstation Admin tab", (rel) => {
            const text = page(rel);
            expect(adminTabs().filter((l) => !text.includes(`**${l}**`))).toEqual([]);
        });
    });

    describe("session reconciler scope", () => {
        /** Character ranges of every `if (sessionBootstrapConfig.enabled) { ... }` block in the stack. */
        const gatedRanges = (source: string): Array<[number, number]> => {
            const out: Array<[number, number]> = [];
            const re = /if \(sessionBootstrapConfig\.enabled\)\s*\{/g;
            for (let m = re.exec(source); m; m = re.exec(source)) {
                let depth = 1;
                let i = m.index + m[0].length;
                while (i < source.length && depth > 0) {
                    if (source[i] === "{") depth += 1;
                    else if (source[i] === "}") depth -= 1;
                    i += 1;
                }
                out.push([m.index, i]);
            }
            return out;
        };
        const gated = (source: string, needle: string): boolean => {
            const at = source.indexOf(needle);
            expect(at).toBeGreaterThan(-1);
            return gatedRanges(source).some(([a, b]) => at >= a && at < b);
        };

        test("the stack gates something on session bootstrap (count control)", () => {
            const source = read(STACK);
            expect(gatedRanges(source).length).toBeGreaterThanOrEqual(1);
            expect(gated(source, '"EdhSessionSecretsKey"')).toBe(true);
        });

        // The reconciler and its schedule deploy with the add-on; only the credential work inside the
        // sweep follows the session-bootstrap flag. The pages below say so in the same terms, so a
        // stack that gates the reconciler again fails here beside the rows that would then be wrong.
        test("the stack builds the reconciler and its schedule outside the session-bootstrap condition", () => {
            const source = read(STACK);
            expect(gated(source, "buildEdhSessionReconcilerFunction(")).toBe(false);
            expect(gated(source, '"EdhSessionReconcilerSchedule"')).toBe(false);
        });

        test("the resources page and the overview tie the reconciler to the add-on, not to session bootstrap", () => {
            // The three rows state the same rule in the same words: the sweep runs, and revokes,
            // whatever the session-bootstrap flag says.
            const rule = "revokes credentials with or without `sessionBootstrap.enabled`";
            const resources = page(RESOURCES).split("\n");
            const schedule = resources.find((l) =>
                l.includes("**EDH Session Reconciler Schedule**")
            );
            expect(schedule).toContain("when `app.addons.useEdhWorkstations.enabled`");
            expect(schedule).toContain(rule);
            expect(schedule).not.toMatch(
                /\(when `app\.addons\.useEdhWorkstations\.sessionBootstrap\.enabled`\)/
            );
            const lambdas = resources.find((l) => /edhSessionReconciler`? \(/.test(l));
            expect(lambdas).toContain(rule);
            expect(lambdas).not.toMatch(/every 15 minutes when `sessionBootstrap\.enabled`/);
            const row = page(OVERVIEW)
                .split("\n")
                .find((l) => l.startsWith("| `edhSessionReconciler`"));
            expect(row).toContain("every 15 minutes");
            expect(row).toContain(rule);
            expect(row).not.toMatch(/every 15 minutes \(`sessionBootstrap\.enabled`\)/);
        });
    });

    describe("stale reasons", () => {
        const reasons = (): string[] =>
            Array.from(
                pythonCode(read(EDH_ADMIN_SERVICE)).matchAll(
                    /^STALE_REASON_[A-Z_]+ = "([A-Za-z]+)"$/gm
                ),
                (m) => m[1]
            );

        test("edhAdminService.py declares the reasons (count control)", () => {
            expect(reasons()).toContain("missingInEdh");
            expect(reasons().length).toBeGreaterThanOrEqual(2);
        });

        test.each([OVERVIEW, API_ADDON, CLI_COMMANDS])(
            "%s quotes every staleReason value",
            (rel) => {
                const text = page(rel);
                expect(
                    reasons().filter((v) => !text.includes(`\`${v}\``) && !text.includes(`"${v}"`))
                ).toEqual([]);
            }
        );

        test("VAMS_API.yaml staleReason enum is the declared set", () => {
            const m = /staleReason:\n +type: string\n +enum:\n((?: +- \w+\n)+)/.exec(read(OPENAPI));
            expect(m).not.toBeNull();
            const values = Array.from((m as RegExpExecArray)[1].matchAll(/- (\w+)/g), (x) => x[1]);
            expect(values.sort()).toEqual([...reasons()].sort());
        });
    });

    describe("launch failure codes", () => {
        const codes = (): string[] =>
            Array.from(
                pythonCode(read(EDH_SESSION_STATE)).matchAll(
                    /^LAST_ERROR_[A-Z_]+ = "([A-Z_]+)"$/gm
                ),
                (m) => m[1]
            );

        test("edhSessionState.py declares the codes (count control)", () => {
            expect(codes()).toContain("CREATE_TIMED_OUT");
            expect(codes().length).toBeGreaterThanOrEqual(6);
        });

        // A code the handlers can store must be explained wherever the record is documented.
        test.each([OVERVIEW, API_ADDON, CLI_TROUBLESHOOTING, CLI_COMMANDS])(
            "%s names every lastError code",
            (rel) => {
                const text = page(rel);
                expect(codes().filter((c) => !text.includes(`\`${c}\``))).toEqual([]);
            }
        );

        // The record schema constrains the field, so a code the handlers can store and the enum omits
        // makes the documented response contradict a valid one.
        test("VAMS_API.yaml lastError enum is the declared set plus null", () => {
            const m = /lastError:\n(?: +[a-z]+: .+\n)+ +enum:\n((?: +- \w+\n)+)/.exec(
                read(OPENAPI)
            );
            expect(m).not.toBeNull();
            const values = Array.from((m as RegExpExecArray)[1].matchAll(/- (\w+)/g), (x) => x[1]);
            expect(values.sort()).toEqual([...codes(), "null"].sort());
        });

        // The web shows one sentence per code, and both guides quote it so a reader can match what
        // the badge says to its cause.
        test("the user guide and the overview quote the web's sentence for every code", () => {
            const block = /export const LAST_ERROR_COPY[^=]*= \{\n([\s\S]*?)\n\};/.exec(
                read(STATUS_BADGE)
            );
            expect(block).not.toBeNull();
            const copy = Array.from(
                (block as RegExpExecArray)[1].matchAll(/([A-Z_]+):\s*"([^"]+)"/g),
                (m) => [m[1], m[2]] as const
            );
            expect(copy.map(([code]) => code).sort()).toEqual([...codes()].sort());
            for (const rel of [USER_GUIDE, OVERVIEW]) {
                const text = page(rel);
                expect([rel, copy.filter(([, s]) => !text.includes(s))]).toEqual([rel, []]);
            }
        });
    });

    describe("AMI recipe deployment construct ids", () => {
        // The ids edhAmiBuild-construct.ts gives the recipe bundle and recipe asset deployments, with
        // the recipe and asset ids written as <recipeId> and <assetId>.
        const ids = (): string[] =>
            Array.from(
                read(AMI_BUILD_CONSTRUCT).matchAll(
                    /new s3deployment\.BucketDeployment\(\s*this,\s*`([^`]+)`/g
                ),
                (m) =>
                    m[1]
                        .replace(/\$\{recipe\.recipeId\}/g, "<recipeId>")
                        .replace(/\$\{asset\.id\}/g, "<assetId>")
            );

        test("the construct declares a bundle and an asset deployment (control)", () => {
            expect(ids()).toHaveLength(2);
            expect(ids().filter((id) => !id.includes("<recipeId>"))).toEqual([]);
        });

        test("a steering document of each family names each recipe deployment by its construct id", () => {
            const naming = (files: string[]): string[] =>
                files.filter((rel) => ids().every((id) => read(rel).includes(id)));
            expect(naming(claudeSteeringFiles()).length).toBeGreaterThan(0);
            expect(naming(kiroSteeringFiles()).length).toBeGreaterThan(0);
        });
    });

    describe("AMI recipe asset exclusions", () => {
        // The names an asset tree is uploaded without, spelled as the overview spells them: each
        // `Source.asset` exclude glob as written.
        const spellings = (): string[] => [...ASSET_EXCLUDE_GLOBS];

        test("the exclusions read as the page spells them (control)", () => {
            expect(spellings()).toEqual(
                expect.arrayContaining(["build", "*.pyc", ".env", ".env.*"])
            );
        });

        test("the overview lists every name an asset tree is uploaded without", () => {
            const line = page(OVERVIEW)
                .split("\n")
                .find((l) => l.startsWith("-   Each tree is uploaded to"));
            expect(line).toBeDefined();
            expect(spellings().filter((s) => !line?.includes(`\`${s}\``))).toEqual([]);
        });
    });

    describe("session sign-in on a shipped image", () => {
        // The shipped Linux recipes start the sign-in from an XDG autostart entry when the user's
        // desktop session starts; a boot unit is only the example for an image built another way.
        const AUTOSTART = "/etc/xdg/autostart/vams-session-bootstrap.desktop";

        test("the shipped Linux recipes install the sign-in's autostart entry (control)", () => {
            for (const recipe of ["al2023-desktop", "isaacsim"]) {
                const component = `${RECIPES}/${recipe}/components/060-session-bootstrap.yml`;
                expect(read(component)).toContain(`cat > ${AUTOSTART} <<`);
            }
        });

        test.each([
            [ENABLE, "### Desktop instance role"],
            [ENABLE, "### Session bootstrap"],
            [OVERVIEW, "### Session credentials"],
        ])("%s %s names the autostart entry, not a boot unit", (rel, heading) => {
            const text = sectionOf(withoutFences(page(rel)), heading);
            expect(text).toContain("XDG autostart entry");
            expect(text).not.toMatch(/\bboot unit\b/);
        });
    });

    describe("Isaac Sim recipe", () => {
        // Kit treats each child of an extension folder as an extension named after that child, so the
        // recipe installs the connector as vams.connector.isaacsim inside the folder Kit loads.
        test("the recipe nests the connector as vams.connector.isaacsim", () => {
            const recipe = read(`${RECIPES}/isaacsim/components/050-connector.yml`);
            expect(recipe).toContain(
                "mkdir -p /opt/vams/isaacsim-connector/vams.connector.isaacsim"
            );
            const parents = Array.from(
                recipe.matchAll(/(\S+)\/vams\.connector\.isaacsim(?![\w.])/g),
                (m) => m[1]
            );
            expect(parents).toContain("/opt/vams/isaacsim-connector");
        });
    });

    describe("launch catalog architecture", () => {
        /** The keys of the catalog image item the workstation service builds. */
        const imageKeys = (): string[] =>
            Array.from(
                capture(
                    read(EDH_SERVICE),
                    /def _image_item\(stack\):[\s\S]*?return \{([\s\S]*?)\n {4}\}/
                ).matchAll(/"(\w+)":/g),
                (m) => m[1]
            );
        /** The keys of the instance-type item the workstation service lists. */
        const instanceTypeKeys = (): string[] =>
            Array.from(
                capture(
                    read(EDH_SERVICE),
                    /def list_instance_types\(event\):[\s\S]*?'Items': \[\{([^}]*)\}/
                ).matchAll(/'(\w+)':/g),
                (m) => m[1]
            );
        /**
         * The launch refusal of a type the image's profile lists only under another family. Read by
         * its full text: the service declares the capacity reservation's own `instanceType does not
         * match ...` refusal as well, which a prefix match would pick up first.
         */
        const refusal = (): string =>
            capture(read(EDH_SERVICE), /"(instanceType does not match the image's architecture)"/);

        test("the workstation service declares the fields and the refusal (control)", () => {
            expect(imageKeys()).toEqual(expect.arrayContaining(["imageId", "architecture"]));
            expect(instanceTypeKeys()).toEqual(["instanceType", "architectures"]);
            expect(refusal()).toBe("instanceType does not match the image's architecture");
        });

        /** The YAML path of a catalog list schema's item properties. */
        const itemProperties = (schema: string): string[] => [
            "components",
            "schemas",
            schema,
            "properties",
            "Items",
            "items",
            "properties",
        ];
        const IMAGE_ARCHITECTURE = [...itemProperties("edhCatalogImageList"), "architecture"];
        const TYPE_ARCHITECTURES = [...itemProperties("edhInstanceTypeList"), "architectures"];

        /**
         * The architecture fields VAMS_API.yaml declares with another shape than the service returns:
         * an image's `architecture` is a string or None (`stack_architecture`), an instance type's
         * `architectures` a list of architecture group names.
         */
        const architectureShapeDrift = (yaml: string): string[] => {
            const image = yamlEntries(yaml, IMAGE_ARCHITECTURE);
            const list = yamlEntries(yaml, TYPE_ARCHITECTURES);
            const item = yamlEntries(yaml, [...TYPE_ARCHITECTURES, "items"]);
            return [
                image.type === "string" && image.nullable === "true" ? "" : "image architecture",
                list.type === "array" && item.type === "string"
                    ? ""
                    : "instance-type architectures",
            ].filter((d) => d !== "");
        };

        test("VAMS_API.yaml declares every catalog item field the service returns", () => {
            const yaml = read(OPENAPI);
            const items = (schema: string) => yamlKeys(yaml, itemProperties(schema));
            expect(imageKeys().filter((k) => !items("edhCatalogImageList").includes(k))).toEqual(
                []
            );
            expect(
                instanceTypeKeys().filter((k) => !items("edhInstanceTypeList").includes(k))
            ).toEqual([]);
        });

        test("VAMS_API.yaml declares the architecture fields with the shapes the service returns", () => {
            expect(architectureShapeDrift(read(OPENAPI))).toEqual([]);
        });

        test("the shape check flags an architecture field declared with another shape (control)", () => {
            const yaml = read(OPENAPI);
            const mutated = [
                yamlReplace(yaml, TYPE_ARCHITECTURES, "type: array", "type: string"),
                yamlReplace(yaml, [...TYPE_ARCHITECTURES, "items"], "type: string", "type: object"),
                yamlReplace(yaml, IMAGE_ARCHITECTURE, "type: string", "type: array"),
                yamlReplace(yaml, IMAGE_ARCHITECTURE, "nullable: true", "nullable: false"),
            ];
            expect(mutated.filter((m) => m === yaml)).toEqual([]);
            expect(mutated.map(architectureShapeDrift)).toEqual([
                ["instance-type architectures"],
                ["instance-type architectures"],
                ["image architecture"],
                ["image architecture"],
            ]);
        });

        test.each([
            ["### GET `/addon/edh/catalog/images`", "images"],
            ["### GET `/addon/edh/catalog/instance-types`", "instance types"],
        ])("api/addon.md %s names every item field", (heading, kind) => {
            const keys = kind === "images" ? imageKeys() : instanceTypeKeys();
            const text = sectionOf(page(API_ADDON), heading);
            expect(text).not.toBe("");
            expect(keys.filter((k) => !text.includes(`"${k}"`))).toEqual([]);
        });

        test.each([
            OPENAPI,
            `documentation/docusaurus-site/docs/${API_ADDON}`,
            `documentation/docusaurus-site/docs/${OVERVIEW}`,
            `documentation/docusaurus-site/docs/${CLI_TROUBLESHOOTING}`,
        ])("%s names the launch refusal", (rel) => {
            expect(read(rel)).toContain(refusal());
        });

        test("the CLI commands page names every column the catalog commands print", () => {
            const source = read(CLI_EDH);
            const headers = Array.from(source.matchAll(/rows = \[\[([^\]]+)\]\]/g), (m) =>
                Array.from(m[1].matchAll(/'([^']+)'/g), (c) => c[1])
            );
            expect(headers.length).toBeGreaterThanOrEqual(2);
            const columns = headers.flat();
            expect(columns).toEqual(expect.arrayContaining(["ARCH", "INSTANCE TYPE"]));
            const text = page(CLI_COMMANDS);
            expect(columns.filter((c) => !text.includes(`\`${c}\``))).toEqual([]);
        });

        test("the user guide and the CLI commands page quote the message for a catalog that runs no type of the image", () => {
            const web = capture(
                read(WIZARD),
                /`(No instance type in this deployment runs this image's architecture) \(\$\{/
            );
            const cli = capture(
                read(CLI_EDH),
                /\("(No instance type in this deployment runs this image's architecture) "/
            );
            expect(web).not.toBe("");
            expect(cli).toBe(web);
            expect(page(USER_GUIDE)).toContain(web);
            expect(page(CLI_COMMANDS)).toContain(cli);
        });
    });

    describe("per-recipe launch instance types", () => {
        const FIELD = "launchInstanceTypes";
        const CLI_CLIENT = "tools/VamsCLI/vamscli/utils/addon/edh_api_client.py";
        const RECIPE_STORE = "backend/backend/handlers/addon/edh/common/edhAmiRecipeStore.py";
        /** The launch refusal of a type the image's recipe does not offer. */
        const refusal = (): string =>
            capture(read(EDH_SERVICE), /'(instanceType is not offered for this image)'/);
        /** The request fields a duplicate copies from its source, from the CLI client. */
        const copiedFields = (): string[] =>
            Array.from(
                capture(
                    read(CLI_CLIENT),
                    /_EDH_AMI_RECIPE_COPIED_FIELDS = \(([\s\S]*?)\)/
                ).matchAll(/"(\w+)"/g),
                (m) => m[1]
            );
        /** The recipe fields the content hash covers, from the recipe store. */
        const hashedFields = (): string[] =>
            Array.from(
                capture(read(RECIPE_STORE), /^HASHED_FIELDS = \(([^)]*)\)/m).matchAll(/"(\w+)"/g),
                (m) => m[1]
            );
        /** The launch instance types the shipped Isaac Sim recipe offers. */
        const isaacSimTypes = (): string[] =>
            JSON.parse(read(`${RECIPES}/isaacsim/recipe.json`))[FIELD] ?? [];
        /** The paragraph of the overview's Isaac Sim section that states its requirements. */
        const isaacSimRequirements = (): string =>
            sectionOf(page(OVERVIEW), "### Isaac Sim recipe")
                .split("\n")
                .find((l) => l.startsWith("**Requirements for this recipe.**")) ?? "";

        test("the sources declare the field, the refusal, the copied fields and the hash fields (control)", () => {
            expect(refusal()).toBe("instanceType is not offered for this image");
            expect(copiedFields()).toEqual(expect.arrayContaining(["buildInstanceType", FIELD]));
            expect(hashedFields()).toEqual(expect.arrayContaining(["parentImage"]));
            expect(hashedFields()).not.toContain(FIELD);
            expect(isaacSimTypes().length).toBeGreaterThan(0);
            expect(isaacSimRequirements()).not.toBe("");
        });

        test.each([
            OPENAPI,
            `documentation/docusaurus-site/docs/${API_ADDON}`,
            `documentation/docusaurus-site/docs/${OVERVIEW}`,
            `documentation/docusaurus-site/docs/${CLI_TROUBLESHOOTING}`,
        ])("%s names the launch refusal", (rel) => {
            expect(read(rel)).toContain(refusal());
        });

        test("the overview's duplicate paragraph names every field a duplicate copies", () => {
            const paragraph =
                sectionOf(page(OVERVIEW), "### Duplicating a recipe")
                    .split("\n")
                    .find((l) => l.includes("send the source's request fields")) ?? "";
            expect(paragraph).not.toBe("");
            expect(copiedFields().filter((f) => !paragraph.includes(`\`${f}\``))).toEqual([]);
        });

        test("the CLI commands page's duplicate paragraph names every field a duplicate copies", () => {
            const paragraph =
                page(CLI_COMMANDS)
                    .split("\n")
                    .find((l) => l.includes("creates the copy from its definition")) ?? "";
            expect(paragraph).not.toBe("");
            expect(copiedFields().filter((f) => !paragraph.includes(`\`${f}\``))).toEqual([]);
        });

        test("the overview names the field among those outside the content hash", () => {
            const bullet =
                sectionOf(page(OVERVIEW), "### Content hash and versions")
                    .split("\n")
                    .find((l) => l.includes("sits outside the hash")) ?? "";
            const outside = bullet.slice(bullet.indexOf("Everything else"));
            expect(outside).toContain(`\`${FIELD}\``);
            expect(hashedFields().filter((f) => outside.includes(`\`${f}\``))).toEqual([]);
        });

        test.each([
            [OVERVIEW, "### Recipes"],
            [OVERVIEW, "### Launch catalog"],
            [OVERVIEW, "### Launch sequence"],
            [RESOURCES, ""],
            [CLI_COMMANDS, ""],
            [USER_GUIDE, ""],
            [API_ADDON, "### GET `/addon/edh/catalog/images`"],
            [API_ADDON, "### POST `/addon/edh/admin/ami-recipes`"],
        ])("%s %s names the field", (rel, heading) => {
            const text = heading ? sectionOf(page(rel), heading) : page(rel);
            expect(text).not.toBe("");
            expect(text).toContain(FIELD);
        });

        test("VAMS_API.yaml declares the field on the catalog image, the recipe request and the recipe", () => {
            const yaml = read(OPENAPI);
            const imageItem = [
                "components",
                "schemas",
                "edhCatalogImageList",
                "properties",
                "Items",
                "items",
                "properties",
            ];
            expect(yamlKeys(yaml, imageItem)).toContain(FIELD);
            for (const schema of ["edhAmiRecipeRequest", "edhAmiRecipe"]) {
                expect(yamlKeys(yaml, ["components", "schemas", schema, "properties"])).toContain(
                    FIELD
                );
            }
            const request = yamlEntries(yaml, [
                "components",
                "schemas",
                "edhAmiRecipeRequest",
                "properties",
                FIELD,
            ]);
            expect(request.maxItems).toBe("64");
        });

        test("the overview's Isaac Sim requirements name every type the recipe offers and the families it leaves out", () => {
            const text = isaacSimRequirements();
            expect(isaacSimTypes().filter((t) => !text.includes(`\`${t}\``))).toEqual([]);
            for (const excluded of [
                "`g4dn`",
                "`g6f`",
                "`gr6f`",
                "`g5g`",
                "`g5.xlarge`",
                "`g6.xlarge`",
            ]) {
                expect(text).toContain(excluded);
            }
            expect(text).toContain("at least 32 GiB of instance memory");
        });

        test("the user guide and the CLI commands page quote the message for an image whose recipe offers no allowed type", () => {
            const web = capture(
                read(WIZARD),
                /"(No instance type this deployment allows is offered for this image\.)/
            );
            const cli = capture(
                read(CLI_EDH),
                /\("(No instance type this deployment allows is offered for this image\.) "/
            );
            expect(web).not.toBe("");
            expect(cli).toBe(web);
            expect(page(USER_GUIDE)).toContain(web);
            expect(page(CLI_COMMANDS)).toContain(cli);
        });

        test("the CLI commands page names the recipe list's LAUNCH TYPES column", () => {
            expect(read(CLI_EDH)).toContain("'LAUNCH TYPES':<13");
            expect(page(CLI_COMMANDS)).toContain("`LAUNCH TYPES`");
        });
    });

    describe("admin images architecture", () => {
        /** The keys of the admin image item the admin service builds. */
        const adminImageKeys = (): string[] =>
            Array.from(
                capture(
                    read(EDH_ADMIN_SERVICE),
                    /def _image_item\(kind_label, record\):[\s\S]*?return \{([\s\S]*?)\n {4}\}/
                ).matchAll(/"(\w+)":/g),
                (m) => m[1]
            );
        /** The column headers of the admin Images tab, in order. */
        const imagesTabColumns = (): string[] =>
            Array.from(read(IMAGES_TAB).matchAll(/header: "([^"]+)"/g), (m) => m[1]);
        const ADMIN_IMAGE_ITEM = [
            "components",
            "schemas",
            "edhAdminImageList",
            "properties",
            "Items",
            "items",
            "properties",
        ];

        test("the admin service and the Images tab carry the architecture (control)", () => {
            expect(adminImageKeys()).toEqual(
                expect.arrayContaining(["kind", "imageId", "osFamily", "architecture"])
            );
            expect(imagesTabColumns()).toEqual(expect.arrayContaining(["OS", "Architecture"]));
        });

        test("VAMS_API.yaml declares every admin image field, the architecture as a nullable string", () => {
            const yaml = read(OPENAPI);
            const declared = yamlKeys(yaml, ADMIN_IMAGE_ITEM);
            expect(adminImageKeys().filter((k) => !declared.includes(k))).toEqual([]);
            const architecture = yamlEntries(yaml, [...ADMIN_IMAGE_ITEM, "architecture"]);
            expect([architecture.type, architecture.nullable]).toEqual(["string", "true"]);
        });

        test.each([
            [API_ADDON, "### GET `/addon/edh/admin/images`", '"'],
            [CLI_COMMANDS, "### edh admin images list", "`"],
        ])("%s names every admin image field", (rel, heading, quote) => {
            const text = sectionOf(page(rel), heading);
            expect(text).not.toBe("");
            expect(adminImageKeys().filter((k) => !text.includes(`${quote}${k}${quote}`))).toEqual(
                []
            );
        });

        test("the user guide names every Images tab column in the tab's order", () => {
            const columns = imagesTabColumns();
            const [paragraph] = paragraphsWith(
                sectionOf(page(USER_GUIDE), "### Registering and deleting images"),
                "The **Images** tab lists"
            );
            expect(paragraph).toBeDefined();
            const named = Array.from(paragraph.matchAll(/\*\*([^*]+)\*\*/g), (m) => m[1]);
            expect(named.filter((name) => columns.includes(name))).toEqual(columns);
        });
    });

    describe("capacity reservations", () => {
        const RESERVATIONS = "backend/backend/handlers/addon/edh/common/edhCapacityReservations.py";
        const CAPACITY_RESERVATIONS = "## Capacity reservations";
        /** The answers the workstation service gives a launch that names a reservation. */
        const serviceRefusals = (): string[] =>
            Array.from(
                pythonCode(read(EDH_SERVICE)).matchAll(
                    /^RESERVATION_[A-Z0-9_]+_MESSAGE = "([^"]+)"$/gm
                ),
                (m) => m[1]
            );
        /** The answers the admin service gives the registry routes that are not a view. */
        const adminRefusals = (): string[] =>
            Array.from(
                pythonCode(read(EDH_ADMIN_SERVICE)).matchAll(
                    /^RESERVATION_[A-Z0-9_]+_MESSAGE = "([^"]+)"$/gm
                ),
                (m) => m[1]
            );
        const reservations = (): string => pythonCode(read(RESERVATIONS));
        /** The fields a registry row carries. */
        const registryFields = (): string[] =>
            Array.from(
                capture(reservations(), /^REGISTRY_FIELDS = \(([\s\S]*?)\)/m).matchAll(/"(\w+)"/g),
                (m) => m[1]
            );
        /** The fields of the capacity reservation view: the registration's, then EC2's. */
        const viewFields = (): string[] => [
            ...Array.from(
                capture(reservations(), /^VIEW_FIELDS = \(([^)]*)\)/m).matchAll(/"(\w+)"/g),
                (m) => m[1]
            ),
            ...Array.from(
                capture(reservations(), /^EC2_VIEW_FIELDS = \{([\s\S]*?)\n\}/m).matchAll(
                    /^ {4}"(\w+)":/gm
                ),
                (m) => m[1]
            ),
        ];
        const keyPrefix = (): string =>
            capture(reservations(), /^REGISTRY_KEY_PREFIX = "([^"]+)"$/m);
        const ceiling = (): string =>
            capture(reservations(), /^MAX_REGISTERED_CAPACITY_RESERVATIONS = (\d+)$/m);
        /** The audit actions the admin service writes for the registry. */
        const auditActions = (): string[] =>
            Array.from(
                read(EDH_ADMIN_SERVICE).matchAll(
                    /log_actions\(event, "(edhCapacityReservation\w+)"/g
                ),
                (m) => m[1]
            );

        test("the sources declare the refusals, the fields, the key, the ceiling and the audit actions (control)", () => {
            expect(serviceRefusals()).toEqual(
                expect.arrayContaining([
                    "capacityReservationId is not registered",
                    "The capacity reservation has no free capacity",
                ])
            );
            // The route answers only a launch's reservation checks; a start EDH refuses while the desktop's
            // reservation is full is the launch worker's to record, through the shared classifier.
            expect(serviceRefusals()).toEqual([
                "capacityReservationId is not registered",
                "The capacity reservation could not be read; try again",
                "The capacity reservation is not active",
                "instanceType does not match the capacity reservation",
                "The capacity reservation's platform does not match the image",
                "The capacity reservation has no free capacity",
            ]);
            const worker = pythonCode(
                read("backend/backend/handlers/addon/edh/edhLaunchWorker.py")
            );
            expect(
                capture(worker, /^def _start_refusal_code\(row, err\):([\s\S]*?)^def /m)
            ).toMatch(/reservation_refusal_last_error\(facts\) == LAST_ERROR_RESERVATION_FULL/);
            expect(adminRefusals()).toContain(
                "Capacity reservation not found in this account and Region"
            );
            expect(registryFields()).toEqual(
                expect.arrayContaining(["capacityReservationId", "fallbackToOnDemand", "updatedBy"])
            );
            expect(viewFields()).toEqual(
                expect.arrayContaining(["label", "availableInstanceCount", "reservationType"])
            );
            expect(keyPrefix()).toBe("SETTINGS#capacityReservation#");
            expect(ceiling()).not.toBe("");
            expect(auditActions().sort()).toEqual([
                "edhCapacityReservationDelete",
                "edhCapacityReservationRegister",
                "edhCapacityReservationUpdate",
            ]);
        });

        test.each([
            [OVERVIEW, CAPACITY_RESERVATIONS],
            [API_ADDON, "## EDH Workstations add-on"],
        ])("%s %s quotes every launch and registry answer", (rel, heading) => {
            const text = sectionOf(page(rel), heading);
            expect(text).not.toBe("");
            expect(
                [...serviceRefusals(), ...adminRefusals()].filter((m) => !text.includes(m))
            ).toEqual([]);
        });

        test("the overview's registry table gives every field a registry row carries", () => {
            const rows = tableRows(sectionOf(page(OVERVIEW), "### The registry"));
            // A row's first cell may name two fields (`createdAt`, `createdBy`).
            const named = [...rows.values()].flatMap((cells) =>
                Array.from(cells[0].matchAll(/`(\w+)`/g), (m) => m[1])
            );
            expect(named.length).toBeGreaterThan(0);
            expect(registryFields().filter((f) => !named.includes(f))).toEqual([]);
        });

        test("api/addon.md's capacity reservation view has a row per view field", () => {
            const rows = tableRows(sectionOf(page(API_ADDON), "#### Capacity reservation view"));
            expect(rows.size).toBeGreaterThan(0);
            expect(viewFields().filter((f) => !rows.has(f))).toEqual([]);
            expect([...rows.keys()].filter((f) => !viewFields().includes(f))).toEqual([]);
        });

        test("the overview names the registry key and states its ceiling", () => {
            const text = page(OVERVIEW);
            expect(text).toContain(`\`${keyPrefix()}<cr-id>\``);
            expect(text).toContain(`At most ${ceiling()} reservations are registered at once`);
        });

        test("the resources page names the registry key and its request limits state the ceiling", () => {
            const text = page(RESOURCES);
            expect(text).toContain(`\`${keyPrefix()}<cr-id>\``);
            const row = sectionOf(text, "### Request limits")
                .split("\n")
                .find((l) => l.startsWith("| Registered capacity reservations "));
            expect(row ? row.split("|")[2].trim() : "<no row>").toBe(ceiling());
        });

        test("the overview names every registry audit action", () => {
            const text = page(OVERVIEW);
            expect(auditActions().filter((a) => !text.includes(`\`${a}\``))).toEqual([]);
        });
    });

    describe("CLI JSON error fields", () => {
        /** The `--json-output` keys the add-on's CLI exception classes add. */
        const jsonFieldKeys = (): string[] =>
            Array.from(
                read(CLI_EDH_EXCEPTIONS).matchAll(/json_fields\s*=\s*\{\s*"(\w+)"/g),
                (m) => m[1]
            );

        test("the add-on's CLI exceptions declare the fields (count control)", () => {
            expect(jsonFieldKeys()).toEqual(
                expect.arrayContaining(["outcomeUnknown", "startInProgress"])
            );
        });

        test("the CLI troubleshooting page's JSON Error Fields table lists every field", () => {
            const rows = tableRows(sectionOf(page(CLI_TROUBLESHOOTING), "## JSON Error Fields"));
            expect(jsonFieldKeys().filter((k) => !rows.has(k))).toEqual([]);
        });
    });
});

// DOCS is the docs root the shared readers resolve pages against; the add-on folder sits in it.
test("the add-on page folder is under the docs root (control)", () => {
    expect(fs.existsSync(path.join(DOCS, PAGE_DIR))).toBe(true);
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core aggregator files name no add-on.
 *
 * Every list VAMS aggregates (API routes, constants and permission object types, resource-name
 * keys, CSP sources, default-role routes and constraints, VPC endpoints, WAF rule exclusions,
 * feature switches, web routes and navigation, API service calls, viewer configuration and
 * manifest, CLI commands and API client methods, MCP tools) is composed from a core part and an
 * add-on part kept in a file of its own beside it. The core files are the ones edited on every
 * release and by forks, so an add-on entry placed back in one of them puts add-on code on the path
 * of every core change again.
 *
 * Two further checks cover core files outside the aggregators: a file an add-on reaches only
 * through a generic seam names none of that add-on's identifiers, and a core file with no add-on
 * lane holds only the add-on lines listed for it.
 *
 * Asserted on the source text, across the backend, CDK, CLI, MCP and web trees, because the seam is
 * a file boundary rather than a synthesized behaviour.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

/**
 * The core aggregator files and, beside each, the add-on file its add-on entries live in. An add-on
 * path ending in `/` is a directory of add-on modules.
 */
const CORE_AND_ADDON_FILES: Array<{ core: string; addon: string }> = [
    {
        core: "backend/backend/common/apiRoutes.py",
        addon: "backend/backend/common/addonApiRoutes.py",
    },
    {
        core: "infra/lib/core-stack.ts",
        addon: "infra/lib/nestedStacks/addon/addonBuilder-nestedStack.ts",
    },
    { core: "infra/lib/helper/security.ts", addon: "infra/lib/nestedStacks/addon/addonCsp.ts" },
    {
        core: "infra/lib/nestedStacks/auth/constructs/dynamodb-authdefaults-ro-construct.ts",
        addon: "infra/lib/nestedStacks/addon/addonDefaultRoutes.ts",
    },
    {
        core: "web/src/services/APIService.ts",
        addon: "web/src/services/addons/physnaAPIService.ts",
    },
    {
        core: "web/src/visualizerPlugin/config/viewerConfig.json",
        addon: "web/src/visualizerPlugin/config/addonViewerConfig.json",
    },
    {
        core: "web/src/visualizerPlugin/viewers/manifest.ts",
        addon: "web/src/visualizerPlugin/viewers/addonManifest.ts",
    },
    { core: "infra/common/vamsAppFeatures.ts", addon: "infra/common/addonAppFeatures.ts" },
    {
        core: "tools/VamsCLI/vamscli/constants.py",
        addon: "tools/VamsCLI/vamscli/addon_constants.py",
    },
    { core: "infra/common/resourceParamKeys.ts", addon: "infra/common/addonResourceParamKeys.ts" },
    {
        core: "backend/backend/common/resourceNames.py",
        addon: "backend/backend/common/addonResourceNames.py",
    },
    { core: "web/src/routes.tsx", addon: "web/src/addons/addonRoutes.tsx" },
    { core: "web/src/layout/Navigation.tsx", addon: "web/src/addons/addonNavigation.ts" },
    {
        core: "backend/backend/common/constants.py",
        addon: "backend/backend/common/addonConstants.py",
    },
    {
        core: "infra/lib/nestedStacks/auth/constructs/dynamodb-authdefaults-admin-construct.ts",
        addon: "infra/lib/nestedStacks/addon/addonDefaultConstraints.ts",
    },
    {
        core: "infra/lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts",
        addon: "infra/lib/nestedStacks/addon/addonVpcEndpoints.ts",
    },
    {
        core: "infra/lib/cf-waf-stack.ts",
        addon: "infra/lib/nestedStacks/addon/addonWafExclusions.ts",
    },
    {
        core: "web/src/common/constants/featuresEnabled.ts",
        addon: "web/src/addons/addonFeatures.ts",
    },
    { core: "tools/VamsCLI/vamscli/main.py", addon: "tools/VamsCLI/vamscli/commands/addon/" },
    {
        core: "tools/VamsCLI/vamscli/utils/api_client.py",
        addon: "tools/VamsCLI/vamscli/utils/addon_api_client.py",
    },
    { core: "tools/VamsMCP/vams_mcp/server.py", addon: "tools/VamsMCP/vams_mcp/addon_tools/" },
];

/**
 * Core files outside the aggregators that the EDH Workstations add-on reaches only through a
 * generic seam: the log-redaction keys, the temporary session key, the CLI exceptions and log
 * redaction, the MCP feature pre-flight, the deployment configuration and ConfigBuilder add-on
 * modules, the storage stack (the add-on tables and the artefacts `addon/` prefix live in the
 * add-on stack), and the route parity test's add-on hook. Garnet and Physna configuration stays
 * inline in config.ts and the ConfigBuilder, the storage stack names the Garnet and Physna
 * consumers of a core table, and the parity test names the Physna route it checks, so these files
 * are checked for EDH alone.
 */
const CORE_FILES_WITHOUT_EDH: string[] = [
    "backend/backend/customLogging/logger.py",
    "backend/backend/common/logRedaction.py",
    "backend/backend/handlers/auth/apiKeyService.py",
    "tools/VamsCLI/vamscli/utils/logging.py",
    "tools/VamsCLI/vamscli/utils/exceptions.py",
    "tools/VamsMCP/vams_mcp/client.py",
    "infra/config/config.ts",
    "infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts",
    "documentation/docusaurus-site/src/components/ConfigBuilder/schema.ts",
    "documentation/docusaurus-site/src/components/ConfigBuilder/validation.ts",
    "documentation/docusaurus-site/src/components/ConfigBuilder/defaults.ts",
    "documentation/docusaurus-site/src/components/ConfigBuilder/derived.ts",
    "documentation/docusaurus-site/src/components/ConfigBuilder/ruleHelpers.ts",
    "documentation/docusaurus-site/src/components/ConfigBuilder/README.md",
    "infra/test/api/apiRouteBackendCdkParity.test.ts",
];

/**
 * Core files with no add-on lane that hold an add-on line by necessity, and those lines. Tailwind
 * scans only the files its content globs list, so an add-on module built with Tailwind is listed
 * there by path.
 */
const CORE_FILES_WITH_ADDON_LINES: Record<string, string[]> = {
    "web/tailwind.config.js": ['"./src/addons/edh/**/*.{ts,tsx}",'],
};

/**
 * The add-ons VAMS ships: the stacks under `infra/lib/nestedStacks/addon/<name>` and the licensed
 * viewers, which ship disabled from the add-on viewer files.
 */
const ADDON_NAME = /physna|garnet|vntana|veerum/i;

/**
 * The EDH Workstations add-on, in the spellings its identifiers use: `edh` where no letter precedes
 * it (`edh.py`, `/addon/edh/`, `_edh_error`), `Edh` and `EDH`. Case-sensitive, because a
 * case-insensitive `edh` is found inside expectedHeaders, signedHeaders and ConsolidatedHandler.
 */
const EDH_NAME = /(?<![A-Za-z])edh|Edh|EDH/;

const ADDON_DETECTORS = [ADDON_NAME, EDH_NAME];

/** A line each detector reports, for the controls that place one in a scanned file. */
const SAMPLE_LINES = new Map<RegExp, string>([
    [ADDON_NAME, "ADDON_APP_FEATURES.PHYSNA_ADDON,"],
    [EDH_NAME, "ADDON_APP_FEATURES.EDH_WORKSTATIONS,"],
]);

/** One check: the core file, the detectors it runs and the lines it allows. */
interface Scan {
    relative: string;
    detectors: RegExp[];
    allowed: string[];
}

const AGGREGATOR_SCANS: Scan[] = CORE_AND_ADDON_FILES.map(({ core }) => ({
    relative: core,
    detectors: ADDON_DETECTORS,
    allowed: [],
}));

const SEAM_SCANS: Scan[] = CORE_FILES_WITHOUT_EDH.map((relative) => ({
    relative,
    detectors: [EDH_NAME],
    allowed: [],
}));

const ALLOWED_LINE_SCANS: Scan[] = Object.entries(CORE_FILES_WITH_ADDON_LINES).map(
    ([relative, allowed]) => ({ relative, detectors: ADDON_DETECTORS, allowed })
);

const SCANS: Scan[] = [...AGGREGATOR_SCANS, ...SEAM_SCANS, ...ALLOWED_LINE_SCANS];

const read = (relative: string): string => fs.readFileSync(path.join(REPO_ROOT, relative), "utf8");

/** An add-on file's text, or the text of every file directly inside an add-on directory. */
const readAddon = (relative: string): string => {
    const absolute = path.join(REPO_ROOT, relative);
    if (!fs.statSync(absolute).isDirectory()) {
        return read(relative);
    }
    return fs
        .readdirSync(absolute, { withFileTypes: true })
        .filter((entry) => entry.isFile())
        .map((entry) => read(path.join(relative, entry.name)))
        .join("\n");
};

/** The lines of `text` that a detector matches and `allowed` does not list, as `file:line: text`. */
const offendingLines = (
    relative: string,
    text: string,
    detectors: RegExp[],
    allowed: string[]
): string[] =>
    text
        .split("\n")
        .map((line, index) => ({ line: index + 1, text: line.trim() }))
        .filter(({ text }) => detectors.some((detector) => detector.test(text)))
        .filter(({ text }) => !allowed.includes(text))
        .map(({ line, text }) => `${relative}:${line}: ${text}`);

const scan = ({ relative, detectors, allowed }: Scan): string[] =>
    offendingLines(relative, read(relative), detectors, allowed);

/** `test.each` rows named by the scanned file. */
const rows = (scans: Scan[]): Array<[string, Scan]> =>
    scans.map((entry) => [entry.relative, entry]);

describe("core aggregator files name no add-on", () => {
    const coreFiles = CORE_AND_ADDON_FILES.map((pair) => pair.core);

    test.each(rows(AGGREGATOR_SCANS))("%s", (_relative, entry) => {
        // Move the entry into the add-on file beside this one.
        expect(scan(entry)).toEqual([]);
    });

    test("[control] every add-on file exists and names an add-on", () => {
        // Without this the assertion above passes for a detector that matches nothing.
        const silent = CORE_AND_ADDON_FILES.map(({ addon }) => addon).filter(
            (addon) => !ADDON_DETECTORS.some((detector) => detector.test(readAddon(addon)))
        );
        expect(silent).toEqual([]);
    });

    test("[control] every core file exists and is non-trivial", () => {
        // The smallest core aggregator, vamsAppFeatures.ts, is a single enum under 1 KB.
        for (const relative of coreFiles) {
            expect(read(relative).length).toBeGreaterThan(500);
        }
    });
});

describe("core files behind a generic add-on seam name no EDH identifier", () => {
    test.each(rows(SEAM_SCANS))("%s", (_relative, entry) => {
        // Move the EDH part into the add-on module behind the seam this file exposes.
        expect(scan(entry)).toEqual([]);
    });

    test("[control] every file exists and is non-trivial", () => {
        for (const relative of CORE_FILES_WITHOUT_EDH) {
            expect(read(relative).length).toBeGreaterThan(500);
        }
    });
});

describe("core files without an add-on lane hold only their allowed add-on lines", () => {
    test.each(rows(ALLOWED_LINE_SCANS))("%s", (_relative, entry) => {
        expect(scan(entry)).toEqual([]);
    });

    test("[control] every allowed line is in its file once and names an add-on", () => {
        // A stale entry would allow a line nobody wrote, and an entry no detector matches allows
        // nothing.
        for (const [relative, allowed] of Object.entries(CORE_FILES_WITH_ADDON_LINES)) {
            const lines = read(relative)
                .split("\n")
                .map((line) => line.trim());
            for (const line of allowed) {
                expect({ line, count: lines.filter((text) => text === line).length }).toEqual({
                    line,
                    count: 1,
                });
                expect(ADDON_DETECTORS.some((detector) => detector.test(line))).toBe(true);
            }
        }
    });
});

describe("[control] the detectors", () => {
    test.each([
        "useEdhWorkstations",
        "EDH_CONTAINERS",
        "/addon/edh/workstations",
        "from .edh import edh",
        "self._edh_error(response)",
        "initial_admin_allow_all_edhworkstations",
    ])("the EDH detector finds %s", (text) => {
        expect(EDH_NAME.test(text)).toBe(true);
    });

    test.each(["expectedHeaders", "signedHeaders", "ConsolidatedHandler"])(
        "the EDH detector passes over %s",
        (text) => {
            expect(EDH_NAME.test(text)).toBe(false);
        }
    );

    test.each(rows(SCANS))("an add-on line placed in %s is reported", (relative, entry) => {
        // Proves each check reads its file through a scan that can report a line, for every
        // detector it runs, rather than passing because nothing could be reported. Counted against
        // the file's own findings, so a finding in the file fails its check and not this one.
        const found = scan(entry).length;
        for (const detector of entry.detectors) {
            const sample = SAMPLE_LINES.get(detector) as string;
            const reported = offendingLines(
                relative,
                `${sample}\n${read(relative)}`,
                entry.detectors,
                entry.allowed
            );
            expect(reported[0]).toBe(`${relative}:1: ${sample}`);
            expect(reported).toHaveLength(found + 1);
        }
    });
});

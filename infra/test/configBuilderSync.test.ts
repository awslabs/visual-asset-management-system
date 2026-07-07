/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Drift guard for the docs-site ConfigBuilder component.
 *
 * The interactive config builder at
 *   documentation/docusaurus-site/src/components/ConfigBuilder/
 * is a HAND-MAINTAINED mirror of the deploy-time config source of truth in
 * infra/config/. It is not generated, so it silently drifts whenever a config
 * option is added or a default changes. These tests fail loudly on that drift:
 *
 *   1. defaults.ts presets must deep-equal the two template JSONs.
 *   2. schema.ts FIELDS must cover every leaf of the ConfigPublic interface
 *      (and must not reference paths that no longer exist).
 *
 * When a test here fails, the fix is to update the ConfigBuilder to match the
 * source of truth (see the component's README.md for which files to touch), or
 * — for a deliberately non-editable field — add it to KNOWN_NON_FORM_PATHS
 * below with a justification.
 *
 * Out of scope (mirror imperative logic in getConfig() that cannot be checked
 * declaratively; guarded by the steering-doc rules instead): validation.ts and
 * derived.ts. Note: derived.ts's VPC_IMPLYING_PATHS currently lists a few
 * pipelines (usePreview3dThumbnail, useNvidiaCosmos, useNvidiaGr00t) that the
 * config.ts auto-enable block does not — a known divergence to revisit.
 */

import * as fs from "fs";
import * as path from "path";
import { Project, SyntaxKind, TypeLiteralNode, TypeElementTypes } from "ts-morph";

// Source of truth (infra/config)
const CONFIG_DIR = path.join(__dirname, "../config");
const CONFIG_TS = path.join(CONFIG_DIR, "config.ts");
const TEMPLATE_COMMERCIAL = path.join(CONFIG_DIR, "config.template.commercial.json");
const TEMPLATE_GOVCLOUD = path.join(CONFIG_DIR, "config.template.govcloud.json");
const TEMPLATE_EUSOVEREIGN = path.join(CONFIG_DIR, "config.template.eusovereign.json");

// The hand-maintained mirror (docs site). These modules import only pure-TS
// local helpers (types.ts, pathUtils.ts) — safe to import in a Node test.
// eslint-disable-next-line @typescript-eslint/no-var-requires -- resolved relative to this test file
import { makeDefaultConfig } from "../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { FIELDS } from "../../documentation/docusaurus-site/src/components/ConfigBuilder/schema";

/**
 * ConfigPublic leaves that are intentionally NOT rendered as form fields in the
 * ConfigBuilder because they are derived / overwritten at deploy time and are
 * not user-editable. Keep this list minimal and justified.
 */
const KNOWN_NON_FORM_PATHS: ReadonlySet<string> = new Set([
    "env.partition", // Derived from the deployment region/partition at synth time.
    "env.coreStackName", // "Will get overwritten always when generated" (see config.ts).
]);

function readJson(filePath: string): Record<string, unknown> {
    return JSON.parse(fs.readFileSync(filePath, "utf-8"));
}

/**
 * Enumerate the dotted leaf paths of the ConfigPublic interface by walking the
 * TypeScript AST (via ts-morph). config.ts is parsed as source text only — it
 * is never imported/executed, so its aws-cdk-lib / fs / dotenv dependencies are
 * never loaded. A property whose type is an inline object literal (TypeLiteral)
 * is recursed into; anything else (primitive, array, tuple, type reference) is
 * treated as a leaf — matching how the builder renders one control per leaf.
 */
function collectConfigPublicLeafPaths(): string[] {
    const project = new Project({
        skipAddingFilesFromTsConfig: true,
        skipFileDependencyResolution: true,
        compilerOptions: { allowJs: false },
    });
    const sourceFile = project.addSourceFileAtPath(CONFIG_TS);
    const iface = sourceFile.getInterfaceOrThrow("ConfigPublic");

    const leaves: string[] = [];

    const walk = (members: TypeElementTypes[], prefix: string): void => {
        for (const member of members) {
            const prop = member.asKind(SyntaxKind.PropertySignature);
            if (!prop) continue; // skip index signatures, methods, etc.
            const name = prop.getName();
            const dottedPath = prefix ? `${prefix}.${name}` : name;
            const typeNode = prop.getTypeNode();
            const typeLiteral = typeNode?.asKind(SyntaxKind.TypeLiteral) as
                | TypeLiteralNode
                | undefined;
            if (typeLiteral) {
                walk(typeLiteral.getMembers(), dottedPath);
            } else {
                leaves.push(dottedPath);
            }
        }
    };

    walk(iface.getMembers(), "");
    return leaves;
}

describe("ConfigBuilder ↔ config source-of-truth sync", () => {
    describe("defaults.ts presets deep-equal the template JSONs", () => {
        it("commercial preset equals config.template.commercial.json", () => {
            expect(makeDefaultConfig("commercial")).toEqual(readJson(TEMPLATE_COMMERCIAL));
        });

        it("govcloud preset equals config.template.govcloud.json", () => {
            expect(makeDefaultConfig("govcloud")).toEqual(readJson(TEMPLATE_GOVCLOUD));
        });

        it("eusovereign preset equals config.template.eusovereign.json", () => {
            expect(makeDefaultConfig("eusovereign")).toEqual(readJson(TEMPLATE_EUSOVEREIGN));
        });
    });

    describe("schema.ts FIELDS cover the ConfigPublic interface", () => {
        const configLeafPaths = collectConfigPublicLeafPaths();
        const configLeafSet = new Set(configLeafPaths);
        const fieldPaths = FIELDS.map((f) => f.path);
        const fieldPathSet = new Set(fieldPaths);

        it("has no duplicate FIELDS paths", () => {
            const seen = new Set<string>();
            const dupes: string[] = [];
            for (const p of fieldPaths) {
                if (seen.has(p)) dupes.push(p);
                seen.add(p);
            }
            expect(dupes).toEqual([]);
        });

        it("every ConfigPublic leaf has a FIELDS entry or is allowlisted", () => {
            const missing = configLeafPaths.filter(
                (p) => !fieldPathSet.has(p) && !KNOWN_NON_FORM_PATHS.has(p)
            );
            expect({ missingFromConfigBuilder: missing }).toEqual({ missingFromConfigBuilder: [] });
        });

        it("every FIELDS path points to a real ConfigPublic leaf", () => {
            const unknown = fieldPaths.filter((p) => !configLeafSet.has(p));
            expect({ fieldsPathsNotInConfigPublic: unknown }).toEqual({
                fieldsPathsNotInConfigPublic: [],
            });
        });

        it("allowlisted paths are real ConfigPublic leaves (keep the list honest)", () => {
            const stale = [...KNOWN_NON_FORM_PATHS].filter((p) => !configLeafSet.has(p));
            expect({ staleAllowlistEntries: stale }).toEqual({ staleAllowlistEntries: [] });
        });
    });
});

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
 *   1. defaults.ts presets must deep-equal the three template JSONs.
 *   2. schema.ts FIELDS must cover every leaf of the ConfigPublic interface,
 *      including the add-on lane config.ts intersects into `app.addons` from
 *      infra/config/addon/ (and must not reference paths that no longer exist).
 *
 * When a test here fails, the fix is to update the ConfigBuilder to match the
 * source of truth (see the component's README.md for which files to touch), or
 * — for a deliberately non-editable field — add it to CORE_NON_FORM_PATHS
 * below with a justification (an add-on's field: to the JSON-only paths of its
 * ConfigBuilder module under addon/).
 *
 * Out of scope (mirror imperative logic in getConfig() that cannot be checked
 * declaratively; guarded by the steering-doc rules instead): validation.ts,
 * derived.ts, and the rules and derivations of the add-on modules.
 *
 * Where getConfig() rejects a feature combination rather than adjusting the config,
 * the ConfigBuilder mirrors the rejection as a validation.ts error rule. The VPC
 * requirement is the worked example: getConfig() collects the VPC-requiring
 * features (15 core ones plus those of the add-on lane) and throws when
 * app.useGlobalVpc.enabled is false, and validation.ts carries one error rule per
 * feature. Where getConfig() does normalize a field, derived.ts carries that
 * assignment. Neither file is checked here — a feature added to the getConfig()
 * collection needs adding to validation.ts, which no test enforces.
 */

import * as fs from "fs";
import * as path from "path";
import {
    InterfaceDeclaration,
    Node,
    Project,
    SourceFile,
    SyntaxKind,
    TypeAliasDeclaration,
    TypeElementTypes,
    TypeLiteralNode,
    TypeNode,
} from "ts-morph";

// Source of truth (infra/config)
const CONFIG_DIR = path.join(__dirname, "..", "../config");
const CONFIG_TS = path.join(CONFIG_DIR, "config.ts");
const TEMPLATE_COMMERCIAL = path.join(CONFIG_DIR, "config.template.commercial.json");
const TEMPLATE_GOVCLOUD = path.join(CONFIG_DIR, "config.template.govcloud.json");
const TEMPLATE_EUSOVEREIGN = path.join(CONFIG_DIR, "config.template.eusovereign.json");

// The hand-maintained mirror (docs site). These modules import only pure-TS
// local helpers (types.ts, pathUtils.ts) — safe to import in a Node test.
// eslint-disable-next-line @typescript-eslint/no-var-requires -- resolved relative to this test file
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { FIELDS } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/schema";
import { ADDON_JSON_ONLY_PATHS } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/addon";

/**
 * ConfigPublic leaves that are intentionally NOT rendered as form fields in the
 * ConfigBuilder because they are derived / overwritten at deploy time and are
 * not user-editable. Keep this list minimal and justified.
 */
const CORE_NON_FORM_PATHS: readonly string[] = [
    "env.partition", // Derived from the deployment region/partition at synth time.
    "env.coreStackName", // "Will get overwritten always when generated" (see config.ts).
    // The fields below are edited through a COMPOSITE ConfigBuilder component rather than one input
    // per field, so schema.ts carries the parent path only. They are listed individually so that
    // adding a field to either interface fails this test until both this list and the component are
    // updated — which is the whole point of resolving type references in the walker above.
    // Component: fields/PresignedUrlRestrictionsField.tsx
    "app.assetBuckets.presignedUrlNetworkRestrictions.allowedIpRanges",
    "app.assetBuckets.presignedUrlNetworkRestrictions.allowedVpceIds",
    // Component: fields/ExternalBucketsField.tsx
    "app.assetBuckets.externalAssetBuckets.bucketArn",
    "app.assetBuckets.externalAssetBuckets.baseAssetsPrefix",
    "app.assetBuckets.externalAssetBuckets.defaultSyncDatabaseId",
    "app.assetBuckets.externalAssetBuckets.isDefault",
    "app.assetBuckets.externalAssetBuckets.bucketAccountId",
    "app.assetBuckets.externalAssetBuckets.bucketRegion",
    "app.assetBuckets.externalAssetBuckets.bucketKmsKeyArn",
    // Component: fields/ExternalBridgesField.tsx
    "app.pipelines.externalPipelineBridges.accountId",
    "app.pipelines.externalPipelineBridges.roleArnPattern",
    "app.pipelines.externalPipelineBridges.invokeTargets",
];

/** The core list plus the paths each ConfigBuilder add-on module edits only in the downloaded config.json. */
const KNOWN_NON_FORM_PATHS: ReadonlySet<string> = new Set([
    ...CORE_NON_FORM_PATHS,
    ...ADDON_JSON_ONLY_PATHS,
]);

function readJson(filePath: string): Record<string, unknown> {
    return JSON.parse(fs.readFileSync(filePath, "utf-8"));
}

/**
 * Enumerate the dotted leaf paths of the ConfigPublic interface by walking the
 * TypeScript AST (via ts-morph). config.ts, and each module it imports a type
 * from, is parsed as source text only — never imported/executed, so its
 * aws-cdk-lib / fs / dotenv dependencies are never loaded. A property whose type
 * is an inline object literal (TypeLiteral) or an intersection is recursed into;
 * anything else (primitive, array, tuple, type reference) is treated as a leaf —
 * matching how the builder renders one control per leaf.
 */
/**
 * The object members a property's type exposes, or undefined when the type is a leaf.
 *
 * Four shapes are resolved, not one:
 *
 *   { a: string }                  an inline type literal
 *   ConfigPresignedUrlNetworkRestrictions   a reference to an interface or type alias, declared in
 *                                  the file that names it or in a module that file imports it from
 *   [ConfigPublicAssetS3Buckets] / X[]      a tuple or array whose element is either of the above
 *   { a: string } & ConfigAddonLane         an intersection: the members of every constituent
 *
 * Resolving only the first left the fields inside the two referenced interfaces invisible to this
 * drift guard: `allowedIpRanges`, `allowedVpceIds`, `isDefault`, `bucketAccountId`, `bucketRegion` and
 * `bucketKmsKeyArn` were recorded as two leaves (`...presignedUrlNetworkRestrictions` and
 * `...externalAssetBuckets`) and satisfied by two composite ConfigBuilder inputs, so adding a seventh
 * field to either interface could not fail this test.
 */
function objectMembersOf(typeNode: TypeNode | undefined): TypeElementTypes[] | undefined {
    if (!typeNode) return undefined;

    const literal = typeNode.asKind(SyntaxKind.TypeLiteral) as TypeLiteralNode | undefined;
    if (literal) return literal.getMembers();

    // `A & B` carries the members of both. A constituent with no enumerable members would hide its
    // fields from this guard, so it fails the walk instead.
    const intersection = typeNode.asKind(SyntaxKind.IntersectionType);
    if (intersection) {
        return intersection.getTypeNodes().flatMap((part) => {
            const members = objectMembersOf(part);
            if (!members) {
                throw new Error(
                    `Cannot enumerate the members of intersection constituent ${part.getText()}`
                );
            }
            return members;
        });
    }

    // `X[]` and `[X]` both describe a list of X; the element type is what carries the fields.
    const array = typeNode.asKind(SyntaxKind.ArrayType);
    if (array) return objectMembersOf(array.getElementTypeNode());
    const tuple = typeNode.asKind(SyntaxKind.TupleType);
    if (tuple) {
        const elements = tuple.getElements();
        return elements.length ? objectMembersOf(elements[0]) : undefined;
    }

    const reference = typeNode.asKind(SyntaxKind.TypeReference);
    if (reference) {
        const declaration = resolveTypeDeclaration(
            reference.getTypeName().getText(),
            typeNode.getSourceFile()
        );
        if (Node.isInterfaceDeclaration(declaration)) return declaration.getMembers();
        if (Node.isTypeAliasDeclaration(declaration))
            return objectMembersOf(declaration.getTypeNode());
    }
    return undefined;
}

/**
 * The interface or type alias `name` denotes in `file`: declared there, or brought in by a named
 * import from a relative module, which is added to the project and searched the same way. Names
 * from packages are not followed; they stay leaves.
 */
function resolveTypeDeclaration(
    name: string,
    file: SourceFile
): InterfaceDeclaration | TypeAliasDeclaration | undefined {
    const local = file.getInterface(name) ?? file.getTypeAlias(name);
    if (local) return local;
    for (const declaration of file.getImportDeclarations()) {
        const specifier = declaration.getModuleSpecifierValue();
        if (!specifier.startsWith(".")) continue;
        const imported = declaration
            .getNamedImports()
            .find((named) => (named.getAliasNode()?.getText() ?? named.getName()) === name);
        if (!imported) continue;
        const base = path.resolve(path.dirname(file.getFilePath()), specifier);
        const target = [`${base}.ts`, path.join(base, "index.ts")].find((candidate) =>
            fs.existsSync(candidate)
        );
        if (!target) {
            throw new Error(
                `${file.getBaseName()} imports ${name} from ${specifier}, which does not resolve`
            );
        }
        const project = file.getProject();
        const targetFile = project.getSourceFile(target) ?? project.addSourceFileAtPath(target);
        return resolveTypeDeclaration(imported.getName(), targetFile);
    }
    return undefined;
}

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
            const grouping =
                typeNode?.asKind(SyntaxKind.TypeLiteral) ??
                typeNode?.asKind(SyntaxKind.IntersectionType);
            if (grouping) {
                // A pure grouping in the config shape: it has no value of its own. An intersection
                // groups the members of every constituent, wherever each is declared.
                walk(objectMembersOf(grouping) ?? [], dottedPath);
                continue;
            }
            const members = objectMembersOf(typeNode);
            if (members) {
                // A named interface or list element is BOTH: the ConfigBuilder edits it as a unit
                // through one composite component, so the parent is a real field path, and its
                // members must still be enumerated so a newly added one cannot slip past unnoticed.
                leaves.push(dottedPath);
                walk(members, dottedPath);
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

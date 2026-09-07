/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Drift guard for the three-way SSM resource-name contract, from the version-independent side.
 *
 * A resource name is declared in three places that must stay identical:
 *
 *   1. infra/common/resourceParamKeys.ts                          -- canonical; names the SSM parameters
 *   2. backend/backend/common/resourceNames.py                    -- what every non-pipeline handler reads
 *   3. infra/deploymentDataMigration/tools/ssm_resource_lookup.py -- what migration scripts read
 *
 * This file guards leg 2, which otherwise drifts silently: storageBuilder registers each
 * descriptor under `RESOURCE_PARAM_KEYS.*` and ResourceNamesBuilder publishes it as
 * `{prefix}/{paramKey}`, while resourceNames.py looks the same suffix up in its cache and raises
 * `KeyError` when it is absent. Because handlers resolve names at module level, a key added to the
 * canonical registry and forgotten in the mirror fails the Lambda at import time -- every request
 * to that handler 500s, and nothing in synth, lint, or either unit suite says a word.
 *
 * Leg 3 is asserted here as well, against the version-independent
 * `deploymentDataMigration/tools/` path. migrationTooling.test.ts makes the same comparison, but
 * its other describe blocks are built around a release-pinned
 * `deploymentDataMigration/<from>_to_<to>/upgrade` directory, so that file goes away with the
 * release it belongs to and takes its leg-3 assertion with it. Both copies derive their
 * expectation from the same registry, so they cannot disagree.
 *
 * Nothing here may reference a version-pinned migration path -- not a directory and not a sibling
 * suite pinned to one. The last describe block asserts that on this file's own source text, and
 * takes the positive control for its detector from the migration directory names themselves so no
 * particular release's tooling has to exist.
 */

import * as fs from "fs";
import * as path from "path";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";

const REPO_ROOT = path.join(__dirname, "..", "../..");
const BACKEND_MIRROR_PY = path.join(REPO_ROOT, "backend/backend/common/resourceNames.py");
const SELF = path.join(__dirname, path.basename(__filename).replace(/\.js$/, ".ts"));
const MIGRATION_ROOT = path.join(REPO_ROOT, "infra/deploymentDataMigration");
const MIGRATION_LOOKUP_PY = path.join(MIGRATION_ROOT, "tools/ssm_resource_lookup.py");

/** Matches a release-pinned migration directory name of the form `vX.Y` + `_to_` + `vA.B`. */
const VERSION_PINNED_PATH = /v\d+\.\d+_to_v\d+\.\d+/;

/**
 * Lower bound on the parsed mirror. The count is not the assertion -- it is the anti-vacuous
 * floor: a regex that stops matching (a reformat, a nested class, a switch to single quotes)
 * would otherwise turn the set comparison below into two empty sets reporting success.
 */
const MIRROR_KEY_FLOOR = 50;

/** Every `ResourceParamKey("<key>"` suffix declared in the backend mirror. */
function parseBackendMirrorKeys(source: string): string[] {
    return Array.from(source.matchAll(/ResourceParamKey\(\s*"([^"]+)"/g)).map((m) => m[1]);
}

/** Every `KEY = "some/param/key"` assignment in the migration tooling's ResourceParamKeys class. */
function parseMigrationLookupKeys(source: string): string[] {
    return Array.from(source.matchAll(/^\s{4}[A-Z0-9_]+\s*=\s*"([^"]+)"/gm)).map((m) => m[1]);
}

/** The release-pinned `<from>_to_<to>` directories under deploymentDataMigration. */
function versionPinnedMigrationDirs(): string[] {
    return fs
        .readdirSync(MIGRATION_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name)
        .filter((name) => VERSION_PINNED_PATH.test(name));
}

/** Every canonical key across every category of the CDK registry. */
function canonicalKeys(): string[] {
    return Object.values(RESOURCE_PARAM_KEYS).flatMap((category) =>
        Object.values(category as Record<string, string>)
    );
}

/**
 * Canonical keys the backend mirror intentionally omits: no handler reads them, only deployment
 * and data-migration tooling does, and those consumers resolve names through
 * ssm_resource_lookup.py instead.
 *
 *   * the whole `lambdaFunctions` category -- taken from the registry, so a new entry needs no
 *     edit here; and
 *   * the five deprecated-table keys under `dynamoTables/legacy/` whose only consumer is a
 *     migration script. Each is named through the registry object rather than as a string, so a
 *     rename fails compilation instead of drifting into a stale allow-list entry.
 *
 * `legacy/tagStorage` and `legacy/tagTypeStorage` are deliberately absent from this list, so the
 * mirror is required to carry them. No handler resolves either one -- the per-database tag
 * namespacing migration reads them through ssm_resource_lookup.py -- so in the mirror they are
 * declared but unused. They stay because dropping them is a three-way contract change and an
 * owner decision, not a cleanup: see the note on the test that pins them.
 */
function toolingOnlyKeys(): string[] {
    const legacy = RESOURCE_PARAM_KEYS.dynamoTablesLegacy;
    return [
        ...Object.values(RESOURCE_PARAM_KEYS.lambdaFunctions),
        legacy.assetVersionsStorageV1,
        legacy.assetFileVersionsStorageV1,
        legacy.assetLinksStorage,
        legacy.metadataStorage,
        legacy.metadataSchemaStorage,
    ];
}

/** Keys the mirror must carry: everything canonical that is not tooling-only. */
function expectedBackendKeys(): string[] {
    const omitted = new Set(toolingOnlyKeys());
    return canonicalKeys().filter((key) => !omitted.has(key));
}

/** Two-way difference between the mirror and the derived expectation. */
function diffKeys(mirrorKeys: string[], expected: string[]) {
    const mirror = new Set(mirrorKeys);
    const want = new Set(expected);
    return {
        missingFromMirror: expected.filter((key) => !mirror.has(key)).sort(),
        unknownInMirror: mirrorKeys.filter((key) => !want.has(key)).sort(),
    };
}

const mirrorSource = fs.readFileSync(BACKEND_MIRROR_PY, "utf8");
const mirrorKeys = parseBackendMirrorKeys(mirrorSource);

describe("resourceNames.py parser", () => {
    it("parses a non-empty, duplicate-free key set from the backend mirror", () => {
        expect(mirrorKeys.length).toBeGreaterThan(MIRROR_KEY_FLOOR);
        expect(new Set(mirrorKeys).size).toBe(mirrorKeys.length);

        // Named samples across all three mirrored categories, so a regex that matches only the
        // first block of the class cannot satisfy the floor above on its own.
        expect(mirrorKeys).toContain(RESOURCE_PARAM_KEYS.dynamoTables.assetStorage);
        expect(mirrorKeys).toContain(RESOURCE_PARAM_KEYS.s3Buckets.assetAuxiliary);
        expect(mirrorKeys).toContain(RESOURCE_PARAM_KEYS.cloudwatchLogGroups.auditErrors);
    });

    it("reads the canonical registry as a populated multi-category union", () => {
        // Control for the other side of the comparison: an empty or single-category registry read
        // would make set equality trivially satisfiable.
        expect(Object.keys(RESOURCE_PARAM_KEYS).length).toBeGreaterThanOrEqual(5);
        expect(canonicalKeys().length).toBeGreaterThan(MIRROR_KEY_FLOOR);
        expect(new Set(canonicalKeys()).size).toBe(canonicalKeys().length);
    });
});

describe("backend mirror matches the canonical registry", () => {
    it("declares exactly the canonical keys minus the tooling-only omissions", () => {
        // Repeated here rather than left to the parser describe: run under `-t` filtering this is
        // the only thing standing between a broken parse and a vacuous pass.
        expect(mirrorKeys.length).toBeGreaterThan(MIRROR_KEY_FLOOR);

        const { missingFromMirror, unknownInMirror } = diffKeys(mirrorKeys, expectedBackendKeys());

        // Add the ResourceParamKey to backend/backend/common/resourceNames.py.
        expect(missingFromMirror).toEqual([]);
        // Remove it from the mirror, or publish it from infra/common/resourceParamKeys.ts.
        expect(unknownInMirror).toEqual([]);
    });

    it("omits only tooling-only keys, and every omission is a live canonical key", () => {
        const canonical = new Set(canonicalKeys());
        const mirror = new Set(mirrorKeys);

        for (const key of toolingOnlyKeys()) {
            // A stale allow-list entry (renamed or deleted key) is itself drift.
            expect(canonical.has(key)).toBe(true);
            expect(mirror.has(key)).toBe(false);
            // Bounds what may be omitted: no live table, bucket, or log group can be hidden here.
            expect(key).toMatch(/^(lambdaFunctions\/|dynamoTables\/legacy\/)/);
        }
    });

    it("mirrors both legacy tag keys, which no handler resolves", () => {
        // These two are declared in resourceNames.py and read by nothing: the tag namespacing
        // migration resolves them through ssm_resource_lookup.py instead. Removing them from the
        // mirror is a deliberate three-way contract change -- drop them from the registry, from
        // ssm_resource_lookup.py, and from this assertion together, or the remaining sides drift.
        expect(mirrorKeys).toContain(RESOURCE_PARAM_KEYS.dynamoTablesLegacy.tagStorage);
        expect(mirrorKeys).toContain(RESOURCE_PARAM_KEYS.dynamoTablesLegacy.tagTypeStorage);
    });

    it("reports an injected key and an orphaned key in the right direction", () => {
        // Mutation control. Set equality between two sets built by the same helper can be
        // trivially true, so prove the comparator sees a difference of either sign.
        const injected = diffKeys(mirrorKeys, [
            ...expectedBackendKeys(),
            "dynamoTables/syntheticDriftTable",
        ]);
        expect(injected.missingFromMirror).toEqual(["dynamoTables/syntheticDriftTable"]);
        expect(injected.unknownInMirror).toEqual([]);

        const orphaned = diffKeys(
            [...mirrorKeys, "dynamoTables/syntheticOrphanTable"],
            expectedBackendKeys()
        );
        expect(orphaned.unknownInMirror).toEqual(["dynamoTables/syntheticOrphanTable"]);
        expect(orphaned.missingFromMirror).toEqual([]);
    });
});

describe("migration tooling mirror matches the canonical registry", () => {
    const lookupKeys = parseMigrationLookupKeys(fs.readFileSync(MIGRATION_LOOKUP_PY, "utf8"));

    it("declares every canonical key, and no key the registry does not publish", () => {
        // Same floor as the backend mirror: a regex that stops matching would otherwise compare
        // two empty sets and report success.
        expect(lookupKeys.length).toBeGreaterThan(MIRROR_KEY_FLOOR);
        expect(new Set(lookupKeys).size).toBe(lookupKeys.length);

        const { missingFromMirror, unknownInMirror } = diffKeys(lookupKeys, canonicalKeys());

        // Migration tooling resolves every category, including lambdaFunctions and all of legacy.
        expect(missingFromMirror).toEqual([]);
        expect(unknownInMirror).toEqual([]);
    });
});

describe("this guard is independent of version-pinned migration tooling", () => {
    it("references no vX.Y_to_vA.B migration directory", () => {
        const self = fs.readFileSync(SELF, "utf8");

        // Control: the negative assertion below is only meaningful against real source text.
        expect(self.length).toBeGreaterThan(1000);
        expect(self).toContain("parseBackendMirrorKeys");

        expect(VERSION_PINNED_PATH.test(self)).toBe(false);
    });

    it("uses a detector that does flag a release-pinned migration directory", () => {
        // Positive control: without it the assertion above passes for a detector that matches
        // nothing at all. Read from the directory names rather than from a sibling suite, so this
        // control survives the release whose tooling it happens to describe.
        const pinned = versionPinnedMigrationDirs();
        expect(pinned.length).toBeGreaterThan(0);

        // Negative control: the version-independent tooling directory beside them, which holds
        // leg 3 of the contract, is not flagged -- so the detector is not matching every name.
        expect(fs.existsSync(MIGRATION_LOOKUP_PY)).toBe(true);
        expect(pinned).not.toContain("tools");
    });
});

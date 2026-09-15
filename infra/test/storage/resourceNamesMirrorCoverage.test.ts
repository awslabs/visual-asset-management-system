/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * FIX-067 / S3-CONTRACTS-019 -- meta-coverage for the CDK <-> backend leg of the three-way
 * SSM resource-name contract. The comparison itself lives in resourceNamesMirror.test.ts; this
 * file asserts that such a guard exists, and that it is shaped the way the repository owner
 * required.
 *
 * A resource name is declared in three places that must stay identical:
 *
 *   1. infra/common/resourceParamKeys.ts                     -- canonical, drives the SSM parameters
 *   2. backend/backend/common/resourceNames.py               -- what every non-pipeline handler reads
 *   3. infra/deploymentDataMigration/tools/ssm_resource_lookup.py -- what migration scripts read
 *
 * Leg 3 is guarded by migrationTooling.test.ts. Leg 2 would drift silently: a table added to the
 * canonical registry but forgotten in resourceNames.py has no constant to resolve, so the omission
 * surfaces at runtime as a module-import KeyError inside a deployed Lambda -- every request to that
 * handler 500s, and nothing in synth, lint, or either unit suite says a word.
 *
 * The repository owner imposed two conditions on the guard:
 *
 *   * it must NOT be tied to version-specific migration tooling -- migrationTooling.test.ts is
 *     built around a release-pinned upgrade directory that disappears next release, so extending
 *     that file would take the whole suite down with it; and
 *   * it must assert a DERIVED set, not a key count -- the count form is what trains people to
 *     bump a literal without reading it.
 *
 * Both assertions below are negative in shape, so each carries a control: the scanner control
 * proves the search walks a real corpus and that the release-pinned detector separates the
 * `<from>_to_<to>` migration directories from the version-independent tooling beside them, and the
 * completeness control proves the Python class it inspects is still present and still asserting
 * per-key properties. Neither control names a particular release's migration files, so nothing
 * here fails when the current upgrade directory is renamed or removed.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.join(__dirname, "..", "../..");
const BACKEND_MIRROR_TEST_PY = path.join(REPO_ROOT, "backend/tests/common/test_resourceNames.py");
const INFRA_TEST_DIR = __dirname;
const BACKEND_TEST_DIR = path.join(REPO_ROOT, "backend/tests");
const MIGRATION_ROOT = path.join(REPO_ROOT, "infra/deploymentDataMigration");
const THIS_FILE = path.basename(__filename).replace(/\.js$/, ".ts");

/** Matches a release-pinned migration directory name of the form `vX.Y` + `_to_` + `vA.B`. */
const VERSION_PINNED_PATH = /v\d+\.\d+_to_v\d+\.\d+/;

/** References to the canonical CDK registry, from either language. */
const CANONICAL_REGISTRY_REF = /RESOURCE_PARAM_KEYS|resourceParamKeys/;

/** References to the backend handler mirror (its file path or its constant constructor). */
const BACKEND_MIRROR_REF = /resourceNames\.py|ResourceParamKey\(/;

/** Every `test_*.py` under a directory tree. */
function pythonTestFiles(dir: string): string[] {
    const found: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            found.push(...pythonTestFiles(full));
        } else if (entry.isFile() && /^test_.*\.py$/.test(entry.name)) {
            found.push(full);
        }
    }
    return found;
}

/** The test files that could plausibly host the drift guard, excluding this coverage file. */
function guardCandidates(): { name: string; source: string }[] {
    const infraTests = fs
        .readdirSync(INFRA_TEST_DIR, { withFileTypes: true })
        .filter((e) => e.isFile() && e.name.endsWith(".test.ts"))
        .map((e) => path.join(INFRA_TEST_DIR, e.name));

    return [...infraTests, ...pythonTestFiles(BACKEND_TEST_DIR)]
        .filter((file) => path.basename(file) !== THIS_FILE)
        .map((file) => ({
            name: path.relative(REPO_ROOT, file).replace(/\\/g, "/"),
            source: fs.readFileSync(file, "utf8"),
        }));
}

/**
 * A drift guard is a test that reaches for BOTH the canonical CDK registry and the backend
 * handler mirror -- that pairing is what makes it a leg-2 check rather than a leg-3 one.
 */
function findDriftGuards(): { name: string; source: string }[] {
    return guardCandidates().filter(
        (f) => CANONICAL_REGISTRY_REF.test(f.source) && BACKEND_MIRROR_REF.test(f.source)
    );
}

/** Body of the named Python test class, up to the next class or class-level decorator. */
function pythonClassBody(source: string, className: string): string {
    const start = source.indexOf(`class ${className}`);
    if (start < 0) return "";
    const rest = source.slice(start);
    const end = rest.search(/\n@pytest\.mark|\nclass /);
    return end < 0 ? rest : rest.slice(0, end);
}

describe("FIX-067 CDK <-> backend resource-name drift guard exists", () => {
    it("a drift guard compares the backend mirror against the canonical registry, outside the release-pinned migration suite", () => {
        const guards = findDriftGuards();
        expect(guards.map((g) => g.name)).not.toEqual([]);

        const versionPinned = guards
            .filter((g) => VERSION_PINNED_PATH.test(g.source))
            .map((g) => g.name);
        expect(versionPinned).toEqual([]);
    });

    it("scans a real corpus and flags a release-pinned migration directory (scanner control)", () => {
        const candidates = guardCandidates().map((f) => f.name);

        // Control: without these the guard search above could report "none found" because it
        // walked an empty directory rather than because no guard exists. Both named files are
        // version-independent, so this control does not expire with a release.
        expect(candidates).toContain("infra/test/storage/resourceNameRegistry.test.ts");
        expect(candidates).toContain("backend/tests/common/test_resourceNames.py");
        expect(candidates.length).toBeGreaterThan(20);
        expect(candidates).not.toContain(`infra/test/storage/${THIS_FILE}`);

        // Control: the release-pinned detector separates the `<from>_to_<to>` migration
        // directories from the version-independent tooling directory beside them.
        const migrationEntries = fs
            .readdirSync(MIGRATION_ROOT, { withFileTypes: true })
            .filter((e) => e.isDirectory())
            .map((e) => e.name);
        expect(migrationEntries.filter((name) => VERSION_PINNED_PATH.test(name))).not.toEqual([]);
        expect(migrationEntries).toContain("tools");
        expect(VERSION_PINNED_PATH.test("tools")).toBe(false);
    });
});

describe("FIX-067 backend mirror completeness test asserts a derived set", () => {
    const testSource = fs.readFileSync(BACKEND_MIRROR_TEST_PY, "utf8");
    const completenessBody = pythonClassBody(testSource, "TestConstantsCompleteness");

    it("backend/tests/common/test_resourceNames.py pins the mirror to a derived set, not a hardcoded total", () => {
        // Controls: a renamed class or moved file would otherwise satisfy the negative
        // assertion below by having nothing left to match.
        expect(completenessBody).not.toEqual("");
        expect(completenessBody).toContain("param_key");

        const hardcodedTotals = Array.from(
            completenessBody.matchAll(/^\s*assert\s+len\(.*\)\s*==\s*\d+\s*$/gm)
        ).map((m) => m[0].trim());
        expect(hardcodedTotals).toEqual([]);
    });

    it("still asserts param_key uniqueness and per-key completeness (control)", () => {
        // Whatever replaces the literal totals, these two properties must survive: a duplicated
        // constant and an empty param_key/env_var_names tuple are separate defects that set
        // equality alone does not catch.
        expect(completenessBody).toContain("k.param_key");
        expect(completenessBody).toContain("k.env_var_names");
        expect(completenessBody).toMatch(/\{k\.param_key for k in keys\}/);
    });
});

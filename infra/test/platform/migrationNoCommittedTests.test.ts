/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The data-migration scripts carry no committed tests (infra/CLAUDE.md, Development Rule 6).
 *
 * They are operator-run, one-shot scripts verified against a deployment. A pytest tree under
 * infra/deploymentDataMigration/ is one no configuration collects, so it decays silently, and a `moto`
 * seed drifts from the live table shape without anything failing. Unit-style checks written while
 * developing a transform are run locally and left uncommitted; cross-cutting guarantees about the
 * scripts belong in migrationTooling.test.ts. This walk fails on any file or directory that pytest
 * would treat as a test surface.
 */

import * as fs from "fs";
import * as path from "path";

const MIGRATIONS_ROOT = path.join(__dirname, "..", "../deploymentDataMigration");

const TEST_FILE = /^(test_.*\.py|.*_test\.py|conftest\.py|pytest\.ini|tox\.ini|setup\.cfg)$/;
const TEST_DIR = /^(tests?|__tests__)$/;
const SKIP_DIRS = new Set(["__pycache__", ".pytest_cache", "node_modules"]);

function* walk(dir: string): Generator<string> {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (SKIP_DIRS.has(entry.name)) continue;
            if (TEST_DIR.test(entry.name)) yield full;
            yield* walk(full);
        } else if (TEST_FILE.test(entry.name)) {
            yield full;
        } else if (
            entry.name === "pyproject.toml" &&
            /\[tool\.pytest/.test(fs.readFileSync(full, "utf8"))
        ) {
            yield full;
        }
    }
}

describe("deploymentDataMigration carries no committed tests", () => {
    test("[control] the migration tree exists and holds scripts", () => {
        expect(fs.existsSync(MIGRATIONS_ROOT)).toBe(true);
        const scripts: string[] = [];
        for (const release of fs.readdirSync(MIGRATIONS_ROOT)) {
            const upgrade = path.join(MIGRATIONS_ROOT, release, "upgrade");
            if (fs.existsSync(upgrade)) {
                scripts.push(...fs.readdirSync(upgrade).filter((f) => /_migration\.py$/.test(f)));
            }
        }
        expect(scripts.length).toBeGreaterThan(0);
    });

    test("no test file, test directory, or pytest configuration is present", () => {
        const found = Array.from(walk(MIGRATIONS_ROOT)).map((p) =>
            path.relative(MIGRATIONS_ROOT, p)
        );
        expect(found).toEqual([]);
    });

    test("[control] the matcher recognizes each shape it forbids", () => {
        for (const name of [
            "test_v2_6_to_v2_7_cli.py",
            "migration_test.py",
            "conftest.py",
            "pytest.ini",
        ]) {
            expect({ name, matched: TEST_FILE.test(name) }).toEqual({ name, matched: true });
        }
        for (const name of [
            "v2.6_to_v2.7_migration.py",
            "run_migration.sh",
            "ssm_resource_lookup.py",
        ]) {
            expect({ name, matched: TEST_FILE.test(name) }).toEqual({ name, matched: false });
        }
        expect(TEST_DIR.test("tests")).toBe(true);
        expect(TEST_DIR.test("tools")).toBe(false);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Guards the deployment data-migration tooling against three silent-failure modes:
 *
 *   1. Each release's shell wrapper pipes the migration through `tee`, so it must read the
 *      migration's status from PIPESTATUS. Reading `$?` reports tee's status, which is always 0,
 *      so a failed migration would be announced as a success and exit 0.
 *   2. ssm_resource_lookup.py's ResourceParamKeys is a hand-maintained mirror of the
 *      dynamoTables, dynamoTablesLegacy, s3Buckets, cloudwatchLogGroups, and lambdaFunctions
 *      keys in common/resourceParamKeys.ts. It drifts silently, because a missing constant only
 *      surfaces when a migration script reaches for it.
 *   3. A release README's IAM policy is the operator's only statement of what the script needs. A
 *      boto3 call the script makes but the policy omits surfaces as AccessDenied halfway through a
 *      run, so every mutating or paid call is mapped to the action the README must name — whether
 *      the script calls the method or hands it to a paginator as a bound method.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawnSync } from "child_process";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";

const MIGRATIONS_ROOT = path.join(__dirname, "..", "../deploymentDataMigration");
const SSM_RESOURCE_LOOKUP_PY = path.join(MIGRATIONS_ROOT, "tools/ssm_resource_lookup.py");

interface ReleaseMigration {
    dir: string;
    script: string;
    configFile: string;
    readme: string;
    successBanner: string;
}

/** Every release directory that ships a wrapper + README pair. The LAST entry is the current release. */
const MIGRATIONS: ReleaseMigration[] = [
    {
        dir: "v2.5_to_v2.6",
        script: "v2.5_to_v2.6_migration.py",
        configFile: "v2.5_to_v2.6_migration_config.json",
        readme: "v2.5_to_v2.6_migration_README.md",
        successBanner: "Reindex migration completed successfully.",
    },
    {
        dir: "v2.6_to_v2.7",
        script: "v2.6_to_v2.7_migration.py",
        configFile: "v2.6_to_v2.7_migration_config.json",
        readme: "v2.6_to_v2.7_migration_README.md",
        successBanner: "Migration completed successfully.",
    },
];
const CURRENT = MIGRATIONS[MIGRATIONS.length - 1];

function migrationDir(migration: ReleaseMigration): string {
    return path.join(MIGRATIONS_ROOT, migration.dir, "upgrade");
}

function readMigrationFile(migration: ReleaseMigration, name: string): string {
    return fs.readFileSync(path.join(migrationDir(migration), name), "utf8");
}

/**
 * Runs a release's run_migration.sh in a scratch directory against a stub `python` that exits with
 * `stubExit`, and returns the wrapper's own exit status plus stdout.
 */
function runWrapper(
    migration: ReleaseMigration,
    stubExit: number
): { status: number; stdout: string } {
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "vams-migration-wrapper-"));
    try {
        const binDir = path.join(scratch, "bin");
        fs.mkdirSync(binDir);

        // Stub interpreter: succeeds for the `python -c "import boto3"` probe, and for the
        // migration invocation prints a line (so tee has input) and exits with stubExit.
        const stub = [
            "#!/bin/bash",
            'for arg in "$@"; do',
            '    if [ "$arg" = "-c" ]; then exit 0; fi',
            "done",
            'echo "stub migration output"',
            `exit ${stubExit}`,
            "",
        ].join("\n");
        fs.writeFileSync(path.join(binDir, "python"), stub, { mode: 0o755 });

        fs.copyFileSync(
            path.join(migrationDir(migration), "run_migration.sh"),
            path.join(scratch, "run_migration.sh")
        );
        // The wrapper requires the config file to exist; its contents are never read by the stub.
        fs.writeFileSync(path.join(scratch, migration.configFile), "{}");

        const result = spawnSync("bash", ["run_migration.sh"], {
            cwd: scratch,
            encoding: "utf8",
            env: { ...process.env, PATH: `${binDir}${path.delimiter}${process.env.PATH}` },
        });
        return { status: result.status as number, stdout: `${result.stdout ?? ""}` };
    } finally {
        fs.rmSync(scratch, { recursive: true, force: true });
    }
}

describe.each(MIGRATIONS)("$dir run_migration.sh exit-status propagation", (migration) => {
    it("propagates a non-zero migration exit through the tee pipe", () => {
        const { status, stdout } = runWrapper(migration, 3);
        expect(status).toBe(3);
        expect(stdout).toContain("Migration failed");
        expect(stdout).not.toContain("completed successfully");
    });

    it("reports success and exits 0 when the migration succeeds", () => {
        const { status, stdout } = runWrapper(migration, 0);
        expect(status).toBe(0);
        expect(stdout).toContain(migration.successBanner);
        expect(stdout).not.toContain("Migration failed");
    });

    it("reads the migration status from PIPESTATUS rather than the pipeline's own status", () => {
        const script = readMigrationFile(migration, "run_migration.sh");
        expect(script).toMatch(/PIPESTATUS\[0\]/);
        // `$?` immediately after the tee pipe is tee's status, never the migration's.
        expect(script).not.toMatch(/tee -a "\$LOG_FILE"\s*\n\s*\n?if \[ \$\? -eq 0 \]/);
    });
});

describe("ssm_resource_lookup.py mirrors resourceParamKeys.ts", () => {
    const lookupSource = fs.readFileSync(SSM_RESOURCE_LOOKUP_PY, "utf8");

    /** Every `KEY = "some/param/key"` assignment in the Python ResourceParamKeys class. */
    const pythonKeys = new Set(
        Array.from(lookupSource.matchAll(/^\s{4}[A-Z0-9_]+\s*=\s*"([^"]+)"/gm)).map((m) => m[1])
    );

    const canonicalKeys: string[] = Object.values(RESOURCE_PARAM_KEYS).flatMap((category) =>
        Object.values(category as Record<string, string>)
    );

    it("has a constant for every canonical param key", () => {
        const missing = canonicalKeys.filter((key) => !pythonKeys.has(key));
        expect(missing).toEqual([]);
    });

    it("defines no param key that the canonical registry does not publish", () => {
        const canonical = new Set(canonicalKeys);
        const extra = Array.from(pythonKeys).filter((key) => !canonical.has(key));
        expect(extra).toEqual([]);
    });

    it("covers all nine audit log groups", () => {
        const logGroupKeys = Object.values(RESOURCE_PARAM_KEYS.cloudwatchLogGroups);
        expect(logGroupKeys).toHaveLength(9);
        for (const key of logGroupKeys) {
            expect(pythonKeys.has(key)).toBe(true);
        }
    });
});

/**
 * boto3 call -> the IAM action an operator needs for it. Patterns rather than `name(` literals: the
 * scripts read through `_paginate(client.query, ...)` as well as `client.query(...)`, and a bound-method
 * reference needs the action exactly as a call does.
 */
const CALL_TO_ACTION: ReadonlyArray<[RegExp, string]> = [
    [/\.update_item\(/, "dynamodb:UpdateItem"],
    [/\.put_item\(/, "dynamodb:PutItem"],
    [/\.get_item\(/, "dynamodb:GetItem"],
    [/\.batch_write_item\(/, "dynamodb:BatchWriteItem"],
    [/\.delete_item\(/, "dynamodb:DeleteItem"],
    [/\.query\b/, "dynamodb:Query"],
    [/\.scan\b/, "dynamodb:Scan"],
    [/\.invoke\(/, "lambda:InvokeFunction"],
    [/\.delete_object\(/, "s3:DeleteObject"],
];

describe.each(MIGRATIONS)("$dir migration README IAM policy", (migration) => {
    /** The mapped actions the script needs that `readmeText` does not name. */
    function undocumentedActions(readmeText: string): string[] {
        const migrationScript = readMigrationFile(migration, migration.script);
        return CALL_TO_ACTION.filter(
            ([call, action]) => call.test(migrationScript) && !readmeText.includes(action)
        ).map(([, action]) => action);
    }

    it("documents an IAM action for every mutating or paid call the migration makes", () => {
        expect(undocumentedActions(readMigrationFile(migration, migration.readme))).toEqual([]);
    });

    it("reports each needed action once the README stops naming it (control)", () => {
        // Proves the guard fires: strip one documented action at a time from the README text and
        // expect exactly that action back. For v2.6_to_v2.7 this covers the bound-method
        // `dynamodb_client.query` / `.scan` forms that a `.query(` literal never matched.
        const readme = readMigrationFile(migration, migration.readme);
        const migrationScript = readMigrationFile(migration, migration.script);
        const needed = CALL_TO_ACTION.filter(([call]) => call.test(migrationScript)).map(
            ([, action]) => action
        );
        expect(needed.length).toBeGreaterThan(0);
        for (const action of needed) {
            expect(undocumentedActions(readme.split(action).join(""))).toEqual([action]);
        }
    });
});

const DOCS_ROOT = path.join(__dirname, "..", "../../documentation/docusaurus-site/docs");

describe(`${CURRENT.dir} steps are documented where the operator looks`, () => {
    const script = readMigrationFile(CURRENT, CURRENT.script);
    const choicesLine = script.match(/^STEP_CHOICES\s*=\s*\(([^)]*)\)/m);
    /** The release's step names, read from the script so a renamed or added step is caught here. */
    const steps = Array.from((choicesLine?.[1] ?? "").matchAll(/'([A-Za-z]+)'/g))
        .map((match) => match[1])
        .filter((step) => step !== "all");

    it("declares its steps in a STEP_CHOICES tuple", () => {
        expect(choicesLine).not.toBeNull();
        expect(steps.length).toBeGreaterThan(0);
    });

    it("names every step in the release README", () => {
        const readme = readMigrationFile(CURRENT, CURRENT.readme);
        for (const step of steps) {
            expect(readme).toContain(`\`${step}\``);
        }
    });

    it("lists every step in the PowerShell wrapper's -Steps ValidateSet", () => {
        const ps1 = readMigrationFile(CURRENT, "run_migration.ps1");
        const validateSet = ps1.match(/\[ValidateSet\(([^)]*)\)\]\s*\r?\n\s*\[string\]\$Steps/);
        expect(validateSet).not.toBeNull();
        const listed = validateSet?.[1] ?? "";
        for (const step of steps) {
            expect(listed).toContain(`"${step}"`);
        }
    });

    it("is indexed in the data-migration README", () => {
        const index = fs.readFileSync(path.join(MIGRATIONS_ROOT, "README.md"), "utf8");
        expect(index).toContain(`./${CURRENT.dir}/upgrade/${CURRENT.readme}`);
    });

    it("has a `### v2.6 to v2.7` section in update-the-solution.md that names every step", () => {
        const page = fs.readFileSync(
            path.join(DOCS_ROOT, "deployment/update-the-solution.md"),
            "utf8"
        );
        const heading = "\n### v2.6 to v2.7\n";
        const start = page.indexOf(heading);
        expect(start).toBeGreaterThan(-1);
        // The section runs to the next `## ` or `### ` heading (a `#### ` sub-heading stays inside it).
        const rest = page.slice(start + 1);
        const next = rest.search(/\n#{2,3} /);
        const section = next === -1 ? rest : rest.slice(0, next);
        for (const step of steps) {
            expect(section).toContain(`\`${step}\``);
        }
        expect(section).toContain(`infra/deploymentDataMigration/${CURRENT.dir}/upgrade`);
        // The section is what getConfig() points at when it rejects a retired configuration key.
        expect(section).toContain("useGenAiMetadata3dLabeling");
        expect(section).toContain("useConversionCadMeshMetadataExtraction");
        // The model-change procedure lives here and nowhere else in the upgrade guide.
        expect(section).toContain('"operation": "clear"');
        expect(section).toContain("execution.status.json");
        expect(section).toContain("SYSTEM - Preview");
    });

    it("adds the release to the breaking-changes checklist", () => {
        const page = fs.readFileSync(
            path.join(DOCS_ROOT, "deployment/update-the-solution.md"),
            "utf8"
        );
        const checklist = page.slice(page.indexOf("\n## Breaking changes checklist\n"));
        expect(checklist).toMatch(/\| Retired metadata pipelines[^\n]*\| v2\.6 to v2\.7 /);
        expect(checklist).toMatch(/\| Built-in category rename[^\n]*\| v2\.6 to v2\.7 /);
    });
});

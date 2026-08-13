/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Guards the deployment data-migration tooling against two silent-failure modes:
 *
 *   1. The v2.5 -> v2.6 shell wrapper pipes the migration through `tee`, so it must read the
 *      migration's status from PIPESTATUS. Reading `$?` reports tee's status, which is always 0,
 *      so a failed migration would be announced as a success and exit 0.
 *   2. ssm_resource_lookup.py's ResourceParamKeys is a hand-maintained mirror of the
 *      dynamoTables, dynamoTablesLegacy, s3Buckets, cloudwatchLogGroups, and lambdaFunctions
 *      keys in common/resourceParamKeys.ts. It drifts silently, because a missing constant only
 *      surfaces when a migration script reaches for it.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawnSync } from "child_process";
import { RESOURCE_PARAM_KEYS } from "../common/resourceParamKeys";

const MIGRATION_DIR = path.join(__dirname, "../deploymentDataMigration/v2.5_to_v2.6/upgrade");
const RUN_MIGRATION_SH = path.join(MIGRATION_DIR, "run_migration.sh");
const SSM_RESOURCE_LOOKUP_PY = path.join(
    __dirname,
    "../deploymentDataMigration/tools/ssm_resource_lookup.py"
);

/**
 * Runs run_migration.sh in a scratch directory against a stub `python` that exits with
 * `stubExit`, and returns the wrapper's own exit status plus stdout.
 */
function runWrapper(stubExit: number): { status: number; stdout: string } {
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

        fs.copyFileSync(RUN_MIGRATION_SH, path.join(scratch, "run_migration.sh"));
        // The wrapper requires the config file to exist; its contents are never read by the stub.
        fs.writeFileSync(path.join(scratch, "v2.5_to_v2.6_migration_config.json"), "{}");

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

describe("v2.5_to_v2.6 run_migration.sh exit-status propagation", () => {
    it("propagates a non-zero migration exit through the tee pipe", () => {
        const { status, stdout } = runWrapper(3);
        expect(status).toBe(3);
        expect(stdout).toContain("Migration failed");
        expect(stdout).not.toContain("completed successfully");
    });

    it("reports success and exits 0 when the migration succeeds", () => {
        const { status, stdout } = runWrapper(0);
        expect(status).toBe(0);
        expect(stdout).toContain("Reindex migration completed successfully.");
        expect(stdout).not.toContain("Migration failed");
    });

    it("reads the migration status from PIPESTATUS rather than the pipeline's own status", () => {
        const script = fs.readFileSync(RUN_MIGRATION_SH, "utf8");
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

describe("v2.5_to_v2.6 migration README IAM policy", () => {
    const readme = fs.readFileSync(
        path.join(MIGRATION_DIR, "v2.5_to_v2.6_migration_README.md"),
        "utf8"
    );
    const migrationScript = fs.readFileSync(
        path.join(MIGRATION_DIR, "v2.5_to_v2.6_migration.py"),
        "utf8"
    );

    /** boto3 call -> the IAM action an operator needs for it. */
    const CALL_TO_ACTION: ReadonlyArray<[string, string]> = [
        ["update_item(", "dynamodb:UpdateItem"],
        ["put_item(", "dynamodb:PutItem"],
        ["get_item(", "dynamodb:GetItem"],
        ["batch_write_item(", "dynamodb:BatchWriteItem"],
        ["delete_object(", "s3:DeleteObject"],
    ];

    it("documents an IAM action for every mutating call the migration makes", () => {
        const undocumented = CALL_TO_ACTION.filter(
            ([call, action]) => migrationScript.includes(call) && !readme.includes(action)
        ).map(([, action]) => action);
        expect(undocumented).toEqual([]);
    });
});

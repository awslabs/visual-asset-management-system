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
    stubExit: number,
    args: string[] = [],
    configBody = "{}",
    stubOutput = "stub migration output"
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
            `echo ${JSON.stringify(stubOutput)}`,
            `exit ${stubExit}`,
            "",
        ].join("\n");
        fs.writeFileSync(path.join(binDir, "python"), stub, { mode: 0o755 });

        fs.copyFileSync(
            path.join(migrationDir(migration), "run_migration.sh"),
            path.join(scratch, "run_migration.sh")
        );
        // The wrapper requires the config file to exist; its contents are never read by the stub.
        fs.writeFileSync(path.join(scratch, migration.configFile), configBody);

        const result = spawnSync("bash", ["run_migration.sh", ...args], {
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

describe(`${CURRENT.dir} run_migration.sh footer follows the run mode`, () => {
    it("tells a dry run that nothing was launched and offers no command to watch it", () => {
        const { status, stdout } = runWrapper(CURRENT, 0);
        expect(status).toBe(0);
        expect(stdout).toContain(CURRENT.successBanner);
        expect(stdout).toContain("This was a dry run");
        expect(stdout).not.toContain("Watch the vector backfill");
    });

    it("lists the follow-up steps after an executed run", () => {
        const { status, stdout } = runWrapper(CURRENT, 0, ["--execute"]);
        expect(status).toBe(0);
        expect(stdout).toContain("Mode: EXECUTE");
        expect(stdout).toContain("Watch the vector backfill");
        expect(stdout).not.toContain("This was a dry run");
    });

    it("reports a dry run when --execute is given but the config keeps dry_run true", () => {
        const { status, stdout } = runWrapper(CURRENT, 0, ["--execute"], '{ "dry_run": true }');
        expect(status).toBe(0);
        expect(stdout).not.toContain("Mode: EXECUTE");
        expect(stdout).toContain('"dry_run" is true in');
        expect(stdout).toContain("This was a dry run");
        expect(stdout).not.toContain("Watch the vector backfill");
    });

    it("names no vector backfill to watch when the migration skipped it", () => {
        // The closing summary line the migration prints for a deployment without vector search.
        const skipped = "  vectorBackfill: skipped: vector search is not enabled";
        expect(readMigrationFile(CURRENT, CURRENT.script)).toContain(
            "'skipped: vector search is not enabled'"
        );
        const { status, stdout } = runWrapper(CURRENT, 0, ["--execute"], "{}", skipped);
        expect(status).toBe(0);
        expect(stdout).toContain("Mode: EXECUTE");
        expect(stdout).toContain("The vector backfill was skipped");
        expect(stdout).not.toContain("Watch the vector backfill");
    });

    it("names the metadata backfill to watch when the migration ran it with vector search off", () => {
        const output = [
            "  vectorBackfill: skipped: vector search is not enabled",
            "  metadataBackfill: completed",
        ].join("\n");
        const { status, stdout } = runWrapper(CURRENT, 0, ["--execute"], "{}", output);
        expect(status).toBe(0);
        expect(stdout).toContain("Watch the metadata backfill");
        expect(stdout).not.toContain("The vector backfill was skipped");
        expect(readMigrationFile(CURRENT, "run_migration.ps1")).toContain(
            'Select-String -Path $LogFile -SimpleMatch -Quiet -Pattern "metadataBackfill: completed"'
        );
    });

    it("passes --generate-metadata-for-existing-files through in both wrappers", () => {
        const { status, stdout } = runWrapper(CURRENT, 0, [
            "--generate-metadata-for-existing-files",
        ]);
        expect(status).toBe(0);
        expect(stdout).toContain("--generate-metadata-for-existing-files");
        expect(readMigrationFile(CURRENT, "run_migration.ps1")).toMatch(
            /if \(\$GenerateMetadataForExistingFiles\) \{ \$ExtraArgs \+= "--generate-metadata-for-existing-files"/
        );
    });

    it("keeps the PowerShell wrapper's skip footer on the same summary line", () => {
        const script = readMigrationFile(CURRENT, "run_migration.ps1");
        expect(script).toContain(
            'Select-String -Path $LogFile -SimpleMatch -Quiet -Pattern "vectorBackfill: skipped: vector search is not enabled"'
        );
        expect(script).toMatch(/\$MigrationStatus = \$LASTEXITCODE/);
        expect(script).toMatch(/exit \$MigrationStatus/);
    });

    it("keeps the PowerShell wrapper's mode on the same rule", () => {
        const script = readMigrationFile(CURRENT, "run_migration.ps1");
        expect(script).toMatch(/\$RealRun = \$Execute -and -not \$ConfigDryRun/);
        expect(script).toMatch(/if \(\$RealRun\) \{\s*Write-Host "Mode: EXECUTE/);
    });

    it("keeps the PowerShell wrapper's footer on the same rule", () => {
        const script = readMigrationFile(CURRENT, "run_migration.ps1");
        const footer = script.slice(script.indexOf("Migration completed successfully."));
        const dryRun = footer.indexOf("This was a dry run");
        const watch = footer.indexOf("Watch the vector backfill");
        expect(dryRun).toBeGreaterThan(-1);
        expect(watch).toBeGreaterThan(-1);
        // The watch command sits in the real-run branch, the dry-run note in the other.
        expect(footer).toMatch(
            /if \(\$RealRun\) \{[\s\S]*Watch the vector backfill[\s\S]*\} else \{[\s\S]*This was a dry run/
        );
    });
});

// A migration script reads its config with Python's json.load, which keeps the LAST value of a key
// that appears twice. A template that lists a key in two sections (one per step) therefore drops the
// value an operator fills in under the first one, with no error. Every committed config template is
// read the way the scripts read it, with an object_pairs_hook that records each repeated key.
const MIGRATION_CONFIG_NAME = /^v\d+\.\d+_to_v\d+\.\d+_migration(_[a-z]+)?_config\.json$/;

function committedMigrationConfigs(): string[] {
    return fs
        .readdirSync(MIGRATIONS_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .flatMap((entry) => {
            const upgrade = path.join(MIGRATIONS_ROOT, entry.name, "upgrade");
            return fs.existsSync(upgrade)
                ? fs
                      .readdirSync(upgrade)
                      .filter((name) => MIGRATION_CONFIG_NAME.test(name))
                      .map((name) => path.join(upgrade, name))
                : [];
        })
        .sort();
}

const DUPLICATE_KEYS_PY = [
    "import json, sys",
    "found = {}",
    "for path in sys.argv[1:]:",
    "    repeated = []",
    "    def hook(pairs, repeated=repeated):",
    "        seen = set()",
    "        for key, _ in pairs:",
    "            if key in seen:",
    "                repeated.append(key)",
    "            seen.add(key)",
    "        return dict(pairs)",
    "    with open(path, encoding='utf-8') as f:",
    "        json.load(f, object_pairs_hook=hook)",
    "    found[path] = repeated",
    "print(json.dumps(found))",
].join("\n");

/** Each file's repeated keys (in any object of the file), read by Python's json.load. */
function repeatedConfigKeys(files: string[]): Record<string, string[]> {
    const run = spawnSync("python3", ["-c", DUPLICATE_KEYS_PY, ...files], { encoding: "utf8" });
    if (run.status !== 0) {
        throw new Error(`python3 could not read the migration configs: ${run.stderr}`);
    }
    return JSON.parse(run.stdout);
}

describe("migration config templates name each key once", () => {
    it("finds every release's config template", () => {
        const names = committedMigrationConfigs().map((file) => path.basename(file));
        expect(names).toEqual(expect.arrayContaining(MIGRATIONS.map((m) => m.configFile)));
    });

    it("reports a key that appears twice (control)", () => {
        const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "vams-migration-config-"));
        try {
            const planted = path.join(scratch, "planted_config.json");
            fs.writeFileSync(
                planted,
                '{"asset_storage_table_name": "mine", "other": {"a": 1, "a": 2},' +
                    ' "asset_storage_table_name": null}'
            );
            const clean = path.join(scratch, "clean_config.json");
            fs.writeFileSync(clean, '{"a": 1, "b": {"a": 2}}');
            expect(repeatedConfigKeys([planted, clean])).toEqual({
                [planted]: ["a", "asset_storage_table_name"],
                [clean]: [],
            });
        } finally {
            fs.rmSync(scratch, { recursive: true, force: true });
        }
    });

    it("lists no key twice in any committed template", () => {
        const repeated = Object.entries(repeatedConfigKeys(committedMigrationConfigs()))
            .filter(([, keys]) => keys.length > 0)
            .map(([file, keys]) => `${path.relative(MIGRATIONS_ROOT, file)}: ${keys.join(", ")}`);
        expect(repeated).toEqual([]);
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

describe("the v2.6 to v2.7 migration's terminal statuses mirror the backend's", () => {
    const BACKEND_WORKFLOWS = path.join(__dirname, "..", "../../backend/backend/common/workflows");

    /** The quoted strings of a Python tuple assignment, with `er.NAME` resolved from executionRecords.py. */
    function tupleValues(source: string, name: string): string[] {
        const body = new RegExp(`^${name}\\s*=\\s*\\(([^)]*)\\)`, "m").exec(source)?.[1] ?? "";
        const records = fs.readFileSync(
            path.join(BACKEND_WORKFLOWS, "executionRecords.py"),
            "utf8"
        );
        return body
            .split(",")
            .map((item) => item.trim())
            .filter((item) => item.length > 0)
            .map((item) => {
                const quoted = /^["']([^"']+)["']$/.exec(item);
                if (quoted) return quoted[1];
                const constant = /^er\.([A-Z0-9_]+)$/.exec(item)?.[1];
                const value = constant
                    ? new RegExp(`^${constant}\\s*=\\s*["']([^"']+)["']`, "m").exec(records)?.[1]
                    : undefined;
                return value ?? `<unresolved ${item}>`;
            });
    }

    it("lists every status the backend treats as finished, SKIPPED included", () => {
        const backend = tupleValues(
            fs.readFileSync(path.join(BACKEND_WORKFLOWS, "executionOutputs.py"), "utf8"),
            "TERMINAL_STATUSES"
        );
        const release = MIGRATIONS.find((m) => m.dir === "v2.6_to_v2.7");
        if (!release) throw new Error("the v2.6_to_v2.7 migration is not listed");
        const migration = tupleValues(
            readMigrationFile(release, release.script),
            "TERMINAL_EXECUTION_STATUSES"
        );
        expect(backend).toContain("SKIPPED"); // control: the backend tuple was read
        expect([...migration].sort()).toEqual([...backend].sort());
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
    [/\.transact_write_items\(/, "dynamodb:PutItem"],
    [/\.list_object_versions\(/, "s3:ListBucketVersions"],
];

describe.each(MIGRATIONS)("$dir migration README IAM policy", (migration) => {
    /** The mapped actions the script needs that `readmeText` does not name. */
    function undocumentedActions(readmeText: string): string[] {
        const migrationScript = readMigrationFile(migration, migration.script);
        // Two calls can need one action (put_item and transact_write_items both need PutItem), so each
        // action is reported once.
        return Array.from(
            new Set(
                CALL_TO_ACTION.filter(
                    ([call, action]) => call.test(migrationScript) && !readmeText.includes(action)
                ).map(([, action]) => action)
            )
        );
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
        const needed = Array.from(
            new Set(
                CALL_TO_ACTION.filter(([call]) => call.test(migrationScript)).map(
                    ([, action]) => action
                )
            )
        );
        expect(needed.length).toBeGreaterThan(0);
        for (const action of needed) {
            expect(undocumentedActions(readme.split(action).join(""))).toEqual([action]);
        }
    });
});

const DOCS_ROOT = path.join(__dirname, "..", "../../documentation/docusaurus-site/docs");

describe("the v2.6 to v2.7 vectorBackfill grant names the function the script resolves", () => {
    // CloudFormation generates the reindexer's name from the construct path and truncates it
    // (`<prefix>-VectorIndexingvectorRein-<suffix>`), so a pattern on the handler's name grants nothing.
    const release = MIGRATIONS.find((m) => m.dir === "v2.6_to_v2.7");
    if (!release) throw new Error("the v2.6_to_v2.7 migration is not listed");
    const readme = readMigrationFile(release, release.readme);
    const script = readMigrationFile(release, release.script);
    const policyBlock = readme.slice(
        readme.indexOf("### Required IAM Permissions"),
        readme.indexOf("### Python Dependencies")
    );

    it("scopes lambda:InvokeFunction to the function name, not to a pattern on the handler name", () => {
        const policy = JSON.parse(/```json\n([\s\S]*?)\n```/.exec(policyBlock)![1]);
        const invoke = policy.Statement.filter((s: any) =>
            [].concat(s.Action).includes("lambda:InvokeFunction" as never)
        );
        expect(invoke).toHaveLength(1);
        expect(invoke[0].Resource).toBe(
            "arn:*:lambda:*:*:function:<VECTOR_REINDEXER_FUNCTION_NAME>"
        );
        expect(policyBlock).not.toContain("*vectorReindexer*");
    });

    it("tells the operator to read the name from the SSM key the script reads", () => {
        expect(policyBlock).toContain("/lambdaFunctions/vectorReindexer");
        expect(policyBlock).toContain("aws ssm get-parameter");
        // The script resolves the same key (ssm_resource_lookup.ResourceParamKeys.VECTOR_REINDEXER_FUNCTION).
        expect(script).toContain("VECTOR_REINDEXER_FUNCTION");
        const lookup = fs.readFileSync(
            path.join(MIGRATIONS_ROOT, "tools", "ssm_resource_lookup.py"),
            "utf8"
        );
        expect(lookup).toMatch(/VECTOR_REINDEXER_FUNCTION = "lambdaFunctions\/vectorReindexer"/);
    });

    it("the AccessDenied message names the resolved function", () => {
        const denied = script.slice(script.indexOf("elif error_code == 'AccessDeniedException':"));
        expect(denied.slice(0, 400)).toContain("'{function_name}'");
    });
});

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
        // The section is what getConfig() points at when it refuses an enabled retired pipeline block.
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

describe("the v2.6 to v2.7 migration leaves Download as Zip and large outputs alone", () => {
    // Download as Zip (`app.pipelines.useZipDownloads`) and the large object copy engine ship in 2.7.0
    // with no data to migrate. These pin why no step of this release reaches their records, and that the
    // operator documentation says what the deploy creates for them and in which order a v2.5 deployment
    // runs the two releases' migrations.
    const release = MIGRATIONS.find((m) => m.dir === "v2.6_to_v2.7");
    if (!release) throw new Error("the v2.6_to_v2.7 migration is not listed");
    const script = readMigrationFile(release, release.script);
    const readme = readMigrationFile(release, release.readme);
    const previous = MIGRATIONS.find((m) => m.dir === "v2.5_to_v2.6");
    if (!previous) throw new Error("the v2.5_to_v2.6 migration is not listed");

    /** The quoted strings of a top-level Python tuple assignment (possibly spanning lines). */
    function tuple(source: string, name: string): string[] {
        const body = new RegExp(`^${name}\\s*=\\s*\\(([^)]*)\\)`, "m").exec(source)?.[1] ?? "";
        return Array.from(body.matchAll(/['"]([^'"]+)['"]/g)).map((m) => m[1]);
    }

    it("matches fileUpload trigger rows only, so a system trigger value is never read as a trigger", () => {
        expect(tuple(script, "TRIGGER_BASE_TYPES")).toEqual(["fileUpload"]);
    });

    it("retires and reports only the two retired built-ins, never the Zip Archive workflow", () => {
        const retired = tuple(script, "RETIRED_PIPELINE_IDS");
        expect(retired).toHaveLength(2);
        expect(retired).not.toContain("utility-zip-archive");
        expect(script).toMatch(/^RETIRED_WORKFLOW_IDS = RETIRED_PIPELINE_IDS$/m);
        // The in-flight listing queries the executions of the retired workflows by name, nothing else.
        const listing = script.slice(script.indexOf("def list_inflight_retired_executions("));
        expect(listing.slice(0, 600)).toContain("for workflow_id in RETIRED_WORKFLOW_IDS:");
    });

    it("walks no Amazon S3 bucket itself; the backfills hand the walk to the reindexer", () => {
        // Only the two location steps reach Amazon S3. assetLocationFolderKeys reads one HeadObject, the
        // versions of one exact key at a time, one single-key listing under '{key}/', and one single-version
        // listing under '{key}/'; assetLocationFileToFolder makes its calls from its own module (pinned below).
        const start = script.indexOf("# STEP: assetLocationFolderKeys\n");
        const end = script.indexOf("# STEP: orphanedTriggers\n");
        expect(start).toBeGreaterThan(-1);
        expect(end).toBeGreaterThan(start);
        const locationStep = script.slice(start, end);
        const rest = script.slice(0, start) + script.slice(end);
        expect(rest).not.toMatch(
            /list_objects|list_object_versions|head_object|\.client\(\s*['"]s3['"]/
        );
        expect(script).not.toMatch(/get_paginator\(\s*['"]list_object/);
        const listings = locationStep.match(/list_objects_v2\([^)]*\)/g) ?? [];
        expect(listings).toHaveLength(1);
        expect(listings[0]).toContain("MaxKeys=1");
        const versionListings = locationStep.match(/\.list_object_versions\([^)]*\)/g) ?? [];
        expect(versionListings).toHaveLength(2);
        // The second listing is the archived-folder probe under '{key}/', one entry only.
        expect(versionListings.filter((call) => call.includes('Prefix=f"{key}/"'))).toEqual([
            '.list_object_versions(Bucket=bucket, Prefix=f"{key}/", MaxKeys=1)',
        ]);
        const exact = locationStep.slice(locationStep.indexOf("def exact_key_versions("));
        expect(exact.slice(0, 900)).toContain("if entry['Key'] == key");
        // The auxiliary bucket, which holds vams.preparedDownloads/, is named only where
        // assetLocationFileToFolder resolves it for a move, and the move reaches only a file's preview/ keys.
        const fileToFolderRun = script.slice(
            script.indexOf("def run_asset_location_file_to_folder_step("),
            script.indexOf("# STEP: orphanedTriggers\n")
        );
        expect(fileToFolderRun).toMatch(/ASSET_AUXILIARY_BUCKET/);
        expect(script.split(fileToFolderRun).join("")).not.toMatch(/auxiliary/i);
        const module = readMigrationFile(release, "asset_location_file_to_folder.py");
        expect(module).not.toMatch(/preparedDownloads/);
        const auxMove = module.slice(module.indexOf("def move_auxiliary("));
        expect(auxMove.slice(0, 600)).toMatch(/\/preview\/"/);
    });

    it("moves the copy's records without overwriting, and leaves vectors and execution history", () => {
        const move = script.slice(script.indexOf("def move_record("));
        expect(move.slice(0, 3000)).toContain("'ConditionExpression': 'attribute_not_exists(#k)'");
        expect(move.slice(0, 3000)).toContain("unchanged = ['attribute_exists(#k)']");
        const block = script.slice(script.indexOf("COPY_TABLE_KEYS = ("));
        const tables = Array.from(
            block.slice(0, block.indexOf("\n)\n")).matchAll(/'([A-Z0-9_]+)'/g)
        ).map((m) => m[1]);
        for (const name of [
            "ASSET_FILE_METADATA_STORAGE_TABLE",
            "FILE_ATTRIBUTE_STORAGE_TABLE",
            "ASSET_FILE_VERSIONS_STORAGE_TABLE",
            "ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE",
            "ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE",
        ]) {
            expect(tables).toContain(name);
        }
        expect(tables.join(" ")).not.toMatch(/VECTOR|EXECUTION/);
        expect(readme).toContain("#### Part (b): the copy named like the file");
        // Idempotent without local state: a conditional marker on the asset row, read by the scan.
        expect(script).toMatch(/^COPY_DONE_ATTRIBUTE = 'assetLocationCopyRecordsMovedAt'$/m);
        const mark = script.slice(script.indexOf("def mark_copy_records_done("));
        expect(mark.slice(0, 1200)).toContain("attribute_not_exists(#m)");
        expect(script).toMatch(/assetLocation, '\s*\n\s*f'\{COPY_DONE_ATTRIBUTE\}'/);
        expect(script).not.toMatch(/moved-copies/);
        expect(readme).toContain("assetLocationCopyRecordsMovedAt");
        expect(readme).toContain("Run the step before users edit metadata on single-file assets.");
        expect(readme).toMatch(/run `vectorBackfill` after this step/);
    });

    it("runs assetLocationFolderKeys first of the location steps, gated, with a report and a checkpoint", () => {
        // commentSubscriptionKeys, which reads no location, runs before it (D1878-3)
        expect(tuple(script, "STEP_CHOICES").slice(0, 2)).toEqual([
            "commentSubscriptionKeys",
            "assetLocationFolderKeys",
        ]);
        const body = script.slice(script.indexOf("def main("));
        expect(body.indexOf("run_asset_location_folder_keys_step(")).toBeGreaterThan(-1);
        expect(body.indexOf("run_asset_location_folder_keys_step(")).toBeLessThan(
            body.indexOf("run_orphaned_triggers_step(")
        );
        expect(body).toMatch(/gated=run_location_keys or /);
        expect(script).toContain("ConditionExpression='#loc.#key = :old'");
        expect(script).toMatch(/parser\.add_argument\('--resume'/);
        expect(readme).toContain("assetLocationFolderKeys-dry-run-report-<UTC time>.jsonl");
        expect(readme).toContain("--resume");
    });

    /** The source of one top-level Python function, up to the next top-level definition. */
    function pyFunction(name: string): string {
        const start = script.indexOf(`\ndef ${name}(`);
        expect(start).toBeGreaterThan(-1);
        const next = script.slice(start + 1).search(/\n(def |class |#{5,})/);
        return script.slice(start + 1, next < 0 ? undefined : start + 1 + next + 1);
    }

    it("classes an archived folder by its versions under '{key}/', after the live listing finds none", () => {
        const classify = pyFunction("classify_asset_location");
        const live = classify.indexOf(
            'list_objects_v2(Bucket=bucket, Prefix=f"{key}/", MaxKeys=1)'
        );
        const archived = classify.indexOf(
            'list_object_versions(Bucket=bucket, Prefix=f"{key}/", MaxKeys=1)'
        );
        expect(live).toBeGreaterThan(-1);
        expect(archived).toBeGreaterThan(live);
        expect(classify.slice(archived)).toMatch(
            /if versions\.get\('Versions'\) or versions\.get\('DeleteMarkers'\):\s*\n\s*return 'folder-without-slash', 'archived'/
        );
        expect(classify.slice(archived).indexOf("return 'no-objects'")).toBeGreaterThan(-1);
    });

    it("re-reads each record consistently and deletes it only if it is unchanged since that read", () => {
        const move = pyFunction("move_record");
        const read = move.indexOf("get_item(TableName=table, Key=old_key, ConsistentRead=True)");
        const transact = move.indexOf("transact_write_items(");
        expect(read).toBeGreaterThan(-1);
        expect(transact).toBeGreaterThan(read);
        // The new record is built from the read, and a record that differs from the listing is left.
        expect(move).toContain("new_item = dict(current)");
        expect(move).toMatch(/if current != item:\s*\n\s*return 'changed'/);
        // The delete is conditioned on every non-key attribute of that read.
        expect(move).toMatch(
            /for index, attr in enumerate\(sorted\(a for a in current if a not in key_names\)\)/
        );
        expect(move).toContain('unchanged.append(f"#a{index} = :a{index}")');
        expect(move).toContain("'ReturnValuesOnConditionCheckFailure': 'ALL_OLD'");
    });

    it("decides a cancelled move by its cancellation reasons and raises on any other reason", () => {
        const move = pyFunction("move_record");
        expect(move).toContain("e.response.get('CancellationReasons')");
        expect(move).toMatch(
            /if codes\[:1\] == \['ConditionalCheckFailed'\]:\s*\n\s*return 'clash'/
        );
        expect(move).toMatch(
            /codes\[1\] == 'ConditionalCheckFailed':\s*\n\s*return 'changed' if reasons\[1\]\.get\('Item'\) else 'gone'/
        );
        // Any other cancellation (a transaction conflict, throttling) is an error: the function ends in a raise.
        expect(move.trimEnd()).toMatch(/\n\s+raise$/);
        // No probe of the target decides the outcome after a cancellation.
        const after = move.slice(move.indexOf("except ClientError as e:"));
        expect(after).not.toContain("get_item(");
    });

    it("leaves an asset unmarked, and the step non-zero, while a record of it was edited during the move", () => {
        const part = pyFunction("move_companion_copy_records");
        expect(part).toMatch(
            /if dry_run or counts\['errors'\] != errors_before or counts\['records-changed'\] != changed_before:\s*\n\s*return/
        );
        expect(tuple(script, "COPY_COUNTERS")).toEqual(
            expect.arrayContaining(["records-changed", "copy-chains-deeper"])
        );
        const step = pyFunction("run_asset_location_folder_keys_step");
        expect(step).toMatch(
            /incomplete = counts\['errors'\] or counts\['unreadable'\] or \(\s*not dry_run and \(counts\['records-changed'\] or /
        );
        expect(readme).toContain("edited during the move");
    });

    it("keeps a real run non-zero, so the backfills wait, until a copy of the copy is moved by hand", () => {
        const step = pyFunction("run_asset_location_folder_keys_step");
        expect(step).toMatch(
            /incomplete = [^\n]*\n?[^\n]*not dry_run and \(counts\['records-changed'\] or counts\['copy-chains-deeper'\]\)\)/
        );
        // The README gives the hand move and the marker, its attribute, type and value form.
        expect(readme).toContain("SET assetLocationCopyRecordsMovedAt = :t");
        expect(readme).toContain("attribute_not_exists(assetLocationCopyRecordsMovedAt)");
        expect(readme).toMatch(/string\s+attribute \(`S`\) holding the UTC time in ISO 8601/);
        expect(readme).toMatch(/On\s+a real run the step then exits non-zero/);
    });

    it("looks for a copy of the copy before it marks a single-file asset that has no copy", () => {
        const part = pyFunction("move_companion_copy_records");
        const body = part.slice(part.indexOf("name = key.rsplit('/', 1)[-1]"));
        const chain = body.indexOf('exact_key_versions(s3_client, bucket, f"{key}/{name}/{name}"');
        const earlyFinish = body.search(/if not copy_versions:\s*\n\s*finish\(\)/);
        expect(chain).toBeGreaterThan(-1);
        expect(earlyFinish).toBeGreaterThan(chain);
        // The read lists name that listing.
        expect(readme).toContain("one version of `{key}/{name}/{name}` (`MaxKeys=1`)");
    });

    it("writes one report per run, so a later run keeps the lines an earlier one wrote", () => {
        const files = pyFunction("_location_files");
        expect(files).toContain("stamp = started.strftime('%Y%m%dT%H%M%SZ')");
        expect(files).toContain('f"{LOCATION_STEP}-{mode}-report-{stamp}.jsonl"');
        const fix = pyFunction("fix_asset_location_folder_keys");
        expect(fix).toContain("with open(report_path, 'a', encoding='utf-8') as report:");
        expect(fix).not.toMatch(/report_mode|open\(report_path, 'w'/);
        expect(fix).toContain("report_path = checkpoint['report']");
    });

    it("holds the billed backfills on a real run whose location step did not finish", () => {
        const body = pyFunction("main");
        expect(body).toMatch(
            /if rc != 0:\s*\n\s*exit_code = rc\s*\n\s*location_keys_unfinished = not dry_run\n/
        );
        expect(body.indexOf("location_keys_unfinished = not dry_run")).toBeLessThan(
            body.indexOf("run_vector_backfill_step(")
        );
        expect(body).toContain("if run_vector_backfill and not location_keys_unfinished:");
        expect(body).toContain("if run_metadata_backfill and not location_keys_unfinished:");
        expect(readme).toContain("skips `vectorBackfill` and `metadataBackfill`");
        // A key that can never be read (an unregistered bucket) has a way on: the backfill run on its own.
        expect(readme).toMatch(/whose bucket is not registered in the S3 asset buckets table/);
        expect(body).toMatch(/A key that can never be read \(an "\s*\n\s*"unregistered bucket\)/);
    });

    it("documents what the deploy creates for Download as Zip and the copy engine", () => {
        for (const text of [
            "ZipDownloadRequestsStorageTable",
            "LargeObjectCopyJobsStorageTable",
            "vams.preparedDownloads/",
            "app.pipelines.useZipDownloads.enabled",
            "GLOBAL/utility-zip-archive",
            "System-ZipDownload",
            "AWS Batch, Amazon ECR API and Amazon ECR Docker interface",
        ]) {
            expect({ text, inReadme: readme.includes(text) }).toEqual({ text, inReadme: true });
        }
    });

    it("tells a v2.5 deployment to run the v2.5 to v2.6 migration first, which skips the zip prefix", () => {
        expect(readme).toContain("### Upgrading from v2.5");
        expect(readme).toContain("../../v2.5_to_v2.6/upgrade/v2.5_to_v2.6_migration_README.md");
        expect(
            fs.existsSync(path.join(migrationDir(previous), "v2.5_to_v2.6_migration_README.md"))
        ).toBe(true);
        const reserved = tuple(
            readMigrationFile(previous, previous.script),
            "_AUX_RESERVED_TOP_PREFIXES"
        );
        const patterns = fs.readFileSync(
            path.join(__dirname, "..", "../../backend/backend/common/s3PathPatterns.py"),
            "utf8"
        );
        const prefix = /^AUXILIARY_PREPARED_DOWNLOADS_PREFIX = "([^"]+)\/"$/m.exec(patterns)?.[1];
        expect(prefix).toBe("vams.preparedDownloads");
        expect(reserved).toContain(prefix);
        const page = fs.readFileSync(
            path.join(DOCS_ROOT, "deployment/update-the-solution.md"),
            "utf8"
        );
        const section = page.slice(page.indexOf("\n### v2.6 to v2.7\n"));
        const end = section.slice(1).search(/\n#{2,3} /);
        const v27 = end === -1 ? section : section.slice(0, end + 1);
        expect(v27).toContain("[v2.5 to v2.6](#v25-to-v26)");
        expect(v27).toContain("ZipDownloadRequestsStorageTable");
        expect(v27).toContain("LargeObjectCopyJobsStorageTable");
        expect(v27).toContain("`vams.preparedDownloads/` lifecycle rules");
    });

    it("resolves the zip download table and the vector-search names from one SSM key list", () => {
        const lookup = fs.readFileSync(SSM_RESOURCE_LOOKUP_PY, "utf8");
        expect(lookup).toMatch(
            /ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE = "dynamoTables\/zipDownloadRequestsStorage"/
        );
        expect(lookup).toMatch(
            /VECTOR_EMBEDDINGS_STORAGE_TABLE = "dynamoTables\/vectorEmbeddingsStorage"/
        );
        expect(lookup).toMatch(/VECTOR_REINDEXER_FUNCTION = "lambdaFunctions\/vectorReindexer"/);
        expect(lookup).toMatch(
            /LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE = "dynamoTables\/largeObjectCopyJobsStorage"/
        );
    });
});

describe("the v2.6 to v2.7 commentSubscriptionKeys step and what users did before its first run", () => {
    // A permanent delete on v2.7 removes an asset's row, versions and V2 comments and subscription row but not
    // its v2.6 rows, and an unsubscribe removes the topic subscription but not the v2.6 subscriber. The first
    // run cannot see either in the v2.6 tables. These pin that it does not then copy a deleted asset's rows onto
    // another database's asset with the same id (#1860 D1878-1), that a single candidate's subscriber is copied
    // only from its topic (D1878-2), and that the step runs first (D1878-3).
    const release = MIGRATIONS.find((m) => m.dir === "v2.6_to_v2.7");
    if (!release) throw new Error("the v2.6_to_v2.7 migration is not listed");
    const script = readMigrationFile(release, release.script);
    const readme = readMigrationFile(release, release.readme);
    const config = readMigrationFile(release, "v2.6_to_v2.7_migration_config.json");
    const history = fs.readFileSync(
        path.join(__dirname, "..", "../../backend/backend/common/assetHistory.py"),
        "utf8"
    );
    const storage = fs.readFileSync(
        path.join(__dirname, "..", "../lib/nestedStacks/storage/storageBuilder-nestedStack.ts"),
        "utf8"
    );
    const guide = fs.readFileSync(
        path.join(DOCS_ROOT, "deployment/update-the-solution.md"),
        "utf8"
    );
    const v27 = (() => {
        const section = guide.slice(guide.indexOf("\n### v2.6 to v2.7\n"));
        const end = section.slice(1).search(/\n#{2,3} /);
        return end === -1 ? section : section.slice(0, end + 1);
    })();

    /** The quoted strings of a top-level Python tuple assignment (possibly spanning lines). */
    function tuple(name: string): string[] {
        const body = new RegExp(`^${name}\\s*=\\s*\\(([^)]*)\\)`, "m").exec(script)?.[1] ?? "";
        return Array.from(body.matchAll(/['"]([^'"]+)['"]/g)).map((m) => m[1]);
    }

    /** The string value assigned to `name` at module level in a Python source. */
    function pyString(source: string, name: string): string {
        const match = new RegExp(`^${name}\\s*=\\s*(?:'([^']*)'|"([^"]*)")\\s*$`, "m").exec(source);
        if (!match) throw new Error(`${name} is not assigned a string literal`);
        return match[1] ?? match[2];
    }

    /** The source of one method of CommentSubscriptionCopy, up to the next method. */
    function method(name: string): string {
        const cls = script.slice(script.indexOf("\nclass CommentSubscriptionCopy:"));
        const start = cls.indexOf(`\n    def ${name}(`);
        expect(start).toBeGreaterThan(-1);
        const next = cls.slice(start + 1).search(/\n {4}def |\n(def |class )/);
        return cls.slice(start + 1, next < 0 ? undefined : start + 1 + next);
    }

    /** The source of one top-level Python function, up to the next top-level definition. */
    function pyFunction(name: string): string {
        const start = script.indexOf(`\ndef ${name}(`);
        expect(start).toBeGreaterThan(-1);
        const next = script.slice(start + 1).search(/\n(def |class |#{5,})/);
        return script.slice(start + 1, next < 0 ? undefined : start + 1 + next + 1);
    }

    it("reads the permanent-delete record the backend writes, under the history table's key", () => {
        expect(pyString(script, "ASSET_HISTORY_PERMANENT_DELETE")).toBe(
            pyString(history, "CHANGE_SOURCE_PERMANENT_DELETE")
        );
        expect(history).toContain("'databaseId:assetId': f\"{database_id}:{asset_id}\"");
        expect(history).toContain("'changeSource': change_source");
        expect(history).toContain("'recordDate': record_date");
        const table = storage.slice(
            storage.indexOf('new dynamodb.Table(scope, "AssetHistoryStorageTable"')
        );
        expect(table.slice(0, 300)).toContain(
            `name: "${pyString(script, "ASSET_HISTORY_PARTITION_KEY")}"`
        );
    });

    it("counts a database whose asset with the id was permanently deleted as a candidate", () => {
        const resolve = method("resolve");
        const deleted = resolve.indexOf("self.deleted_databases(asset_id)");
        expect(deleted).toBeGreaterThan(-1);
        expect(deleted).toBeLessThan(resolve.indexOf("if len(candidates) == 1:"));
        expect(resolve).toContain("candidates = sorted(set(live) | set(deleted))");
        // A row the evidence or the mapping file gives to the deleted asset is not copied anywhere
        expect(
            resolve.match(
                /if (?:matching\[0\]|mapped) in deleted:\s*\n\s*return None, 'noAsset', candidates/g
            )
        ).toHaveLength(2);
        // Its versions are gone, so nothing rules it out for a comment made before its last delete
        const copy = method("copy_comment");
        expect(copy).toMatch(
            /if database_id in self\.deleted_databases\(asset_id\):\s*\n\s*return True/
        );
        expect(copy).toContain("made=created");
        expect(resolve).toContain("if not made or not _iso_before(when, made)");
    });

    it("finds those databases with one filtered scan of the history table per run, cached", () => {
        // The cost follows the history table, not databases x asset ids (D1919-4)
        const deletes = method("_permanent_deletes");
        expect(deletes).toContain("if self._deletes is None:");
        expect(deletes).toContain(
            "_paginate(self.ddb.scan, TableName=self.cfg['asset_history_storage_table_name']"
        );
        expect(deletes).toContain("FilterExpression='#c = :d'");
        expect(deletes).toContain("ASSET_HISTORY_PERMANENT_DELETE");
        expect(deletes).toContain("ProjectionExpression='#p, #db, #a, #r'");
        expect(deletes).not.toMatch(/\.query\b/);
        const deleted = method("deleted_databases");
        expect(deleted).toContain("if asset_id not in self._deleted:");
        expect(deleted).toContain("self._permanent_deletes().get(asset_id, {})");
        expect(deleted).not.toMatch(/\.(query|scan)\b|for database_id in self\._all_databases/);
        // The step's own code, config block and IAM clause name no database table. (bucketReadConstraints,
        // a separate step, reads the database ids; these pins are scoped so it does not hide a regression.)
        const commentSection = script.slice(
            script.indexOf("\n# STEP: commentSubscriptionKeys\n"),
            script.indexOf("\n# CLI\n")
        );
        expect(commentSection.length).toBeGreaterThan(1000);
        expect(commentSection).not.toContain("database_storage_table_name");
        expect(commentSection).not.toContain("DATABASE_STORAGE_TABLE");
        const step = pyFunction("run_comment_subscription_keys_step");
        expect(step).toContain("ResourceParamKeys.ASSET_HISTORY_STORAGE_TABLE");
        expect(step).not.toContain("ResourceParamKeys.DATABASE_STORAGE_TABLE");
        expect(config).toContain('"asset_history_storage_table_name": null');
        const commentConfig = config.slice(
            config.indexOf('"_comment_comments_step"'),
            config.indexOf('"_comment_index_step"')
        );
        expect(commentConfig).toContain('"comment_subscription_resolution_file"');
        expect(commentConfig).not.toContain("database_storage_table_name");
        expect(readme).toContain(
            "`Scan` on the asset history table (its `permanentDelete` records)"
        );
        const commentIam = readme.slice(
            readme.indexOf("commentSubscriptionKeys step: DynamoDB"),
            readme.indexOf("bucketReadConstraints step: DynamoDB")
        );
        expect(commentIam).toContain("`Scan` on the asset history table");
        expect(commentIam).not.toContain("`Scan` on the database table");
    });

    it("tries a failed history read once per run: it ends the step, never a row", () => {
        // D1919-7: a ClientError there becomes HistoryScanError, which is no ClientError, so run_table's per-row
        // catch does not report it against the row and go on to the next row (which would scan again); the
        // step's catch-all ends the run with exit 1 and the --resume hint, as an interruption does.
        const deletes = method("_permanent_deletes");
        const tried = deletes.indexOf("try:");
        const caught = deletes.indexOf("except ClientError as e:");
        expect(tried).toBeGreaterThan(-1);
        expect(caught).toBeGreaterThan(deletes.indexOf("_paginate(self.ddb.scan"));
        expect(deletes.slice(caught)).toMatch(/raise HistoryScanError\(/);
        expect(deletes.slice(caught)).toContain(") from e");
        expect(script).toMatch(/^class HistoryScanError\(Exception\):/m);
        expect(script).not.toMatch(/class HistoryScanError\([^)]*ClientError/);
        const table = method("run_table");
        expect(table).toMatch(/records = copy\(item\)\s*\n\s*except ClientError as e:/);
        expect(table).not.toMatch(/except (Exception|HistoryScanError)/);
        for (const name of [
            "copy_comment",
            "copy_subscription",
            "resolve",
            "place_subscriber",
            "deleted_databases",
        ]) {
            expect({
                name,
                catchesAll: /except (Exception|HistoryScanError)\b|except:/.test(method(name)),
            }).toEqual({
                name,
                catchesAll: false,
            });
        }
        const step = pyFunction("run_comment_subscription_keys_step");
        expect(step).toMatch(
            /except Exception as e:\s*\n\s*logger\.error\(f"\{COMMENT_SUBSCRIPTION_STEP\} step failed: \{e\}\. Re-run with --resume/
        );
        expect(readme).toMatch(/If that read fails, the step\s+stops at once/);
    });

    it("copies a single candidate's subscriber only when it is on that asset's topic", () => {
        expect(tuple("COMMENT_SUBSCRIPTION_CLASSES")).toContain("notOnTopic");
        expect(tuple("_COMMENT_SUBSCRIPTION_LEFT")).toContain("notOnTopic");
        const place = method("place_subscriber");
        expect(place).toMatch(/if result == 'placed' and not evidence\(database_id\):/);
        expect(place).toContain("return None, 'notOnTopic', candidates");
        for (const cls of tuple("COMMENT_SUBSCRIPTION_CLASSES")) {
            expect({ cls, inReadme: readme.includes(`| \`${cls}\``) }).toEqual({
                cls,
                inReadme: true,
            });
        }
    });

    it("runs first under --steps all, and the location steps never hold it", () => {
        expect(tuple("STEP_CHOICES")[0]).toBe("commentSubscriptionKeys");
        const body = pyFunction("main");
        const comments = body.indexOf("rc = run_comment_subscription_keys_step(");
        expect(comments).toBeGreaterThan(-1);
        expect(comments).toBeLessThan(body.indexOf("rc = run_asset_location_folder_keys_step("));
        expect(body.slice(0, comments)).not.toContain("location_keys_unfinished = not dry_run");
        expect(body.indexOf("step_plan.append('commentSubscriptionKeys')")).toBeLessThan(
            body.indexOf("step_plan.append('assetLocationFolderKeys')")
        );
    });

    it("tells the operator not to delete assets or change subscriptions before the first run", () => {
        for (const page of [readme, v27]) {
            expect(page).toMatch(
                /do not permanently delete assets and do not subscribe, unsubscribe or delete\s+subscriptions/
            );
            expect(page).toContain("`notOnTopic`");
        }
        expect(readme).toContain("`deletedCandidates`");
    });

    it("says the deleted-asset handling needs the permanentDelete history record", () => {
        for (const page of [readme, v27]) {
            expect(page).toMatch(
                /writes? that record last\s+and\s+best-effort|writes last and best-effort/
            );
        }
    });

    it("names the v2.6 causes of notOnTopic and the v2.1 noAsset edge the mapping cannot place", () => {
        for (const page of [readme, v27]) {
            expect(page).toMatch(/never\s+confirmed/);
            expect(page).toContain("unsubscribe link");
        }
        expect(readme).toMatch(
            /upgraded from v2\.1, a `noAsset` comment line with `deletedCandidates`/
        );
    });

    it("says the location steps run after commentSubscriptionKeys wherever the order is stated", () => {
        const revisions = fs.readFileSync(path.join(DOCS_ROOT, "additional/revisions.md"), "utf8");
        const changelog = fs.readFileSync(path.join(__dirname, "..", "../../CHANGELOG.md"), "utf8");
        for (const [name, text] of [
            ["script", script],
            ["readme", readme],
            ["config", config],
            ["guide", v27],
            ["revisions", revisions],
        ] as const) {
            expect({
                name,
                stale: /assetLocationFolderKeys`? runs first(?! of the location steps)|assetLocationFolderKeys \(runs first\)|It runs first, before/.test(
                    text
                ),
            }).toEqual({ name, stale: false });
        }
        expect(config).not.toMatch(/The (six|seven|eight|nine) table names/);
        // The release record carries the window instruction too (D1919-6)
        const note = changelog.slice(
            changelog.indexOf("**Migration note:** comments and subscriptions move")
        );
        expect(note.slice(0, 1200)).toContain(
            "do not permanently delete assets and do not subscribe"
        );
        expect(revisions).toMatch(
            /not shown\. Until it has run, do not permanently delete assets and do not subscribe/
        );
    });
});

describe("the v2.6 to v2.7 assetLocationIndex step writes what the backend reads", () => {
    // backend/backend/common/assetLocations.py decides index_ready() from the marker this step writes,
    // and AssetLocationGSI indexes the two attributes the step sets. A renamed constant on either side
    // leaves the index empty or the marker unread, and nothing else fails.
    const release = MIGRATIONS.find((m) => m.dir === "v2.6_to_v2.7");
    if (!release) throw new Error("the v2.6_to_v2.7 migration is not listed");
    const script = readMigrationFile(release, release.script);
    const backend = fs.readFileSync(
        path.join(__dirname, "..", "../../backend/backend/common/assetLocations.py"),
        "utf8"
    );

    /** The string or integer value assigned to `name` at module level in a Python source. */
    function pyConstant(source: string, name: string): string {
        const match = new RegExp(`^${name}\\s*=\\s*(?:'([^']*)'|"([^"]*)"|(\\d+))\\s*$`, "m").exec(
            source
        );
        if (!match) throw new Error(`${name} is not assigned a literal`);
        return match[1] ?? match[2] ?? match[3];
    }

    const PAIRS: ReadonlyArray<[string, string]> = [
        ["ASSET_LOCATION_BUCKET_ATTRIBUTE", "LOCATION_BUCKET_ATTRIBUTE"],
        ["ASSET_LOCATION_KEY_ATTRIBUTE", "LOCATION_KEY_ATTRIBUTE"],
        ["ASSET_LOCATION_INDEX_MARKER_SUFFIX", "INDEX_READY_PARAMETER_SUFFIX"],
        ["ASSET_LOCATION_INDEX_MARKER_VERSION", "INDEX_READY_MARKER_VERSION"],
        ["ASSET_LOCATION_INDEX_STATUS_COMPLETE", "INDEX_READY_STATUS_COMPLETE"],
    ];

    it("names the same preview pattern as the backend's single-file rule", () => {
        const patterns = fs.readFileSync(
            path.join(__dirname, "..", "../../backend/backend/common/s3PathPatterns.py"),
            "utf8"
        );
        expect(pyConstant(script, "ASSET_LOCATION_PREVIEW_FILE_PATTERN")).toBe(
            pyConstant(patterns, "PREVIEW_FILE_PATTERN")
        );
        expect(backend).toContain("from common.s3PathPatterns import PREVIEW_FILE_PATTERN");
    });

    it.each(PAIRS)("%s equals the backend's %s", (migrationName, backendName) => {
        expect(pyConstant(script, migrationName)).toBe(pyConstant(backend, backendName));
    });

    it("runs after assetLocationFolderKeys, whose fixed keys it indexes, and not after it failed", () => {
        const body = script.slice(script.indexOf("def main("));
        const folderKeys = body.indexOf("run_asset_location_folder_keys_step(");
        const index = body.indexOf("run_asset_location_index_step(");
        expect(folderKeys).toBeGreaterThan(-1);
        expect(index).toBeGreaterThan(folderKeys);
        const steps = Array.from(
            (/^STEP_CHOICES\s*=\s*\(([^)]*)\)/m.exec(script)?.[1] ?? "").matchAll(/'([A-Za-z]+)'/g)
        ).map((m) => m[1]);
        expect(steps.indexOf("assetLocationIndex")).toBe(steps.indexOf("all") - 1);
        expect(steps.slice(0, 2)).toEqual(["commentSubscriptionKeys", "assetLocationFolderKeys"]);
        expect(body).toMatch(/if run_asset_location_index and location_keys_unfinished:/);
        // A key the folder-keys step fixes after the index ran keeps its indexed key in step.
        const append = script.slice(script.indexOf("def append_location_slash("));
        expect(append.slice(0, append.indexOf("\ndef "))).toContain("ASSET_LOCATION_KEY_ATTRIBUTE");
    });

    it("is held on a real run whose folder-keys step did not finish, with a way on for a key never read", () => {
        const body = script.slice(script.indexOf("def main("));
        // The flag is set in the folder-keys step's failure branch, before the steps it holds.
        const folderKeys = body.slice(body.indexOf("run_asset_location_folder_keys_step("));
        const branch = folderKeys.slice(0, folderKeys.indexOf("if run_orphaned_triggers:"));
        expect(branch).toMatch(
            /if rc != 0:\s*\n\s*exit_code = rc\s*\n\s*location_keys_unfinished = not dry_run\n/
        );
        // The index step runs only in the branch the flag does not hold.
        const held = body.slice(
            body.indexOf("if run_asset_location_index and location_keys_unfinished:")
        );
        const runs = held.indexOf("elif run_asset_location_index:");
        expect(runs).toBeGreaterThan(-1);
        expect(runs).toBeLessThan(held.indexOf("run_asset_location_index_step("));
        const skip = held.slice(0, runs).replace(/"\s*\n\s*"/g, "");
        expect(skip).toContain("outcomes.append(('assetLocationIndex', held_outcome))");
        expect(skip).toContain("exit_code = exit_code or 1");
        expect(skip).toContain(
            "once you have checked its report, run --steps assetLocationIndex on its own."
        );
        const readme = readMigrationFile(release, release.readme);
        const guide = fs.readFileSync(
            path.join(DOCS_ROOT, "deployment/update-the-solution.md"),
            "utf8"
        );
        for (const page of [readme, guide]) {
            expect(page).toMatch(/run `--steps assetLocationIndex` on\s+its own/);
            expect(page).not.toContain("skipped: assetLocationFolderKeys failed in this run");
        }
        // The step writes rows whose attributes are missing or wrong, not only rows that lack them.
        const config = readMigrationFile(release, "v2.6_to_v2.7_migration_config.json");
        expect(readme).not.toContain("writes only the rows that lack the attributes");
        expect(guide).not.toContain("writes only rows that lack the two attributes");
        expect(config).not.toContain("every asset row that lacks them");
    });

    it("writes the table name into the marker, which the backend requires", () => {
        expect(script).toMatch(/'assetTable': self\.asset_table/);
        expect(backend).toMatch(/marker\.get\('assetTable'\) == asset_table_name/);
    });

    it("the GSI keys on the same attribute names", () => {
        const storage = fs.readFileSync(
            path.join(__dirname, "..", "../lib/nestedStacks/storage/storageBuilder-nestedStack.ts"),
            "utf8"
        );
        const at = storage.indexOf("indexName: ASSET_LOCATION_INDEX,");
        expect(at).toBeGreaterThan(-1);
        const block = storage.slice(at);
        expect(storage).toContain(
            `export const ASSET_LOCATION_INDEX = "${pyConstant(backend, "LOCATION_INDEX_NAME")}";`
        );
        expect(block.slice(0, 400)).toContain(
            `name: "${pyConstant(backend, "LOCATION_BUCKET_ATTRIBUTE")}"`
        );
        expect(block.slice(0, 400)).toContain(
            `name: "${pyConstant(backend, "LOCATION_KEY_ATTRIBUTE")}"`
        );
    });

    it("[control] the constant reader rejects a name that is not assigned", () => {
        expect(() => pyConstant(script, "NO_SUCH_CONSTANT")).toThrow();
    });
});

describe("reindex_utility.py --tag-synced reaches its readiness check with the backend it loads", () => {
    // tag_synced_objects() imports the backend's common/assetLocations.py at run time and calls
    // index_ready() before it reads anything else. A signature change there does not show in a text merge
    // and fails only when an operator runs the tool, so the real tool and the real backend module are run
    // to and past that check by a standard-library harness with a fake boto3 session.
    const HARNESS = path.join(__dirname, "fixtures", "tagSyncedReadinessHarness.py");
    const TOOL = path.join(MIGRATIONS_ROOT, "tools", "tag_synced_objects.py");
    const BACKEND = path.join(__dirname, "..", "../../backend/backend");
    const MARKER = "/vams-test/resourceNames/dataMigrations/assetLocationIndex";
    const NOT_READY = "The asset location index is not ready";

    interface Outcome {
        exception: string | null;
        result: { error?: string; objectsSeen?: number } | null;
        markerReads: string[];
        sessionClients: string[];
        sessionResources: string[];
        scans: string[];
        defaultReaderLoaded: boolean;
    }

    let results: Record<string, Outcome>;
    beforeAll(() => {
        const run = spawnSync("python3", [HARNESS, TOOL, BACKEND], { encoding: "utf8" });
        if (run.status !== 0) {
            throw new Error(`the harness failed (${run.status}): ${run.stderr}`);
        }
        results = JSON.parse(run.stdout);
    });

    it.each(["missing", "throttled", "otherTable", "complete"])(
        "%s marker: the readiness check runs and reads the marker once with the tool's session",
        (scenario) => {
            const outcome = results[scenario];
            expect(outcome.exception).toBeNull();
            expect(outcome.markerReads).toEqual([MARKER]);
            expect(outcome.sessionClients).toEqual(["ssm"]);
            expect(outcome.defaultReaderLoaded).toBe(false);
        }
    );

    it.each(["missing", "throttled", "otherTable"])(
        "%s marker: the run stops before any table is read",
        (scenario) => {
            const outcome = results[scenario];
            expect(outcome.result?.error).toContain(NOT_READY);
            expect(outcome.sessionResources).toEqual([]);
            expect(outcome.scans).toEqual([]);
        }
    );

    it("a completed marker for the table lets the run go on to the bucket registrations", () => {
        const outcome = results.complete;
        expect(outcome.result?.error).toBeUndefined();
        expect(outcome.result?.objectsSeen).toBe(0);
        expect(outcome.sessionResources).toEqual(["dynamodb"]);
        expect(outcome.scans).toEqual(["vams-test-s3AssetBucketsStorageTable"]);
    });
});

describe("the v2.6 to v2.7 assetLocationFileToFolder step", () => {
    // The step converts asset locations that name a file into folders. It runs on an operator
    // machine without the backend, so its copies of the backend's names are pinned to the backend here.
    const release = MIGRATIONS.find((m) => m.dir === "v2.6_to_v2.7");
    if (!release) throw new Error("the v2.6_to_v2.7 migration is not listed");
    const script = readMigrationFile(release, release.script);
    const module = readMigrationFile(release, "asset_location_file_to_folder.py");
    const readme = readMigrationFile(release, release.readme);
    const config = JSON.parse(readMigrationFile(release, release.configFile));
    const backend = (relative: string) =>
        fs.readFileSync(path.join(__dirname, "..", "../../backend/backend", relative), "utf8");

    function pyString(source: string, name: string): string {
        const match = new RegExp(`^${name}\\s*=\\s*(?:'([^']*)'|"([^"]*)"|(\\d+))\\s*$`, "m").exec(
            source
        );
        if (!match) throw new Error(`${name} is not assigned a literal`);
        return match[1] ?? match[2] ?? match[3];
    }

    it("runs right after assetLocationFolderKeys and not after it failed, and is gated", () => {
        const steps = Array.from(
            (/^STEP_CHOICES\s*=\s*\(([^)]*)\)/m.exec(script)?.[1] ?? "").matchAll(/'([A-Za-z]+)'/g)
        ).map((m) => m[1]);
        // commentSubscriptionKeys, which reads no location, runs before both location steps
        expect(steps.slice(0, 3)).toEqual([
            "commentSubscriptionKeys",
            "assetLocationFolderKeys",
            "assetLocationFileToFolder",
        ]);
        expect(pyString(module, "STEP")).toBe("assetLocationFileToFolder");
        const body = script.slice(script.indexOf("def main("));
        const folderKeys = body.indexOf("run_asset_location_folder_keys_step(");
        const convert = body.indexOf("run_asset_location_file_to_folder_step(");
        expect(convert).toBeGreaterThan(folderKeys);
        expect(convert).toBeLessThan(body.indexOf("run_orphaned_triggers_step("));
        expect(body.indexOf("step_plan.append('assetLocationFileToFolder')")).toBeGreaterThan(
            body.indexOf("step_plan.append('assetLocationFolderKeys')")
        );
        expect(body.indexOf("step_plan.append('assetLocationFileToFolder')")).toBeLessThan(
            body.indexOf("step_plan.append('orphanedTriggers')")
        );
        expect(body).toMatch(/if run_file_to_folder and location_keys_unfinished:/);
        expect(body).toMatch(/gated=run_location_keys or run_file_to_folder or /);
        // A conversion that did not finish keeps the billed backfills from analysing files at old paths.
        expect(body).toMatch(
            /backfills_wait = file_to_folder_failed and not dry_run and not location_keys_unfinished/
        );
        expect(body).toMatch(/if run_vector_backfill and backfills_wait:/);
    });

    it("stamps a move's copies with the backend's relocation source, which starts no trigger", () => {
        // A copy stamped fileMove ran the fileUpload workflows and re-embedded every copied version (D15)
        const keys = backend("common/s3MetadataKeys.py");
        const relocation =
            /VAMS_CHANGE_SOURCE_RELOCATION_VALUES[^=]*=\s*frozenset\(\s*\{([^}]*)\}/.exec(keys);
        expect(relocation?.[1]).toContain("VAMS_CHANGE_SOURCE_FILE_RELOCATE");
        expect(module).toContain("CHANGE_SOURCE_METADATA_KEY: CHANGE_SOURCE_FILE_RELOCATE,");
        expect(module).not.toMatch(/'fileMove'/);
        const matching = backend("common/workflows/triggerMatching.py");
        expect(matching).toContain("if change_source in RELOCATION_CHANGE_SOURCES:");
    });

    it("notes a multipart copy's upload and aborts it when the move is resumed", () => {
        const execute = module.slice(
            module.indexOf("def _execute("),
            module.indexOf("def _stop_move(")
        );
        expect(execute).toContain("abort_noted_upload(s3_client, bucket, candidate.conversion)");
        expect(module).toContain("events.register('after-call.s3.CreateMultipartUpload'");
        expect(readme).toContain("`AbortIncompleteMultipartUpload`");
    });

    it("reads a path in the asset by one leading slash, as the backend's object_key_for_path does", () => {
        // '//x' is the key with an empty segment after the location; stripping every '/' made it 'x' (#1977)
        const mirror = module.slice(module.indexOf("def object_key_for_path("));
        expect(mirror.slice(0, mirror.indexOf("\ndef "))).toContain("relative = relative[1:]");
        const shipped = backend("common/assetLocations.py");
        const backendPath = shipped.slice(shipped.indexOf("def object_key_for_path("));
        expect(backendPath.slice(0, backendPath.indexOf("\ndef "))).toContain(
            "relative = relative[1:]"
        );
        for (const use of [
            "if object_key_for_path(target, old) in keys_with_versions",
            "return object_key_for_path(target, old) in target_keys",
            "if legacy_form(old) is not None:",
        ]) {
            expect(module).toContain(use);
        }
        expect(module).not.toMatch(/target \+ old\.lstrip\('\/'\)|old\.lstrip\('\/'\)\] =/);
    });

    it("ships with no resolutions, so a copied config never moves a file", () => {
        expect(config.asset_location_file_to_folder_resolutions).toBeNull();
        expect(config.dry_run).toBe(true);
    });

    it("names the backend's preview pattern, vector keys, touch record and move metadata", () => {
        const pairs: ReadonlyArray<[string, string, string]> = [
            ["PREVIEW_FILE_PATTERN", "common/s3PathPatterns.py", "PREVIEW_FILE_PATTERN"],
            ["VECTOR_PARTITION_KEY", "common/vectorsearch/vectorStore.py", "PARTITION_KEY"],
            ["VECTOR_SORT_KEY", "common/vectorsearch/vectorStore.py", "SORT_KEY"],
            [
                "VECTOR_FILE_PATH_KEY_BUDGET",
                "common/indexing/documentIds.py",
                "FILE_PATH_KEY_BUDGET",
            ],
            ["VECTOR_SORT_KEY_MAX_BYTES", "common/indexing/documentIds.py", "SORT_KEY_MAX_BYTES"],
            [
                "REINDEX_METADATA_RECORD_KEY",
                "common/dynamoDbMetadataKeys.py",
                "REINDEX_METADATA_RECORD_KEY",
            ],
            [
                "CHANGE_SOURCE_METADATA_KEY",
                "common/s3MetadataKeys.py",
                "VAMS_CHANGE_SOURCE_METADATA_KEY",
            ],
            [
                "CHANGE_USER_ID_METADATA_KEY",
                "common/s3MetadataKeys.py",
                "VAMS_CHANGE_USER_ID_METADATA_KEY",
            ],
            [
                "CHANGE_FILE_VERSION_FROM_METADATA_KEY",
                "common/s3MetadataKeys.py",
                "VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY",
            ],
            [
                "CHANGE_SOURCE_FILE_RELOCATE",
                "common/s3MetadataKeys.py",
                "VAMS_CHANGE_SOURCE_FILE_RELOCATE",
            ],
            ["HISTORY_CHANGE_SOURCE", "common/assetHistory.py", "CHANGE_SOURCE_EDIT"],
        ];
        for (const [name, file, backendName] of pairs) {
            expect({ name, value: pyString(module, name) }).toEqual({
                name,
                value: pyString(backend(file), backendName),
            });
        }
        // The row marker assetLocationFolderKeys writes, which the step waits for
        expect(pyString(module, "COPY_DONE_ATTRIBUTE")).toBe(
            pyString(script, "COPY_DONE_ATTRIBUTE")
        );
    });

    it("never deletes an object version, and writes every record move as put-if-absent", () => {
        expect(module).not.toMatch(/delete_object_version|delete_objects\(/);
        for (const call of module.match(/\.delete_object\([^)]*\)/g) ?? []) {
            expect(call).not.toContain("VersionId");
        }
        // A record is moved as assetLocationFolderKeys moves one: read again consistently, compared with the
        // listing, written only where no record is, and deleted only while every attribute is as read.
        const move = module.slice(
            module.indexOf("def move_record("),
            module.indexOf("\ndef move_item(")
        );
        expect(move).toContain("ConsistentRead=True");
        expect(move).toContain("if current != item:");
        expect(move).toContain("'ConditionExpression': 'attribute_not_exists(#k)'");
        expect(move).toContain('unchanged.append(f"#a{index} = :a{index}")');
        expect(move).toContain("return 'changed' if reasons[1].get('Item') else 'gone'");
        const step0 = script.slice(script.indexOf("def move_record("));
        expect(step0.slice(0, 3000)).toContain('unchanged.append(f"#a{index} = :a{index}")');
        // The row flip is conditional on the file key and on no conversion having started.
        const flip = module.slice(module.indexOf("def flip_row("));
        expect(flip.slice(0, 1600)).toContain("#al.#k = :from AND attribute_not_exists(#c)");
    });

    /** boto3 call -> the IAM action the README must name for it. */
    const MODULE_CALLS: ReadonlyArray<[RegExp, string]> = [
        ...CALL_TO_ACTION,
        [/\.copy\(/, "s3:PutObject"],
        [/\.copy\(/, "s3:GetObjectVersion"],
        [/\.get_bucket_versioning\(/, "s3:GetBucketVersioning"],
        [/\.get_object_tagging\(/, "s3:GetObjectVersionTagging"],
        [/\.put_object_tagging\(/, "s3:PutObjectVersionTagging"],
        [/\.list_objects_v2\(/, "s3:ListBucket"],
        [/\.head_object\(/, "s3:GetObject"],
    ];

    it("documents an IAM action for every call the step module makes", () => {
        const missing = MODULE_CALLS.filter(
            ([call, action]) => call.test(module) && !readme.includes(action)
        ).map(([, action]) => action);
        expect(missing).toEqual([]);
        // control: the module makes the calls the list expects
        expect(MODULE_CALLS.filter(([call]) => call.test(module)).length).toBeGreaterThan(10);
    });

    it("is documented in the README and the upgrade guide, with its report name", () => {
        expect(readme).toContain(
            "### Step 0b — Asset location file to folder (`assetLocationFileToFolder`)"
        );
        expect(readme).toContain(
            "assetLocationFileToFolder-<dry-run|execute>-report-<UTC time>.jsonl"
        );
        expect(readme).toContain("asset_location_file_to_folder_resolutions");
        expect(module).toMatch(/f"\{STEP\}-\{mode\}-report-\{stamp\}\.jsonl"/);
        const page = fs.readFileSync(
            path.join(DOCS_ROOT, "deployment/update-the-solution.md"),
            "utf8"
        );
        const checklist = page.slice(page.indexOf("\n## Breaking changes checklist\n"));
        expect(checklist).toMatch(
            /\| Existing-key assets are folders only[^\n]*\| v2\.6 to v2\.7 /
        );
    });
});

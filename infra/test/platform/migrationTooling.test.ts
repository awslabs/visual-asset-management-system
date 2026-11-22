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
        // Only assetLocationFolderKeys reads Amazon S3: one HeadObject, the versions of one exact key at a
        // time, and one single-key listing under '{key}/'.
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
        const versionListings = locationStep.match(/\.list_object_versions\(/g) ?? [];
        expect(versionListings).toHaveLength(1);
        const exact = locationStep.slice(locationStep.indexOf("def exact_key_versions("));
        expect(exact.slice(0, 900)).toContain("if entry['Key'] == key");
        expect(script).not.toMatch(/auxiliary/i);
    });

    it("moves the copy's records without overwriting, and leaves vectors and execution history", () => {
        const move = script.slice(script.indexOf("def move_record("));
        expect(move.slice(0, 2000)).toContain("'ConditionExpression': 'attribute_not_exists(#k)'");
        expect(move.slice(0, 2000)).toContain("'ConditionExpression': 'attribute_exists(#k)'");
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

    it("runs assetLocationFolderKeys first, gated, with a report and a checkpoint", () => {
        expect(tuple(script, "STEP_CHOICES")[0]).toBe("assetLocationFolderKeys");
        const body = script.slice(script.indexOf("def main("));
        expect(body.indexOf("run_asset_location_folder_keys_step(")).toBeGreaterThan(-1);
        expect(body.indexOf("run_asset_location_folder_keys_step(")).toBeLessThan(
            body.indexOf("run_orphaned_triggers_step(")
        );
        expect(body).toMatch(/gated=run_location_keys or /);
        expect(script).toContain("ConditionExpression='#loc.#key = :old'");
        expect(script).toMatch(/parser\.add_argument\('--resume'/);
        expect(readme).toContain("assetLocationFolderKeys-dry-run-report.jsonl");
        expect(readme).toContain("--resume");
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
        expect(steps[0]).toBe("assetLocationFolderKeys");
        expect(body).toMatch(
            /if run_asset_location_index and location_keys_failed and not dry_run:/
        );
        // A key the folder-keys step fixes after the index ran keeps its indexed key in step.
        const append = script.slice(script.indexOf("def append_location_slash("));
        expect(append.slice(0, append.indexOf("\ndef "))).toContain("ASSET_LOCATION_KEY_ATTRIBUTE");
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
        const block = storage.slice(storage.indexOf('indexName: "AssetLocationGSI"'));
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

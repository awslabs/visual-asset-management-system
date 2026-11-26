/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The v2.6 to v2.7 migration's `bucketReadConstraints` step (issue #1978).
 *
 * From v2.7 a role lists a bucket (GET /buckets) and puts a database on it (POST /database, PUT
 * /database/{databaseId}) only with a `bucket` constraint, which a role stored before 2.7.0 does not have.
 * The step gives each role whose own constraints allow one of those routes, and that has no bucket
 * constraint, the one the shipped role templates carry, and nothing else. The real step and the real
 * main() run here against a fake constraints table and database table
 * (fixtures/bucketReadConstraintsHarness.py, standard library only); that its route decisions equal the
 * authorizer's is pinned against the real Casbin enforcer in
 * backend/tests/handlers/authz/test_bucket_read_migration_parity.py.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawnSync } from "child_process";

const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const UPGRADE_DIR = path.join(REPO_ROOT, "infra/deploymentDataMigration/v2.6_to_v2.7/upgrade");
const HARNESS = path.join(__dirname, "fixtures", "bucketReadConstraintsHarness.py");
const TEMPLATES_DIR = path.join(REPO_ROOT, "documentation/permissionsTemplates");
const DOCS = path.join(REPO_ROOT, "documentation/docusaurus-site/docs");
const OVERLAY = "deny-tagged-assets.json";

interface Put {
    item: Record<string, string>;
    condition: string;
    table: string;
}

interface StepRun {
    counts: Record<string, number>;
    routes: Record<string, number>;
    databaseScans: number;
    report: string;
    reportLines: Array<Record<string, any>>;
    puts: Put[];
    queries: string[];
    existingItemsUnchanged: boolean;
    itemCount: number;
    exitStatus: number;
    summary: string[];
}

interface MainRun {
    exit: number;
    puts: string[];
    log: string[];
}

interface Results {
    template: Record<string, any>;
    moduleImports: string[];
    dryRun: StepRun;
    execute: StepRun;
    rerun: StepRun;
    indexLag: StepRun;
    writeFails: StepRun;
    mainDryRun: MainRun;
    mainExecute: MainRun;
    mainExecuteWrongAccount: MainRun;
}

const ADDED_ROLES = ["creator", "db-editor", "legacy-admin", "list-route"];
const LIST = "GET /buckets";
const CREATE = "POST /database";
const UPDATE = "PUT /database/{databaseId}";
const SEEDED_ITEMS = 19;

function outcomes(run: StepRun): Record<string, string> {
    return Object.fromEntries(
        run.reportLines.map((line) => [line.role ?? `user:${line.user}`, line.outcome])
    );
}

let results: Results;
let scratch: string;
beforeAll(() => {
    scratch = fs.mkdtempSync(path.join(os.tmpdir(), "bucket-read-constraints-"));
    const run = spawnSync("python3", [HARNESS, UPGRADE_DIR, scratch], { encoding: "utf8" });
    if (run.status !== 0) {
        throw new Error(`the harness failed (${run.status}): ${run.stderr}`);
    }
    results = JSON.parse(run.stdout);
});
afterAll(() => {
    fs.rmSync(scratch, { recursive: true, force: true });
});

describe("the bucketReadConstraints step writes the shipped role templates' bucket read", () => {
    const templates = fs.readdirSync(TEMPLATES_DIR).filter((name) => name.endsWith(".json"));

    it.each(templates.filter((name) => name !== OVERLAY))(
        "%s carries exactly the constraint the step writes",
        (name) => {
            const document = JSON.parse(fs.readFileSync(path.join(TEMPLATES_DIR, name), "utf8"));
            const bucket = document.constraints.filter((c: any) => c.objectType === "bucket");
            expect(bucket).toEqual([results.template]);
        }
    );

    it("covers the eleven role templates, and the deny overlay carries no bucket constraint", () => {
        expect(templates).toHaveLength(12);
        const overlay = JSON.parse(fs.readFileSync(path.join(TEMPLATES_DIR, OVERLAY), "utf8"));
        expect(overlay.constraints.filter((c: any) => c.objectType === "bucket")).toEqual([]);
    });

    it("writes it as a template import stores it, under an id derived from the role", () => {
        const put = results.execute.puts.find((p) => p.item.groupId === "legacy-admin") as Put;
        expect(put).toBeDefined();
        expect(put.table).toBe("vams-test-constraintsStorageTable");
        expect(put.condition).toBe("attribute_not_exists(constraintId)");
        expect(put.item.constraintId).toMatch(/^[0-9a-f-]{36}#group#legacy-admin$/);
        expect(put.item.groupId).toBe("legacy-admin");
        expect(put.item.name).toBe("legacy-admin-buckets");
        expect(put.item.description).toBe(results.template.description);
        expect(put.item.objectType).toBe("bucket");
        expect(JSON.parse(put.item.criteriaAnd)).toEqual(results.template.criteriaAnd);
        expect(JSON.parse(put.item.criteriaOr)).toEqual([]);
        expect(JSON.parse(put.item.userPermissions)).toEqual([]);
        expect(JSON.parse(put.item.groupPermissions).map(({ id, ...rest }: any) => rest)).toEqual([
            { groupId: "legacy-admin", permission: "GET", permissionType: "allow" },
        ]);
        expect(put.item.createdBy).toBe("SYSTEM_USER");
        expect(put.item.modifiedBy).toBe("SYSTEM_USER");
    });

    it("imports no backend code", () => {
        expect(results.moduleImports).toEqual([]);
        const source = fs.readFileSync(
            path.join(UPGRADE_DIR, "bucket_read_constraints.py"),
            "utf8"
        );
        expect(source).not.toMatch(/^\s*(from|import) (backend|boto3)\b/m);
    });
});

describe("what the step changes", () => {
    it("reads the api and bucket constraints through ObjectTypeIndex and the database ids, every page", () => {
        const queries = results.dryRun.queries;
        expect(new Set(queries)).toEqual(new Set(["api", "bucket"]));
        // Two items a page: the stored items take more than one page per type.
        expect(queries.filter((t) => t === "api").length).toBeGreaterThan(1);
        // Three database rows, two a page; the archived one is left out.
        expect(results.dryRun.databaseScans).toBe(2);
        expect(results.dryRun.counts.databasesChecked).toBe(2);
    });

    it("a dry run reports every role and writes nothing", () => {
        const run = results.dryRun;
        expect(run.puts).toEqual([]);
        expect(run.existingItemsUnchanged).toBe(true);
        expect(run.report).toMatch(/^bucketReadConstraints-dry-run-report-\d{8}T\d{6}Z\.jsonl$/);
        expect(outcomes(run)).toEqual({
            "legacy-admin": "wouldAdd",
            "list-route": "wouldAdd",
            creator: "wouldAdd",
            "db-editor": "wouldAdd",
            "has-bucket": "alreadyHasBucket",
            reader: "noQualifyingRoute",
            denied: "noQualifyingRoute",
            "post-only": "noQualifyingRoute",
            "future-db-editor": "noQualifyingRoute",
            "archived-db-editor": "noQualifyingRoute",
            "create-denied": "noQualifyingRoute",
            broken: "unreadable",
            "user:alice": "userGrantNotChanged",
            "user:bob": "userGrantNotChanged",
        });
        expect(run.counts).toMatchObject({
            rolesExamined: 12,
            rolesQualified: 5,
            wouldAdd: 4,
            added: 0,
            alreadyHasBucket: 1,
            noQualifyingRoute: 6,
            unreadable: 1,
            usersQualified: 2,
        });
        expect(run.routes).toEqual({ [LIST]: 3, [CREATE]: 2, [UPDATE]: 2 });
        expect(run.exitStatus).toBe(0);
    });

    it("names the routes that qualified each role, and the database the update route was allowed on", () => {
        const byRole = Object.fromEntries(
            results.dryRun.reportLines.map((line) => [line.role ?? `user:${line.user}`, line])
        );
        expect(byRole["legacy-admin"].qualifiedBy).toEqual([LIST, CREATE, UPDATE]);
        expect(byRole["legacy-admin"].updateAllowedOnDatabase).toBe("db-a");
        expect(byRole["list-route"].qualifiedBy).toEqual([LIST]);
        expect(byRole.creator.qualifiedBy).toEqual([CREATE]);
        expect(byRole.creator.grantedBy).toEqual({ [CREATE]: ["creator-api"] });
        expect(byRole["db-editor"].qualifiedBy).toEqual([UPDATE]);
        expect(byRole["db-editor"].updateAllowedOnDatabase).toBe("db-a");
        expect(byRole["user:bob"].qualifiedBy).toEqual([UPDATE]);
        expect(byRole["user:bob"].updateAllowedOnDatabase).toBe("db-b");
        // Update only on a database the deployment does not hold, or holds archived: not qualified.
        expect(byRole["future-db-editor"].qualifiedBy).toBeUndefined();
        expect(byRole["archived-db-editor"].qualifiedBy).toBeUndefined();
        expect(byRole["create-denied"].deniedBy).toEqual({ [CREATE]: ["create-denied-deny"] });
    });

    it("a real run adds the bucket read only to the roles allowed a bucket route that had none", () => {
        const run = results.execute;
        expect(run.puts.map((p) => p.item.groupId)).toEqual(ADDED_ROLES);
        expect(run.existingItemsUnchanged).toBe(true);
        expect(run.itemCount).toBe(SEEDED_ITEMS + ADDED_ROLES.length);
        expect(run.counts).toMatchObject({ added: 4, wouldAdd: 0, failed: 0 });
        expect(run.report).toMatch(/^bucketReadConstraints-execute-report-\d{8}T\d{6}Z\.jsonl$/);
        const added = run.reportLines.filter((line) => line.outcome === "added");
        expect(added.map((line) => line.constraintId)).toEqual(
            run.puts.map((p) => p.item.constraintId)
        );
        // The audit trail: which stored constraint gave the role the route.
        expect(added.map((line) => line.grantedBy)).toEqual([
            { [CREATE]: ["creator-api"] },
            { [UPDATE]: ["db-editor-api"] },
            {
                [LIST]: ["legacy-admin-api"],
                [CREATE]: ["legacy-admin-api"],
                [UPDATE]: ["legacy-admin-api"],
            },
            { [LIST]: ["list-route-api"] },
        ]);
        expect(run.reportLines.find((line) => line.role === "denied")?.deniedBy).toEqual({
            [LIST]: ["denied-buckets"],
        });
    });

    it("leaves a role that already has a bucket constraint, a user grant and an unreadable role alone", () => {
        const written = results.execute.puts.map((p) => p.item.groupId);
        for (const left of [
            "has-bucket",
            "alice",
            "bob",
            "broken",
            "denied",
            "post-only",
            "reader",
            "future-db-editor",
            "archived-db-editor",
            "create-denied",
        ]) {
            expect(written).not.toContain(left);
        }
        expect(results.execute.reportLines.find((line) => line.role === "broken")?.error).toMatch(
            /broken-api: criteriaAnd is not valid JSON/
        );
    });

    it("a repeat writes nothing", () => {
        const run = results.rerun;
        expect(run.puts).toEqual([]);
        expect(run.counts).toMatchObject({ alreadyHasBucket: 5, added: 0, failed: 0 });
        expect(run.exitStatus).toBe(0);
    });

    it("a repeat the index has not caught up with is refused by the condition and overwrites nothing", () => {
        const run = results.indexLag;
        expect(run.puts.map((p) => p.item.groupId)).toEqual(ADDED_ROLES);
        expect(run.counts).toMatchObject({ alreadyAdded: 4, added: 0, failed: 0 });
        expect(run.existingItemsUnchanged).toBe(true);
        expect(run.exitStatus).toBe(0);
    });

    it("a failed write fails the step and is reported", () => {
        const run = results.writeFails;
        expect(run.counts).toMatchObject({ failed: 4, added: 0 });
        expect(run.exitStatus).toBe(1);
        expect(
            run.reportLines.filter((line) => line.outcome === "failed").map((line) => line.error)
        ).toEqual(Array(4).fill("AccessDeniedException"));
    });

    it("prints a summary that counts each outcome", () => {
        const summary = results.execute.summary.join("\n");
        expect(summary).toMatch(/Roles allowed a bucket route:\s+5/);
        expect(summary).toMatch(/allowed GET \/buckets:\s+3/);
        expect(summary).toMatch(/allowed POST \/database:\s+2/);
        expect(summary).toMatch(/allowed PUT \/database\/\{databaseId\}:\s+2/);
        expect(summary).toMatch(/Databases the update route was checked on:\s+2/);
        expect(summary).toMatch(/given the bucket read \(added\):\s+4/);
        expect(summary).toMatch(/\(unreadable\):\s+1/);
        expect(summary).toMatch(/\(userGrantNotChanged\):\s+2/);
        expect(results.dryRun.summary.join("\n")).toMatch(
            /would get the bucket read \(wouldAdd\):\s+4/
        );
    });
});

describe("the step in the migration script", () => {
    const script = fs.readFileSync(path.join(UPGRADE_DIR, "v2.6_to_v2.7_migration.py"), "utf8");
    const steps = Array.from(
        (/^STEP_CHOICES\s*=\s*\(([^)]*)\)/m.exec(script)?.[1] ?? "").matchAll(/'([A-Za-z]+)'/g)
    ).map((m) => m[1]);
    const main = script.slice(script.indexOf("\ndef main("));

    it("runs after orphanedTriggers and before the backfills, and the location steps never hold it", () => {
        expect(steps.indexOf("bucketReadConstraints")).toBe(steps.indexOf("orphanedTriggers") + 1);
        expect(steps.indexOf("bucketReadConstraints")).toBeLessThan(
            steps.indexOf("vectorBackfill")
        );
        const call = main.indexOf("rc = run_bucket_read_constraints_step(");
        expect(call).toBeGreaterThan(main.indexOf("rc = run_orphaned_triggers_step("));
        expect(call).toBeLessThan(main.indexOf("run_vector_backfill_step("));
        expect(main).toContain("    if run_bucket_read_constraints:\n");
        expect(main).not.toMatch(/run_bucket_read_constraints and not location_keys_unfinished/);
    });

    it("runs on its own as a dry run by default", () => {
        const run = results.mainDryRun;
        expect(run.exit).toBe(0);
        expect(run.puts).toEqual([]);
        expect(run.log).toContain("  Steps:        bucketReadConstraints");
        expect(run.log).toContain("  bucketReadConstraints: completed");
    });

    it("confirms a real run against the account and names what it writes", () => {
        const run = results.mainExecute;
        expect(run.exit).toBe(0);
        expect(run.puts).toEqual(ADDED_ROLES);
        expect(run.log).toContain(
            "  This run will: add a bucket read constraint to each role allowed GET /buckets, POST /database " +
                "or PUT /database/{databaseId} that has no bucket constraint (bucketReadConstraints)"
        );
        const refused = results.mainExecuteWrongAccount;
        expect(refused.exit).toBe(1);
        expect(refused.puts).toEqual([]);
    });
});

describe("the step is documented where the operator looks", () => {
    const readme = fs.readFileSync(
        path.join(UPGRADE_DIR, "v2.6_to_v2.7_migration_README.md"),
        "utf8"
    );
    const guide = fs.readFileSync(path.join(DOCS, "deployment/update-the-solution.md"), "utf8");
    const changelog = fs.readFileSync(path.join(REPO_ROOT, "CHANGELOG.md"), "utf8");
    const revisions = fs.readFileSync(path.join(DOCS, "additional/revisions.md"), "utf8");
    const config = JSON.parse(
        fs.readFileSync(path.join(UPGRADE_DIR, "v2.6_to_v2.7_migration_config.json"), "utf8")
    );

    it("names the IAM actions the step needs", () => {
        const iam = readme.slice(
            readme.indexOf("### Required IAM Permissions"),
            readme.indexOf("### Python Dependencies")
        );
        expect(iam).toMatch(
            /bucketReadConstraints step: DynamoDB `Query` on the\s+constraints table's `ObjectTypeIndex`, `Scan` on the database table and `PutItem` on the constraints table/
        );
    });

    it("tells an operator who skips it to re-import the templates", () => {
        expect(readme).toMatch(/\*\*If you skip it\*\*, re-import the permission\s+templates/);
        expect(guide).toMatch(/If you skipped the step, re-import the templates/);
        for (const page of [changelog, revisions]) {
            expect(page).toContain(
                "Roles stored before 2.7.0 keep what they had: the v2.6 to v2.7 migration's " +
                    "`bucketReadConstraints` step adds the templates' bucket read to every role allowed " +
                    "`GET /buckets`, `POST /database` or `PUT /database/{databaseId}`"
            );
            expect(page).toContain("if you skip the step, re-import the templates");
        }
    });

    it("names the three routes, and keeps the manual step 8 only for roles it cannot read", () => {
        expect(readme).toMatch(
            /-\s+`GET` on `\/buckets`;\s+-\s+`POST` on `\/database`;\s+-\s+`PUT` on `\/database\/<databaseId>`/
        );
        const stepEight = guide.slice(
            guide.indexOf("\n8.  Read the `bucketReadConstraints` report.")
        );
        expect(stepEight.length).toBeLessThan(guide.length);
        expect(stepEight.slice(0, 1200)).toMatch(
            /every role allowed `GET \/buckets`, `POST \/database` or `PUT \/database\/\{databaseId\}` that had none, so those roles need nothing more/
        );
        expect(stepEight.slice(0, 1200)).toMatch(/only to each role it reports `unreadable`/);
        expect(guide).not.toContain(
            "8.  Give each custom role that lists buckets or creates or moves databases"
        );
    });

    it("has its config keys in the shipped config", () => {
        expect(config).toHaveProperty("constraints_storage_table_name", null);
        expect(config).toHaveProperty("database_storage_table_name", null);
        expect(config).toHaveProperty("bucket_read_constraints_output_dir", null);
    });
});

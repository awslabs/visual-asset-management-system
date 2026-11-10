/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Download as Zip (app.zipDownloads) in the synthesized templates.
 *
 * Always deployed, whatever `enabled` says: the job table, the auxiliary-bucket expiry rules on
 * `vams.preparedDownloads/` (including the one-day abort of incomplete uploads), the table's SSM
 * name, and one zip Lambda, the zip download service, which serves the six routes and the delayed
 * sweep's passes. Only with `enabled`: the zipArchive system pipeline stack, the ZIPDOWNLOADS feature
 * switch, the sweep state machine that invokes the service, and its two cross-call grants (launch
 * and abort), each on an exact function ARN. In no cell is the service a terminal callback: no
 * terminal writer names it and no role but the sweep's may invoke it.
 *
 * Retention is a fixed 7 days (ZIP_DOWNLOAD_RETENTION_DAYS), not configuration: the feature-on cells
 * synthesize a config that still carries the removed `retentionDays` key (14), which is ignored.
 */

import {
    ALL_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    synthTemplate,
} from "../support/templateSynth";
import * as fs from "fs";
import * as path from "path";
import {
    ZIP_DOWNLOAD_ROUTES,
    ZIP_DOWNLOAD_SWEEP_RETRY,
    ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS,
} from "../../lib/lambdaBuilder/assetFunctions";
import { ZIP_DOWNLOAD_RETENTION_DAYS } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";

jest.setTimeout(900_000);

const SERVICE_HANDLER = "handlers.assets.zipDownloadService.lambda_handler";
const ARCHIVE_EXECUTE_HANDLER = "vamsExecuteZipArchivePipeline.lambda_handler";
/** The terminal writers' callback registry variable, removed with the registry (#1135). */
const CALLBACK_REGISTRY_ENV = "CALLBACK_FUNCTIONS_BY_SYSTEM_TRIGGER";

/** Feature on, from a config written before retention became fixed (its retentionDays is ignored). */
function enabledMutate(legacyRetentionDays: number) {
    return (c: any) => {
        c.app.zipDownloads.enabled = true;
        c.app.zipDownloads.retentionDays = legacyRetentionDays;
        c.app.zipDownloads.computeVcpu = 8;
        c.app.zipDownloads.computeMemoryGiB = 24;
        c.app.useGlobalVpc.enabled = true;
    };
}

const synthOff = (name: TemplateName): SynthResult => synthTemplate(name);
const synthOn = (name: TemplateName): SynthResult =>
    synthTemplate(name, {
        mutate: enabledMutate(14),
        mutateKey: "zip-downloads-on-legacy-retention-14",
    });

function functionByHandler(synth: SynthResult, handler: string): Resource {
    const found = synth.where("AWS::Lambda::Function", (r) => r.properties.Handler === handler);
    expect(found).toHaveLength(1);
    return found[0];
}

function envOf(fn: Resource): Record<string, unknown> {
    return fn.properties.Environment?.Variables ?? {};
}

/** Every statement of every inline policy attached to a function's role. */
function roleStatements(synth: SynthResult, fn: Resource): any[] {
    const roleRef = JSON.stringify(fn.properties.Role);
    const roleId = (fn.properties.Role?.["Fn::GetAtt"] ?? [])[0];
    return synth
        .ofType("AWS::IAM::Policy")
        .filter((p) => p.stack === fn.stack)
        .filter((p) => JSON.stringify(p.properties.Roles ?? []).includes(roleId ?? roleRef))
        .flatMap((p) => p.properties.PolicyDocument.Statement as any[]);
}

function actionsOf(statement: any): string[] {
    return ([] as string[]).concat(statement.Action);
}

/** Every Lambda whose handler belongs to Download as Zip's API side. */
function zipApiFunctions(synth: SynthResult): Resource[] {
    return synth.where("AWS::Lambda::Function", (r) =>
        /(^|\.)zipDownload|handlers\.zipDownloads\./.test(String(r.properties.Handler ?? ""))
    );
}

/**
 * One Lambda serves the routes and the sweep: no second zip function, no handler under the removed
 * handlers/zipDownloads package, and no role or log group of the removed callback function.
 */
function expectOneZipFunction(synth: SynthResult): void {
    const zip = zipApiFunctions(synth);
    expect(zip.map((r) => r.properties.Handler)).toEqual([SERVICE_HANDLER]);
    expect(synth.resources.filter((r) => /^zipDownloadCallback/i.test(r.logicalId))).toEqual([]);
    expect(synth.grep("zipDownloadCallback")).toEqual([]);
}

/**
 * No function carries a terminal-callback registry variable (the workflow core keeps none since #1135,
 * and the zip download service never registers one), and the only IAM policy that grants
 * lambda:InvokeFunction on the service is the one on the sweep state machine's role (`sweepRoleId`,
 * or none when the sweep is not deployed).
 */
function expectNoZipCallback(synth: SynthResult, sweepRoleId?: string): void {
    const service = functionByHandler(synth, SERVICE_HANDLER);
    const serviceId = service.logicalId;
    const naming = synth.where(
        "AWS::Lambda::Function",
        (r) => envOf(r)[CALLBACK_REGISTRY_ENV] !== undefined
    );
    expect(naming).toEqual([]);
    const invokers = [
        ...synth.ofType("AWS::IAM::Policy"),
        ...synth.ofType("AWS::IAM::ManagedPolicy"),
    ]
        .filter((p) => p.stack === service.stack)
        .filter((p) =>
            (p.properties.PolicyDocument.Statement as any[]).some(
                (st) =>
                    actionsOf(st).includes("lambda:InvokeFunction") &&
                    [].concat(st.Resource).some((r) => SynthResult.flatten(r).includes(serviceId))
            )
        );
    const roles = invokers.flatMap((p) =>
        (p.properties.Roles ?? []).map((r: { Ref?: string }) => r?.Ref ?? JSON.stringify(r))
    );
    expect(roles).toEqual(sweepRoleId ? [sweepRoleId] : []);
}

/** Every S3 action that writes, deletes or aborts is granted only under vams.preparedDownloads/. */
function expectS3MutationsPrefixScoped(statements: any[]): void {
    const mutations = statements.filter((s) =>
        actionsOf(s).some((a) => /^s3:(Put|Delete|Abort|Restore)/.test(a))
    );
    expect(mutations.length).toBeGreaterThan(0);
    for (const s of mutations) {
        for (const r of [].concat(s.Resource)) {
            expect(SynthResult.flatten(r)).toMatch(/\/vams\.preparedDownloads\/\*$/);
        }
    }
}

describe.each(ALL_TEMPLATES)("always deployed (%s, feature off as shipped)", (name) => {
    let synth: SynthResult;
    beforeAll(() => {
        synth = synthOff(name);
    });

    test("the job table: userId/requestId keys, UserAssetIndex, TTL, PITR, retained", () => {
        const tables = synth.where("AWS::DynamoDB::Table", (r) =>
            r.logicalId.startsWith("ZipDownloadRequestsStorageTable")
        );
        expect(tables).toHaveLength(1);
        const table = tables[0];
        expect(table.properties.KeySchema).toEqual([
            { AttributeName: "userId", KeyType: "HASH" },
            { AttributeName: "requestId", KeyType: "RANGE" },
        ]);
        expect(table.properties.TimeToLiveSpecification).toEqual({
            AttributeName: "expiresAt",
            Enabled: true,
        });
        expect(table.properties.BillingMode).toBe("PAY_PER_REQUEST");
        expect(table.properties.PointInTimeRecoverySpecification?.PointInTimeRecoveryEnabled).toBe(
            true
        );
        expect(table.raw.DeletionPolicy).toBe("Retain");
        expect(table.properties.TableName).toBeUndefined();
        const indexes = table.properties.GlobalSecondaryIndexes as any[];
        expect(indexes).toHaveLength(1);
        expect(indexes[0].IndexName).toBe("UserAssetIndex");
        expect(indexes[0].KeySchema).toEqual([
            { AttributeName: "userAssetKey", KeyType: "HASH" },
            { AttributeName: "requestId", KeyType: "RANGE" },
        ]);
        expect(indexes[0].Projection).toEqual({ ProjectionType: "ALL" });
    });

    test("the table name is published to SSM for the handlers and migration tooling", () => {
        expect(
            synth.where("AWS::SSM::Parameter", (r) =>
                SynthResult.flatten(r.properties.Name).endsWith(
                    "/resourceNames/dynamoTables/zipDownloadRequestsStorage"
                )
            )
        ).toHaveLength(1);
    });

    test("the auxiliary bucket expires vams.preparedDownloads/ after 7 days, and its markers", () => {
        const buckets = synth.where("AWS::S3::Bucket", (r) =>
            JSON.stringify(r.properties.LifecycleConfiguration ?? {}).includes(
                "PreparedDownloadsExpiration"
            )
        );
        expect(buckets).toHaveLength(1);
        const rules = buckets[0].properties.LifecycleConfiguration.Rules as any[];
        const expiry = rules.find((r) => r.Id === "PreparedDownloadsExpiration");
        expect(expiry).toMatchObject({
            Status: "Enabled",
            Prefix: "vams.preparedDownloads/",
            ExpirationInDays: 7,
            NoncurrentVersionExpiration: { NoncurrentDays: 1 },
            AbortIncompleteMultipartUpload: { DaysAfterInitiation: 1 },
        });
        const markers = rules.find((r) => r.Id === "PreparedDownloadsDeleteMarkers");
        expect(markers).toMatchObject({
            Status: "Enabled",
            Prefix: "vams.preparedDownloads/",
            ExpiredObjectDeleteMarker: true,
        });
        // A database id (^[-_a-zA-Z0-9]{3,63}$) has no dot, so the {databaseId}/ data of a database
        // named preparedDownloads is outside both rules.
        for (const rule of [expiry, markers]) {
            expect("preparedDownloads/xasset1/scan.e57/preview/PotreeViewer/r.json").not.toMatch(
                new RegExp(`^${rule.Prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`)
            );
        }
    });

    test("the auxiliary bucket sends no object events (no bucket sync, indexer or trigger reads it)", () => {
        const aux = synth.where("AWS::S3::Bucket", (r) =>
            JSON.stringify(r.properties.LifecycleConfiguration ?? {}).includes(
                "PreparedDownloadsExpiration"
            )
        )[0];
        expect(aux.properties.NotificationConfiguration).toBeUndefined();
        const notifications = synth
            .ofType("Custom::S3BucketNotifications")
            .filter(
                (r) => r.stack === aux.stack && JSON.stringify(r.properties).includes(aux.logicalId)
            );
        expect(notifications).toEqual([]);
    });

    test("the service is deployed off, with no cross-call name or grant", () => {
        const service = functionByHandler(synth, SERVICE_HANDLER);
        const env = envOf(service);
        expect(env.ZIPDOWNLOADS_ENABLED).toBe("false");
        // Retention is a backend constant; CDK passes no retention variable.
        expect(env.ZIP_DOWNLOAD_RETENTION_DAYS).toBeUndefined();
        expect(env.ZIP_ARCHIVE_WORKFLOW_DATABASE_ID).toBe("GLOBAL");
        expect(env.ZIP_ARCHIVE_WORKFLOW_ID).toBe("utility-zip-archive");
        expect(env.PRESIGNED_URL_TIMEOUT_SECONDS).toBe("900");
        expect(env.EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME).toBeUndefined();
        expect(env.EXECUTION_SERVICE_FUNCTION_NAME).toBeUndefined();
        const invoke = roleStatements(synth, service).filter((s) =>
            actionsOf(s).includes("lambda:InvokeFunction")
        );
        expect(invoke).toEqual([]);
    });

    test("one zip Lambda serves the routes and the sweep", () => {
        expectOneZipFunction(synth);
    });

    test("the zip download service is no terminal callback, and nothing may invoke it", () => {
        expectNoZipCallback(synth);
    });

    test("no zipArchive pipeline and no ZIPDOWNLOADS switch", () => {
        expect(
            synth.where(
                "AWS::Lambda::Function",
                (r) => r.properties.Handler === ARCHIVE_EXECUTE_HANDLER
            )
        ).toEqual([]);
        expect(
            synth.ofType("AWS::Batch::JobDefinition").filter((r) => /ZipArchive/.test(r.stack))
        ).toEqual([]);
        expect(synth.grep("ZIPDOWNLOADS").filter((r) => !r.type.startsWith("AWS::Lambda"))).toEqual(
            []
        );
    });

    test("the service's job-prefix grants are scoped to vams.preparedDownloads/", () => {
        const service = functionByHandler(synth, SERVICE_HANDLER);
        const statements = roleStatements(synth, service);
        const put = statements.filter((s) => actionsOf(s).includes("s3:PutObject"));
        expect(put).toHaveLength(1);
        expect(SynthResult.flatten(put[0].Resource)).toMatch(/\/vams\.preparedDownloads\/\*$/);
        const list = statements.find((s) => actionsOf(s).includes("s3:ListBucketVersions"));
        expect(list.Condition).toEqual({
            StringLike: { "s3:prefix": ["vams.preparedDownloads/*"] },
        });
    });

    test("the service deletes and lists versions only under vams.preparedDownloads/ (#1125 D13)", () => {
        const service = functionByHandler(synth, SERVICE_HANDLER);
        const statements = roleStatements(synth, service);
        expectS3MutationsPrefixScoped(statements);
        const deletes = statements.filter((s) =>
            actionsOf(s).some((a) => a.startsWith("s3:Delete"))
        );
        expect(deletes).toHaveLength(1);
        expect(actionsOf(deletes[0]).sort()).toEqual(
            [
                "s3:AbortMultipartUpload",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:GetObject",
                "s3:ListMultipartUploadParts",
                "s3:PutObject",
            ].sort()
        );
        expect(SynthResult.flatten(deletes[0].Resource)).toMatch(/\/vams\.preparedDownloads\/\*$/);
        const list = statements.find((s) => actionsOf(s).includes("s3:ListBucketVersions"));
        expect(list.Condition).toEqual({
            StringLike: { "s3:prefix": ["vams.preparedDownloads/*"] },
        });
    });

    test("the service holds s3:ListBucket on the auxiliary bucket, only for vams.preparedDownloads/ (#1125 D21)", () => {
        // Without it S3 answers HeadObject on a missing archive key with 403, and every delayed
        // sweep pass failed in landed_archive. A unit test that mocks a 404 cannot catch this.
        const statements = roleStatements(synth, functionByHandler(synth, SERVICE_HANDLER));
        const lists = statements.filter((s) => actionsOf(s).includes("s3:ListBucket"));
        const aux = lists.filter((s) => s.Condition !== undefined);
        expect(aux).toHaveLength(1);
        expect(aux[0].Condition).toEqual({
            StringLike: { "s3:prefix": ["vams.preparedDownloads/*"] },
        });
        // The bucket ARN itself (a partition-aware reference), never an object or wildcard.
        expect(SynthResult.flatten(aux[0].Resource)).not.toMatch(/\*/);
        expect(JSON.stringify(aux[0].Resource)).not.toMatch(/arn:aws:/);
    });

    test("the service confirms an abort with ListParts, only under vams.preparedDownloads/ (#1125 D16)", () => {
        {
            const statements = roleStatements(synth, functionByHandler(synth, SERVICE_HANDLER));
            const listParts = statements.filter((s) =>
                actionsOf(s).includes("s3:ListMultipartUploadParts")
            );
            expect(listParts).toHaveLength(1);
            expect(actionsOf(listParts[0])).toContain("s3:AbortMultipartUpload");
            expect(SynthResult.flatten(listParts[0].Resource)).toMatch(
                /\/vams\.preparedDownloads\/\*$/
            );
        }
    });

    test("no delayed sweep with the feature off: no state machine, no ARN, no grant (#1125 D16)", () => {
        expect(
            synth.where("AWS::StepFunctions::StateMachine", (r) =>
                r.logicalId.startsWith("ZipDownloadSweepStateMachine")
            )
        ).toEqual([]);
        const fn = functionByHandler(synth, SERVICE_HANDLER);
        expect(envOf(fn).ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN).toBeUndefined();
        expect(
            roleStatements(synth, fn).filter((s) => actionsOf(s).includes("states:StartExecution"))
        ).toEqual([]);
    });
});

describe.each(ALL_TEMPLATES)("feature on (%s)", (name) => {
    let synth: SynthResult;
    beforeAll(() => {
        synth = synthOn(name);
    });

    test("the service is on, with the configured limits", () => {
        const env = envOf(functionByHandler(synth, SERVICE_HANDLER));
        expect(env.ZIPDOWNLOADS_ENABLED).toBe("true");
        // The config's legacy retentionDays (14) reaches nothing.
        expect(env.ZIP_DOWNLOAD_RETENTION_DAYS).toBeUndefined();
        expect(env.ZIP_DOWNLOAD_MAX_FILES_PER_REQUEST).toBe("100000");
        expect(env.ZIP_DOWNLOAD_MAX_TOTAL_BYTES_PER_REQUEST).toBe("5497558138880");
        expect(env.ZIP_DOWNLOAD_MAX_ACTIVE_PER_USER).toBe("5");
        expect(env.EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME).toBeDefined();
        expect(env.EXECUTION_SERVICE_FUNCTION_NAME).toBeDefined();
    });

    test("one zip Lambda serves the routes and the sweep; its S3 writes stay prefix-scoped", () => {
        expectOneZipFunction(synth);
        expectS3MutationsPrefixScoped(
            roleStatements(synth, functionByHandler(synth, SERVICE_HANDLER))
        );
    });

    test("the service may invoke exactly the execute and execution service functions", () => {
        const service = functionByHandler(synth, SERVICE_HANDLER);
        const invoke = roleStatements(synth, service).filter((s) =>
            actionsOf(s).includes("lambda:InvokeFunction")
        );
        expect(invoke).toHaveLength(1);
        const resources = [].concat(invoke[0].Resource);
        expect(resources).toHaveLength(2);
        const executeId = functionByHandler(
            synth,
            "handlers.workflows.executeWorkflow.lambda_handler"
        ).logicalId;
        const flattened = resources.map((r) => SynthResult.flatten(r));
        expect(flattened.some((r) => r.includes(executeId))).toBe(true);
        for (const r of flattened) {
            expect(r).not.toContain("*");
        }
    });

    // #1125 D16 round 2 (the #1123 D5 rule after the demo21 recheck): an abort's immediate answer is
    // not final, so every stopper starts the job's delayed sweep.
    test("the delayed sweep waits, sweeps, waits again and sweeps a last time", () => {
        const machines = synth.where("AWS::StepFunctions::StateMachine", (r) =>
            r.logicalId.startsWith("ZipDownloadSweepStateMachine")
        );
        expect(machines).toHaveLength(1);
        const machine = machines[0];
        const definition = JSON.parse(SynthResult.flatten(machine.properties.DefinitionString));
        const states = definition.States;
        expect(definition.StartAt).toBe("ZipDownloadSweepFirstWait");
        expect(states.ZipDownloadSweepFirstWait).toEqual(
            expect.objectContaining({
                Type: "Wait",
                Seconds: ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS.first,
                Next: "ZipDownloadSweepFirst",
            })
        );
        expect(states.ZipDownloadSweepSecondWait).toEqual(
            expect.objectContaining({
                Type: "Wait",
                Seconds: ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS.second,
                Next: "ZipDownloadSweepFinal",
            })
        );
        expect(ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS).toEqual({ first: 300, second: 600 });
        const serviceId = functionByHandler(synth, SERVICE_HANDLER).logicalId;
        for (const [id, final] of [
            ["ZipDownloadSweepFirst", false],
            ["ZipDownloadSweepFinal", true],
        ] as [string, boolean][]) {
            const task = states[id];
            expect(JSON.stringify(task.Resource)).toContain(":lambda:invoke");
            expect(JSON.stringify(task.Parameters.FunctionName)).toContain(serviceId);
            expect(task.Parameters.Payload).toEqual({
                action: "sweep",
                "zipSweep.$": "$.zipSweep",
                final,
            });
            expect(task.ResultPath).toBeNull();
            // Bounded: one fault retrier, then the pass is given up.
            expect(task.Retry).toEqual([
                expect.objectContaining({
                    ErrorEquals: ["States.ALL"],
                    MaxAttempts: ZIP_DOWNLOAD_SWEEP_RETRY.maxAttempts,
                }),
            ]);
        }
        // A failed first pass still runs the last one.
        expect(states.ZipDownloadSweepFirst.Next).toBe("ZipDownloadSweepSecondWait");
        expect(states.ZipDownloadSweepFirst.Catch).toEqual([
            expect.objectContaining({
                ErrorEquals: ["States.ALL"],
                Next: "ZipDownloadSweepSecondWait",
            }),
        ]);
        expect(states.ZipDownloadSweepFinal.Next).toBe("ZipDownloadSwept");
        expect(states.ZipDownloadSwept.Type).toBe("Succeed");
        expect(Object.keys(states)).toHaveLength(5);
        // Bounded history: two waits and two tasks of at most 5 + 3 x maxAttempts events.
        expect(2 * 3 + 2 * (5 + 3 * ZIP_DOWNLOAD_SWEEP_RETRY.maxAttempts) + 4).toBeLessThan(100);
        expect(machine.properties.StateMachineType).toBe("STANDARD");
        expect(machine.properties.LoggingConfiguration.Level).toBe("ALL");
        expect(machine.properties.TracingConfiguration).toEqual({ Enabled: true });
        expect(machine.properties.StateMachineName).toMatch(/^ZipDownloadSweep-/);
        const backend = fs.readFileSync(
            path.join(__dirname, "../../../backend/backend/common/zipDownloads.py"),
            "utf8"
        );
        expect(backend).toMatch(
            new RegExp(`^SWEEP_FIRST_WAIT_SECONDS = ${ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS.first}$`, "m")
        );
        expect(backend).toMatch(
            new RegExp(
                `^SWEEP_SECOND_WAIT_SECONDS = ${ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS.second}$`,
                "m"
            )
        );
    });

    test("the service may start the sweep, on its exact ARN only", () => {
        const machine = synth.where("AWS::StepFunctions::StateMachine", (r) =>
            r.logicalId.startsWith("ZipDownloadSweepStateMachine")
        )[0];
        const name = machine.properties.StateMachineName;
        {
            const fn = functionByHandler(synth, SERVICE_HANDLER);
            const arn = SynthResult.flatten(envOf(fn).ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN);
            expect(arn).toMatch(new RegExp(`:states:[^:]+:[^:]+:stateMachine:${name}$`));
            // Built from the fixed name: the function does not reference the state machine.
            expect(JSON.stringify(envOf(fn))).not.toContain(machine.logicalId);
            const starts = roleStatements(synth, fn).filter((s) =>
                actionsOf(s).includes("states:StartExecution")
            );
            expect(starts).toHaveLength(1);
            expect(actionsOf(starts[0])).toEqual(["states:StartExecution"]);
            expect([].concat(starts[0].Resource).map((r) => SynthResult.flatten(r))).toEqual([arn]);
        }
    });

    test("the zip download service is no terminal callback: only the sweep may invoke it", () => {
        const machine = synth.where("AWS::StepFunctions::StateMachine", (r) =>
            r.logicalId.startsWith("ZipDownloadSweepStateMachine")
        )[0];
        const sweepRoleId = machine.properties.RoleArn["Fn::GetAtt"][0];
        expectNoZipCallback(synth, sweepRoleId);
    });

    test("the lifecycle rule stays 7 days (legacy retentionDays ignored) and aborts incomplete uploads after a day", () => {
        const bucket = synth.where("AWS::S3::Bucket", (r) =>
            JSON.stringify(r.properties.LifecycleConfiguration ?? {}).includes(
                "PreparedDownloadsExpiration"
            )
        )[0];
        const rule = (bucket.properties.LifecycleConfiguration.Rules as any[]).find(
            (r) => r.Id === "PreparedDownloadsExpiration"
        );
        expect(rule.ExpirationInDays).toBe(ZIP_DOWNLOAD_RETENTION_DAYS);
        expect(ZIP_DOWNLOAD_RETENTION_DAYS).toBe(7);
        expect(rule.NoncurrentVersionExpiration).toEqual({ NoncurrentDays: 1 });
        // The backstop for an upload no reader aborted: no terminal callback finalizes a job.
        expect(rule.Prefix).toBe("vams.preparedDownloads/");
        expect(rule.AbortIncompleteMultipartUpload).toEqual({ DaysAfterInitiation: 1 });
    });

    test("the ZIPDOWNLOADS feature switch is written", () => {
        expect(synth.grep("ZIPDOWNLOADS").some((r) => !r.type.startsWith("AWS::Lambda"))).toBe(
            true
        );
    });

    test("the zipArchive job: Fargate size from config, 11 h bound, the job table env", () => {
        const jobs = synth
            .ofType("AWS::Batch::JobDefinition")
            .filter((r) => /ZipArchiveBuilder/.test(r.stack));
        expect(jobs).toHaveLength(1);
        const container = jobs[0].properties.ContainerProperties;
        expect(container.ResourceRequirements).toEqual(
            expect.arrayContaining([
                { Type: "VCPU", Value: "8" },
                { Type: "MEMORY", Value: "24576" },
            ])
        );
        expect(jobs[0].properties.Timeout).toEqual({ AttemptDurationSeconds: 39600 });
        expect(jobs[0].properties.PlatformCapabilities).toEqual(["FARGATE"]);
        const names = (container.Environment as any[]).map((e) => e.Name);
        expect(names).toEqual(
            expect.arrayContaining([
                "ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE_NAME",
                "ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME",
                "ASSET_AUXILIARY_BUCKET_NAME",
                "PREPARED_DOWNLOADS_PREFIX",
            ])
        );
    });

    test("useCodeBuild off (default): the image is a local Docker asset, with no CodeBuild build", () => {
        // codeBuildEcrEncryption.test.ts covers useCodeBuild on (repository, build trigger, job image).
        const archive = synth.resources.filter((r) => /ZipArchiveBuilder/.test(r.stack));
        expect(archive.filter((r) => r.type === "AWS::ECR::Repository")).toEqual([]);
        expect(archive.filter((r) => r.type === "AWS::CodeBuild::Project")).toEqual([]);
        const jobs = archive.filter((r) => r.type === "AWS::Batch::JobDefinition");
        expect(jobs).toHaveLength(1);
        // The CDK asset repository, not a VAMS-owned one.
        expect(SynthResult.flatten(jobs[0].properties.ContainerProperties.Image)).toContain(
            "cdk-hnb659fds-container-assets"
        );
    });

    test("the zipArchive state machine ends before the workflow's 12 h task timeout", () => {
        const machines = synth
            .ofType("AWS::StepFunctions::StateMachine")
            .filter((r) => /ZipArchiveBuilder/.test(r.stack));
        expect(machines).toHaveLength(1);
        const definition = SynthResult.flatten(machines[0].properties.DefinitionString);
        expect(definition).toContain('"TimeoutSeconds":41400');
        expect(definition).toContain('"ZipArchiveBatchJob"');
    });

    // #1125 D26: a container killed outside VAMS ends its run instead of holding the job 11 h.
    test("the batch job state keeps its 11 h timeout and gains a 30 min heartbeat timeout", () => {
        const machine = synth
            .ofType("AWS::StepFunctions::StateMachine")
            .filter((r) => /ZipArchiveBuilder/.test(r.stack))[0];
        const states = JSON.parse(SynthResult.flatten(machine.properties.DefinitionString)).States;
        const job = states.ZipArchiveBatchJob;
        expect(job.Resource).toContain("waitForTaskToken");
        expect(job.TimeoutSeconds).toBe(39600);
        expect(job.HeartbeatSeconds).toBe(1800);
        // Every error, the heartbeat timeout and the container's stop answer included, reaches
        // pipelineEnd, which answers the VAMS workflow's token.
        expect(job.Catch).toEqual([
            expect.objectContaining({ ErrorEquals: ["States.ALL"], Next: "HandleBatchError" }),
        ]);
        const container = fs.readFileSync(
            path.join(
                __dirname,
                "../../../backendPipelines/utility/zipArchive/container/zip_archive_pipeline/runner.py"
            ),
            "utf8"
        );
        const interval = Number(/^HEARTBEAT_SECONDS = (\d+)$/m.exec(container)?.[1]);
        expect(interval).toBe(120);
        expect(interval * 5).toBeLessThan(job.HeartbeatSeconds);
    });

    test("the container job role may heartbeat and answer task tokens, in its own account only", () => {
        const roles = synth
            .ofType("AWS::IAM::Role")
            .filter(
                (r) =>
                    /ZipArchiveBuilder/.test(r.stack) &&
                    /ZipArchiveContainerJobRole/.test(r.logicalId)
            );
        expect(roles).toHaveLength(1);
        type InlinePolicy = { PolicyDocument: { Statement: Record<string, unknown>[] } };
        const statements = (roles[0].properties.Policies as InlinePolicy[]).flatMap(
            (p) => p.PolicyDocument.Statement
        );
        const token = statements.filter((s) => actionsOf(s).includes("states:SendTaskHeartbeat"));
        expect(token).toHaveLength(1);
        expect(actionsOf(token[0]).sort()).toEqual([
            "states:SendTaskFailure",
            "states:SendTaskHeartbeat",
            "states:SendTaskSuccess",
        ]);
        expect(SynthResult.flatten(([] as unknown[]).concat(token[0].Resource)[0])).toMatch(
            /^arn:[^:]+:states:[^:]+:[^:]+:\*$/
        );
    });

    test("the system pipeline registers with fixed GLOBAL/utility-zip-archive ids", () => {
        const registrations = synth.where(
            "AWS::CloudFormation::CustomResource",
            (r) =>
                /ZipArchiveBuilder/.test(r.stack) &&
                JSON.stringify(r.raw).includes("utility-zip-archive")
        );
        expect(registrations).toHaveLength(1);
        const text = JSON.stringify(registrations[0].raw);
        expect(text).toContain('\\"pipelineId\\":\\"utility-zip-archive\\"');
        expect(text).toContain('\\"workflowDatabaseId\\":\\"GLOBAL\\"');
    });

    test("the container job role writes only under vams.preparedDownloads/ in the auxiliary bucket", () => {
        const roles = synth
            .ofType("AWS::IAM::Role")
            .filter(
                (r) =>
                    /ZipArchiveBuilder/.test(r.stack) &&
                    r.logicalId.includes("ZipArchiveContainerJobRole")
            );
        expect(roles).toHaveLength(1);
        const output = (roles[0].properties.Policies as any[]).find(
            (p) => p.PolicyName === "OutputBucketPolicy"
        );
        for (const statement of output.PolicyDocument.Statement as any[]) {
            if (!actionsOf(statement).some((a) => a.startsWith("s3:"))) continue;
            if (actionsOf(statement).includes("s3:ListBucket")) {
                // The bucket-level grant, scoped by the prefix condition (#1125 D21).
                expect(actionsOf(statement)).toEqual(["s3:ListBucket"]);
                expect(statement.Condition).toEqual({
                    StringLike: { "s3:prefix": ["vams.preparedDownloads/*"] },
                });
                expect(SynthResult.flatten(statement.Resource)).not.toMatch(/\*/);
                continue;
            }
            expect(SynthResult.flatten(statement.Resource)).toMatch(
                /\/vams\.preparedDownloads\/\*$/
            );
        }
        expect(
            (output.PolicyDocument.Statement as any[]).filter((s) =>
                actionsOf(s).includes("s3:ListBucket")
            )
        ).toHaveLength(1);
        // The job deletes only its own late archive (one outcome): never an asset bucket object.
        const outputActions = (output.PolicyDocument.Statement as any[]).flatMap(actionsOf);
        expect(outputActions).toEqual(
            expect.arrayContaining(["s3:DeleteObject", "s3:DeleteObjectVersion"])
        );
        for (const policy of roles[0].properties.Policies as any[]) {
            if (policy.PolicyName === "OutputBucketPolicy") continue;
            const other = (policy.PolicyDocument.Statement as any[]).flatMap(actionsOf);
            expect(other.filter((a) => a.startsWith("s3:Delete"))).toEqual([]);
        }
        const table = (roles[0].properties.Policies as any[]).find(
            (p) => p.PolicyName === "TablePolicy"
        );
        const actions = (table.PolicyDocument.Statement as any[]).flatMap(actionsOf).sort();
        expect(actions).toEqual(["dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"]);
    });

    test("every literal ARN in the archive stack names the template's own partition", () => {
        const archive = synth.resources.filter((r) => /ZipArchiveBuilder/.test(r.stack));
        expect(archive.length).toBeGreaterThan(20);
        const partitions = new Set(
            Array.from(
                JSON.stringify(archive.map((r) => r.raw)).matchAll(/arn:(aws[a-z-]*):/g)
            ).map((m) => m[1])
        );
        expect(Array.from(partitions)).toEqual([synth.partition]);
    });

    test("the isolated subnets get the Batch and ECR endpoints the Fargate job pulls through", () => {
        const services = synth
            .ofType("AWS::EC2::VPCEndpoint")
            .map((r) => SynthResult.flatten(r.properties.ServiceName));
        expect(services.some((s) => s.endsWith(".batch"))).toBe(true);
        expect(services.some((s) => s.endsWith(".ecr.dkr"))).toBe(true);
    });
});

test("the six routes are registered in the CDK route list", () => {
    expect(ZIP_DOWNLOAD_ROUTES.map((r) => `${r.method} ${r.routePath}`).sort()).toEqual(
        [
            "DELETE /database/{databaseId}/assets/{assetId}/download/zip/{requestId}",
            "GET /database/{databaseId}/assets/{assetId}/download/zip",
            "GET /database/{databaseId}/assets/{assetId}/download/zip/{requestId}",
            "GET /download/zip",
            "POST /database/{databaseId}/assets/{assetId}/download/zip",
            "POST /database/{databaseId}/assets/{assetId}/download/zip/{requestId}/url",
        ].sort()
    );
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Download as Zip (app.zipDownloads) in the synthesized templates.
 *
 * Always deployed, whatever `enabled` says: the job table, the auxiliary-bucket expiry rules on
 * `vams.preparedDownloads/`, the table's SSM name, the zip download service with its six routes, and the
 * terminal callback function. Only with `enabled`: the zipArchive system pipeline stack, the
 * ZIPDOWNLOADS feature switch, the callback's registration under Zip-Download, and the service's two
 * cross-call grants (launch and abort), each on an exact function ARN.
 */

import {
    ALL_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    synthTemplate,
} from "../support/templateSynth";
import { SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV } from "../../lib/lambdaBuilder/systemPipelineCallbacks";
import * as fs from "fs";
import * as path from "path";
import {
    ZIP_DOWNLOAD_ROUTES,
    ZIP_DOWNLOAD_SWEEP_RETRY,
    ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS,
} from "../../lib/lambdaBuilder/zipDownloadFunctions";

jest.setTimeout(900_000);

const SERVICE_HANDLER = "handlers.zipDownloads.zipDownloadService.lambda_handler";
const CALLBACK_HANDLER = "handlers.zipDownloads.zipDownloadCallback.lambda_handler";
const ARCHIVE_EXECUTE_HANDLER = "vamsExecuteZipArchivePipeline.lambda_handler";

function enabledMutate(retentionDays: number) {
    return (c: any) => {
        c.app.zipDownloads.enabled = true;
        c.app.zipDownloads.retentionDays = retentionDays;
        c.app.zipDownloads.computeVcpu = 8;
        c.app.zipDownloads.computeMemoryGiB = 24;
        c.app.useGlobalVpc.enabled = true;
    };
}

const synthOff = (name: TemplateName): SynthResult => synthTemplate(name);
const synthOn = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enabledMutate(14), mutateKey: "zip-downloads-on-14" });

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

    test("the auxiliary bucket expires vams.preparedDownloads/ after the retention, and its markers", () => {
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
        expect(env.ZIP_DOWNLOAD_RETENTION_DAYS).toBe("7");
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

    test("the callback function exists but no terminal writer names it", () => {
        functionByHandler(synth, CALLBACK_HANDLER);
        const writersWithRegistry = synth.where(
            "AWS::Lambda::Function",
            (r) => envOf(r)[SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV] !== undefined
        );
        expect(writersWithRegistry).toEqual([]);
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

    test("the callback deletes and lists versions only under vams.preparedDownloads/ (#1125 D13)", () => {
        const callback = functionByHandler(synth, CALLBACK_HANDLER);
        const statements = roleStatements(synth, callback);
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
            ].sort()
        );
        expect(SynthResult.flatten(deletes[0].Resource)).toMatch(/\/vams\.preparedDownloads\/\*$/);
        const list = statements.find((s) => actionsOf(s).includes("s3:ListBucketVersions"));
        expect(list.Condition).toEqual({
            StringLike: { "s3:prefix": ["vams.preparedDownloads/*"] },
        });
    });

    test("both zip functions confirm an abort with ListParts, only under vams.preparedDownloads/ (#1125 D16)", () => {
        for (const handler of [SERVICE_HANDLER, CALLBACK_HANDLER]) {
            const statements = roleStatements(synth, functionByHandler(synth, handler));
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
        for (const handler of [SERVICE_HANDLER, CALLBACK_HANDLER]) {
            const fn = functionByHandler(synth, handler);
            expect(envOf(fn).ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN).toBeUndefined();
            expect(
                roleStatements(synth, fn).filter((s) =>
                    actionsOf(s).includes("states:StartExecution")
                )
            ).toEqual([]);
        }
    });

    test("the callback reads no asset buckets table: its auxiliary bucket Region is registered (#1125 D17)", () => {
        const statements = roleStatements(synth, functionByHandler(synth, CALLBACK_HANDLER));
        const dynamoResources = statements
            .filter((s) => actionsOf(s).some((a) => a.startsWith("dynamodb:")))
            .flatMap((s) => [].concat(s.Resource).map((r) => SynthResult.flatten(r)));
        expect(dynamoResources.some((r) => /S3AssetBuckets|bucketNameGSI/i.test(r))).toBe(false);
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
        expect(env.ZIP_DOWNLOAD_RETENTION_DAYS).toBe("14");
        expect(env.ZIP_DOWNLOAD_MAX_FILES_PER_REQUEST).toBe("100000");
        expect(env.ZIP_DOWNLOAD_MAX_TOTAL_BYTES_PER_REQUEST).toBe("5497558138880");
        expect(env.ZIP_DOWNLOAD_MAX_ACTIVE_PER_USER).toBe("5");
        expect(env.EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME).toBeDefined();
        expect(env.EXECUTION_SERVICE_FUNCTION_NAME).toBeDefined();
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
        const callbackId = functionByHandler(synth, CALLBACK_HANDLER).logicalId;
        for (const [id, final] of [
            ["ZipDownloadSweepFirst", false],
            ["ZipDownloadSweepFinal", true],
        ] as [string, boolean][]) {
            const task = states[id];
            expect(JSON.stringify(task.Resource)).toContain(":lambda:invoke");
            expect(JSON.stringify(task.Parameters.FunctionName)).toContain(callbackId);
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

    test("the service and the callback may start the sweep, on its exact ARN only", () => {
        const machine = synth.where("AWS::StepFunctions::StateMachine", (r) =>
            r.logicalId.startsWith("ZipDownloadSweepStateMachine")
        )[0];
        const name = machine.properties.StateMachineName;
        for (const handler of [SERVICE_HANDLER, CALLBACK_HANDLER]) {
            const fn = functionByHandler(synth, handler);
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

    test("the callback is registered under Zip-Download on the terminal writers", () => {
        const callbackId = functionByHandler(synth, CALLBACK_HANDLER).logicalId;
        const writers = synth.where(
            "AWS::Lambda::Function",
            (r) => envOf(r)[SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV] !== undefined
        );
        expect(writers).toHaveLength(3);
        for (const writer of writers) {
            const registry = SynthResult.flatten(
                envOf(writer)[SYSTEM_PIPELINE_CALLBACK_FUNCTIONS_ENV]
            );
            expect(registry).toContain('"Zip-Download"');
            expect(registry).toContain(callbackId);
        }
    });

    test("the lifecycle rule follows retentionDays", () => {
        const bucket = synth.where("AWS::S3::Bucket", (r) =>
            JSON.stringify(r.properties.LifecycleConfiguration ?? {}).includes(
                "PreparedDownloadsExpiration"
            )
        )[0];
        const rule = (bucket.properties.LifecycleConfiguration.Rules as any[]).find(
            (r) => r.Id === "PreparedDownloadsExpiration"
        );
        expect(rule.ExpirationInDays).toBe(14);
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
            expect(SynthResult.flatten(statement.Resource)).toMatch(
                /\/vams\.preparedDownloads\/\*$/
            );
        }
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

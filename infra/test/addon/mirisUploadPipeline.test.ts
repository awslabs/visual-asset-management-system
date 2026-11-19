/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Miris upload pipeline's timeouts, failure routes, IAM shape, environment and registration. The
 * state machine outlasts the Batch attempt, so an end state always reports and releases the claim; an
 * EventBridge rule covers the ends no state reaches.
 */

import * as fs from "fs";
import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import { MIRIS_DEPENDENCY_MANIFEST_PREFIX } from "../../lib/nestedStacks/addon/miris/upload/lambdaBuilder/mirisUploadFunctions";
import {
    MIRIS_UPLOAD_CONTAINER_OVERHEAD_SECONDS,
    MIRIS_UPLOAD_MEMORY_MIB,
    MIRIS_UPLOAD_ORCHESTRATION_MARGIN_SECONDS,
} from "../../lib/nestedStacks/addon/miris/upload/constructs/mirisUpload-construct";
import { parseAsl, singleStateMachine } from "../support/asl";
import {
    MIRIS_TEST_SECRET_ARN,
    MirisUploadHarness,
    makeMirisUploadHarness,
} from "./mirisUploadHarness";

const PIPELINE_DIR = path.resolve(__dirname, "../../../backendPipelines/conversion/mirisUpload");
const HANDLER_STEMS = [
    "vamsExecuteMirisUpload",
    "mirisUploadGate",
    "openPipeline",
    "constructPipeline",
    "pipelineEnd",
];

let harness: MirisUploadHarness;
let template: Template;
let resources: Record<string, any>;

beforeAll(() => {
    harness = makeMirisUploadHarness("MirisUploadPipelineStack");
    template = Template.fromStack(harness.stack);
    resources = template.toJSON().Resources;
});

const byType = (type: string) =>
    Object.entries(resources).filter(([, r]) => r.Type === type) as [string, any][];

const roleId = (fragment: string): string => {
    const ids = byType("AWS::IAM::Role")
        .map(([id]) => id)
        .filter((id) => id.includes(fragment));
    expect(ids).toHaveLength(1);
    return ids[0];
};

const functionOf = (handler: string): [string, any] => {
    const fns = byType("AWS::Lambda::Function").filter(([, r]) => r.Properties.Handler === handler);
    expect(fns).toHaveLength(1);
    return fns[0];
};

const lambdaRoleId = (handler: string): string =>
    functionOf(handler)[1].Properties.Role["Fn::GetAtt"][0];

const envOf = (handler: string): Record<string, any> =>
    functionOf(handler)[1].Properties.Environment?.Variables ?? {};

/** Every statement attached to a role: its inline Policies and any AWS::IAM::Policy naming it. */
const statementsOf = (id: string): any[] => [
    ...(resources[id].Properties.Policies ?? []).flatMap((p: any) => p.PolicyDocument.Statement),
    ...byType("AWS::IAM::Policy")
        .filter(([, p]) => JSON.stringify(p.Properties.Roles ?? []).includes(`"${id}"`))
        .flatMap(([, p]) => p.Properties.PolicyDocument.Statement),
];

const actionsOf = (statement: any): string[] => [statement.Action].flat();

const auxBucketId = (): string =>
    Object.keys(template.findResources("AWS::S3::Bucket")).find((id) =>
        id.startsWith("AssetAuxiliaryBucket")
    )!;

describe("Miris upload timeouts", () => {
    const attemptSeconds = (): number => {
        const jobDefinitions = byType("AWS::Batch::JobDefinition");
        expect(jobDefinitions).toHaveLength(1);
        return jobDefinitions[0][1].Properties.Timeout.AttemptDurationSeconds;
    };

    test("the Batch attempt is the Miris processing wait plus the container overhead", () => {
        const taskTimeoutSeconds =
            harness.config.app.addons.useMirisStreaming.upload.taskTimeoutSeconds;
        expect(taskTimeoutSeconds).toBeGreaterThan(0);
        expect(attemptSeconds()).toBe(taskTimeoutSeconds + MIRIS_UPLOAD_CONTAINER_OVERHEAD_SECONDS);
    });

    test("the state machine outlasts the Batch attempt by the orchestration margin", () => {
        // The top-level TimeoutSeconds of the ASL is the execution timeout.
        const executionSeconds = parseAsl(singleStateMachine(template)).TimeoutSeconds;
        expect(MIRIS_UPLOAD_ORCHESTRATION_MARGIN_SECONDS).toBeGreaterThanOrEqual(900);
        expect(executionSeconds).toBe(attemptSeconds() + MIRIS_UPLOAD_ORCHESTRATION_MARGIN_SECONDS);
    });

    test("the container is given the execution's start, both time limits and its memory", () => {
        const asl = parseAsl(singleStateMachine(template));
        const environment: { Name: string; Value?: string; "Value.$"?: string }[] =
            asl.States.SubmitMirisUploadBatchJob.Parameters.ContainerOverrides.Environment;
        const byName = Object.fromEntries(environment.map((e) => [e.Name, e]));
        expect(byName.MIRIS_UPLOAD_EXECUTION_START_TIME).toEqual({
            Name: "MIRIS_UPLOAD_EXECUTION_START_TIME",
            "Value.$": "$$.Execution.StartTime",
        });
        expect(byName.MIRIS_UPLOAD_EXECUTION_TIMEOUT_SECONDS.Value).toBe(
            String(asl.TimeoutSeconds)
        );
        expect(byName.MIRIS_UPLOAD_ATTEMPT_DURATION_SECONDS.Value).toBe(String(attemptSeconds()));
        expect(byName.VAMS_TASK_MEMORY_MIB.Value).toBe(String(MIRIS_UPLOAD_MEMORY_MIB));
    });

    test("the workflow task timeout in the vamsSchema bundle covers the state machine", () => {
        const pipeline = JSON.parse(
            fs.readFileSync(path.join(PIPELINE_DIR, "vamsSchema", "pipeline.json"), "utf-8")
        );
        const executionSeconds = parseAsl(singleStateMachine(template)).TimeoutSeconds;
        expect(Number(pipeline.executionConfig.taskTimeout)).toBeGreaterThan(executionSeconds);
    });
});

describe("Miris upload state machine and failure routes", () => {
    test("the Batch state is a cancellable .sync submission that keeps the claim fields", () => {
        const state = parseAsl(singleStateMachine(template)).States.SubmitMirisUploadBatchJob;
        expect(state.Resource).toContain("batch:submitJob.sync");
        // DISCARD renders as a null ResultPath.
        expect(state.ResultPath).toBeNull();
        expect(state.Next).toBe("MirisUploadEnd");
        const actions = statementsOf(roleId("InnerStateMachineRole")).flatMap(actionsOf);
        expect(actions).toEqual(
            expect.arrayContaining(["batch:TerminateJob", "batch:DescribeJobs"])
        );
    });

    test("both work states catch into the failure end task with the error under $.error", () => {
        const states = parseAsl(singleStateMachine(template)).States;
        for (const name of ["ConstructPipelineTask", "SubmitMirisUploadBatchJob"]) {
            expect(states[name].Catch).toEqual([
                {
                    ErrorEquals: ["States.ALL"],
                    ResultPath: "$.error",
                    Next: "MirisUploadEndFailure",
                },
            ]);
        }
        expect(states.MirisUploadEndFailure.Next).toBe("MirisUploadFail");
        expect(states.MirisUploadFail.Type).toBe("Fail");
    });

    test("an EventBridge rule runs the end Lambda when the inner execution ends unreported", () => {
        const stateMachineId = byType("AWS::StepFunctions::StateMachine")[0][0];
        const [endFunctionId] = functionOf("pipelineEnd.lambda_handler");

        const rules = byType("AWS::Events::Rule").filter(([, r]) =>
            JSON.stringify(r.Properties.EventPattern ?? {}).includes("aws.states")
        );
        expect(rules).toHaveLength(1);
        const rule = rules[0][1].Properties;

        // Scoped to this pipeline's own state machine, not every execution in the account.
        expect(rule.EventPattern).toEqual({
            source: ["aws.states"],
            "detail-type": ["Step Functions Execution Status Change"],
            detail: {
                status: expect.arrayContaining(["ABORTED", "FAILED", "TIMED_OUT"]),
                stateMachineArn: [{ Ref: stateMachineId }],
            },
        });
        expect(rule.EventPattern.detail.status).toHaveLength(3);
        expect(rule.Targets).toEqual([
            expect.objectContaining({
                Arn: { "Fn::GetAtt": [endFunctionId, "Arn"] },
                RetryPolicy: { MaximumRetryAttempts: 2 },
            }),
        ]);

        // EventBridge may invoke the end Lambda.
        const permissions = byType("AWS::Lambda::Permission").filter(
            ([, p]) =>
                p.Properties.Principal === "events.amazonaws.com" &&
                JSON.stringify(p.Properties.FunctionName).includes(endFunctionId)
        );
        expect(permissions).toHaveLength(1);
    });

    test("the end tasks hand the end Lambda the status, the error and the claim fields", () => {
        const states = parseAsl(singleStateMachine(template)).States;
        const claim = {
            "externalSfnTaskToken.$": "$.externalSfnTaskToken",
            "databaseId.$": "$.databaseId",
            "assetId.$": "$.assetId",
            "claimFingerprint.$": "$.claimFingerprint",
            "inputOutputS3AssetAuxiliaryFilesPath.$": "$.inputOutputS3AssetAuxiliaryFilesPath",
        };
        expect(states.MirisUploadEnd.Parameters.Payload).toEqual({ ...claim, status: "success" });
        expect(states.MirisUploadEndFailure.Parameters.Payload).toEqual({
            ...claim,
            status: "failed",
            "errorType.$": "$.error.Error",
            "errorCause.$": "$.error.Cause",
            "ambiguousRootReportS3Uri.$": "$.ambiguousRootReportS3Uri",
        });
    });

    test("the state machine logs ALL with execution data to a vended group, traced", () => {
        const sm = singleStateMachine(template);
        expect(sm.TracingConfiguration).toEqual({ Enabled: true });
        expect(sm.LoggingConfiguration.Level).toBe("ALL");
        expect(sm.LoggingConfiguration.IncludeExecutionData).toBe(true);
        const groupId =
            sm.LoggingConfiguration.Destinations[0].CloudWatchLogsLogGroup.LogGroupArn[
                "Fn::GetAtt"
            ][0];
        const group = resources[groupId].Properties;
        expect(JSON.stringify(group.LogGroupName)).toContain(
            "/aws/vendedlogs/VAMSStateMachine-MirisUpload"
        );
        expect(group.RetentionInDays).toBe(365);
    });
});

test("the job definition is a Fargate job with 4 GiB for parsing text USD", () => {
    const jd = byType("AWS::Batch::JobDefinition")[0][1].Properties;
    expect(jd.PlatformCapabilities).toEqual(["FARGATE"]);
    expect(jd.ContainerProperties.ResourceRequirements).toEqual(
        expect.arrayContaining([
            { Type: "VCPU", Value: "1" },
            { Type: "MEMORY", Value: "4096" },
        ])
    );
    expect(jd.ContainerProperties.EphemeralStorage).toEqual({ SizeInGiB: 21 });
});

describe("Miris upload IAM", () => {
    test("no role in the pipeline grants the s3:HeadObject pseudo-action", () => {
        // HeadObject is authorized by s3:GetObject; s3:HeadObject is not an IAM action.
        expect(JSON.stringify(resources)).not.toContain("s3:HeadObject");
    });

    test("the container roles make no Step Functions call and touch the auxiliary bucket only to write manifests", () => {
        for (const id of [roleId("ContainerJobRole"), roleId("ContainerExecutionRole")]) {
            const statements = statementsOf(id);
            expect(statements.flatMap(actionsOf).filter((a) => a.startsWith("states:"))).toEqual(
                []
            );
            const aux = statements.filter((s) => JSON.stringify(s).includes(auxBucketId()));
            if (id === roleId("ContainerExecutionRole")) {
                expect(aux).toEqual([]);
                continue;
            }
            // One write-only statement on exactly the manifest prefix.
            expect(aux).toHaveLength(1);
            expect(actionsOf(aux[0])).toEqual(["s3:PutObject"]);
            expect(aux[0].Resource).toEqual({
                "Fn::Join": [
                    "",
                    [
                        { "Fn::GetAtt": [auxBucketId(), "Arn"] },
                        `/${MIRIS_DEPENDENCY_MANIFEST_PREFIX}/*`,
                    ],
                ],
            });
        }
        // The execution role pulls the image and writes logs only.
        const execution = roleId("ContainerExecutionRole");
        expect(resources[execution].Properties.Policies ?? []).toEqual([]);
        expect(
            statementsOf(execution)
                .flatMap(actionsOf)
                .filter((a) => /^(s3|secretsmanager):/.test(a))
        ).toEqual([]);
    });

    test("the job role writes only s3:PutObject, under the run-output area or the manifest prefix", () => {
        const statements = statementsOf(roleId("ContainerJobRole"));
        const s3Actions = new Set(
            statements.flatMap(actionsOf).filter((a: string) => a.startsWith("s3:"))
        );
        expect([...s3Actions].sort()).toEqual([
            "s3:GetObject",
            "s3:GetObjectVersion",
            "s3:ListBucket",
            "s3:PutObject",
        ]);

        const writes = statements.filter((s) => actionsOf(s).includes("s3:PutObject"));
        expect(writes.length).toBeGreaterThan(0);
        for (const s of writes) {
            // Write and read never share a statement, so the write scope stays its own.
            expect(actionsOf(s)).toEqual(["s3:PutObject"]);
            for (const resource of [s.Resource].flat()) {
                expect(JSON.stringify(resource)).toMatch(
                    /\/pipelines\/\*"|\/pipelines\/miris-upload\/manifests\/\*"/
                );
            }
        }
    });

    test("the manifest prefix is the one the container writes under", () => {
        const source = fs.readFileSync(
            path.join(PIPELINE_DIR, "container", "dependency_manifest.py"),
            "utf-8"
        );
        expect(source).toContain(`MANIFEST_PREFIX = "${MIRIS_DEPENDENCY_MANIFEST_PREFIX}"`);
    });

    test("the job role reads the Integration Key secret it is configured with, and nothing else", () => {
        const secretStatements = statementsOf(roleId("ContainerJobRole")).filter((s) =>
            actionsOf(s).includes("secretsmanager:GetSecretValue")
        );
        expect(secretStatements).toHaveLength(1);
        expect(secretStatements[0].Resource).toBe(MIRIS_TEST_SECRET_ARN);
        // No Lambda reads the secret: the container fetches the key itself.
        for (const stem of HANDLER_STEMS) {
            expect(
                statementsOf(lambdaRoleId(`${stem}.lambda_handler`)).flatMap(actionsOf)
            ).not.toContain("secretsmanager:GetSecretValue");
        }
    });

    test("the construct Lambda touches no S3 bucket", () => {
        const actions = statementsOf(lambdaRoleId("constructPipeline.lambda_handler"))
            .flatMap(actionsOf)
            .filter((a: string) => a.startsWith("s3:"));
        expect(actions).toEqual([]);
    });

    test("the gate lists the asset buckets and writes objects only under the claim prefix", () => {
        const statements = statementsOf(lambdaRoleId("mirisUploadGate.lambda_handler"));
        const listing = statements.filter((s) => actionsOf(s).includes("s3:ListBucket"));
        expect(listing).toHaveLength(1);
        // Bucket ARNs only: a listing grant carries no object wildcard.
        expect(actionsOf(listing[0])).toEqual(["s3:ListBucket"]);
        for (const resource of [listing[0].Resource].flat()) {
            expect(JSON.stringify(resource)).not.toContain("*");
            expect(JSON.stringify(resource)).not.toContain(auxBucketId());
        }
        const objectStatements = statements.filter((s) =>
            actionsOf(s).some((a: string) => a.startsWith("s3:") && a !== "s3:ListBucket")
        );
        expect(objectStatements.length).toBeGreaterThan(0);
        for (const s of objectStatements) {
            expect(JSON.stringify(s.Resource)).toContain("/locks/miris-upload/*");
        }
    });

    test("the end Lambda reads asset-bucket objects only as the ambiguous_root report", () => {
        const reads = statementsOf(lambdaRoleId("pipelineEnd.lambda_handler")).filter(
            (s) =>
                actionsOf(s).includes("s3:GetObject") &&
                !JSON.stringify(s.Resource).includes(auxBucketId())
        );
        expect(reads).toHaveLength(1);
        expect(actionsOf(reads[0])).toEqual(["s3:GetObject"]);
        const objects = [reads[0].Resource].flat();
        expect(objects.length).toBeGreaterThan(0);
        for (const resource of objects) {
            expect(JSON.stringify(resource)).toMatch(/\/pipelines\/\*\/ambiguous_root\.json"/);
        }
    });

    test("the open Lambda lists nothing and starts only its own state machine", () => {
        const statements = statementsOf(lambdaRoleId("openPipeline.lambda_handler"));
        expect(statements.flatMap(actionsOf)).not.toContain("s3:ListBucket");
        const stateMachineId = byType("AWS::StepFunctions::StateMachine")[0][0];
        const starts = statements.filter((s) => actionsOf(s).includes("states:StartExecution"));
        expect(starts).toHaveLength(1);
        expect(starts[0].Resource).toEqual({ Ref: stateMachineId });
    });

    test("vamsExecute reads the asset record and invokes the open and gate Lambdas only", () => {
        const statements = statementsOf(lambdaRoleId("vamsExecuteMirisUpload.lambda_handler"));
        expect(statements.flatMap(actionsOf)).toContain("dynamodb:GetItem");
        const invokes = statements.filter((s) => actionsOf(s).includes("lambda:InvokeFunction"));
        expect(invokes).toHaveLength(1);
        const [openId] = functionOf("openPipeline.lambda_handler");
        const [gateId] = functionOf("mirisUploadGate.lambda_handler");
        expect([invokes[0].Resource].flat()).toEqual([
            { "Fn::GetAtt": [openId, "Arn"] },
            { "Fn::GetAtt": [gateId, "Arn"] },
        ]);
    });

    test("every task-token grant is scoped to this account and Region", () => {
        const grants = HANDLER_STEMS.flatMap((stem) =>
            statementsOf(lambdaRoleId(`${stem}.lambda_handler`)).filter((s) =>
                actionsOf(s).some((a) => a.startsWith("states:SendTask"))
            )
        );
        // Control: vamsExecute, the gate, openPipeline and pipelineEnd each report on a token.
        expect(grants).toHaveLength(4);
        for (const grant of grants) {
            const resource = JSON.stringify(grant.Resource);
            expect(resource).toContain(":states:us-east-1:123456789012:*");
        }
    });

    test("each container-role IAM5 suppression the pipeline authors names what it applies to", () => {
        const ownRules = (id: string, reason: RegExp) =>
            (resources[id].Metadata?.cdk_nag?.rules_to_suppress ?? []).filter(
                (r: any) => r.id === "AwsSolutions-IAM5" && reason.test(r.reason)
            );

        const jobRules = ownRules(roleId("ContainerJobRole"), /pipelines\/ run-output area/);
        expect(jobRules).toHaveLength(1);
        // A blanket IAM5 suppression would hide any wildcard added to the role later.
        expect(jobRules[0].applies_to.length).toBeGreaterThan(0);
        for (const target of jobRules[0].applies_to) {
            expect(typeof target).toBe("object");
            expect(target.regex.startsWith("/^")).toBe(true);
            expect(target.regex.endsWith("$/g")).toBe(true);
        }

        const executionRules = ownRules(
            roleId("ContainerExecutionRole"),
            /ecr:GetAuthorizationToken/
        );
        expect(executionRules).toHaveLength(1);
        expect(executionRules[0].applies_to).toEqual(["Resource::*"]);
    });
});

describe("Miris upload auxiliary-bucket grants", () => {
    const CLAIM_OBJECTS = "${AUX}/locks/miris-upload/*";

    /** A resource rendered as text, with the auxiliary bucket's ARN as ${AUX}. */
    const render = (value: any): string => {
        if (typeof value === "string") return value;
        if (Array.isArray(value)) return value.map(render).join("");
        if (value?.["Fn::Join"]) return value["Fn::Join"][1].map(render).join(value["Fn::Join"][0]);
        if (value?.["Fn::GetAtt"]) {
            const [id, attribute] = value["Fn::GetAtt"];
            return id === auxBucketId() && attribute === "Arn"
                ? "${AUX}"
                : `\${${id}.${attribute}}`;
        }
        return JSON.stringify(value);
    };

    /** Each S3 statement of the handler's role that names the auxiliary bucket, normalized. */
    const auxStatements = (handler: string) =>
        statementsOf(lambdaRoleId(handler))
            .map((s) => ({
                actions: actionsOf(s)
                    .filter((a) => a.startsWith("s3:"))
                    .sort(),
                resources: [s.Resource].flat().map(render).sort(),
                condition: s.Condition,
            }))
            .filter((s) => s.actions.length > 0 && s.resources.some((r) => r.includes("${AUX}")))
            .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));

    const claimObjects = (actions: string[]) => ({
        actions,
        resources: [CLAIM_OBJECTS],
        condition: undefined,
    });
    const readDelete = claimObjects(["s3:DeleteObject", "s3:GetObject"]);

    test.each([
        ["mirisUploadGate", [claimObjects(["s3:GetObject", "s3:PutObject"])]],
        ["openPipeline", [readDelete]],
        ["vamsExecuteMirisUpload", [readDelete]],
        [
            "pipelineEnd",
            [
                readDelete,
                {
                    actions: ["s3:ListBucket"],
                    resources: ["${AUX}"],
                    condition: { StringLike: { "s3:prefix": ["locks/miris-upload/*"] } },
                },
            ],
        ],
    ])("%s touches the auxiliary bucket only under the claim prefix", (stem, expected) => {
        expect(auxStatements(`${stem}.lambda_handler`)).toEqual(expected);
    });

    test("no Lambda can delete an object version or delete with a wildcard action", () => {
        for (const stem of HANDLER_STEMS) {
            const actions = statementsOf(lambdaRoleId(`${stem}.lambda_handler`)).flatMap(actionsOf);
            expect([stem, actions.filter((a) => /^s3:DeleteObject(Version|\*)/.test(a))]).toEqual([
                stem,
                [],
            ]);
        }
    });
});

describe("Miris upload Lambda environment", () => {
    test("the construct Lambda reads the add-on settings; three Lambdas get the extensions", () => {
        const miris = harness.config.app.addons.useMirisStreaming;
        expect(envOf("constructPipeline.lambda_handler")).toEqual(
            expect.objectContaining({
                MIRIS_API_BASE_URL: miris.mirisApiBaseUrl,
                MIRIS_API_KEY_SECRET_ARN: MIRIS_TEST_SECRET_ARN,
                MIRIS_UPLOAD_TASK_TIMEOUT_SECONDS: String(miris.upload.taskTimeoutSeconds),
                MIRIS_UPLOAD_MAX_ASSET_SIZE_BYTES: String(miris.upload.maxAssetSizeBytes),
            })
        );
        for (const stem of ["mirisUploadGate", "openPipeline", "constructPipeline"]) {
            expect(envOf(`${stem}.lambda_handler`).ALLOWED_INPUT_FILEEXTENSIONS).toBe(
                miris.upload.triggerExtensions
            );
        }
    });

    test("vamsExecute names the open and gate Lambdas and the asset table, and no cross-Region list", () => {
        const env = envOf("vamsExecuteMirisUpload.lambda_handler");
        const [openId] = functionOf("openPipeline.lambda_handler");
        const [gateId] = functionOf("mirisUploadGate.lambda_handler");
        const tableId = Object.keys(template.findResources("AWS::DynamoDB::Table"))[0];
        expect(env.OPEN_PIPELINE_FUNCTION_NAME).toEqual({ Ref: openId });
        expect(env.MIRIS_UPLOAD_GATE_FUNCTION_NAME).toEqual({ Ref: gateId });
        expect(env.ASSET_STORAGE_TABLE_NAME).toEqual({ Ref: tableId });
        // The pipeline runs same-Region only; the handler refuses a remote input itself.
        expect(env.CROSS_REGION_S3_ENDPOINT_REGIONS).toBeUndefined();
    });

    test.each(HANDLER_STEMS)(
        "every os.environ[...] key %s.py reads at import is set by its builder",
        (stem) => {
            const source = fs.readFileSync(
                path.join(PIPELINE_DIR, "lambda", `${stem}.py`),
                "utf-8"
            );
            const required = Array.from(source.matchAll(/os\.environ\["([A-Z_]+)"\]/g))
                .map((m) => m[1])
                // Set by the Lambda runtime.
                .filter((key) => key !== "AWS_REGION");
            const env = envOf(`${stem}.lambda_handler`);
            for (const key of required) {
                expect([stem, key, key in env]).toEqual([stem, key, true]);
            }
        }
    );
});

describe("Miris upload registration", () => {
    let registration: Template;

    beforeAll(() => {
        const h = makeMirisUploadHarness("MirisUploadRegistrationStack", {
            mutate: (c) => {
                c.app.addons.useMirisStreaming.upload.autoRegisterWithVAMS = true;
                c.app.addons.useMirisStreaming.upload.autoRegisterAutoTriggerOnFileUpload = false;
            },
        });
        registration = Template.fromStack(h.stack);
    });

    test("registers the bundle under the pipeline id the bundle's trigger names", () => {
        // Control: registration is off in the harness default.
        expect(template.findResources("AWS::CloudFormation::CustomResource")).toEqual({});
        const resourcesByType = Object.values(
            registration.findResources("AWS::CloudFormation::CustomResource")
        ) as any[];
        expect(resourcesByType).toHaveLength(1);
        const props = resourcesByType[0].Properties;
        expect(JSON.parse(props.idOverrides)).toEqual({
            pipelineId: "miris-upload",
            workflowId: "miris-upload",
        });
        expect(props.triggerEnabled).toBe("false");

        const workflow = JSON.parse(
            fs.readFileSync(path.join(PIPELINE_DIR, "vamsSchema", "workflow.json"), "utf-8")
        );
        const composites = workflow.triggers.flatMap((t: any) =>
            Object.keys(t.defaultTemplateIds ?? {})
        );
        expect(composites).toContain("GLOBAL:miris-upload");
    });
});

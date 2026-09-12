/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Locks in the IAM, environment, encryption and logging shape of the Video SOP/BOM pipeline, every part
 * of which fails silently or late at runtime.
 *
 *   1. The container JOB role — the credentials reachable from inside a container that decodes untrusted
 *      media — carries inline statements only: versioned reads on every asset bucket (two are registered
 *      here, one under a key prefix), writes only under the DEFAULT record's pipelines/ prefix and the
 *      auxiliary bucket, Bedrock on a Region-wildcard foundation-model ARN plus the configured inference
 *      profile only, Transcribe pinned to the auxiliary bucket by condition key, and task-token
 *      success/failure with no heartbeat.
 *   2. The EXECUTION role is the ECS agent's: the two managed policies, no inline policies, and on top
 *      only the image pull the ECR bind writes and the awslogs write the container log group needs.
 *   3. The state-machine role can describe and terminate the .sync job it submitted, or an abort stops
 *      the sub-execution and leaves the container running to its attempt bound.
 *   4. Each Lambda can fail and succeed the workflow callback token, holds the grants its handler's one
 *      job needs, and pipelineEnd holds nothing else.
 *   5. Each Lambda's environment is exactly the registry's key set for its handler stem, the caps are
 *      String()-wrapped config constants, and every `os.environ[...]` read in the handler's own source is
 *      a key its builder sets — a missing key is a KeyError at import that no synth, lint or
 *      env-patching unit test sees.
 *   6. Both log groups and the ECR repository take the deployment key; the log groups are vended groups
 *      with one-year retention, the job definition writes to the Pipelines group and the state machine
 *      logs ALL with execution data to the other, traced.
 *   7. Every AwsSolutions-IAM5 suppression the pipeline authors names its wildcard shape in a well-formed
 *      entry, and a nag-enabled synth of the same construct reports no IAM4/IAM5 finding at all — the
 *      deployed app runs AwsSolutionsChecks (bin/infra.ts), while every other harness here leaves it off.
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import { AwsSolutionsChecks } from "cdk-nag";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { VideoSopBomConstruct } from "../../lib/nestedStacks/pipelines/genAi/videoSopBom/constructs/videoSopBom-construct";
import { resolveVideoSopBomBedrockModelId } from "../../lib/nestedStacks/pipelines/genAi/videoSopBom/lambdaBuilder/videoSopBomFunctions";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";
const EXTERNAL_KEY_ARN = `arn:aws:kms:${REGION}:210987654321:key/external-asset-bucket-key`;
const STEMS = [
    "vamsExecuteVideoSopBomPipeline",
    "openPipeline",
    "constructPipeline",
    "pipelineEnd",
];
const LAMBDA_SOURCE_DIR = path.resolve(
    __dirname,
    "../../../backendPipelines/genAi/videoSopBom/lambda"
);

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.app.useKmsCmkEncryption.enabled = true;
    config.app.pipelines.useGenAiVideoSopBom.enabled = true;
    // Sources the container image from an ECR repository instead of a local Docker build.
    config.app.pipelines.useGenAiVideoSopBom.useCodeBuild = true;
    config.app.pipelines.useGenAiVideoSopBom.autoRegisterWithVAMS = false;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

/** The test-owned resources one construct is built against. */
interface PipelineStack {
    stack: cdk.Stack;
    kmsKey: kms.Key;
    assetBucket: s3.Bucket;
    otherBucket: s3.Bucket;
    assetAuxiliaryBucket: s3.Bucket;
}

/** Builds the construct in a fresh stack of `app`; the nag-enabled describe reuses it under an Aspect. */
const buildPipelineStack = (app: cdk.App, id: string, config: Config.Config): PipelineStack => {
    const stack = new cdk.Stack(app, id, { env: { account: ACCOUNT, region: REGION } });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];
    const kmsKey = new kms.Key(stack, "Key");
    const assetAuxiliaryBucket = new s3.Bucket(stack, "AuxBucket");

    // Two asset-bucket records. The first is the default (run) bucket at the root prefix, encrypted with
    // an external customer managed key. The second is NOT the default and sits under a key prefix: it is
    // what separates "every registered bucket" from "the only bucket", and "the isDefault record" from
    // "records[0]" — with one record both readings pass.
    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    const otherBucket = new s3.Bucket(stack, "OtherAssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, EXTERNAL_KEY_ARN, true);
    s3AssetBuckets.addS3AssetBucket(otherBucket, "/team-a/", "db2");

    const storage = {
        encryption: { kmsKey },
        s3: {
            assetAuxiliaryBucket,
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    new VideoSopBomConstruct(stack, "VideoSopBomPipeline", {
        config,
        vpc,
        pipelineSubnets: vpc.privateSubnets,
        pipelineSecurityGroups: securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        assetAuxiliaryBucket,
        storageResources: storage,
        kmsKey,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });

    return { stack, kmsKey, assetBucket, otherBucket, assetAuxiliaryBucket };
};

let mockConfig: Config.Config;
let template: Template;
let sharedKeyLogicalId: string;
let assetBucketLogicalId: string;
let otherBucketLogicalId: string;
let auxBucketLogicalId: string;

beforeAll(() => {
    mockConfig = createMockConfig();
    Service.SetConfig(mockConfig);

    const built = buildPipelineStack(newTestApp(), "VideoSopBomGrantsTestStack", mockConfig);
    template = Template.fromStack(built.stack);
    sharedKeyLogicalId = built.stack.resolve(built.kmsKey.keyArn)["Fn::GetAtt"][0];
    assetBucketLogicalId = built.stack.resolve(built.assetBucket.bucketArn)["Fn::GetAtt"][0];
    otherBucketLogicalId = built.stack.resolve(built.otherBucket.bucketArn)["Fn::GetAtt"][0];
    auxBucketLogicalId = built.stack.resolve(built.assetAuxiliaryBucket.bucketName).Ref;
});

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

/** Resources may be plain ARNs or unresolved intrinsics, so they stay untyped. */
const resourcesOf = (statement: any): any[] =>
    Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource];

/** Fn::Join / Ref / Fn::GetAtt flattened to one string, in the `<LogicalId.Attr>` form cdk-nag prints. */
const flatten = (value: any): string => {
    if (typeof value === "string") return value;
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(flatten).join("");
    if (value["Fn::Join"]) {
        const [separator, parts] = value["Fn::Join"];
        return (parts as any[]).map(flatten).join(separator);
    }
    if (value.Ref) return `<${value.Ref}>`;
    if (value["Fn::GetAtt"]) return `<${(value["Fn::GetAtt"] as any[]).join(".")}>`;
    return JSON.stringify(value);
};

/** The single role whose logical id contains the fragment: [logicalId, Properties]. */
const roleNamed = (roleIdFragment: string): [string, any] => {
    const roles = Object.entries(template.findResources("AWS::IAM::Role")).filter(([logicalId]) =>
        logicalId.includes(roleIdFragment)
    );
    expect(roles).toHaveLength(1);
    return [roles[0][0], (roles[0][1] as any).Properties];
};

/** Inline-policy statements of the single role whose logical id contains the fragment. */
const inlineStatements = (roleIdFragment: string): any[] =>
    ((roleNamed(roleIdFragment)[1].Policies ?? []) as any[]).flatMap(
        (policy) => policy.PolicyDocument.Statement
    );

/**
 * Every attached policy document in the template: [logicalId, Properties]. A role's default policy can
 * spill into AWS::IAM::ManagedPolicy when it outgrows the inline size limit, and a scan of
 * AWS::IAM::Policy alone would then report correct grants as missing (or pass a negative over nothing).
 */
const policyResources = (): Array<[string, any]> =>
    Object.entries({
        ...template.findResources("AWS::IAM::Policy"),
        ...template.findResources("AWS::IAM::ManagedPolicy"),
    }).map(([logicalId, policy]) => [logicalId, (policy as any).Properties]);

/** Statements of every attached policy whose logical id contains the fragment. */
const attachedStatements = (policyIdFragment: string): any[] =>
    policyResources()
        .filter(([logicalId]) => logicalId.includes(policyIdFragment))
        .flatMap(([, properties]) => properties.PolicyDocument.Statement);

/** Statements of every attached policy whose Roles list references the role by logical id. */
const statementsAttachedToRole = (roleLogicalId: string): any[] =>
    policyResources()
        .filter(([, properties]) =>
            ((properties.Roles ?? []) as any[]).some((role) => role.Ref === roleLogicalId)
        )
        .flatMap(([, properties]) => properties.PolicyDocument.Statement);

/** True when a statement allows kms:Decrypt on the shared key. */
const grantsSharedKeyDecrypt = (statement: any): boolean =>
    actionsOf(statement).includes("kms:Decrypt") &&
    resourcesOf(statement).some((resource) => flatten(resource) === `<${sharedKeyLogicalId}.Arn>`);

/** Logical id of the log group whose name starts with the prefix. */
const logGroupLogicalId = (prefix: string): string => {
    const groups = Object.entries(template.findResources("AWS::Logs::LogGroup")).filter(
        ([, group]) => String((group as any).Properties.LogGroupName).startsWith(prefix)
    );
    expect(groups).toHaveLength(1);
    return groups[0][0];
};
const containerLogGroupLogicalId = () => logGroupLogicalId("/aws/vendedlogs/Pipelines/VideoSopBom");
const stateMachineLogGroupLogicalId = () =>
    logGroupLogicalId("/aws/vendedlogs/VAMSStateMachine-VideoSopBom");

/** The one state machine in the template: [logicalId, Properties]. */
const stateMachine = (): [string, any] => {
    const machines = Object.entries(template.findResources("AWS::StepFunctions::StateMachine"));
    expect(machines).toHaveLength(1);
    return [machines[0][0], (machines[0][1] as any).Properties];
};

/** The one Lambda whose handler is `<stem>.lambda_handler`: [logicalId, Properties]. */
const lambdaNamed = (stem: string): [string, any] => {
    const functions = Object.entries(
        template.findResources("AWS::Lambda::Function", {
            Properties: { Handler: `${stem}.lambda_handler` },
        })
    );
    expect(functions).toHaveLength(1);
    return [functions[0][0], (functions[0][1] as any).Properties];
};

/**
 * Why a `/…/flags` appliesTo regex entry is malformed, or null when it is well formed: cdk-nag parses the
 * entry by its first and last slash (`nag-suppression-helper.js`), a body that does not compile is
 * skipped silently, a catch-all over `Resource::` suppresses every wildcard under the scope, and an
 * unescaped `*` is "zero or more of the preceding character", which matches nothing.
 */
const regexEntryDefect = (value: string): string | null => {
    const wrapped = /^\/(.*)\/([a-z]*)$/.exec(value);
    if (!wrapped) return "not wrapped in slashes";
    const [, body, flags] = wrapped;
    try {
        new RegExp(body, flags);
    } catch (error) {
        return "does not compile";
    }
    if (/^\^?Resource::\.\*\$?$/.test(body)) return "catch-all over Resource";
    // Every `*` meant literally is written `\*` (source `\\*`); `.*` is the wildcard the shapes need.
    if (/(^|[^\\.])\*/.test(body.replace(/\.\*/g, ""))) return "unescaped literal asterisk";
    return null;
};

describe("VideoSopBomContainerJobRole", () => {
    test("[control] carries exactly the seven inline policy documents this file reads", () => {
        const [, role] = roleNamed("VideoSopBomContainerJobRole");
        const names = ((role.Policies ?? []) as any[]).map((policy) => policy.PolicyName).sort();
        expect(names).toEqual([
            "AuxBucketPolicy",
            "BedrockPolicy",
            "InputBucketPolicy",
            "KmsKeyPolicy",
            "RunBucketPolicy",
            "StateTaskPolicy",
            "TranscribePolicy",
        ]);
    });

    test("carries no managed policy", () => {
        // The execution role holds the ECS agent's managed policies; the job role must not, or a
        // container defect can pull any image in the account and write to any log group.
        expect(roleNamed("VideoSopBomContainerJobRole")[1].ManagedPolicyArns).toBeUndefined();
    });

    test("reads every registered asset bucket under its own prefix, including a specific object version", () => {
        const statements = inlineStatements("VideoSopBomContainerJobRole").filter((statement) =>
            actionsOf(statement).includes("s3:GetObjectVersion")
        );
        // One statement per registered record.
        expect(statements).toHaveLength(2);
        const resources = statements.flatMap(resourcesOf).map(flatten);
        expect(resources).toContain(`<${assetBucketLogicalId}.Arn>`);
        expect(resources).toContain(`<${assetBucketLogicalId}.Arn>/*`);
        // The second, non-default record: its bucket, and its objects under the registered prefix only —
        // a construct that dropped the prefix would render `/*` here.
        expect(resources).toContain(`<${otherBucketLogicalId}.Arn>`);
        expect(resources).toContain(`<${otherBucketLogicalId}.Arn>/team-a/*`);
        expect(resources).not.toContain(`<${otherBucketLogicalId}.Arn>/*`);
        for (const statement of statements) {
            expect(actionsOf(statement)).not.toContain("s3:PutObject");
        }
    });

    test("writes only under the DEFAULT record's pipelines/ prefix and to the auxiliary bucket", () => {
        const putStatements = inlineStatements("VideoSopBomContainerJobRole").filter((statement) =>
            actionsOf(statement).includes("s3:PutObject")
        );
        expect(putStatements).toHaveLength(2);
        const resources = putStatements.flatMap(resourcesOf).map(flatten).sort();
        expect(resources).toEqual(
            [
                `<${assetBucketLogicalId}.Arn>/pipelines/*`,
                `<${auxBucketLogicalId}.Arn>`,
                `<${auxBucketLogicalId}.Arn>/*`,
            ].sort()
        );
        // The non-default record is a registered asset bucket and nothing may be written to it. Implied
        // by the exact list above; stated on its own so a failure names the cause.
        expect(resources.some((resource) => resource.includes(otherBucketLogicalId))).toBe(false);
    });

    test("invokes Bedrock on the model (Region-wildcard) and the configured inference profile only", () => {
        const statement = inlineStatements("VideoSopBomContainerJobRole").find((entry) =>
            actionsOf(entry).includes("bedrock:InvokeModel")
        );
        expect(statement).toBeDefined();
        expect(actionsOf(statement)).toContain("bedrock:InvokeModelWithResponseStream");
        const resources = resourcesOf(statement).map(flatten);
        expect(resources).toHaveLength(3);
        const wildcardRegion = resources.filter((resource) =>
            /^arn:aws:bedrock:\*::foundation-model\/[^*]+$/.test(resource)
        );
        expect(wildcardRegion).toHaveLength(1);
        // The cross-Region inference-profile prefix is stripped: the ARN names the model, not the profile.
        expect(wildcardRegion[0]).not.toMatch(
            /foundation-model\/(global|us-gov|us|eu|apac|au|jp)\./
        );
        // The inference-profile ARN names the configured id exactly; no profile wildcard is granted.
        const configuredModelId = mockConfig.app.pipelines.useGenAiVideoSopBom.bedrockModelId;
        expect(configuredModelId).toMatch(/^global\./);
        expect(resources).toContain(
            `arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/${configuredModelId}`
        );
        expect(resources.some((resource) => /inference-profile\/\*/.test(resource))).toBe(false);
        expect(resources.some((resource) => /\*$/.test(resource))).toBe(false);
    });

    test("starts transcription jobs only into the auxiliary bucket under the deployment key", () => {
        const start = inlineStatements("VideoSopBomContainerJobRole").find((entry) =>
            actionsOf(entry).includes("transcribe:StartTranscriptionJob")
        );
        expect(start).toBeDefined();
        // No resource type exists for this action; the condition keys are the scope.
        expect(resourcesOf(start)).toEqual(["*"]);
        const equals = start.Condition.StringEquals;
        expect(flatten(equals["transcribe:OutputBucketName"])).toBe(`<${auxBucketLogicalId}>`);
        expect(flatten(equals["transcribe:OutputEncryptionKMSKeyId"])).toBe(
            `<${sharedKeyLogicalId}.Arn>`
        );
    });

    test("reads and deletes only its own transcription jobs", () => {
        const statement = inlineStatements("VideoSopBomContainerJobRole").find((entry) =>
            actionsOf(entry).includes("transcribe:GetTranscriptionJob")
        );
        expect(statement).toBeDefined();
        expect(actionsOf(statement)).toContain("transcribe:DeleteTranscriptionJob");
        expect(resourcesOf(statement).map(flatten)).toEqual([
            `arn:aws:transcribe:${REGION}:${ACCOUNT}:transcription-job/vams-video-sop-bom-*`,
        ]);
    });

    test("releases the inner task token in both directions and never heartbeats", () => {
        const statements = inlineStatements("VideoSopBomContainerJobRole");
        const actions = statements.flatMap(actionsOf);
        expect(actions).toContain("states:SendTaskSuccess");
        expect(actions).toContain("states:SendTaskFailure");
        expect(actions).not.toContain("states:SendTaskHeartbeat");
        const statement = statements.find((entry) =>
            actionsOf(entry).includes("states:SendTaskFailure")
        );
        expect(resourcesOf(statement).map(flatten)).toEqual([
            `arn:aws:states:${REGION}:${ACCOUNT}:*`,
        ]);
    });

    test("can use the shared KMS key once, and the external asset-bucket key", () => {
        expect(
            inlineStatements("VideoSopBomContainerJobRole").filter(grantsSharedKeyDecrypt)
        ).toHaveLength(1);
        const externalKeyGrant = attachedStatements("VideoSopBomContainerJobRole").find(
            (statement) => resourcesOf(statement).includes(EXTERNAL_KEY_ARN)
        );
        expect(externalKeyGrant).toBeDefined();
        expect(actionsOf(externalKeyGrant)).toContain("kms:Decrypt");
    });
});

describe("VideoSopBomContainerExecutionRole", () => {
    test("carries no inline policies and both managed policies the ECS agent needs", () => {
        const [, role] = roleNamed("VideoSopBomContainerExecutionRole");
        // Undefined rather than an empty array: CDK omits the property when there are none.
        expect(role.Policies).toBeUndefined();
        const managed = JSON.stringify(role.ManagedPolicyArns ?? []);
        expect(managed).toContain("AmazonECSTaskExecutionRolePolicy");
        expect(managed).toContain("AWSXrayWriteOnlyAccess");
    });

    test("its attached policy grants only the image pull and the container log-stream write", () => {
        const [roleLogicalId] = roleNamed("VideoSopBomContainerExecutionRole");
        const statements = statementsAttachedToRole(roleLogicalId);
        const actions = statements.flatMap(actionsOf);
        // The awslogs driver's grant, scoped to the container log group.
        expect(actions).toEqual(
            expect.arrayContaining(["logs:CreateLogStream", "logs:PutLogEvents"])
        );
        // The image pull: EcsContainerDefinitionBase binds the image with this role as
        // `obtainExecutionRole`, and ContainerImage.fromEcrRepository's bind calls repository.grantPull on
        // it — the three repository-scoped pull actions plus the account-level token exchange on `*`.
        expect(actions).toEqual(
            expect.arrayContaining([
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:GetAuthorizationToken",
            ])
        );
        expect(actions.filter((a) => !a.startsWith("logs:") && !a.startsWith("ecr:"))).toEqual([]);
        const logStatement = statements.find((s) => actionsOf(s).includes("logs:PutLogEvents"));
        expect(resourcesOf(logStatement).map(flatten)).toEqual([
            `<${containerLogGroupLogicalId()}.Arn>`,
        ]);
        const tokenStatement = statements.find((s) =>
            actionsOf(s).includes("ecr:GetAuthorizationToken")
        );
        expect(resourcesOf(tokenStatement)).toEqual(["*"]);
    });
});

describe("VideoSopBomProcessing state machine role", () => {
    test("can submit, describe and terminate the .sync Batch job", () => {
        const actions = attachedStatements("StateMachineRoleDefaultPolicy").flatMap(actionsOf);
        expect(actions).toContain("batch:SubmitJob");
        expect(actions).toContain("batch:DescribeJobs");
        expect(actions).toContain("batch:TerminateJob");
    });

    test("TerminateJob is scoped to this account's jobs; DescribeJobs has no resource type", () => {
        const statements = attachedStatements("StateMachineRoleDefaultPolicy");
        const terminate = statements.find((s) => actionsOf(s).includes("batch:TerminateJob"));
        expect(resourcesOf(terminate).map(flatten)).toEqual([
            `arn:aws:batch:${REGION}:${ACCOUNT}:job/*`,
        ]);
        const describe = statements.find((s) => actionsOf(s).includes("batch:DescribeJobs"));
        expect(resourcesOf(describe)).toEqual(["*"]);
    });
});

describe("the four pipeline Lambdas", () => {
    /**
     * What each handler's one job needs, beside the token grant every stem carries. `grantRead` spells
     * the object read as `s3:GetObject*`, `grantPut` includes the plain `s3:PutObject`, and the shared KMS
     * statement includes `kms:Decrypt`; pipelineEnd's only AWS call is the token release.
     */
    const MUST_GRANT: Record<string, string[]> = {
        vamsExecuteVideoSopBomPipeline: ["lambda:InvokeFunction", "s3:GetObject*", "kms:Decrypt"],
        openPipeline: ["states:StartExecution", "events:PutEvents"],
        constructPipeline: ["s3:GetObject*", "s3:PutObject", "kms:Decrypt"],
        pipelineEnd: [],
    };

    test.each(STEMS)("%s can fail and succeed the workflow callback token", (stem) => {
        const statements = attachedStatements(stem);
        // Control: an empty statement list would satisfy the action assertions vacuously, and the
        // logical-id filter is exactly the kind of predicate that silently matches nothing after a
        // rename.
        expect(statements.length).toBeGreaterThan(0);
        const actions = statements.flatMap(actionsOf);
        expect(actions).toContain("states:SendTaskFailure");
        expect(actions).toContain("states:SendTaskSuccess");
        expect(actions).toEqual(expect.arrayContaining(MUST_GRANT[stem]));
        if (stem === "pipelineEnd") {
            // Nothing beyond the token release and the SSM resource-name read every VAMS Lambda gets
            // from globalLambdaEnvironmentsAndPermissions.
            expect(
                actions.filter((a) => !a.startsWith("states:SendTask") && !a.startsWith("ssm:"))
            ).toEqual([]);
        }
    });

    test("openPipeline starts THIS state machine and vamsExecute invokes THIS openPipeline", () => {
        // The action alone would pass against any state machine or function in the account.
        const [stateMachineLogicalId] = stateMachine();
        const start = attachedStatements("openPipeline").find((s) =>
            actionsOf(s).includes("states:StartExecution")
        );
        expect(resourcesOf(start).map(flatten)).toEqual([`<${stateMachineLogicalId}>`]);

        const [openPipelineLogicalId] = lambdaNamed("openPipeline");
        const invoke = attachedStatements("vamsExecuteVideoSopBomPipeline").find((s) =>
            actionsOf(s).includes("lambda:InvokeFunction")
        );
        // grantInvoke names the function and its version/alias qualifiers.
        expect(resourcesOf(invoke).map(flatten).sort()).toEqual(
            [`<${openPipelineLogicalId}.Arn>`, `<${openPipelineLogicalId}.Arn>:*`].sort()
        );
    });
});

describe("Lambda environment", () => {
    /** The registry table, one row per handler stem. */
    const EXPECTED_ENV: Record<string, string[]> = {
        vamsExecuteVideoSopBomPipeline: [
            "OPEN_PIPELINE_FUNCTION_NAME",
            "ALLOWED_INPUT_FILEEXTENSIONS",
            "VIDEO_SOP_BOM_MAX_VIDEO_FILES",
            "VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB",
            "VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB",
        ],
        openPipeline: [
            "STATE_MACHINE_ARN",
            "ALLOWED_INPUT_FILEEXTENSIONS",
            "ORCHESTRATION_BUS_NAME",
            "STATE_MACHINE_LOG_GROUP_NAME",
            "STATE_MACHINE_LOG_GROUP_ARN",
        ],
        constructPipeline: [
            "VIDEO_SOP_BOM_MAX_VIDEO_FILES",
            "VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB",
            "VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB",
            "VIDEO_SOP_BOM_MAX_TOTAL_DURATION_MINUTES",
            "VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING",
            "BEDROCK_MODEL_ID",
            "KMS_KEY_ARN",
        ],
        pipelineEnd: [],
    };
    /** Every key any row names; the comparison below is over this universe, so a key that leaked from
     *  one stem to another is caught and the keys globalLambdaEnvironmentsAndPermissions adds are not. */
    const REGISTRY_KEYS = new Set(Object.values(EXPECTED_ENV).flat());

    const variablesOf = (stem: string): Record<string, any> =>
        lambdaNamed(stem)[1].Environment?.Variables ?? {};

    test.each(STEMS)("%s carries exactly the registry's keys for its stem", (stem) => {
        const pipelineKeys = Object.keys(variablesOf(stem))
            .filter((key) => REGISTRY_KEYS.has(key))
            .sort();
        expect(pipelineKeys).toEqual([...EXPECTED_ENV[stem]].sort());
    });

    test("the values are the config constants, String()-wrapped, and the construct's own references", () => {
        const vamsExecute = variablesOf("vamsExecuteVideoSopBomPipeline");
        const openPipeline = variablesOf("openPipeline");
        const constructPipeline = variablesOf("constructPipeline");
        const limits = mockConfig.app.pipelines.useGenAiVideoSopBom.limits;

        for (const vars of [vamsExecute, constructPipeline]) {
            expect(vars.VIDEO_SOP_BOM_MAX_VIDEO_FILES).toBe(String(limits.maxVideoFiles));
            expect(vars.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB).toBe(
                String(Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB)
            );
            expect(vars.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB).toBe(
                String(Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB)
            );
        }
        expect(constructPipeline.VIDEO_SOP_BOM_MAX_TOTAL_DURATION_MINUTES).toBe(
            String(limits.maxTotalDurationMinutes)
        );
        expect(constructPipeline.VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING).toBe(
            String(Config.VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING)
        );
        expect(constructPipeline.BEDROCK_MODEL_ID).toBe(
            resolveVideoSopBomBedrockModelId(mockConfig)
        );
        expect(flatten(constructPipeline.KMS_KEY_ARN)).toBe(`<${sharedKeyLogicalId}.Arn>`);

        // The five-places extension rule: the same literal the bundle and the handler default carry.
        for (const vars of [vamsExecute, openPipeline]) {
            expect(vars.ALLOWED_INPUT_FILEEXTENSIONS).toBe(".mp4,.mov,.m4v,.webm,.mkv");
        }
        const [openPipelineLogicalId] = lambdaNamed("openPipeline");
        expect(flatten(vamsExecute.OPEN_PIPELINE_FUNCTION_NAME)).toBe(`<${openPipelineLogicalId}>`);
        const [stateMachineLogicalId] = stateMachine();
        expect(flatten(openPipeline.STATE_MACHINE_ARN)).toBe(`<${stateMachineLogicalId}>`);
        expect(flatten(openPipeline.STATE_MACHINE_LOG_GROUP_NAME)).toBe(
            `<${stateMachineLogGroupLogicalId()}>`
        );
        expect(flatten(openPipeline.STATE_MACHINE_LOG_GROUP_ARN)).toBe(
            `<${stateMachineLogGroupLogicalId()}.Arn>`
        );
    });

    test.each(STEMS)(
        "%s: every os.environ[...] its handler reads at import is a key the builder sets",
        (stem) => {
            // The pairing control against the handlers. `os.environ["NAME"]` has no default, so a name the builder
            // does not set is a KeyError on cold start; `.get(...)` reads carry their own default and are
            // not required here. readFileSync throws when the handler is absent — never skips.
            const source = fs.readFileSync(path.join(LAMBDA_SOURCE_DIR, `${stem}.py`), "utf-8");
            const required = [...source.matchAll(/os\.environ\[\s*["']([A-Z0-9_]+)["']\s*\]/g)]
                .map((match) => match[1])
                // Provided by the Lambda runtime, not by the builder.
                .filter((name) => !name.startsWith("AWS_"));
            if (stem !== "pipelineEnd") {
                // Control: a handler that read nothing would pass the loop below over an empty list.
                expect(required.length).toBeGreaterThan(0);
            }
            const keys = Object.keys(variablesOf(stem));
            for (const name of required) {
                expect(keys).toContain(name);
            }
        }
    );
});

describe("encryption at rest and logging", () => {
    test.each([
        "/aws/vendedlogs/VAMSStateMachine-VideoSopBom",
        "/aws/vendedlogs/Pipelines/VideoSopBom",
    ])("%s<hash10> is KMS-encrypted with one-year retention", (prefix) => {
        const group = template.toJSON().Resources[logGroupLogicalId(prefix)].Properties;
        expect(String(group.LogGroupName)).toMatch(
            new RegExp(`^${prefix.replace(/[/]/g, "\\/")}[0-9a-f]{10}$`)
        );
        expect(group.RetentionInDays).toBe(365);
        expect(flatten(group.KmsKeyId)).toBe(`<${sharedKeyLogicalId}.Arn>`);
    });

    test("the ECR repository encrypts its image layers with the deployment key", () => {
        const repositories = Object.values(template.findResources("AWS::ECR::Repository")) as any[];
        expect(repositories).toHaveLength(1);
        const encryption = repositories[0].Properties.EncryptionConfiguration;
        expect(encryption.EncryptionType).toBe("KMS");
        expect(flatten(encryption.KmsKey)).toBe(`<${sharedKeyLogicalId}.Arn>`);
        expect(repositories[0].Properties.RepositoryName).toBe(
            `${(commercialTemplate as any).name}-vams-test-videosopbom`.toLowerCase()
        );
    });

    test("the job definition writes its container stream to the Pipelines group", () => {
        const jobDefinitions = Object.values(
            template.findResources("AWS::Batch::JobDefinition")
        ) as any[];
        expect(jobDefinitions).toHaveLength(1);
        const logConfiguration = jobDefinitions[0].Properties.ContainerProperties.LogConfiguration;
        expect(logConfiguration.LogDriver).toBe("awslogs");
        expect(logConfiguration.Options["awslogs-group"]).toEqual({
            Ref: containerLogGroupLogicalId(),
        });
        // `VideoSopBomJob_<config.name>_<baseStackName>` plus the construct's 10-hex hash; the name is
        // read from the shipped template rather than restated.
        expect(String(jobDefinitions[0].Properties.JobDefinitionName)).toMatch(
            new RegExp(`^VideoSopBomJob_${(commercialTemplate as any).name}_vams-test[0-9a-f]{10}$`)
        );
    });

    test("the state machine logs ALL with execution data to the VAMSStateMachine group, traced", () => {
        // The group existing, encrypted and retained proves nothing about who writes to it: a construct
        // that dropped the `logs:` block or pointed it elsewhere leaves every assertion above green.
        const [, properties] = stateMachine();
        expect(properties.LoggingConfiguration.Level).toBe("ALL");
        expect(properties.LoggingConfiguration.IncludeExecutionData).toBe(true);
        expect(
            flatten(
                properties.LoggingConfiguration.Destinations[0].CloudWatchLogsLogGroup.LogGroupArn
            )
        ).toBe(`<${stateMachineLogGroupLogicalId()}.Arn>`);
        expect(properties.TracingConfiguration.Enabled).toBe(true);
    });
});

describe("cdk-nag suppressions authored by the pipeline", () => {
    test("every AwsSolutions-IAM5 entry names a well-formed wildcard shape it covers", () => {
        // Scans the whole template, the CodeBuild child included: every IAM5 entry this pipeline writes
        // carries appliesTo (master plan Global Constraints), and each entry is a shape cdk-nag can act
        // on. Applied-with-children suppressions from the shared helpers are stamped on the same
        // resources and are checked by the same rule.
        const resources = template.toJSON().Resources as Record<string, any>;
        const offenders: string[] = [];
        let examined = 0;
        for (const [logicalId, resource] of Object.entries(resources)) {
            const rules: any[] = resource.Metadata?.cdk_nag?.rules_to_suppress ?? [];
            for (const rule of rules) {
                if (rule.id !== "AwsSolutions-IAM5") continue;
                examined++;
                const shapes: any[] = Array.isArray(rule.applies_to) ? rule.applies_to : [];
                if (shapes.length === 0) {
                    offenders.push(`${logicalId}: no appliesTo (${rule.reason})`);
                    continue;
                }
                for (const shape of shapes) {
                    if (typeof shape === "string") {
                        if (!/^(Resource|Action)::/.test(shape)) {
                            offenders.push(
                                `${logicalId}: ${shape} (not a Resource:: or Action:: finding)`
                            );
                        }
                    } else {
                        const defect = regexEntryDefect(String(shape?.regex));
                        if (defect) offenders.push(`${logicalId}: ${shape?.regex} (${defect})`);
                    }
                }
            }
        }
        // Control: the job role alone carries seven entries, so a scan that found fewer is broken.
        expect(examined).toBeGreaterThan(6);
        expect(offenders).toEqual([]);
    });

    test("[control] the shape validator rejects the four malformed forms and accepts the shipped ones", () => {
        expect(regexEntryDefect("^Resource::\\*$/g")).toBe("not wrapped in slashes");
        expect(regexEntryDefect("/^Resource::(\\*$/g")).toBe("does not compile");
        expect(regexEntryDefect("/^Resource::.*$/g")).toBe("catch-all over Resource");
        expect(regexEntryDefect("/^Resource::*$/g")).toBe("unescaped literal asterisk");
        expect(regexEntryDefect("/^Resource::\\*$/g")).toBeNull();
        expect(regexEntryDefect("/^Resource::<.*Bucket.*\\.Arn>/.*\\*$/g")).toBeNull();
        expect(regexEntryDefect("/^Resource::arn:.*:states:.*:\\*$/g")).toBeNull();
    });
});

describe("a nag-enabled synth of the construct", () => {
    // The deployed app adds AwsSolutionsChecks (bin/infra.ts) and every deploy therefore runs it; the T1
    // harness and the direct harnesses above leave it off, so a suppression whose shape misses its
    // finding is invisible to every other test in this WP and fails only the real deploy. This builds
    // the same construct under the Aspect and reads the findings back.
    let nagStack: cdk.Stack;

    beforeAll(() => {
        const config = createMockConfig();
        config.enableCdkNag = true;
        Service.SetConfig(config);
        const app = newTestApp();
        cdk.Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
        nagStack = buildPipelineStack(app, "VideoSopBomNagTestStack", config).stack;
        Template.fromStack(nagStack);
    });

    const errorsMatching = (pattern: string): string[] =>
        Annotations.fromStack(nagStack)
            .findError("*", Match.stringLikeRegexp(pattern))
            .map((message) => `${message.id}: ${String(message.entry.data)}`);

    test("[control] the Aspect ran: the harness's own plain buckets are reported", () => {
        // Without this, "no IAM finding" is also what an app with no Aspect reports. AwsSolutions-S1
        // (server access logs disabled) fires on every test-owned s3.Bucket here.
        expect(errorsMatching("AwsSolutions-S1").length).toBeGreaterThan(0);
    });

    test("reports no AwsSolutions-IAM4 or AwsSolutions-IAM5 finding anywhere in the stack", () => {
        // Only IAM findings: the test-owned VPC, buckets and key carry their own non-IAM findings, and the
        // construct's own roles are the only IAM entities in the stack. A leftover finding prints
        // `AwsSolutions-IAM5[Resource::…]` or `[Action::…]` — the exact appliesTo string to add.
        expect(errorsMatching("AwsSolutions-IAM[45]")).toEqual([]);
    });
});

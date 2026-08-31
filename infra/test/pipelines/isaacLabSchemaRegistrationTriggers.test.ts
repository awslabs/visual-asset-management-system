/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Locks in that the Isaac Lab registrations register no fileUpload trigger, and that removing one from
 * a bundle actually re-runs the registration custom resource.
 *
 *   1. Both Isaac Lab pipelines are launched manually. The registration construct only forces a
 *      trigger's `enabled` flag when `triggerEnabled` is supplied, so a trigger the bundle ships is
 *      registered as a workflow-triggers row whatever the deployment configures — the bundle not
 *      declaring one is the only thing that keeps the row out of the table.
 *   2. `schemaHash` is what makes CloudFormation re-invoke the custom resource. It must cover the
 *      workflow file's trigger declaration, or an edit that drops a trigger leaves the resource
 *      properties unchanged, the CR never re-runs, and the already-registered row is never rewritten.
 *   3. The bundles that DO ship a trigger still get `triggerEnabled` wired from their pipeline config.
 *      An edit that stripped the wiring more widely would leave those triggers stuck on their schema
 *      default with no deploy-time control.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { IsaacLabTrainingConstruct } from "../../lib/nestedStacks/pipelines/simulation/isaacLabTraining/constructs/isaacLabTraining-construct";
import { VamsSchemaRegistration } from "../../lib/nestedStacks/pipelines/constructs/vamsSchemaRegistration-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

const BACKEND_PIPELINES = path.join(__dirname, "..", "..", "..", "backendPipelines");
const ISAACLAB_SCHEMA_ROOT = path.join(
    BACKEND_PIPELINES,
    "simulation",
    "isaacLabTraining",
    "vamsSchema"
);
/** A bundle that legitimately ships a trigger, used as the control for every absence assertion. */
const TRIGGER_SHIPPING_BUNDLE = path.join(
    BACKEND_PIPELINES,
    "preview",
    "3dThumbnail",
    "vamsSchema"
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
    config.app.pipelines.useIsaacLabTraining.enabled = true;
    config.app.pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
    config.app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS = true;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

/** A registration custom resource, keyed by the pipeline id its idOverrides name. */
interface Registration {
    pipelineId: string;
    properties: Record<string, any>;
}

function registrationsIn(template: Template): Registration[] {
    return Object.values(template.findResources("AWS::CloudFormation::CustomResource"))
        .map((r) => (r as any).Properties as Record<string, any>)
        .filter((properties) => properties.bundleS3Keys !== undefined)
        .map((properties) => ({
            pipelineId: JSON.parse(properties.idOverrides || "{}").pipelineId || "",
            properties,
        }));
}

let isaacLabRegistrations: Registration[];
let controlRegistration: Registration | undefined;

beforeAll(() => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, "IsaacLabRegistrationTestStack", {
        env: { account: ACCOUNT, region: REGION },
    });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const kmsKey = new kms.Key(stack, "Key");
    const artefactsBucket = new s3.Bucket(stack, "ArtefactsBucket");

    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(new s3.Bucket(stack, "AssetBucket"), "/", "db");

    const storage = {
        encryption: { kmsKey },
        s3: { assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"), artefactsBucket },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    new IsaacLabTrainingConstruct(stack, "IsaacLabTrainingPipeline", {
        config,
        vpc,
        pipelineSubnets: vpc.privateSubnets,
        pipelineSubnetsIsolated: vpc.isolatedSubnets,
        pipelineSecurityGroups: [new ec2.SecurityGroup(stack, "Sg", { vpc })],
        storageResources: storage,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflowV2",
        // Sources the container image from ECR so synth does not run a local Docker build.
        codeBuildRepository: ecr.Repository.fromRepositoryName(stack, "Repo", "isaaclab"),
    });

    // The control: the same construct the pipeline stacks use, invoked the way a trigger-shipping
    // pipeline invokes it. Without it, "no triggerEnabled property" would also pass against a
    // construct that had stopped emitting the property at all.
    new VamsSchemaRegistration(stack, "ControlRegistration", {
        importFunctionName: "importGlobalPipelineWorkflowV2",
        artefactsBucket,
        vamsSchemaDir: TRIGGER_SHIPPING_BUNDLE,
        idOverrides: { pipelineId: "preview-3d-thumbnail", workflowId: "preview-3d-thumbnail" },
        triggerEnabled: true,
    });

    const template = Template.fromStack(stack);
    const all = registrationsIn(template);
    isaacLabRegistrations = all.filter((r) => r.pipelineId.startsWith("isaaclab-"));
    controlRegistration = all.find((r) => r.pipelineId === "preview-3d-thumbnail");
});

describe("Isaac Lab vamsSchema registrations", () => {
    test("both Isaac Lab bundles are registered", () => {
        expect(isaacLabRegistrations.map((r) => r.pipelineId).sort()).toEqual([
            "isaaclab-evaluation",
            "isaaclab-training",
        ]);
    });

    test("neither registration emits a triggerEnabled property", () => {
        for (const registration of isaacLabRegistrations) {
            expect(Object.keys(registration.properties)).not.toContain("triggerEnabled");
        }
    });

    test("a trigger-shipping registration does emit triggerEnabled", () => {
        // Positive control for the assertion above.
        expect(controlRegistration?.properties.triggerEnabled).toEqual("true");
    });
});

/** Synthesizes one standalone registration over `dir` and returns the schemaHash CFN would diff. */
function schemaHashOf(dir: string): string {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "HashStack", { env: { account: ACCOUNT, region: REGION } });
    new VamsSchemaRegistration(stack, "Reg", {
        importFunctionName: "importGlobalPipelineWorkflowV2",
        artefactsBucket: s3.Bucket.fromBucketName(stack, "Artefacts", "artefacts-bucket"),
        vamsSchemaDir: dir,
        idOverrides: { pipelineId: "isaaclab-evaluation", workflowId: "isaaclab-evaluation" },
    });
    const hashes = registrationsIn(Template.fromStack(stack)).map((r) => r.properties.schemaHash);
    expect(hashes).toHaveLength(1);
    return hashes[0];
}

const FILE_UPLOAD_TRIGGER = {
    triggerType: "fileUpload",
    inputFileFilters: { allow: ["*.json"], exclude: [] },
    defaultTemplateIds: { "GLOBAL:isaaclab-evaluation": "isaaclab-evaluation-cartpole" },
    enabled: false,
};

/**
 * A copy of the evaluation bundle whose workflow.json differs from its sibling variant ONLY in the
 * presence of the trigger declaration. Both variants re-serialize the document the same way, so the
 * hash comparison cannot be satisfied by incidental formatting.
 */
function bundleVariant(withTrigger: boolean): string {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "isaaclabEval-"));
    fs.cpSync(path.join(ISAACLAB_SCHEMA_ROOT, "evaluation"), dir, { recursive: true });
    const workflowPath = path.join(dir, "workflow.json");
    const workflow = JSON.parse(fs.readFileSync(workflowPath, "utf-8"));
    delete workflow.triggers;
    if (withTrigger) workflow.triggers = [FILE_UPLOAD_TRIGGER];
    fs.writeFileSync(workflowPath, JSON.stringify(workflow, null, 4) + "\n");
    return dir;
}

describe("the shipped evaluation bundle", () => {
    test("declares no fileUpload trigger", () => {
        const workflow = JSON.parse(
            fs.readFileSync(path.join(ISAACLAB_SCHEMA_ROOT, "evaluation", "workflow.json"), "utf-8")
        );
        expect(workflow.workflowName).toEqual("Isaac Lab RL Evaluation");
        expect(workflow.triggers).toBeUndefined();
    });
});

describe("the registration hash for the evaluation bundle", () => {
    const variants: string[] = [];

    afterAll(() => {
        for (const dir of variants) fs.rmSync(dir, { recursive: true, force: true });
    });

    const variant = (withTrigger: boolean) => {
        const dir = bundleVariant(withTrigger);
        variants.push(dir);
        return dir;
    };

    test("covers the trigger declaration", () => {
        // So dropping the trigger re-runs the registration custom resource rather than leaving the
        // resource properties — and the already-registered trigger row — untouched.
        expect(schemaHashOf(variant(false))).not.toEqual(schemaHashOf(variant(true)));
    });

    test("is identical for two byte-identical bundles in different directories", () => {
        // Positive control: the hash is content-addressed and path-independent, so the difference above
        // is the trigger declaration and not the temporary directory or synth-to-synth noise.
        expect(schemaHashOf(variant(false))).toEqual(schemaHashOf(variant(false)));
    });
});

/** Every pipeline construct source file, relative to lib/nestedStacks/pipelines. */
function pipelineConstructSources(): string[] {
    const root = path.join(__dirname, "..", "..", "lib", "nestedStacks", "pipelines");
    const found: string[] = [];
    const walk = (dir: string) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) walk(full);
            else if (entry.name.endsWith("-construct.ts")) found.push(full);
        }
    };
    walk(root);
    return found;
}

describe("deploy-time trigger enable across the pipeline constructs", () => {
    let registering: string[];
    let wiringTriggerEnabled: string[];

    beforeAll(() => {
        const sources = pipelineConstructSources().map((file) => ({
            file: path.basename(file),
            text: fs.readFileSync(file, "utf-8"),
        }));
        registering = sources
            .filter((s) => s.text.includes("new VamsSchemaRegistration("))
            .map((s) => s.file)
            .sort();
        wiringTriggerEnabled = sources
            .filter((s) => /^\s*triggerEnabled:/m.test(s.text))
            .map((s) => s.file)
            .sort();
    });

    test("the scan reaches the Isaac Lab construct", () => {
        // Positive control for the exclusion below: a walk that found the wrong directory would report
        // the Isaac Lab construct as passing no triggerEnabled without ever reading it.
        expect(registering).toContain("isaacLabTraining-construct.ts");
        expect(registering.length).toBeGreaterThanOrEqual(14);
    });

    test("the Isaac Lab construct wires no deploy-time trigger enable", () => {
        expect(wiringTriggerEnabled).not.toContain("isaacLabTraining-construct.ts");
    });

    test("the constructs that own a trigger still wire it", () => {
        expect(wiringTriggerEnabled).toEqual([
            "conversionMeshCadMetadataExtraction-construct.ts",
            "coordinateTransform-construct.ts",
            "cosmos3-construct.ts",
            "cosmosPredict-construct.ts",
            "cosmosReason-construct.ts",
            "cosmosTransfer-construct.ts",
            "metadata3dLabeling-construct.ts",
            "pcPotreeViewer-construct.ts",
            "preview3dThumbnail-construct.ts",
        ]);
    });
});

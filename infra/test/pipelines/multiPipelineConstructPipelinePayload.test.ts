/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The two multi/* conversion pipelines place their converted output beside its source, and both do
 * it by reading the workflow's `assetId` inside their constructPipeline step. Whether that value is
 * reachable there is decided by the EMITTED state machine definition, not by the lambda:
 *
 *   1. rapidPipelineEKS declares an EXPLICIT `payload` on its ConstructPipeline task, so the task
 *      sends exactly the fields it enumerates. A field openPipeline threads but the task omits is
 *      unreachable inside the operation, the output key silently flattens to the asset root, and a
 *      unit test that hand-builds the operation's event cannot see it.
 *   2. modelOps declares NO payload, so the whole state input flows through (`"Payload.$": "$"`).
 *      That is what carries its threaded `assetId` — and adding an explicit payload there would drop
 *      it just as silently.
 *
 * Both are asserted against the ASL inside the synthesized CloudFormation template.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { RapidPipelineEKSConstruct } from "../../lib/nestedStacks/pipelines/multi/rapidPipelineEKS/constructs/rapidPipelineEKS-construct";
import { ModelOpsConstruct } from "../../lib/nestedStacks/pipelines/multi/modelOps/constructs/modelOps-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    // Registration uploads the vamsSchema bundle and adds custom resources; neither is under test.
    config.app.pipelines.useRapidPipeline.useEks.autoRegisterWithVAMS = false;
    config.app.pipelines.useModelOps.autoRegisterWithVAMS = false;
    config.app.pipelines.useModelOps.ecrContainerImageURI = `${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/modelops:latest`;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

interface Harness {
    stack: cdk.Stack;
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: lambda.LayerVersion;
    storage: storageResources;
}

const makeHarness = (id: string): Harness => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, id, { env: { account: ACCOUNT, region: REGION } });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];

    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    // Module-level registry with no reset of its own: a second synth in the same process otherwise
    // collides on the construct id it derives from the previous stack name.
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, undefined, true);

    const storage = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    return {
        stack,
        config,
        vpc,
        subnets: vpc.privateSubnets,
        securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        storage,
    };
};

/**
 * The state machine's ASL, parsed out of the emitted `DefinitionString`.
 *
 * The definition is an `Fn::Join` of literal fragments and unresolved tokens (ARNs), and every token
 * sits inside a JSON string value — so substituting a placeholder for each one yields a document that
 * still parses. Asserting on the parsed ASL is what makes this a check of the emitted template rather
 * than of the construct call.
 */
const parseAsl = (template: Template, logicalIdFragment?: string): any => {
    const machines = Object.entries(
        template.findResources("AWS::StepFunctions::StateMachine")
    ).filter(([logicalId]) => !logicalIdFragment || logicalId.includes(logicalIdFragment));
    expect(machines).toHaveLength(1);
    const definition = (machines[0][1] as any).Properties.DefinitionString;
    const parts: any[] = typeof definition === "string" ? [definition] : definition["Fn::Join"][1];
    const flattened = parts.map((part) => (typeof part === "string" ? part : "__TOKEN__")).join("");
    return JSON.parse(flattened);
};

describe("rapidPipelineEKS CONSTRUCT_PIPELINE task payload", () => {
    let asl: any;

    beforeAll(() => {
        const h = makeHarness("RapidPipelineEksPayloadStack");
        new RapidPipelineEKSConstruct(h.stack, "RapidPipelineEKSConstruct", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnetsPrivate: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            kubectlLayer: lambda.LayerVersion.fromLayerVersionArn(
                h.stack,
                "KubectlLayer",
                `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:kubectl:1`
            ),
            kubernetesLayer: lambda.LayerVersion.fromLayerVersionArn(
                h.stack,
                "KubernetesLayer",
                `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:kubernetes:1`
            ),
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        asl = parseAsl(Template.fromStack(h.stack));
    });

    /** The Payload object the ConstructPipeline state sends, straight out of the emitted ASL. */
    const payload = (): Record<string, unknown> => {
        const state = asl.States["ConstructPipeline"];
        expect(state).toBeDefined();
        expect(state.Type).toEqual("Task");
        const value = state.Parameters.Payload;
        expect(value).toBeDefined();
        return value as Record<string, unknown>;
    };

    test("the emitted task payload passes the workflow assetId through", () => {
        // Positive control for the parse: the operation discriminator proves this really is the
        // CONSTRUCT_PIPELINE payload and not an empty object.
        expect(payload()["operation"]).toEqual("CONSTRUCT_PIPELINE");
        expect(payload()["assetId.$"]).toEqual("$.assetId");
    });

    test("every field the output placement reads is in the emitted payload", () => {
        // Set containment rather than an exact payload: the task may carry more (it already carries
        // the preview/metadata paths and the callback token), but each of these is read by
        // handle_construct_pipeline when it composes the output key.
        const keys = Object.keys(payload());
        for (const field of [
            "inputS3AssetFilePath",
            "outputS3AssetFilesPath",
            "inputConfigurationS3Location",
            "outputFileType",
            "assetId",
        ]) {
            expect(keys).toContain(`${field}.$`);
        }
    });

    test("the payload reads each field from the top level of the state input", () => {
        // openPipeline starts the state machine with a flat input, so a JsonPath that reached into a
        // nested object would resolve to nothing at runtime.
        for (const [key, value] of Object.entries(payload())) {
            if (key.endsWith(".$")) {
                expect(value).toEqual(`$.${key.slice(0, -2)}`);
            }
        }
    });
});

describe("modelOps constructPipeline task payload", () => {
    let asl: any;

    beforeAll(() => {
        const h = makeHarness("ModelOpsPayloadStack");
        const nested = new ModelOpsConstruct(h.stack, "ModelOpsConstruct", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnetsPrivate: h.subnets,
            pipelineSubnetsIsolated: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        asl = parseAsl(Template.fromStack(nested));
    });

    test("the whole state input reaches constructPipeline, which is what carries the assetId", () => {
        const state = asl.States["ConstructPipelineTask"];
        expect(state).toBeDefined();
        expect(state.Type).toEqual("Task");
        // No explicit payload: `"Payload.$": "$"` forwards openPipeline's whole SFN input, so a field
        // it threads is readable in the lambda. An enumerated payload here would silently drop
        // assetId and flatten every converted output to the asset root.
        expect(state.Parameters["Payload.$"]).toEqual("$");
        expect(Object.keys(state.Parameters)).not.toContain("Payload");
    });
});

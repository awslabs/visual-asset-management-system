/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Container user enforcement for Batch job definitions.
 *
 * **Fargate jobs:** No job definition may name a container user, so the image's own `USER` is what
 * runs. `BatchFargatePipelineConstruct` hardcoded `user: "root"` on its
 * `EcsFargateContainerDefinition`. That value becomes `ContainerProperties.User`, which REPLACES
 * the user the image declares — so the coordinateTransform Dockerfile's `USER coordxform` was inert
 * at runtime while the Dockerfile and its guard test in `containerBuildSources.test.ts` were both
 * green. Removing the override is what makes every one of those Dockerfiles take effect, and the
 * assertion below is what stops it coming back.
 *
 * **EC2 GPU jobs (issue #327):** GPU pipeline containers now run as uid/gid 10000:10000, enforced
 * via the image's `USER` directive (ContainerProperties.User stays absent, or it would replace the
 * image's USER). Two things are guarded below, both as real assertions: the seven GPU Dockerfiles
 * each declare `USER 10000:10000` after their last COPY (a static parse — deleting a USER line fails
 * the test), and the Isaac Lab EC2 job definition carries its EFS access point in the synthesized
 * template (AccessPointId + TransitEncryption), so a revert to a raw-root mount fails the test.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { IsaacLabTrainingConstruct } from "../../lib/nestedStacks/pipelines/simulation/isaacLabTraining/constructs/isaacLabTraining-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

/** backendPipelines/ tree, for the static Dockerfile USER parses below. */
const PIPELINES_DIR = path.join(__dirname, "..", "..", "..", "backendPipelines");

/**
 * Enable the four pipelines that build Fargate Batch jobs.
 *
 * Duplicated from `fargateBatchAttemptDuration.test.ts` rather than shared: each suite owns its own
 * mutation and `mutateKey`, and a shared mutator would couple the two synth caches together.
 */
function fargatePipelines(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.addVpcEndpoints = true;
    for (const flag of [
        "useConversionCoordinateTransform",
        "useGenAiMetadata3dLabeling",
        "usePreview3dThumbnail",
        "usePreviewPcPotreeViewer",
    ]) {
        if (c.app.pipelines[flag]) {
            c.app.pipelines[flag].enabled = true;
            if (c.app.pipelines[flag].autoRegisterWithVAMS !== undefined) {
                c.app.pipelines[flag].autoRegisterWithVAMS = false;
            }
        }
    }
}

/** Fargate job definitions, identified by the platform capability Batch receives. */
function fargateJobDefinitions(synth: SynthResult) {
    return synth.ofType("AWS::Batch::JobDefinition").filter((jd) => {
        const capabilities = ((jd.properties as any).PlatformCapabilities ?? []) as string[];
        return capabilities.includes("FARGATE");
    });
}

describe("Fargate Batch container user", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: fargatePipelines,
            mutateKey: "fargate-container-user",
        });
    });

    test("[control] Fargate job definitions ARE emitted in this synth", () => {
        // All four pipelines ship disabled, so the absence assertion below is otherwise satisfied by a
        // template that emitted nothing to inspect. Five are expected: coordinate transform, metadata
        // labeling, the 3D thumbnail, and PDAL plus Potree from the point-cloud viewer.
        expect(fargateJobDefinitions(synth).length).toBeGreaterThanOrEqual(5);
    });

    test("[control] the emitted job definitions carry ContainerProperties at all", () => {
        // Second control, and the one that matters for an absence assertion on a nested property: if
        // `ContainerProperties` were itself missing, "no User anywhere" would pass for the wrong reason.
        const withoutContainerProps = fargateJobDefinitions(synth)
            .filter((jd) => !(jd.properties as any).ContainerProperties)
            .map((jd) => `${jd.stack}/${jd.logicalId}`);
        expect(withoutContainerProps).toEqual([]);
    });

    test("no Fargate job definition names a container user", () => {
        // Any value is a regression, not only "root": the point is that the image decides. A future
        // pipeline that genuinely needs a named account should declare it in its Dockerfile.
        const overridden = fargateJobDefinitions(synth)
            .map((jd) => ({
                id: `${jd.stack}/${jd.logicalId}`,
                user: (jd.properties as any).ContainerProperties?.User,
            }))
            .filter((jd) => jd.user !== undefined)
            .map((jd) => `${jd.id} User=${JSON.stringify(jd.user)}`);
        expect(overridden).toEqual([]);
    });

    test("the shared construct sets no user on its container definition", () => {
        // The source-level half. The template assertion above covers the pipelines this synth enables;
        // this one covers the construct itself, so a new caller cannot reintroduce the override through a
        // pipeline no template turns on.
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/pipelines/constructs/batch-fargate-pipeline.ts"
            ),
            "utf-8"
        );
        // Control on the read: the container definition this assertion is about must be in the file.
        expect(source).toContain("new batch.EcsFargateContainerDefinition(");
        expect(source).not.toMatch(/^\s*user:/m);
    });
});

/**
 * GPU container USER assertions (issue #327)
 *
 * The 7 GPU pipeline Dockerfiles (Cosmos 3/Predict-v1/Predict-v2.5/Reason/Transfer, GR00T, Isaac Lab)
 * each declare `USER 10000:10000`. These are EC2 Batch jobs, so the image's USER takes effect (unlike
 * Fargate, where ContainerProperties.User would override it). None of these Dockerfiles is reachable
 * from a synthesized template (CodeBuild receives them as an s3assets.Asset CloudFormation never
 * inspects — same reason containerBuildSources.test.ts parses them directly), so this is a static
 * parse of each file: the USER must be the numeric 10000:10000 pair, and it must sit after the last
 * COPY so the copied application is not left root-owned. Deleting or weakening any USER line fails
 * the test — the vacuity the earlier placeholder had is gone.
 */
const GPU_DOCKERFILES: { label: string; file: string }[] = [
    {
        label: "cosmos 3",
        file: path.resolve(PIPELINES_DIR, "genAi/nvidia/cosmos/3/container/Dockerfile"),
    },
    {
        label: "cosmos predict v1",
        file: path.resolve(PIPELINES_DIR, "genAi/nvidia/cosmos/predict/containerv1/Dockerfile"),
    },
    {
        label: "cosmos predict v2.5",
        file: path.resolve(PIPELINES_DIR, "genAi/nvidia/cosmos/predict/containerv2.5/Dockerfile"),
    },
    {
        label: "cosmos reason",
        file: path.resolve(PIPELINES_DIR, "genAi/nvidia/cosmos/reason/container/Dockerfile"),
    },
    {
        label: "cosmos transfer",
        file: path.resolve(PIPELINES_DIR, "genAi/nvidia/cosmos/transfer/container/Dockerfile"),
    },
    {
        label: "gr00t",
        file: path.resolve(PIPELINES_DIR, "genAi/nvidia/gr00t/container/Dockerfile"),
    },
    {
        label: "isaac lab training",
        file: path.resolve(PIPELINES_DIR, "simulation/isaacLabTraining/container/Dockerfile"),
    },
];

describe.each(GPU_DOCKERFILES)("GPU image $label runs as non-root (issue #327)", ({ file }) => {
    const lines = fs.readFileSync(file, "utf-8").split(/\r?\n/);
    const userLines = lines
        .map((l, i) => ({ i, m: /^USER\s+(\S+)/.exec(l.trim()) }))
        .filter((x) => x.m !== null);

    it("declares exactly one USER, set to 10000:10000", () => {
        // Numeric uid:gid, not a name: a runtime runAsNonRoot check verifies it without reading the
        // image's /etc/passwd, and the EFS ownership (userdata chown / access point) is on 10000:10000.
        expect(userLines.length).toBe(1);
        expect(userLines[0].m![1]).toBe("10000:10000");
    });

    it("switches to that USER after the last COPY", () => {
        // A USER ahead of the last COPY leaves the copied application root-owned; several of these
        // images regressed exactly that way (USER before the build steps) and could not even build.
        const lastCopyAt = lines.reduce((acc, l, i) => (/^COPY\s/.test(l.trim()) ? i : acc), -1);
        expect(userLines[0].i).toBeGreaterThan(lastCopyAt);
    });
});

describe("GPU Fargate-shape guard is not applicable", () => {
    // The GPU pipelines are EC2 Batch jobs; the "no ContainerProperties.User override" property is a
    // Fargate concern already covered by the top suite. The Isaac Lab EC2 job definition's access-point
    // wiring — the load-bearing C1 guard — is asserted in the synth block below.
    test("[doc] GPU USER coverage is the Dockerfile parse above", () => {
        expect(GPU_DOCKERFILES.length).toBe(7);
    });
});

/**
 * EFS Access Point wiring (issue #327) — the load-bearing C1 regression guard.
 *
 * The 4 Cosmos pipelines and GR00T mount the EFS root in launch-template userdata and `chown -R`
 * the cache dir to 10000:10000, so NO AccessPointId appears in their job definitions. Isaac Lab
 * DOES wire its access point through batch.EcsVolume.efs({ accessPointId, enableTransitEncryption,
 * useJobRole }); that must reach the synthesized EC2 job definition, or the mount reverts to the raw
 * EFS root (root:root 0755) and the non-root container gets EACCES on makedirs. This synthesizes the
 * real IsaacLabTrainingConstruct (as isaacLabSchemaRegistrationTriggers.test.ts does — no web/dist,
 * no full CoreVAMSStack) and asserts on the emitted template. It fails if the volume props are
 * dropped or the container gains a root user override.
 */
describe("EFS Access Point wiring (issue #327)", () => {
    const ACCOUNT = "123456789012";
    const REGION = "us-east-1";
    let jobDef: any;

    beforeAll(() => {
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
        config.app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS = false;
        (config as any).enableCdkNag = false;
        config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
        Service.SetConfig(config);

        const app = newTestApp();
        const stack = new cdk.Stack(app, "IsaacLabEfsTestStack", {
            env: { account: ACCOUNT, region: REGION },
        });
        const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
        const kmsKey = new kms.Key(stack, "Key");

        s3AssetBuckets.getS3AssetBucketRecords().length = 0;
        s3AssetBuckets.addS3AssetBucket(new s3.Bucket(stack, "AssetBucket"), "/", "db");

        const storage = {
            encryption: { kmsKey },
            s3: {
                assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
                artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
            },
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
            // ECR-sourced so synth does not run a local Docker build.
            codeBuildImage: {
                repository: ecr.Repository.fromRepositoryName(stack, "Repo", "isaaclab"),
                tag: "0123456789abcdef0123456789abcdef01234567",
            },
        });

        const template = Template.fromStack(stack);
        const jobDefs = Object.values(template.findResources("AWS::Batch::JobDefinition")) as any[];
        // Exactly one EC2 (non-Fargate) GPU job definition is expected from this construct.
        const ec2JobDefs = jobDefs.filter(
            (jd) => !(jd.Properties?.PlatformCapabilities ?? []).includes("FARGATE")
        );
        expect(ec2JobDefs.length).toBe(1);
        jobDef = ec2JobDefs[0];
    });

    test("[control] the EC2 job definition has an EFS volume", () => {
        const volumes = jobDef.Properties?.ContainerProperties?.Volumes ?? [];
        const efsVolumes = volumes.filter((v: any) => v.EfsVolumeConfiguration);
        expect(efsVolumes.length).toBeGreaterThanOrEqual(1);
    });

    test("the EFS volume carries AccessPointId and TransitEncryption ENABLED", () => {
        // The C1 guard: a revert to batch.EcsVolume.efs() without accessPointId (the raw-root mount)
        // drops AuthorizationConfig.AccessPointId, and this fails. AWS Batch requires TransitEncryption
        // whenever an access point is used, so it is asserted alongside.
        const volumes = jobDef.Properties.ContainerProperties.Volumes as any[];
        const efsVol = volumes.find((v) => v.EfsVolumeConfiguration);
        const cfg = efsVol.EfsVolumeConfiguration;
        expect(cfg.TransitEncryption).toBe("ENABLED");
        expect(cfg.AuthorizationConfig?.AccessPointId).toBeDefined();
    });

    test("the EC2 job definition names no root container user", () => {
        // The image's USER 10000:10000 must decide; a ContainerProperties.User would replace it.
        expect(jobDef.Properties.ContainerProperties.User).toBeUndefined();
    });
});

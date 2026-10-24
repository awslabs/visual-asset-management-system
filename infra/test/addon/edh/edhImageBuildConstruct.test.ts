/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * One EDH container image: a VAMS-owned repository EDH can discover by resource tag, built by
 * CodeBuild from a repo-shipped buildspec under one content-addressed tag (beside the `latest`
 * cache alias the buildspec pushes), and a trigger that re-fires on any of the three inputs that
 * can leave the repository empty.
 *
 * Asserted at construct level on a bare stack, so every property is decidable without a full
 * synth. The T1 synth of the whole add-on lives in edhContainers.test.ts.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import * as kms from "aws-cdk-lib/aws-kms";
import * as Config from "../../../config/config";
import { IMAGE_TAG_LENGTH } from "../../../lib/helper/containerImageTag";
import {
    CONTAINER_SOURCE_EXCLUDE,
    EdhImageBuildConstruct,
} from "../../../lib/nestedStacks/addon/edhWorkstations/constructs/edhImageBuild-construct";
import { newTestApp } from "../../support/testApp";

/** The two config members the construct reads. */
const CONFIG = { name: "vams", app: { baseStackName: "t1" } } as unknown as Config.Config;
/** A tracked Dockerfile directory, so the source asset stages on a fresh checkout. */
const DOCKERFILE_DIR = "backendPipelines/conversion/coordinateTransform/container";

/** Sorted names of the one CodeBuild project's environment variables. */
function variableNames(template: Template): string[] {
    const projects = Object.values(template.findResources("AWS::CodeBuild::Project"));
    expect(projects).toHaveLength(1);
    return projects[0].Properties.Environment.EnvironmentVariables.map((v: any) => v.Name).sort();
}

/** A key ARN standing in for the shared VAMS key; the construct only reads its ARN. */
const KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/11111111-2222-3333-4444-555555555555";

function build(platform?: string, imagePullRoleArns: string[] = [], withKey = false) {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "T", {
        env: { account: "123456789012", region: "us-east-1" },
    });
    const kmsKey = withKey ? kms.Key.fromKeyArn(stack, "VamsKey", KEY_ARN) : undefined;
    const construct = new EdhImageBuildConstruct(stack, "ImageBuild-sim-worker", {
        config: CONFIG,
        imageName: "sim-worker",
        dockerfileDir: DOCKERFILE_DIR,
        platform,
        edhClusterId: "cluster-a",
        imagePullRoleArns,
        kmsKey,
    });
    return { construct, template: Template.fromStack(stack) };
}

describe("EdhImageBuildConstruct", () => {
    const { construct, template } = build("linux/amd64");

    test("[control] exactly one repository, one project and one trigger", () => {
        template.resourceCountIs("AWS::ECR::Repository", 1);
        template.resourceCountIs("AWS::CodeBuild::Project", 1);
        template.resourceCountIs("AWS::CloudFormation::CustomResource", 1);
    });

    test("the repository is explicitly named, EDH-tagged, scanned, capped and destroyed with the stack", () => {
        template.hasResourceProperties("AWS::ECR::Repository", {
            RepositoryName: "vams-t1-edh-sim-worker",
            EmptyOnDelete: true,
            ImageScanningConfiguration: { ScanOnPush: true },
            LifecyclePolicy: {
                LifecyclePolicyText: Match.stringLikeRegexp('"countNumber":10'),
            },
            Tags: Match.arrayWith([{ Key: "edh:visibility:cluster-a", Value: "true" }]),
        });
        template.hasResource("AWS::ECR::Repository", { DeletionPolicy: "Delete" });
    });

    test("the project runs outside the VPC, privileged, on the shared build image", () => {
        template.hasResourceProperties("AWS::CodeBuild::Project", {
            Environment: Match.objectLike({
                PrivilegedMode: true,
                Image: "aws/codebuild/standard:7.0",
                Type: "LINUX_CONTAINER",
            }),
            VpcConfig: Match.absent(),
            TimeoutInMinutes: 120,
            Source: Match.objectLike({ Type: "S3" }),
        });
    });

    test("the project is told the repository, a content-addressed tag and the platform", () => {
        template.hasResourceProperties("AWS::CodeBuild::Project", {
            Environment: Match.objectLike({
                EnvironmentVariables: Match.arrayWith([
                    Match.objectLike({ Name: "ECR_REPO_URI" }),
                    Match.objectLike({
                        Name: "IMAGE_TAG",
                        Value: Match.stringLikeRegexp(`^[0-9a-f]{${IMAGE_TAG_LENGTH}}$`),
                    }),
                    Match.objectLike({ Name: "IMAGE_PLATFORM", Value: "linux/amd64" }),
                ]),
            }),
        });
        expect(construct.imageTag).toHaveLength(IMAGE_TAG_LENGTH);
        expect(construct.imageUri.endsWith(`:${construct.imageTag}`)).toBe(true);
        // Exactly the variables backendAddons/edh/buildspec.yml reads.
        expect(variableNames(template)).toEqual([
            "AWS_DEFAULT_REGION",
            "ECR_REPO_URI",
            "IMAGE_PLATFORM",
            "IMAGE_TAG",
        ]);
    });

    test("the buildspec is the repo-shipped asset, not a file inside the operator source", () => {
        // fromSourceFilename would render the literal "buildspec.yml"; the shipped spec renders as
        // the S3 object ARN of its own asset.
        const projects = template.findResources("AWS::CodeBuild::Project");
        const buildSpec = JSON.stringify(Object.values(projects)[0].Properties.Source.BuildSpec);
        expect(buildSpec).not.toBe('"buildspec.yml"');
        expect(buildSpec).toContain(":s3:::");
    });

    test("the trigger carries the project, the source hash and the repository URI", () => {
        template.hasResourceProperties("AWS::CloudFormation::CustomResource", {
            ProjectName: Match.anyValue(),
            SourceHash: Match.anyValue(),
            EcrRepositoryUri: Match.anyValue(),
        });
    });

    test("the project may log in to the registry and push to its repository; the trigger may start it", () => {
        // Stated over the policy text: the minimizePolicies feature flag may merge statements, so
        // a shape assertion on one statement would be brittle for no gain.
        const policies = JSON.stringify(template.findResources("AWS::IAM::Policy"));
        expect(policies).toContain("ecr:GetAuthorizationToken");
        expect(policies).toContain("ecr:PutImage");
        expect(policies).toContain("codebuild:StartBuild");
    });

    test("the platform variable is omitted when the image declares none", () => {
        const { template: bare } = build(undefined);
        expect(variableNames(bare)).toEqual(["AWS_DEFAULT_REGION", "ECR_REPO_URI", "IMAGE_TAG"]);
    });

    test("the repository grants pull to the configured roles and to nothing else", () => {
        const roles = [
            "arn:aws:iam::123456789012:role/edh-batch-execution",
            "arn:aws:iam::123456789012:role/service-role/edh-ecs-task-execution",
        ];
        const { template: withRoles } = build("linux/amd64", roles);
        const repos = Object.values(withRoles.findResources("AWS::ECR::Repository"));
        expect(repos).toHaveLength(1);
        const statements = repos[0].Properties.RepositoryPolicyText.Statement as any[];
        expect(statements).toHaveLength(1);
        expect(statements[0].Effect).toBe("Allow");
        expect(([] as string[]).concat(statements[0].Action).sort()).toEqual([
            "ecr:BatchCheckLayerAvailability",
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer",
        ]);
        expect(([] as string[]).concat(statements[0].Principal.AWS).sort()).toEqual(
            [...roles].sort()
        );
        expect(statements[0].Principal).not.toHaveProperty("Service");
        expect(JSON.stringify(statements[0])).not.toContain('"*"');
    });

    test("no repository policy is emitted while the role list is empty", () => {
        const repos = Object.values(template.findResources("AWS::ECR::Repository"));
        expect(repos).toHaveLength(1);
        expect(repos[0].Properties.RepositoryPolicyText).toBeUndefined();
    });

    test("the trigger handler returns Data to the Provider and never answers CloudFormation itself", () => {
        // The inline handler is the one function whose code is a ZipFile literal; the Provider
        // framework's own functions are assets.
        const inline = Object.values(template.findResources("AWS::Lambda::Function")).filter(
            (fn) => typeof fn.Properties.Code?.ZipFile === "string"
        );
        expect(inline).toHaveLength(1);
        const code: string = inline[0].Properties.Code.ZipFile;
        expect(code).toContain("start_build");
        expect(code).not.toContain("cfnresponse");
        expect(code).toContain('"BuildId"');

        // CloudFormation calls the framework, which calls the handler and reports its return value.
        const [trigger] = Object.values(
            template.findResources("AWS::CloudFormation::CustomResource")
        );
        const [serviceTokenTarget] = trigger.Properties.ServiceToken["Fn::GetAtt"];
        expect(serviceTokenTarget).toMatch(/BuildProviderEdhsimworkerframeworkonEvent/);
        expect(serviceTokenTarget).not.toMatch(/BuildTriggerEdhsimworker/);
    });
});

describe("EdhImageBuildConstruct repository encryption", () => {
    const off = build("linux/amd64").template;
    const on = build("linux/amd64", [], true).template;

    function repository(template: Template): any {
        const repos = Object.values(template.findResources("AWS::ECR::Repository"));
        expect(repos).toHaveLength(1);
        return repos[0].Properties;
    }

    test("[control] both builds emit exactly one repository", () => {
        off.resourceCountIs("AWS::ECR::Repository", 1);
        on.resourceCountIs("AWS::ECR::Repository", 1);
    });

    test("with the CMK on, the repository is KMS-encrypted with the deployment key", () => {
        expect(repository(on).EncryptionConfiguration).toEqual({
            EncryptionType: "KMS",
            KmsKey: KEY_ARN,
        });
    });

    test("with the CMK off, the repository has no EncryptionConfiguration (AES-256)", () => {
        const config = repository(off).EncryptionConfiguration;
        expect(config === undefined || config.EncryptionType === "AES256").toBe(true);
        expect(config?.KmsKey).toBeUndefined();
    });

    test("the explicit repository name ends in -kms exactly when the CMK is on", () => {
        // Encryption is immutable, so a flip replaces the repository, and CloudFormation cannot
        // replace an explicitly named resource under the same name.
        expect(repository(off).RepositoryName).toBe("vams-t1-edh-sim-worker");
        expect(repository(on).RepositoryName).toBe("vams-t1-edh-sim-worker-kms");
    });

    test("no role in the build gets a permission on the key", () => {
        // Amazon ECR holds its own grant on the key; the project and trigger roles need none.
        const policies = JSON.stringify(on.findResources("AWS::IAM::Policy"));
        expect(policies).not.toContain("kms:");
        expect(policies).not.toContain(KEY_ARN);
    });
});

describe("the image build source upload", () => {
    // `.envrc` starts with `.env` but carries no dot after it, so it is kept.
    const KEPT = [".envrc", "Dockerfile", "app/main.py"];
    let dir: string;

    /** Every file under `root` as a sorted POSIX-relative path. */
    const filesUnder = (root: string, relative = ""): string[] =>
        fs
            .readdirSync(path.join(root, relative), { withFileTypes: true })
            .flatMap((entry) => {
                const child = relative ? `${relative}/${entry.name}` : entry.name;
                return entry.isDirectory() ? filesUnder(root, child) : [child];
            })
            .sort();

    beforeAll(() => {
        dir = fs.mkdtempSync(path.join(os.tmpdir(), "edh-image-source-"));
        for (const relative of [".env", ".env.local", "app/.env.production", ...KEPT]) {
            fs.mkdirSync(path.dirname(path.join(dir, relative)), { recursive: true });
            fs.writeFileSync(path.join(dir, relative), "placeholder\n");
        }
    });

    afterAll(() => {
        fs.rmSync(dir, { recursive: true, force: true });
    });

    test("[control] the directory holds every credential file and every kept file", () => {
        expect(filesUnder(dir)).toHaveLength(3 + KEPT.length);
    });

    test("the upload stages every file but .env and .env.*, at every depth", () => {
        const staged = fs.mkdtempSync(path.join(os.tmpdir(), "edh-image-staged-"));
        try {
            cdk.FileSystem.copyDirectory(dir, staged, { exclude: CONTAINER_SOURCE_EXCLUDE });
            expect(filesUnder(staged)).toEqual(KEPT);
        } finally {
            fs.rmSync(staged, { recursive: true, force: true });
        }
    });
});

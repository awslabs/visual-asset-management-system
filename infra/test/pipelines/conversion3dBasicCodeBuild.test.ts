/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: `app.pipelines.useConversion3dBasic.useCodeBuild` across the three shipped templates.
 *
 * With the flag on, AWS CodeBuild builds the 3D Basic Conversion Lambda container image into a VAMS
 * Amazon ECR repository and the function is created from that image. What differs from the AWS Batch
 * pipelines' CodeBuild path, and is asserted here because no unit test sees it:
 *
 *   - AWS Lambda resolves the image when CloudFormation creates or updates the function, so the build
 *     trigger must WAIT for the build. A start-only trigger deploys green on the template and fails the
 *     first create with a missing image. So: the provider has an is-complete handler, the function
 *     depends on the custom resource, and the waiter outlives the build's own timeouts.
 *   - Lambda pulls through the repository policy, not an IAM role, so the policy must name the Lambda
 *     service principal of the partition being deployed to.
 *
 * With the flag off (as every template ships) the function keeps its CDK image asset and none of these
 * resources exist; `expectAbsent` carries the function itself as its positive control.
 */

import {
    ALL_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";
import { parseAsl } from "../support/asl";
import { SERVICE_LOOKUP, TYPE_SERVICE_LOOKUP } from "../../lib/helper/const";
import { IMAGE_TAG_LENGTH } from "../../lib/helper/containerImageTag";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";

// Two full-app synths per template (flag on, and as shipped); each is ~20 s.
jest.setTimeout(600_000);

const CONFIG_NAME: Record<TemplateName, string> = {
    commercial: (commercialTemplate as any).name,
    govcloud: (govcloudTemplate as any).name,
    eusovereign: (eusovereignTemplate as any).name,
};

const CONTENT_TAG = new RegExp(`^[0-9a-f]{${IMAGE_TAG_LENGTH}}$`);

const synthOn = (name: TemplateName): SynthResult =>
    synthTemplate(name, {
        mutate: (c: any) => {
            c.app.pipelines.useConversion3dBasic.enabled = true;
            c.app.pipelines.useConversion3dBasic.useCodeBuild = true;
        },
        mutateKey: "conversion3dbasic-codebuild-on",
    });

const synthOff = (name: TemplateName): SynthResult => synthTemplate(name);

/** The 3D Basic Conversion function: a container-image function, selected by construct id. */
const basicFunctions = (s: SynthResult): Resource[] =>
    s.where(
        "AWS::Lambda::Function",
        (r) =>
            r.logicalId.startsWith("vamsExecute3dBasicConversion") &&
            r.properties.PackageType === "Image"
    );

/** The resources only the CodeBuild path creates, selected by the construct id they are built under. */
const basicCodeBuildResources = (s: SynthResult): Resource[] =>
    s.resources.filter(
        (r) =>
            [
                "AWS::CodeBuild::Project",
                "AWS::ECR::Repository",
                "AWS::CloudFormation::CustomResource",
            ].includes(r.type) && r.logicalId.startsWith("Conversion3dBasicCodeBuild")
    );

const inStackOf = (s: SynthResult, type: string, of: Resource, idFragment: string): Resource[] =>
    s.where(type, (r) => r.stack === of.stack && r.logicalId.includes(idFragment));

const envOf = (project: Resource): Record<string, any> =>
    Object.fromEntries(
        ((project.properties.Environment?.EnvironmentVariables ?? []) as any[]).map((v) => [
            v.Name,
            v.Value,
        ])
    );

const getAttId = (value: any): string | undefined => value?.["Fn::GetAtt"]?.[0];

/** Every statement of the IAM policies attached to the role of `fn`. */
const roleStatements = (s: SynthResult, fn: Resource): any[] => {
    const roleId = getAttId(fn.properties.Role);
    return s
        .where(
            "AWS::IAM::Policy",
            (p) =>
                p.stack === fn.stack &&
                (p.properties.Roles ?? []).some((role: any) => role?.Ref === roleId)
        )
        .flatMap((p) => p.properties.PolicyDocument.Statement);
};

const asList = (value: any): any[] => (Array.isArray(value) ? value : [value]);

describe.each(ALL_TEMPLATES)("%s: 3D Basic Conversion built by CodeBuild", (name) => {
    let s: SynthResult;
    let fn: Resource;
    let project: Resource;
    let repository: Resource;

    beforeAll(() => {
        s = synthOn(name);
        fn = basicFunctions(s)[0];
        project =
            fn && inStackOf(s, "AWS::CodeBuild::Project", fn, "Conversion3dBasicCodeBuild")[0];
        repository = fn && inStackOf(s, "AWS::ECR::Repository", fn, "EcrRepo3dBasic")[0];
    });

    it("[control] emits the function, its build project and its repository", () => {
        expect(basicFunctions(s)).toHaveLength(1);
        expect(
            inStackOf(s, "AWS::CodeBuild::Project", fn, "Conversion3dBasicCodeBuild")
        ).toHaveLength(1);
        expect(inStackOf(s, "AWS::ECR::Repository", fn, "EcrRepo3dBasic")).toHaveLength(1);
        // The selector the flag-off absence assertion below relies on finds them here.
        expect(
            basicCodeBuildResources(s)
                .map((r) => r.type)
                .sort()
        ).toEqual([
            "AWS::CloudFormation::CustomResource",
            "AWS::CodeBuild::Project",
            "AWS::ECR::Repository",
        ]);
    });

    it("the function pulls the content-addressed tag the build is told to push", () => {
        const imageTag = SynthResult.flatten(envOf(project).IMAGE_TAG);
        expect(imageTag).toMatch(CONTENT_TAG);

        const imageUri = SynthResult.flatten(fn.properties.Code.ImageUri);
        expect(imageUri.endsWith(`:${imageTag}`)).toBe(true);
        // From the 3D Basic repository, not from the CDK container-assets repository.
        expect(imageUri).toContain(`\${${repository.logicalId}}`);
        expect(imageUri).not.toContain("container-assets");
        expect(SynthResult.flatten(envOf(project).ECR_REPO_URI)).toContain(
            `\${${repository.logicalId}}`
        );
    });

    it("the build project runs the container's buildspec in privileged mode", () => {
        expect(project.properties.Source.Type).toBe("S3");
        expect(project.properties.Source.BuildSpec).toBe("buildspec.yml");
        expect(project.properties.Environment.PrivilegedMode).toBe(true);
    });

    it("the repository is explicitly named, scanned, size-bounded and removed with the stack", () => {
        expect(repository.properties.RepositoryName).toBe(
            `${CONFIG_NAME[name]}-t1-${name}-3dbasic`.toLowerCase()
        );
        expect(repository.raw.DeletionPolicy).toBe("Delete");
        expect(repository.properties.EmptyOnDelete).toBe(true);
        expect(repository.properties.ImageScanningConfiguration).toEqual({ ScanOnPush: true });
        const lifecycle = JSON.parse(repository.properties.LifecyclePolicy.LifecyclePolicyText);
        expect(lifecycle.rules.map((r: any) => r.selection.countNumber)).toEqual([10]);
    });

    it("the repository policy lets this partition's Lambda service principal pull the image", () => {
        const principal = SERVICE_LOOKUP[TYPE_SERVICE_LOOKUP.LAMBDA][s.partition].principal;
        const pulls = (repository.properties.RepositoryPolicyText?.Statement ?? []).filter(
            (st: any) => st.Effect === "Allow" && asList(st.Principal?.Service).includes(principal)
        );
        expect(pulls).toHaveLength(1);
        expect(asList(pulls[0].Action)).toEqual(
            expect.arrayContaining(["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"])
        );
    });

    describe("the build trigger waits for the build", () => {
        let trigger: Resource;
        let frameworkOnEvent: Resource;

        beforeAll(() => {
            trigger = s.where(
                "AWS::CloudFormation::CustomResource",
                (r) => r.stack === fn.stack && r.properties.ProjectName?.Ref === project.logicalId
            )[0];
            frameworkOnEvent =
                trigger &&
                s.where(
                    "AWS::Lambda::Function",
                    (r) =>
                        r.stack === fn.stack &&
                        r.logicalId === getAttId(trigger.properties.ServiceToken)
                )[0];
        });

        it("a custom resource triggers this project's build", () => {
            expect(trigger).toBeDefined();
            // The re-fire inputs: a source change and a repository replacement each change one.
            expect(Object.keys(trigger.properties)).toEqual(
                expect.arrayContaining(["SourceHash", "EcrRepositoryUri", "ImageTag"])
            );
            expect(trigger.properties.ImageTag).toBe(SynthResult.flatten(envOf(project).IMAGE_TAG));
        });

        it("its provider has an is-complete handler, so CloudFormation waits on a state machine", () => {
            const env = frameworkOnEvent.properties.Environment.Variables;
            const handlerOf = (arn: any) =>
                s.where("AWS::Lambda::Function", (r) => r.logicalId === getAttId(arn))[0]
                    ?.properties.Handler;
            expect(handlerOf(env.USER_ON_EVENT_FUNCTION_ARN)).toBe("codeBuildTrigger.on_event");
            expect(handlerOf(env.USER_IS_COMPLETE_FUNCTION_ARN)).toBe(
                "codeBuildTrigger.is_complete"
            );
            expect(env.WAITER_STATE_MACHINE_ARN?.Ref).toBeDefined();
        });

        it("the function is created only after the build trigger completes", () => {
            expect(asList(fn.raw.DependsOn ?? [])).toContain(trigger.logicalId);
        });

        it("the build reaches its own terminal status before the waiter gives up", () => {
            // A build that outlives the waiter fails the deploy with the framework's generic
            // "Operation timed out" instead of the build's status and log link.
            const waiterId =
                frameworkOnEvent.properties.Environment.Variables.WAITER_STATE_MACHINE_ARN.Ref;
            const waiter = s.where(
                "AWS::StepFunctions::StateMachine",
                (r) => r.logicalId === waiterId
            )[0];
            const retries = Object.values<any>(parseAsl(waiter.properties).States).flatMap(
                (state) => state.Retry ?? []
            );
            expect(retries).toHaveLength(1);
            const waiterSeconds = retries[0].IntervalSeconds * retries[0].MaxAttempts;
            const buildSeconds =
                (project.properties.TimeoutInMinutes + project.properties.QueuedTimeoutInMinutes) *
                60;
            expect(buildSeconds).toBeLessThan(waiterSeconds);
            // AWS CloudFormation waits at most one hour for a custom resource response.
            expect(waiterSeconds).toBeLessThanOrEqual(3600);
        });

        it("the trigger functions hold only their own CodeBuild and ECR actions", () => {
            const handlerFn = (handler: string) =>
                s.where(
                    "AWS::Lambda::Function",
                    (r) => r.stack === fn.stack && r.properties.Handler === handler
                )[0];
            const grants = (handler: string) =>
                roleStatements(s, handlerFn(handler)).map((st) => ({
                    actions: asList(st.Action),
                    resources: asList(st.Resource).map((res) => getAttId(res) ?? res),
                }));

            expect(grants("codeBuildTrigger.on_event")).toEqual([
                { actions: ["codebuild:StartBuild"], resources: [project.logicalId] },
                { actions: ["ecr:DescribeImages"], resources: [repository.logicalId] },
            ]);
            expect(grants("codeBuildTrigger.is_complete")).toEqual([
                { actions: ["codebuild:BatchGetBuilds"], resources: [project.logicalId] },
            ]);
        });
    });
});

describe.each(ALL_TEMPLATES)("%s: 3D Basic Conversion as shipped (useCodeBuild false)", (name) => {
    it("the function is built from a CDK image asset", () => {
        const fns = basicFunctions(synthOff(name));
        expect(fns).toHaveLength(1);
        expect(SynthResult.flatten(fns[0].properties.Code.ImageUri)).toContain("container-assets");
    });

    it("no 3D Basic build project, repository or build trigger is emitted", () => {
        const s = synthOff(name);
        const found = basicCodeBuildResources(s);
        expectAbsent("3D Basic CodeBuild resources", found, {
            description: "the 3D Basic Conversion function is emitted",
            count: basicFunctions(s).length,
        });
    });
});

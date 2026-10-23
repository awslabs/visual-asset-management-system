/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * CDK Nag over the EDH Workstations add-on.
 *
 * The shipped templates disable the add-on, so a `cdk synth` with `enableCdkNag` on never evaluates
 * the add-on's resources — or the suppressions they carry — until an operator enables it. This synth
 * applies the AwsSolutions rule pack to the commercial hybrid with session bootstrap, containers and
 * AMI builds on, the widest set of add-on resources, and requires that no error-level finding lands
 * under the add-on's construct path. The rest of the app is out of scope here: it is synthesized
 * from test-only placeholder values the real deployment does not use.
 *
 * The controls are the pack's own log of suppressed findings: with `logIgnores` on, every finding a
 * suppression covered is reported as an informational annotation, so an add-on synth that produced none
 * of those was not evaluated, and the empty error list would prove nothing. The image build project
 * is controlled on its own: the containers mutate is what constructs it, so a Lambda suppression
 * logged elsewhere under the path would show neither that the project was built nor that its
 * scoped regexes matched the ARN shapes CodeBuild emits; the control names the finding each of
 * the project's entries covered. The AMI build resources are controlled the same way, and their
 * control additionally asserts the resources the suppressions target are in the template — the
 * pipeline-start statement and the build instance role — so an AMI block that stopped synthesizing
 * fails here instead of quietly emptying both the finding list and the suppression log.
 */

import { ACCOUNT, SynthResult, synthTemplate } from "../../support/templateSynth";
import { enableEdhAmiBuilds, enableEdhContainers } from "./support/edhSynth";

// A full-app synth is ~20 s; the rule pack adds to that.
jest.setTimeout(600_000);

const ADDON_PATH = /\/AddonBuilder\/EdhWorkstationsBuilderNestedStack\//;
const PROJECT_PATH =
    /\/EdhWorkstationsBuilderNestedStack\/ImageBuild-sim-worker\/CodeBuild-Edh-sim-worker\//;
const AMI_ROLE_PATH = /\/EdhWorkstationsBuilderNestedStack\/EdhAmiBuild\/EdhAmiBuildInstanceRole\//;
const AMI_LAMBDA_PATH =
    /\/EdhWorkstationsBuilderNestedStack\/(edhAmiRecipeService|edhAmiBuildRegistrar)\/ServiceRole\/DefaultPolicy\//;

/**
 * Logged suppressions the image build project raises: the project resource's default-encryption
 * finding, then its default role's wildcards — the control names one finding per `appliesTo`-scoped
 * entry of the construct (the asset-bucket entry also covers the three s3 action wildcards), and the
 * log-group and report-group ARNs name the project's own logical id. A suppressed finding is logged
 * as `CdkNagSuppression[<finding>]: <rule> was triggered but suppressed.`; a rule without a finding
 * id (CB4) omits the bracket.
 */
const PROJECT_SUPPRESSED = [
    /^CdkNagSuppression: AwsSolutions-CB4 was triggered but suppressed/,
    /\[Resource::arn:<AWS::Partition>:logs:[^\]]*:log-group:\/aws\/codebuild\/<\w*CodeBuildEdhsimworker\w*>:\*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:codebuild:[^\]]*:report-group\/<\w*CodeBuildEdhsimworker\w*>-\*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:s3:::cdk-[^\]]*-assets-[^\]]*\/\*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::\*\]: AwsSolutions-IAM5 was triggered/,
];

/**
 * The two AWS managed policies the build instance carries, one IAM4 finding each, and the
 * prefix-scoped object read that lets a build component fetch its recipe's asset trees.
 */
const AMI_ROLE_SUPPRESSED = [
    /\[Policy::arn:<AWS::Partition>:iam::aws:policy\/EC2InstanceProfileForImageBuilder\]: AwsSolutions-IAM4 was triggered/,
    /\[Policy::arn:<AWS::Partition>:iam::aws:policy\/AmazonSSMManagedInstanceCore\]: AwsSolutions-IAM4 was triggered/,
    /\[Resource::[^\]]*\/addon\/edh\/amiRecipes\/\*\]: AwsSolutions-IAM5 was triggered/,
];

/**
 * One finding per ARN-shaped IAM5 entry the build Lambdas' policies carry: the six Image Builder
 * object types the add-on creates under the deployment prefix, the AMI and snapshot resources it
 * tags and deregisters, the public parameter path an `ssm:` parent image resolves through, and the
 * service-linked role Image Builder creates on first use. Named individually so a scoped regex that
 * stops matching the ARN shape the grant emits fails the control rather than the error list.
 */
const AMI_LAMBDA_SUPPRESSED = [
    /\[Resource::arn:<AWS::Partition>:imagebuilder:[^\]]*:component\/[^\]]*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:imagebuilder:[^\]]*:image-recipe\/[^\]]*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:imagebuilder:[^\]]*:image-pipeline\/[^\]]*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:imagebuilder:[^\]]*:image\/[^\]]*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:imagebuilder:[^\]]*:infrastructure-configuration\/[^\]]*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:imagebuilder:[^\]]*:distribution-configuration\/[^\]]*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:ec2:[^\]]*::image\/\*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:ec2:[^\]]*::snapshot\/\*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:ssm:[^\]]*::parameter\/aws\/service\/\*\]: AwsSolutions-IAM5 was triggered/,
    /\[Resource::arn:<AWS::Partition>:iam::[^\]]*:role\/aws-service-role\/imagebuilder\.amazonaws\.com\/\*\]: AwsSolutions-IAM5 was triggered/,
];

/**
 * The three roles that read capacity reservations live: `ec2:DescribeCapacityReservations` takes no
 * resource ARN, so each role's policy carries a `*` resource that its own suppression covers.
 */
const RESERVATION_READERS = ["edhWorkstationService", "edhLaunchWorker", "edhAdminService"];
const readerPolicyPath = (module: string): RegExp =>
    new RegExp(`/EdhWorkstationsBuilderNestedStack/${module}/ServiceRole/DefaultPolicy/`);
const WILDCARD_RESOURCE_SUPPRESSED = /\[Resource::\*\]: AwsSolutions-IAM5 was triggered/;

const nagSynth = (): SynthResult =>
    synthTemplate("commercial", {
        mutate: (c: any) => {
            // The containers hybrid re-applies the add-on base, which leaves the AMI builds
            // block alone, so it comes second.
            enableEdhAmiBuilds(c);
            enableEdhContainers(c);
            const edh = c.app.addons.useEdhWorkstations;
            edh.sessionBootstrap.enabled = true;
            edh.sessionBootstrap.workstationInstanceRoleArns = [
                `arn:${c.env.partition}:iam::${ACCOUNT}:role/EdhDesktopInstanceRole`,
            ];
            // A tracked Dockerfile directory, so the source asset stages on a fresh checkout.
            edh.containers.buildAndPushImages = [
                {
                    name: "sim-worker",
                    dockerfileDir: "backendPipelines/conversion/coordinateTransform/container",
                },
            ];
            edh.containers.jobDefinitions = [
                { name: "edh-sim-train", imageRef: "sim-worker", vcpus: 4, memory: 16384 },
            ];
            edh.containers.imagePullRoleArns = [
                `arn:${c.env.partition}:iam::${ACCOUNT}:role/edh-batch-execution`,
            ];
        },
        mutateKey: "edhNagBootstrapContainersAmiBuilds",
        nag: true,
    });

const loggedUnder = (s: SynthResult, path: RegExp): string[] =>
    s.infos
        .filter((m) => path.test(m.path) && /^CdkNagSuppression/.test(m.message))
        .map((m) => m.message);

describe("CDK Nag over the EDH Workstations add-on", () => {
    test("[control] the rule pack evaluated the add-on: it logged suppressed findings under its path", () => {
        const s = nagSynth();
        const suppressed = s.infos.filter(
            (m) => ADDON_PATH.test(m.path) && /CdkNagSuppression/.test(m.message)
        );
        expect(suppressed.length).toBeGreaterThan(0);
    });

    test("[control] the image build project was evaluated: its encryption finding and each scoped IAM finding were logged as suppressed", () => {
        const s = nagSynth();
        expect(loggedUnder(s, PROJECT_PATH)).toEqual(
            expect.arrayContaining(
                PROJECT_SUPPRESSED.map((pattern) => expect.stringMatching(pattern))
            )
        );
    });

    test("[control] the AMI build resources the suppressions target are in the template and each of their scoped findings was logged as suppressed", () => {
        const s = nagSynth();

        // The build Lambdas' IAM5 entries scope Image Builder to the deployment prefix; the
        // statement that starts a pipeline is the one those entries cover.
        const startsPipelines = s.where("AWS::IAM::Policy", (r) =>
            (r.properties.PolicyDocument?.Statement ?? []).some((st: any) =>
                [st.Action].flat().includes("imagebuilder:StartImagePipelineExecution")
            )
        );
        expect(startsPipelines.length).toBeGreaterThan(0);

        // The IAM4 entries cover the build instance role's two AWS managed policies.
        const buildInstanceRoles = s.where("AWS::IAM::Role", (r) =>
            SynthResult.flatten(r.properties.ManagedPolicyArns).includes(
                "iam::aws:policy/EC2InstanceProfileForImageBuilder"
            )
        );
        expect(buildInstanceRoles).toHaveLength(1);

        expect(loggedUnder(s, AMI_ROLE_PATH)).toEqual(
            expect.arrayContaining(
                AMI_ROLE_SUPPRESSED.map((pattern) => expect.stringMatching(pattern))
            )
        );
        expect(loggedUnder(s, AMI_LAMBDA_PATH)).toEqual(
            expect.arrayContaining(
                AMI_LAMBDA_SUPPRESSED.map((pattern) => expect.stringMatching(pattern))
            )
        );
    });

    test("[control] each capacity reservation reader's wildcard resource was evaluated and logged as suppressed", () => {
        const s = nagSynth();
        // The statement the suppression covers is in the template, on every reader role.
        const describes = s.where("AWS::IAM::Policy", (r) =>
            (r.properties.PolicyDocument?.Statement ?? []).some((st: any) =>
                [st.Action].flat().includes("ec2:DescribeCapacityReservations")
            )
        );
        expect(describes.length).toBeGreaterThan(0);
        expect(
            RESERVATION_READERS.map((module) => [
                module,
                loggedUnder(s, readerPolicyPath(module)).some((m) =>
                    WILDCARD_RESOURCE_SUPPRESSED.test(m)
                ),
            ])
        ).toEqual(RESERVATION_READERS.map((module) => [module, true]));
    });

    test("no error-level finding lands under the add-on's construct path", () => {
        const s = nagSynth();
        const inAddon = s.errors
            .filter((m) => ADDON_PATH.test(m.path))
            .map((m) => `${m.path}: ${m.message}`);
        expect(inAddon).toEqual([]);
    });
});

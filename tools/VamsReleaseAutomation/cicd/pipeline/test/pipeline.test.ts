/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Offline synthesis test: builds the stack in memory with placeholder accounts (no AWS calls, no
// context lookups) and fails on any unsuppressed cdk-nag AwsSolutions finding.
import { readFileSync } from "fs";
import { join } from "path";
import { App, Aspects } from "aws-cdk-lib";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import { AwsSolutionsChecks } from "cdk-nag";
import { PipelineConfig, loadConfig } from "../lib/config";
import { VraPipelineStack } from "../lib/vra-pipeline-stack";

// Use the same feature flags as `cdk synth` (cdk.json context).
const cdkContext = JSON.parse(readFileSync(join(__dirname, "..", "cdk.json"), "utf8")).context;

function synthConfig(config: PipelineConfig) {
    const app = new App({ context: cdkContext });
    const stack = new VraPipelineStack(app, "vra-pipeline", {
        config,
        env: { account: config.toolsAccount, region: config.region },
    });
    Aspects.of(app).add(new AwsSolutionsChecks());
    app.synth();
    return { stack, template: Template.fromStack(stack) };
}

function synth(configFile: string) {
    return synthConfig(loadConfig(join(__dirname, "..", "config", configFile)));
}

describe.each(["pipeline.example.json", "pipeline.example.commercial-github.json"])(
    "%s",
    (file) => {
        const { stack, template } = synth(file);
        const config = loadConfig(join(__dirname, "..", "config", file));
        const envs = config.release.envs ?? [];
        const dev = envs.filter((e) => !e.promotesFrom && !e.requiresApproval).map((e) => e.name);
        const gated = envs.filter((e) => !dev.includes(e.name)).map((e) => e.name);

        test("no unsuppressed cdk-nag errors", () => {
            const errors = Annotations.fromStack(stack).findError(
                "*",
                Match.stringLikeRegexp("AwsSolutions-.*")
            );
            expect(errors.map((e) => `${e.id}: ${e.entry.data}`)).toEqual([]);
        });

        test("no unsuppressed cdk-nag warnings", () => {
            const warnings = Annotations.fromStack(stack).findWarning(
                "*",
                Match.stringLikeRegexp("AwsSolutions-.*")
            );
            expect(warnings.map((w) => `${w.id}: ${w.entry.data}`)).toEqual([]);
        });

        test("release pipeline has a manual approval before every gated env", () => {
            const pipelines = template.findResources("AWS::CodePipeline::Pipeline");
            const release = Object.values(pipelines).find(
                (p) => p.Properties.Name === "vra-release"
            );
            expect(release).toBeDefined();
            const stages: string[] = release?.Properties.Stages.map(
                (s: { Name: string }) => s.Name
            );
            expect(stages).toEqual([
                "Source",
                "Validate",
                "Build",
                "Deploy-dev",
                "Approve-test",
                "Deploy-test",
                "Approve-prod",
                "Deploy-prod",
            ]);
            const approvals = release?.Properties.Stages.filter((s: { Name: string }) =>
                s.Name.startsWith("Approve-")
            );
            for (const s of approvals) {
                expect(s.Actions[0].ActionTypeId.Category).toBe("Approval");
            }
        });

        test("no hardcoded aws partition in IAM policies", () => {
            // ARNs copied verbatim from the config file (connection, topic, key) carry the operator's
            // partition; everything the stack builds itself must use ${AWS::Partition}.
            const json = JSON.stringify({
                ...template.findResources("AWS::IAM::Policy"),
                ...template.findResources("AWS::IAM::ManagedPolicy"),
            }).replace(/arn:aws:(codeconnections|codestar-connections|sns|kms):[^"]*/g, "");
            expect(json).not.toMatch(/arn:aws:/);
        });

        test("daily watch schedule targets the watch project", () => {
            template.resourceCountIs("AWS::Scheduler::Schedule", 1);
            template.hasResourceProperties("AWS::CodeBuild::Project", {
                Name: "vra-watch",
                ConcurrentBuildLimit: 1,
            });
        });

        test("every job knows the home repository; deploy projects run one build at a time", () => {
            const projects = template.findResources("AWS::CodeBuild::Project");
            for (const p of Object.values(projects)) {
                const vars = p.Properties.Environment.EnvironmentVariables as Array<{
                    Name: string;
                    Value: unknown;
                }>;
                expect(vars.find((v) => v.Name === "VRA_HOME_REPO")?.Value).toBe(
                    config.home.repoUrl
                );
                if (String(p.Properties.Name).startsWith("vra-deploy-")) {
                    expect(p.Properties.ConcurrentBuildLimit).toBe(1);
                }
            }
        });

        // ---------------------------------------------------------------- trust tiers
        const projects = template.findResources("AWS::CodeBuild::Project");
        // inline role policies and the per-tier state-store managed policies
        const policies = {
            ...template.findResources("AWS::IAM::Policy"),
            ...template.findResources("AWS::IAM::ManagedPolicy"),
        };

        /** Every statement attached to the service role of the CodeBuild project named `projectName`. */
        function statementsOf(
            projectName: string
        ): Array<{ Effect: string; Action: unknown; Resource: unknown }> {
            const proj = Object.values(projects).find((p) => p.Properties.Name === projectName);
            expect(proj).toBeDefined();
            const roleRef = JSON.stringify(proj?.Properties.ServiceRole); // {"Fn::GetAtt":["<RoleId>","Arn"]}
            const roleId = JSON.parse(roleRef)["Fn::GetAtt"][0];
            return Object.values(policies)
                .filter((p) => JSON.stringify(p.Properties.Roles).includes(`"${roleId}"`))
                .flatMap((p) => p.Properties.PolicyDocument.Statement);
        }

        function assumable(projectName: string): string {
            return JSON.stringify(
                statementsOf(projectName).filter(
                    (s) =>
                        s.Effect === "Allow" && JSON.stringify(s.Action).includes("sts:AssumeRole")
                )
            );
        }

        test("watch and build tiers can never assume a gated env's role", () => {
            for (const name of [
                "vra-watch",
                "vra-build",
                "vra-validate",
                ...dev.map((d) => `vra-deploy-${d}`),
            ]) {
                const text = assumable(name);
                for (const env of gated) {
                    expect(text).not.toContain(`vra-deploy-${env}`);
                    expect(text).not.toContain(`vra-data-copy-source-${env}`);
                    expect(text).not.toContain(`vra-data-copy-target-${env}`);
                }
            }
            // the build tier gets no env deploy role at all, only build-only synth roles
            expect(assumable("vra-build")).not.toContain("vra-deploy-");
            expect(assumable("vra-build")).toContain("vra-synth-");
        });

        test("each gated env's projects assume only that env's deploy role", () => {
            for (const env of gated) {
                const text = assumable(`vra-deploy-${env}`);
                expect(text).toContain(`vra-deploy-${env}`);
                for (const other of envs.map((e) => e.name).filter((n) => n !== env)) {
                    expect(text).not.toContain(`vra-deploy-${other}"`);
                }
            }
        });

        test("gated envs run their live tests under a test-only tier by default", () => {
            for (const env of gated) {
                const text = assumable(`vra-test-${env}`);
                expect(text).toContain(`vra-test-${env}`);
                expect(text).not.toContain("vra-deploy-");
            }
        });

        test("only the build tier may sign builds; deploying tiers only verify", () => {
            const signing = (name: string) =>
                statementsOf(name).filter(
                    (s) =>
                        s.Effect === "Allow" && /kms:(Sign|Verify)\b/.test(JSON.stringify(s.Action))
                );
            if (!config.signingKeyArn) {
                expect(signing("vra-build")).toEqual([]);
                return;
            }
            expect(JSON.stringify(signing("vra-build"))).toContain('"kms:Sign"');
            for (const name of [
                "vra-watch",
                "vra-validate",
                ...envs.map((e) => `vra-deploy-${e.name}`),
            ]) {
                const text = JSON.stringify(signing(name));
                expect(text).toContain("kms:Verify");
                expect(text).not.toContain('"kms:Sign"');
            }
        });

        test("no tier may delete history records in the state store", () => {
            const bucket = config.stateStore?.bucketName;
            for (const p of Object.values(projects)) {
                const stmts = statementsOf(p.Properties.Name);
                if (bucket) {
                    const deny = stmts.find(
                        (s) =>
                            s.Effect === "Deny" &&
                            JSON.stringify(s.Action).includes("s3:DeleteObject")
                    );
                    expect(deny && JSON.stringify(deny.Resource)).toContain("/history/");
                }
                // (CDK's own grants on the pipeline artifact bucket may delete artifacts; the state bucket never)
                const allowDelete = stmts.filter(
                    (s) =>
                        s.Effect === "Allow" &&
                        /s3:DeleteObject/.test(JSON.stringify(s.Action)) &&
                        !!bucket &&
                        JSON.stringify(s.Resource).includes(bucket)
                );
                expect(allowDelete).toEqual([]);
            }
        });

        test("deploy actions carry the pipeline execution id (approval binding)", () => {
            const pipelines = template.findResources("AWS::CodePipeline::Pipeline");
            const release = Object.values(pipelines).find(
                (p) => p.Properties.Name === "vra-release"
            );
            const deployStage = release?.Properties.Stages.find(
                (s: { Name: string }) => s.Name === `Deploy-${gated[0]}`
            );
            const envVars = JSON.parse(deployStage.Actions[0].Configuration.EnvironmentVariables);
            const byName = Object.fromEntries(
                envVars.map((v: { name: string; value: string }) => [v.name, v.value])
            );
            expect(byName.VRA_PIPELINE_EXECUTION_ID).toBe("#{codepipeline.PipelineExecutionId}");
            expect(byName.VRA_APPROVAL_ACTION).toBeDefined();
        });

        test("artifact bucket is KMS encrypted", () => {
            template.hasResourceProperties("AWS::S3::Bucket", {
                BucketEncryption: {
                    ServerSideEncryptionConfiguration: [
                        Match.objectLike({
                            ServerSideEncryptionByDefault: Match.objectLike({
                                SSEAlgorithm: "aws:kms",
                            }),
                        }),
                    ],
                },
            });
        });
    }
);

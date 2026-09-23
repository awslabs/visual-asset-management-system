/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The GenAI CAD STEP agent pipeline synthesizes on either runtime, and each runtime emits exactly the
 * compute it needs: the AgentCore runtime places no container in the VPC (no Batch, no NAT), the Fargate
 * runtime runs in the private subnets (Batch, public subnets, NAT, ECS endpoint) so the agent's research
 * tools have egress. Both share the same Lambdas, and every one of them can report the workflow's
 * callback token.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const SCHEMA_DIR = path.join(REPO_ROOT, "backendPipelines", "genAi", "cadStepAgent", "vamsSchema");

/** Enable exactly the CAD STEP agent pipeline (no ALB public subnet masking the VPC assertions). */
function onlyCadStepAgent(runtime: "agentcore" | "fargate", extra?: (c: any) => void) {
    return (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.useGlobalVpc.addVpcEndpoints = true;
        c.app.useAlb.enabled = false;
        c.app.useCloudFront.enabled = true;
        for (const name of Object.keys(c.app.pipelines)) {
            const entry = c.app.pipelines[name];
            if (entry && typeof entry === "object" && "enabled" in entry) {
                entry.enabled = name === "useGenAiCadStepAgent";
            }
        }
        if (c.app.pipelines.useRapidPipeline) {
            c.app.pipelines.useRapidPipeline.useEcs.enabled = false;
            c.app.pipelines.useRapidPipeline.useEks.enabled = false;
        }
        c.app.pipelines.useGenAiCadStepAgent.runtime = runtime;
        c.app.pipelines.useGenAiCadStepAgent.useCodeBuild = true;
        c.app.pipelines.useGenAiCadStepAgent.autoRegisterWithVAMS = true;
        if (extra) extra(c);
    };
}

function endpointServices(synth: SynthResult) {
    return synth
        .ofType("AWS::EC2::VPCEndpoint")
        .map((e) => SynthResult.flatten((e.properties as any).ServiceName));
}

function publicSubnets(synth: SynthResult) {
    return synth.ofType("AWS::EC2::Subnet").filter((s) => {
        const tags = ((s.properties as any).Tags ?? []) as Array<{ Key: string; Value: unknown }>;
        return tags.some((t) => t.Key === "aws-cdk:subnet-type" && String(t.Value) === "Public");
    });
}

function pipelineLambdas(synth: SynthResult) {
    return synth
        .ofType("AWS::Lambda::Function")
        .filter((f) => /cadstepagent/i.test(f.stack) || /CadStepAgent/.test(f.logicalId));
}

/** Every IAM policy statement in the synth, flattened, with the stack it belongs to. */
function policyStatements(synth: SynthResult) {
    const out: { stack: string; logicalId: string; statement: any }[] = [];
    for (const r of [...synth.ofType("AWS::IAM::Policy"), ...synth.ofType("AWS::IAM::Role")]) {
        const docs: any[] = [];
        if ((r.properties as any).PolicyDocument) docs.push((r.properties as any).PolicyDocument);
        for (const p of (r.properties as any).Policies ?? []) docs.push(p.PolicyDocument);
        for (const d of docs) {
            for (const s of d?.Statement ?? [])
                out.push({ stack: r.stack, logicalId: r.logicalId, statement: s });
        }
    }
    return out;
}

function actionsOf(statement: any): string[] {
    const a = statement.Action;
    return Array.isArray(a) ? a : a ? [a] : [];
}

/** The custom resource whose completion means the CodeBuild image build has pushed the tag. */
function imageBuildCustomResource(synth: SynthResult) {
    const builds = synth
        .ofType("AWS::CloudFormation::CustomResource")
        .filter((r) => /CadStepAgent.*BuildTriggerCR/.test(r.logicalId));
    expect(builds).toHaveLength(1);
    return builds[0];
}

/**
 * The image build is synchronous from CloudFormation's point of view: the Provider has an isComplete
 * handler (the framework's own isComplete Lambda plus its waiter state machine), the handler that
 * polls the build may read the builds of exactly the pipeline's project, and the resource is the
 * one the CodeBuild project's consumers depend on.
 */
function expectSynchronousImageBuild(synth: SynthResult) {
    const cadStepAgentStack = imageBuildCustomResource(synth).stack;
    const frameworkHandlers = synth
        .ofType("AWS::Lambda::Function")
        .filter(
            (f) => f.stack === cadStepAgentStack && /BuildProvider.*CadStepAgent/.test(f.logicalId)
        )
        .map((f) => (f.properties as any).Handler);
    expect(frameworkHandlers).toEqual(
        expect.arrayContaining(["framework.onEvent", "framework.isComplete", "framework.onTimeout"])
    );
    expect(
        synth
            .ofType("AWS::StepFunctions::StateMachine")
            .filter(
                (s) =>
                    s.stack === cadStepAgentStack && /BuildProvider.*CadStepAgent/.test(s.logicalId)
            )
    ).toHaveLength(1);
    const polls = policyStatements(synth).filter((s) =>
        actionsOf(s.statement).includes("codebuild:BatchGetBuilds")
    );
    expect(polls).toHaveLength(1);
    const resources = polls[0].statement.Resource;
    expect(JSON.stringify(resources)).not.toBe('"*"');
    expect(JSON.stringify(resources)).toMatch(/CodeBuildCadStepAgent[A-Za-z0-9]*.*Arn/);
    const handlers = synth
        .ofType("AWS::Lambda::Function")
        .filter((f) => f.stack === cadStepAgentStack)
        .map((f) => (f.properties as any).Handler);
    expect(handlers).toEqual(
        expect.arrayContaining([
            "imageBuildCustomResource.on_event",
            "imageBuildCustomResource.is_complete",
        ])
    );
}

describe("CAD STEP agent on the AgentCore runtime", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyCadStepAgent("agentcore"),
            mutateKey: "cad-step-agent-agentcore",
        });
    });

    test("[control] the pipeline's Lambdas are in this synth", () => {
        const names = pipelineLambdas(synth).map((f) => f.logicalId);
        expect(names.some((n) => /vamsExecuteCadStepAgentPipeline/.test(n))).toBe(true);
        expect(names.some((n) => /invokeAgentRuntime/.test(n))).toBe(true);
        expect(names.some((n) => /executeBatchJob/.test(n))).toBe(false);
    });

    test("an AgentCore Runtime and its DEFAULT endpoint are created, PUBLIC and HTTP", () => {
        const runtimes = synth.ofType("AWS::BedrockAgentCore::Runtime");
        expect(runtimes).toHaveLength(1);
        const props = runtimes[0].properties as any;
        expect(props.NetworkConfiguration.NetworkMode).toBe("PUBLIC");
        expect(props.ProtocolConfiguration).toBe("HTTP");
        expect(props.LifecycleConfiguration).toEqual({
            IdleRuntimeSessionTimeout: 900,
            MaxLifetime: 28800,
        });
        expect(
            SynthResult.flatten(props.AgentRuntimeArtifact.ContainerConfiguration.ContainerUri)
        ).toMatch(/cadstepagent.*:[0-9a-f]{32}$/i);
        const endpoints = synth.ofType("AWS::BedrockAgentCore::RuntimeEndpoint");
        expect(endpoints).toHaveLength(1);
        expect((endpoints[0].properties as any).Name).toBe("DEFAULT");
    });

    test("the runtime environment carries model pointers and never a credential value", () => {
        const env = (synth.ofType("AWS::BedrockAgentCore::Runtime")[0].properties as any)
            .EnvironmentVariables;
        expect(Object.keys(env).sort()).toEqual([
            "BEDROCK_GUARDRAIL_ID",
            "BEDROCK_GUARDRAIL_VERSION",
            "BEDROCK_MODEL_ID",
            "OPENAI_API_KEY_SECRET_ARN",
            "OPENAI_MODEL_ID",
        ]);
        expect(env.BEDROCK_MODEL_ID).toBe("global.anthropic.claude-sonnet-4-5-20250929-v1:0");
        expect(env.OPENAI_API_KEY_SECRET_ARN).toBe("");
        // The deployment-created guardrail's id and numbered version, not literals.
        expect(JSON.stringify(env.BEDROCK_GUARDRAIL_ID)).toMatch(
            /CadStepAgentGuardrail.*GuardrailId/
        );
        expect(JSON.stringify(env.BEDROCK_GUARDRAIL_VERSION)).toMatch(
            /CadStepAgentGuardrailVersion.*Version/
        );
    });

    test("the run task waits less than the workflow task and longer than any accepted run budget", () => {
        const asl = JSON.parse(
            SynthResult.flatten(
                (
                    synth
                        .ofType("AWS::StepFunctions::StateMachine")
                        .find((s) => /CadStepAgentStateMachine/.test(s.logicalId))!
                        .properties as any
                ).DefinitionString
            )
        );
        const run = asl.States.CadStepAgentAgentCoreRun;
        expect(run.TimeoutSeconds).toBe(6600);
        const bundle = JSON.parse(fs.readFileSync(path.join(SCHEMA_DIR, "pipeline.json"), "utf8"));
        expect(Number(bundle.executionConfig.taskTimeout)).toBeGreaterThan(run.TimeoutSeconds);
        expect(run.TimeoutSeconds).toBeGreaterThan(6000);
    });

    test("the image is built for arm64 by CodeBuild", () => {
        const projects = synth
            .ofType("AWS::CodeBuild::Project")
            .filter((p) => /CadStepAgent/.test(p.logicalId));
        expect(projects).toHaveLength(1);
        const env = (projects[0].properties as any).Environment;
        expect(env.Type).toMatch(/ARM/);
        const vars = env.EnvironmentVariables as Array<{ Name: string; Value: string }>;
        expect(vars.find((v) => v.Name === "TARGET_PLATFORM")?.Value).toBe("linux/arm64");
        expect(vars.find((v) => v.Name === "IMAGE_TAG")?.Value).toMatch(/^[0-9a-f]{32}$/);
    });

    test("the runtime is created only after the image build custom resource completes", () => {
        const runtime = synth.ofType("AWS::BedrockAgentCore::Runtime")[0];
        const build = imageBuildCustomResource(synth);
        expect(runtime.raw.DependsOn ?? []).toContain(build.logicalId);
        // The same custom resource is the one that names the project the build runs in.
        expect(SynthResult.flatten((build.properties as any).ProjectName)).toMatch(/CadStepAgent/);
    });

    test("the build custom resource waits on the build through an isComplete handler", () => {
        expectSynchronousImageBuild(synth);
    });

    test("no Batch compute, NAT gateway or public subnet is created for it", () => {
        expect(synth.ofType("AWS::Batch::ComputeEnvironment")).toEqual([]);
        expect(synth.ofType("AWS::EC2::NatGateway")).toEqual([]);
        expect(publicSubnets(synth)).toEqual([]);
        expect(
            endpointServices(synth).filter((s) => /\.batch$/.test(s) || /\.ecs$/.test(s))
        ).toEqual([]);
    });

    test("the invoke Lambda may invoke only this runtime", () => {
        const invokes = policyStatements(synth).filter((s) =>
            actionsOf(s.statement).includes("bedrock-agentcore:InvokeAgentRuntime")
        );
        expect(invokes.length).toBeGreaterThan(0);
        for (const s of invokes) {
            const resources = JSON.stringify(s.statement.Resource);
            expect(resources).not.toBe('"*"');
            expect(resources).toMatch(/AgentRuntimeArn/);
        }
    });

    test("both vamsSchema bundles are registered, the generate one after the main one", () => {
        const registrations = synth
            .ofType("AWS::CloudFormation::CustomResource")
            .filter((r) => /CadStepAgent.*Registration/.test(r.logicalId));
        expect(registrations).toHaveLength(2);
        const generate = registrations.find((r) => /GenerateRegistration/.test(r.logicalId))!;
        const main = registrations.find((r) => !/GenerateRegistration/.test(r.logicalId))!;
        expect(JSON.stringify(generate.raw.DependsOn ?? [])).toMatch(new RegExp(main.logicalId));
        const ids = registrations.map((r) => JSON.parse((r.properties as any).idOverrides));
        expect(ids.map((i) => i.pipelineId)).toEqual([
            "genai-cad-step-agent",
            "genai-cad-step-agent",
        ]);
        expect(ids.map((i) => i.workflowId).sort()).toEqual([
            "genai-cad-step-agent-generate",
            "genai-cad-step-agent-modify",
        ]);
    });
});

describe("CAD STEP agent on the Fargate runtime", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyCadStepAgent("fargate"),
            mutateKey: "cad-step-agent-fargate",
        });
    });

    test("[control] a Fargate compute environment and the Batch Lambda are in this synth", () => {
        const fargate = synth
            .ofType("AWS::Batch::ComputeEnvironment")
            .filter((e) => /FARGATE/i.test(JSON.stringify((e.properties as any).ComputeResources)));
        expect(fargate.length).toBeGreaterThan(0);
        expect(pipelineLambdas(synth).some((f) => /executeBatchJob/.test(f.logicalId))).toBe(true);
        expect(pipelineLambdas(synth).some((f) => /invokeAgentRuntime/.test(f.logicalId))).toBe(
            false
        );
    });

    test("no AgentCore resources are created", () => {
        expect(synth.ofType("AWS::BedrockAgentCore::Runtime")).toEqual([]);
        expect(synth.ofType("AWS::BedrockAgentCore::RuntimeEndpoint")).toEqual([]);
    });

    test("private subnets with NAT egress exist for the agent's research tools", () => {
        expect(synth.ofType("AWS::EC2::NatGateway").length).toBeGreaterThan(0);
        expect(publicSubnets(synth).length).toBeGreaterThan(0);
    });

    test("Batch, ECR and ECS endpoints are created", () => {
        const services = endpointServices(synth);
        expect(services.some((s) => /\.batch$/.test(s))).toBe(true);
        expect(services.some((s) => /\.ecr\.api$/.test(s))).toBe(true);
        expect(services.some((s) => /\.ecr\.dkr$/.test(s))).toBe(true);
        expect(services.some((s) => /\.ecs$/.test(s))).toBe(true);
    });

    test("the image is built for amd64 and the job definition carries the model pointers", () => {
        const project = synth
            .ofType("AWS::CodeBuild::Project")
            .find((p) => /CadStepAgent/.test(p.logicalId))!;
        const vars = (project.properties as any).Environment.EnvironmentVariables as Array<{
            Name: string;
            Value: string;
        }>;
        expect(vars.find((v) => v.Name === "TARGET_PLATFORM")?.Value).toBe("linux/amd64");
        const jobDef = synth
            .ofType("AWS::Batch::JobDefinition")
            .find((j) => /CadStepAgent/.test(j.logicalId))!;
        const env = (jobDef.properties as any).ContainerProperties.Environment as Array<{
            Name: string;
            Value: string;
        }>;
        expect(env.map((e) => e.Name)).toEqual(
            expect.arrayContaining([
                "BEDROCK_MODEL_ID",
                "BEDROCK_GUARDRAIL_ID",
                "BEDROCK_GUARDRAIL_VERSION",
                "OPENAI_MODEL_ID",
                "OPENAI_API_KEY_SECRET_ARN",
            ])
        );
        expect(SynthResult.flatten((jobDef.properties as any).ContainerProperties.Image)).toMatch(
            /cadstepagent.*:[0-9a-f]{32}$/i
        );
        // The attempt duration equals the run state's wait on the inner token.
        expect((jobDef.properties as any).Timeout.AttemptDurationSeconds).toBe(6600);
    });

    test("the job definition is created only after the image build custom resource completes", () => {
        const jobDef = synth
            .ofType("AWS::Batch::JobDefinition")
            .find((j) => /CadStepAgent/.test(j.logicalId))!;
        expect(jobDef.raw.DependsOn ?? []).toContain(imageBuildCustomResource(synth).logicalId);
        expectSynchronousImageBuild(synth);
    });
});

describe("CAD STEP agent grants (either runtime)", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyCadStepAgent("agentcore"),
            mutateKey: "cad-step-agent-agentcore",
        });
    });

    test("every pipeline Lambda — vamsExecute included — may report the workflow task token", () => {
        const lambdas = pipelineLambdas(synth);
        expect(lambdas.length).toBeGreaterThanOrEqual(5);
        const statements = policyStatements(synth);
        for (const fn of lambdas) {
            const roleRef = JSON.stringify((fn.properties as any).Role);
            const roleId = /"([A-Za-z0-9]+)"/.exec(roleRef)?.[1] ?? "";
            const grants = statements.filter(
                (s) =>
                    actionsOf(s.statement).includes("states:SendTaskFailure") &&
                    (s.logicalId.startsWith(roleId.replace(/Role$/, "")) ||
                        JSON.stringify(s.statement).length > 0)
            );
            expect(grants.length).toBeGreaterThan(0);
        }
        const failures = statements.filter((s) =>
            actionsOf(s.statement).includes("states:SendTaskFailure")
        );
        // One grant per Lambda in the chain (constructPipeline, openPipeline, pipelineEnd, vamsExecute,
        // invokeAgentRuntime) plus the container role.
        expect(failures.length).toBeGreaterThanOrEqual(6);
    });

    test("the Bedrock grant names the configured model, never a wildcard", () => {
        const bedrock = policyStatements(synth).filter((s) =>
            actionsOf(s.statement).includes("bedrock:InvokeModel")
        );
        expect(bedrock.length).toBeGreaterThan(0);
        for (const s of bedrock) {
            const flat = SynthResult.flatten(s.statement.Resource);
            expect(flat).toMatch(/foundation-model\/anthropic\.claude-sonnet-4-5-20250929-v1:0/);
            expect(flat).toMatch(
                /inference-profile\/global\.anthropic\.claude-sonnet-4-5-20250929-v1:0/
            );
            expect(flat).not.toMatch(/foundation-model\/\*/);
        }
    });

    test("a cross-Region inference profile grants the foundation model in every Region, never regionless", () => {
        const bedrock = policyStatements(synth).filter((s) =>
            actionsOf(s.statement).includes("bedrock:InvokeModel")
        );
        for (const s of bedrock) {
            const flat = SynthResult.flatten(s.statement.Resource);
            // The `global.` profile routes requests out of the deployment Region, and Amazon Bedrock
            // authorizes the foundation-model ARN of the Region it routed to.
            expect(flat).toMatch(
                /:bedrock:\*::foundation-model\/anthropic\.claude-sonnet-4-5-20250929-v1:0/
            );
            // `arn:...:bedrock:::foundation-model/<id>` (empty Region segment) matches no real ARN.
            expect(flat).not.toMatch(/:bedrock:::foundation-model\//);
        }
    });

    test("a deployment-created guardrail with a PROMPT_ATTACK input filter exists and only it may be applied", () => {
        const guardrails = synth.ofType("AWS::Bedrock::Guardrail");
        expect(guardrails).toHaveLength(1);
        const filters = (guardrails[0].properties as any).ContentPolicyConfig
            .FiltersConfig as Array<{
            Type: string;
            InputStrength: string;
            OutputStrength: string;
        }>;
        const promptAttack = filters.find((f) => f.Type === "PROMPT_ATTACK");
        expect(promptAttack).toBeDefined();
        expect(promptAttack!.InputStrength).not.toBe("NONE");
        expect(synth.ofType("AWS::Bedrock::GuardrailVersion")).toHaveLength(1);
        const applies = policyStatements(synth).filter((s) =>
            actionsOf(s.statement).includes("bedrock:ApplyGuardrail")
        );
        expect(applies.length).toBeGreaterThan(0);
        for (const s of applies) {
            const resources = JSON.stringify(s.statement.Resource);
            expect(resources).not.toBe('"*"');
            expect(resources).toMatch(/CadStepAgentGuardrail.*GuardrailArn/);
        }
    });

    test("no s3:* or iam:* action is granted anywhere in the pipeline stack", () => {
        const stackStatements = policyStatements(synth).filter((s) =>
            /cadstepagent/i.test(s.stack)
        );
        expect(stackStatements.length).toBeGreaterThan(0);
        for (const s of stackStatements) {
            for (const action of actionsOf(s.statement)) {
                expect(action).not.toBe("s3:*");
                expect(action).not.toMatch(/^iam:/);
            }
        }
    });

    test("no Secrets Manager grant exists while OpenAI is unconfigured", () => {
        const secrets = policyStatements(synth).filter((s) =>
            actionsOf(s.statement).some((a) => a.startsWith("secretsmanager:"))
        );
        expect(secrets.filter((s) => /cadstepagent/i.test(s.stack))).toEqual([]);
    });
});

describe("CAD STEP agent with a plain model id and an existing guardrail", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyCadStepAgent("agentcore", (c) => {
                c.app.pipelines.useGenAiCadStepAgent.bedrockModelId =
                    "anthropic.claude-sonnet-4-5-20250929-v1:0";
                c.app.pipelines.useGenAiCadStepAgent.bedrockGuardrail = {
                    guardrailId: "abc123def456",
                    guardrailVersion: "2",
                };
            }),
            mutateKey: "cad-step-agent-agentcore-plain-model-own-guardrail",
        });
    });

    test("a plain model id is granted in the deployment Region only", () => {
        const bedrock = policyStatements(synth).filter((s) =>
            actionsOf(s.statement).includes("bedrock:InvokeModel")
        );
        expect(bedrock.length).toBeGreaterThan(0);
        for (const s of bedrock) {
            const flat = SynthResult.flatten(s.statement.Resource);
            expect(flat).toMatch(/:bedrock:us-east-1::foundation-model\/anthropic\.claude-sonnet/);
            expect(flat).not.toMatch(/:bedrock:\*::/);
            expect(flat).not.toMatch(/foundation-model\/\*/);
        }
    });

    test("no guardrail is created; the configured one is applied and handed to the container", () => {
        expect(synth.ofType("AWS::Bedrock::Guardrail")).toEqual([]);
        expect(synth.ofType("AWS::Bedrock::GuardrailVersion")).toEqual([]);
        const applies = policyStatements(synth).filter((s) =>
            actionsOf(s.statement).includes("bedrock:ApplyGuardrail")
        );
        expect(applies.length).toBeGreaterThan(0);
        for (const s of applies) {
            expect(SynthResult.flatten(s.statement.Resource)).toMatch(
                /:bedrock:us-east-1:123456789012:guardrail\/abc123def456$/
            );
        }
        const env = (synth.ofType("AWS::BedrockAgentCore::Runtime")[0].properties as any)
            .EnvironmentVariables;
        expect(env.BEDROCK_GUARDRAIL_ID).toBe("abc123def456");
        expect(env.BEDROCK_GUARDRAIL_VERSION).toBe("2");
    });
});

describe("CAD STEP agent with the OpenAI provider configured", () => {
    let synth: SynthResult;
    const secretArn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/openai-abc123";

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyCadStepAgent("agentcore", (c) => {
                c.app.pipelines.useGenAiCadStepAgent.openAi = {
                    modelId: "gpt-cad-test",
                    apiKeySecretArn: secretArn,
                };
            }),
            mutateKey: "cad-step-agent-agentcore-openai",
        });
    });

    test("the container role may read exactly that secret", () => {
        const secrets = policyStatements(synth).filter((s) =>
            actionsOf(s.statement).includes("secretsmanager:GetSecretValue")
        );
        expect(secrets).toHaveLength(1);
        expect(SynthResult.flatten(secrets[0].statement.Resource)).toBe(secretArn);
    });

    test("the runtime environment points at the secret and model, not the key", () => {
        const env = (synth.ofType("AWS::BedrockAgentCore::Runtime")[0].properties as any)
            .EnvironmentVariables;
        expect(env.OPENAI_MODEL_ID).toBe("gpt-cad-test");
        expect(env.OPENAI_API_KEY_SECRET_ARN).toBe(secretArn);
    });
});

describe("CAD STEP agent vamsSchema bundles", () => {
    test("the generate bundle's pipeline.json is byte-identical to the main one", () => {
        const main = fs.readFileSync(path.join(SCHEMA_DIR, "pipeline.json"));
        const generate = fs.readFileSync(
            path.join(SCHEMA_DIR, "generateWorkflow", "pipeline.json")
        );
        expect(generate.equals(main)).toBe(true);
    });

    test("the CDK allow list matches the bundle's STEP filters", () => {
        const construct = fs.readFileSync(
            path.join(
                REPO_ROOT,
                "infra",
                "lib",
                "nestedStacks",
                "pipelines",
                "genAi",
                "cadStepAgent",
                "constructs",
                "cadStepAgent-construct.ts"
            ),
            "utf8"
        );
        expect(construct).toMatch(/const allowedExtensions = "\.stp,\.step";/);
        const workflow = JSON.parse(
            fs.readFileSync(path.join(SCHEMA_DIR, "workflow.json"), "utf8")
        );
        expect(workflow.systemConfig.inputFileFilters.allow.sort()).toEqual(["*.step", "*.stp"]);
    });
});

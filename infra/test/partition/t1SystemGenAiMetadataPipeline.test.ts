/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 arms for the system GenAI metadata pipeline across the three shipped templates: the pipeline's
 * stack is emitted iff the pipeline is enabled; no template in any arm creates a Rekognition
 * endpoint; the Bedrock Runtime endpoint follows the one placement rule; the guardrail grant and the
 * Fargate job definition follow their flags; the video-segment Distributed Map, its self-grant, the
 * segment function and the 89-entry openPipeline allow list are present in every partition. Every
 * negative arm pairs with a positive control.
 *
 * Vector search requires the pipeline (config.ts rejects the other way round), so the "search
 * placement" arm enables both and derives its expectation from searchLambdasInVpc() over the mutated
 * template: GovCloud ships a provisioned OpenSearch domain (in the VPC), the commercial template a
 * public serverless collection (not). The EU Sovereign Cloud rejects vector search outright.
 */

import commercial from "../../config/config.template.commercial.json";
import govcloud from "../../config/config.template.govcloud.json";
import eusovereign from "../../config/config.template.eusovereign.json";
import { searchLambdasInVpc } from "../../lib/helper/searchPlacement";
import { expectAbsent, synthTemplate, SynthResult, TemplateName } from "../support/templateSynth";

const TEMPLATES: Record<TemplateName, any> = { commercial, govcloud, eusovereign };
const CONSTRUCT_PREFIX = "SystemGenAiMetadata";
const ANALYSIS_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0";
const EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0";

/** The pipeline alone: on, unregistered, vector search off. */
const enable = (c: any) => {
    c.app.pipelines.useSystemGenAiMetadata.enabled = true;
    c.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = false;
    c.app.vectorSearch.enabled = false;
    if (c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId === "") {
        c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId = ANALYSIS_MODEL;
    }
};
/** Neither the pipeline nor the vector search that depends on it. */
const disable = (c: any) => {
    c.app.pipelines.useSystemGenAiMetadata.enabled = false;
    c.app.vectorSearch.enabled = false;
};
/** The pipeline registered with its upload trigger, and vector search on it. */
const enableWithSearch = (c: any) => {
    enable(c);
    c.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = true;
    c.app.pipelines.useSystemGenAiMetadata.autoRegisterAutoTriggerOnFileUpload = true;
    c.app.vectorSearch.enabled = true;
    if (c.app.vectorSearch.embeddingModelId === "") {
        c.app.vectorSearch.embeddingModelId = EMBEDDING_MODEL;
    }
};
const inVpc = (c: any) => {
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.addVpcEndpoints = true;
};
const inVpcAllLambdas = (c: any) => {
    inVpc(c);
    c.app.useGlobalVpc.useForAllLambdas = true;
};

const stateMachines = (s: SynthResult) =>
    s.where("AWS::StepFunctions::StateMachine", (r) => r.logicalId.startsWith(CONSTRUCT_PREFIX));
const endpoints = (s: SynthResult, service: string) =>
    s
        .ofType("AWS::EC2::VPCEndpoint")
        .filter((e) => SynthResult.flatten((e.properties as any).ServiceName).includes(service));
const statementsWith = (s: SynthResult, action: string) =>
    s
        .ofType("AWS::IAM::Policy")
        .flatMap((p) => ((p.properties as any).PolicyDocument?.Statement ?? []) as any[])
        .filter((st) => JSON.stringify(st.Action).includes(action));
const renderJobs = (s: SynthResult) =>
    s.where("AWS::Batch::JobDefinition", (r) =>
        SynthResult.flatten((r.properties as any).JobDefinitionName).startsWith(
            "SystemGenAiMetadataRenderJob"
        )
    );

describe.each(["commercial", "govcloud", "eusovereign"] as TemplateName[])("%s", (name) => {
    const shippedEnabled = TEMPLATES[name].app.pipelines.useSystemGenAiMetadata.enabled === true;

    it("emits the pipeline stack iff the pipeline is enabled", () => {
        const on = synthTemplate(name, { mutate: enable, mutateKey: "sysgenai-on" });
        expect(stateMachines(on)).toHaveLength(1);
        const off = synthTemplate(name, { mutate: disable, mutateKey: "sysgenai-off" });
        expectAbsent("system GenAI state machine with the pipeline disabled", stateMachines(off), {
            description: "state machines in the enabled control",
            count: stateMachines(on).length,
        });
        // The shipped template's own arm agrees with its flag.
        const shipped = synthTemplate(name);
        expect(stateMachines(shipped)).toHaveLength(shippedEnabled ? 1 : 0);
    });

    it("creates no Rekognition endpoint in any arm", () => {
        const vpcOn = synthTemplate(name, {
            mutate: (c) => {
                inVpcAllLambdas(c);
                enable(c);
            },
            mutateKey: "sysgenai-vpc-all-on",
        });
        expect(endpoints(vpcOn, "rekognition")).toHaveLength(0);
        // Control: the same arm does create interface endpoints.
        expect(vpcOn.ofType("AWS::EC2::VPCEndpoint").length).toBeGreaterThan(0);
    });

    it("creates the Bedrock Runtime endpoint iff every Lambda is in the VPC and the pipeline is on, or the search placement is in the VPC", () => {
        const on = synthTemplate(name, {
            mutate: (c) => {
                inVpcAllLambdas(c);
                enable(c);
            },
            mutateKey: "sysgenai-vpc-all-on",
        });
        expect(endpoints(on, "bedrock-runtime")).toHaveLength(1);

        const offNoSearch = synthTemplate(name, {
            mutate: (c) => {
                inVpcAllLambdas(c);
                disable(c);
            },
            mutateKey: "sysgenai-vpc-all-off",
        });
        expectAbsent(
            "Bedrock endpoint with the pipeline and vector search disabled",
            endpoints(offNoSearch, "bedrock-runtime"),
            {
                description: "Bedrock endpoints in the enabled control",
                count: endpoints(on, "bedrock-runtime").length,
            }
        );

        const notAllLambdas = synthTemplate(name, {
            mutate: (c) => {
                inVpc(c);
                c.app.useGlobalVpc.useForAllLambdas = false;
                enable(c);
            },
            mutateKey: "sysgenai-vpc-partial-on",
        });
        // With vector search off, only useForAllLambdas can demand the endpoint.
        expect(endpoints(notAllLambdas, "bedrock-runtime")).toHaveLength(0);

        if (name === "eusovereign") return; // vector search is rejected in this partition
        let searchConfig: any;
        const withSearch = synthTemplate(name, {
            mutate: (c) => {
                inVpc(c);
                c.app.useGlobalVpc.useForAllLambdas = false;
                enableWithSearch(c);
                searchConfig = c;
            },
            mutateKey: "sysgenai-vpc-search",
        });
        expect(endpoints(withSearch, "bedrock-runtime")).toHaveLength(
            searchLambdasInVpc(searchConfig) ? 1 : 0
        );
    });

    it("grants bedrock:ApplyGuardrail on the exact guardrail iff configured, once per analysis function", () => {
        const withGuardrail = synthTemplate(name, {
            mutate: (c) => {
                enable(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = {
                    guardrailIdentifier: "gr-t1test",
                    guardrailVersion: "1",
                };
            },
            mutateKey: "sysgenai-guardrail",
        });
        // The whole-file analysis Lambda and the per-segment analysis Lambda, each on the exact ARN.
        const grants = statementsWith(withGuardrail, "bedrock:ApplyGuardrail");
        expect(grants).toHaveLength(2);
        for (const grant of grants) {
            expect(SynthResult.flatten(grant.Resource)).toContain(":guardrail/gr-t1test");
            expect(SynthResult.flatten(grant.Resource)).not.toContain("*");
        }
        const without = synthTemplate(name, { mutate: enable, mutateKey: "sysgenai-on" });
        expect(statementsWith(without, "bedrock:ApplyGuardrail")).toHaveLength(0);
    });

    it("deploys the video-segment Distributed Map, grants the machine StartExecution on itself outside its default policy, and admits the classifier's 89 extensions", () => {
        const on = synthTemplate(name, { mutate: enable, mutateKey: "sysgenai-on" });
        const machines = stateMachines(on).filter((m) =>
            SynthResult.flatten((m.properties as any).DefinitionString).includes("VideoSegmentMap")
        );
        expect(machines).toHaveLength(1);
        const smId = machines[0].logicalId;
        const definition = SynthResult.flatten((machines[0].properties as any).DefinitionString);
        for (const fragment of [
            '"VideoSegmentChoice"',
            '"HandleVideoSegmentsError"',
            '"SegmentAnalyzeTask"',
            '"Label":"VideoSegments"',
            '"ExecutionType":"EXPRESS"',
            '"Key.$":"$.videoSegmentItemsKey"',
            '"Prefix.$":"$.videoSegmentResultsPrefix"',
        ]) {
            expect(definition).toContain(fragment);
        }
        // The reader and writer integration ARNs carry the partition pseudo-parameter, not a literal.
        expect(definition).toContain("arn:${AWS::Partition}:states:::s3:getObject");
        expect(definition).toContain("arn:${AWS::Partition}:states:::s3:putObject");
        expect(definition).not.toMatch(/arn:aws[a-z-]*:states:::s3:/);

        const selfGrants = on.resources
            .filter((r) => /IAM::(Policy|ManagedPolicy)$/.test(r.type))
            .filter((p) =>
                (((p.properties as any).PolicyDocument?.Statement ?? []) as any[]).some(
                    (st) =>
                        JSON.stringify(st.Action).includes("states:StartExecution") &&
                        JSON.stringify(st.Resource).includes(smId)
                )
            );
        // The L2's map policy and the openPipeline Lambda's grant; the state machine role's default
        // policy (the one the machine depends on) never holds the self-grant.
        expect(selfGrants.map((p) => p.logicalId)).toEqual(
            expect.arrayContaining([
                expect.stringMatching(
                    /^SystemGenAiMetadataProcessingStateMachineDistributedMapPolicy/
                ),
            ])
        );
        expect(selfGrants.map((p) => p.logicalId)).not.toEqual(
            expect.arrayContaining([
                expect.stringMatching(
                    /^SystemGenAiMetadataProcessingStateMachineRoleDefaultPolicy/
                ),
            ])
        );
        expect(machines[0].raw.DependsOn ?? []).not.toEqual(
            expect.arrayContaining([expect.stringMatching(/DistributedMapPolicy/)])
        );
        // Control: the same scan finds nothing for a state-machine id that does not exist.
        expect(
            on.resources.filter(
                (r) =>
                    /IAM::(Policy|ManagedPolicy)$/.test(r.type) &&
                    JSON.stringify(r.properties).includes("NoSuchStateMachineControl")
            )
        ).toEqual([]);

        // The reader's and writer's grants name the auxiliary bucket, never `*`.
        const s3Writes = statementsWith(on, "s3:ListMultipartUploadParts");
        expect(s3Writes.length).toBeGreaterThan(0);
        for (const st of s3Writes) {
            expect(SynthResult.flatten(st.Resource)).toMatch(
                /^arn:\$\{AWS::Partition\}:s3:::\$\{.*AssetAuxiliaryBucket.*\}\/\*$/
            );
        }

        // The segment function: the media image with its command pointed at the segment handler.
        const segment = on.where("AWS::Lambda::Function", (r) =>
            r.logicalId.startsWith("SystemGenAiMetadataSegmentAnalyze")
        );
        expect(segment).toHaveLength(1);
        expect((segment[0].properties as any).ImageConfig).toEqual({
            Command: ["segment_handler.lambda_handler"],
        });
        expect((segment[0].properties as any).Timeout).toBe(300);

        // The openPipeline gate admits the classifier's whole allow list, office formats included.
        const openPipeline = on.where(
            "AWS::Lambda::Function",
            (r) => (r.properties as any).Handler === "openPipeline.lambda_handler"
        );
        expect(openPipeline).toHaveLength(1);
        const allowed = String(
            (openPipeline[0].properties as any).Environment.Variables.ALLOWED_INPUT_FILEEXTENSIONS
        ).split(",");
        expect(allowed).toHaveLength(89);
        expect(new Set(allowed).size).toBe(89);
        expect(allowed).toEqual(expect.arrayContaining([".docx", ".pptx", ".xlsx"]));
    });

    it("emits the Fargate render job definition iff useFargateRenderer", () => {
        const fargate = synthTemplate(name, {
            mutate: (c) => {
                inVpc(c);
                enable(c);
                c.app.pipelines.useSystemGenAiMetadata.useFargateRenderer = true;
            },
            mutateKey: "sysgenai-fargate",
        });
        expect(renderJobs(fargate)).toHaveLength(1);
        const lambdaOnly = synthTemplate(name, { mutate: enable, mutateKey: "sysgenai-on" });
        expect(renderJobs(lambdaOnly)).toHaveLength(0);
    });
});

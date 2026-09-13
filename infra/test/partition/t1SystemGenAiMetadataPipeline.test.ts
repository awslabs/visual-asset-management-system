/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 arms for the system GenAI metadata pipeline across the three shipped templates: the pipeline's
 * stack is emitted iff the pipeline is enabled; no template in any arm creates a Rekognition
 * endpoint; the Bedrock Runtime endpoint follows the one placement rule; the guardrail grant and the
 * Fargate job definition follow their flags. Every negative arm pairs with a positive control.
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

    it("grants bedrock:ApplyGuardrail on the exact guardrail iff configured", () => {
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
        const grants = statementsWith(withGuardrail, "bedrock:ApplyGuardrail");
        expect(grants).toHaveLength(1);
        expect(SynthResult.flatten(grants[0].Resource)).toContain(":guardrail/gr-t1test");
        const without = synthTemplate(name, { mutate: enable, mutateKey: "sysgenai-on" });
        expect(statementsWith(without, "bedrock:ApplyGuardrail")).toHaveLength(0);
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

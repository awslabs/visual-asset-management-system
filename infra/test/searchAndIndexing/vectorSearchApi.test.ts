/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * POST /search/nlp and its Lambda exist exactly when vector search is enabled, on every shipped
 * template, and the function's own IAM statements name exact resources: the vector table and its one
 * index for SearchVectors, the one embedding model for InvokeModel (a plain id in this Region, or an
 * inference profile's exact ARN with its model in every Region). OpenSearch grants appear only when
 * an OpenSearch mode is on. Placement follows searchLambdasInVpc().
 */

import { synthTemplate, SynthResult, TemplateName, Resource } from "../support/templateSynth";

const HANDLER = "handlers.osVectorSearch.vectorSearchService.lambda_handler";
const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];
const ENABLED_TEMPLATES: TemplateName[] = ["commercial", "govcloud"];
const enable = (c: any) => {
    c.app.vectorSearch.enabled = true;
};
const disable = (c: any) => {
    c.app.vectorSearch.enabled = false;
};
const enableNoOpenSearch = (c: any) => {
    enable(c);
    c.app.openSearch.useServerless.enabled = false;
    c.app.openSearch.useProvisioned.enabled = false;
};

function fn(synth: SynthResult): Resource[] {
    return synth.where("AWS::Lambda::Function", (r) => r.raw.Properties?.Handler === HANDLER);
}

/** Every statement of every policy attached to the function's execution role. */
function statementsOf(synth: SynthResult, fun: Resource): any[] {
    const roleId = fun.raw.Properties.Role["Fn::GetAtt"][0];
    return synth
        .ofType("AWS::IAM::Policy")
        .filter((p) => (p.raw.Properties.Roles ?? []).some((r: any) => r.Ref === roleId))
        .flatMap((p) => p.raw.Properties.PolicyDocument.Statement);
}
const withAction = (stmts: any[], action: string) =>
    stmts.filter((s) => ([] as string[]).concat(s.Action).includes(action));
/** The ssm:GetParameter statements that name an aos/* parameter (the resourceNames grant is every Lambda's). */
const aosParameterGrants = (stmts: any[]) =>
    withAction(stmts, "ssm:GetParameter").filter((s) =>
        JSON.stringify(s.Resource).includes("/aos/")
    );

describe.each(TEMPLATES)("vector search API on %s", (name) => {
    test("route and Lambda are absent when the feature is off", () => {
        const synth = synthTemplate(name, { mutate: disable, mutateKey: "vectorSearchOff" });
        expect(fn(synth)).toHaveLength(0);
        expect(synth.grep("/search/nlp")).toHaveLength(0);
    });
});

// getConfig() rejects vector search in the European Sovereign Cloud partition, so the enabled arms
// synthesize the two templates a deployment can actually enable it on.
describe.each(ENABLED_TEMPLATES)("vector search API enabled on %s", (name) => {
    test("route, Lambda, env and exact IAM when enabled", () => {
        const synth = synthTemplate(name, { mutate: enable, mutateKey: "vectorSearchOn" });
        const functions = fn(synth);
        expect(functions).toHaveLength(1);
        const [fun] = functions;
        expect(synth.grep("/search/nlp").length).toBeGreaterThan(0);
        const env = fun.raw.Properties.Environment.Variables;
        expect(Object.keys(env)).toEqual(
            expect.arrayContaining([
                "VECTOR_INDEX_NAME",
                "EMBEDDING_MODEL_ID",
                "EMBEDDING_DIMENSIONS",
                "OPENSEARCH_ENDPOINT_SSM_PARAM",
                "OPENSEARCH_ASSET_INDEX_SSM_PARAM",
                "OPENSEARCH_FILE_INDEX_SSM_PARAM",
                "OPENSEARCH_TYPE",
                "OPENSEARCH_DISABLED",
            ])
        );
        expect(env.VECTOR_INDEX_NAME).toMatch(/^vec-[a-z0-9-]+-\d+$/);
        expect(env.VECTOR_INDEX_NAME.endsWith(`-${env.EMBEDDING_DIMENSIONS}`)).toBe(true);

        const stmts = statementsOf(synth, fun);
        const search = withAction(stmts, "dynamodb:SearchVectors");
        expect(search).toHaveLength(1);
        expect(search[0].Resource).toHaveLength(2);
        const searchText = JSON.stringify(search[0].Resource);
        expect(searchText).not.toContain("*");
        expect(searchText).toContain(`/index/${env.VECTOR_INDEX_NAME}`);

        const bedrock = withAction(stmts, "bedrock:InvokeModel");
        expect(bedrock).toHaveLength(1);
        expect(([] as string[]).concat(bedrock[0].Action)).toEqual(["bedrock:InvokeModel"]);
        // The shared Bedrock statement: a plain embedding model id is the deployment Region's and the
        // Region-less model ARN, exact and wildcard-free.
        const bedrockResources: string[] = ([] as string[]).concat(bedrock[0].Resource).sort();
        expect(bedrockResources).toEqual(
            [
                `arn:${synth.partition}:bedrock:${synth.region}::foundation-model/${env.EMBEDDING_MODEL_ID}`,
                `arn:${synth.partition}:bedrock:::foundation-model/${env.EMBEDDING_MODEL_ID}`,
            ].sort()
        );
        for (const arn of bedrockResources) expect(arn).not.toContain("*");
    });

    test("an inference-profile embedding model is granted through its exact profile ARN and the model in every Region", () => {
        // GovCloud ships the `us-gov.` profile prefix; the commercial partition `global.`/`us.`.
        const prefix = name === "govcloud" ? "us-gov." : "us.";
        const synth = synthTemplate(name, {
            mutate: (c: any) => {
                enable(c);
                c.app.vectorSearch.embeddingModelId = `${prefix}amazon.nova-2-multimodal-embeddings-v1:0`;
            },
            mutateKey: "vectorSearchOnProfile",
        });
        const [fun] = fn(synth);
        const bedrock = withAction(statementsOf(synth, fun), "bedrock:InvokeModel");
        expect(bedrock).toHaveLength(1);
        expect(([] as string[]).concat(bedrock[0].Resource).sort()).toEqual(
            [
                `arn:${synth.partition}:bedrock:${synth.region}:123456789012:inference-profile/${prefix}amazon.nova-2-multimodal-embeddings-v1:0`,
                `arn:${synth.partition}:bedrock:*::foundation-model/amazon.nova-2-multimodal-embeddings-v1:0`,
            ].sort()
        );
        // The Region wildcard on the model is the one the function's policy justifies by name.
        const policy = synth
            .ofType("AWS::IAM::Policy")
            .find((p) =>
                JSON.stringify(p.raw.Properties.PolicyDocument).includes("inference-profile/")
            );
        expect(JSON.stringify(policy?.raw.Metadata ?? {})).toContain(
            "cross-Region inference profile"
        );
        expect(synth.grep("inference-profile/*")).toEqual([]);
    });

    test("no OpenSearch grant and OPENSEARCH_DISABLED=true when no OpenSearch mode is on", () => {
        const synth = synthTemplate(name, {
            mutate: enableNoOpenSearch,
            mutateKey: "vectorSearchOnNoOs",
        });
        const [fun] = fn(synth);
        expect(fun).toBeDefined();
        expect(fun.raw.Properties.Environment.Variables.OPENSEARCH_DISABLED).toBe("true");
        expect(aosParameterGrants(statementsOf(synth, fun))).toHaveLength(0);
        expect(fun.raw.Properties.VpcConfig).toBeUndefined();
    });
});

describe("commercial: OpenSearch on", () => {
    test("the exact aos/* parameter ARNs are granted and OPENSEARCH_DISABLED is false", () => {
        const synth = synthTemplate("commercial", { mutate: enable, mutateKey: "vectorSearchOn" });
        const [fun] = fn(synth);
        expect(fun.raw.Properties.Environment.Variables.OPENSEARCH_DISABLED).toBe("false");
        const ssm = aosParameterGrants(statementsOf(synth, fun));
        expect(ssm).toHaveLength(1);
        const resources: string[] = ([] as string[]).concat(ssm[0].Resource);
        expect(resources).toHaveLength(3);
        for (const arn of resources) {
            expect(arn).not.toContain("*");
            expect(arn).toMatch(
                new RegExp(`^arn:${synth.partition}:ssm:${synth.region}:\\d{12}:parameter/.+/aos/`)
            );
        }
    });

    test("the function is in the VPC when the search predicate holds", () => {
        const synth = synthTemplate("commercial", {
            mutate: (c: any) => {
                enable(c);
                c.app.useGlobalVpc.enabled = true;
                c.app.useGlobalVpc.useForAllLambdas = true;
            },
            mutateKey: "vectorSearchOnVpc",
        });
        const [fun] = fn(synth);
        expect(fun.raw.Properties.VpcConfig?.SubnetIds?.length).toBeGreaterThan(0);
    });
});

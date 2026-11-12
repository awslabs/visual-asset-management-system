/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The system-workflow reindexer (the `vectorReindexer` Lambda, the launch queue and its launcher, their
 * dead-letter queues and alarms, and the `lambdaFunctions/vectorReindexer` parameter) exists whenever the
 * system GenAI metadata pipeline is on and registered, with vector search on or off, and never with the
 * pipeline off. With vector search off it is the bulk way to analyze the files that predate the pipeline.
 *
 * The vector-on logical ids are pinned: the vector-off path reuses the same construct ids, so a change
 * that moved a resource to a new id would make every vector-on upgrade replace it (CloudFormation creates
 * the replacement first, and the named SSM parameter would collide).
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

jest.setTimeout(900_000);

const inSearchStack = (r: { stack: string }) => /searchbuilder/i.test(r.stack);

/** The reindexer family's logical ids in the search stack, the same in both cases. */
const REINDEXER_FAMILY = [
    "ResourceNameParamVectorReindexerFC4F8D8B",
    "VectorIndexingSystemWorkflowLaunchDLQAA0DB009",
    "VectorIndexingSystemWorkflowLaunchDLQAlarm8EF83E47",
    "VectorIndexingSystemWorkflowLaunchDLQPolicyBA89F116",
    "VectorIndexingSystemWorkflowLaunchQueue49D54D8C",
    "VectorIndexingSystemWorkflowLaunchQueuePolicyA15D0168",
    "VectorIndexingVectorReindexerAsyncDLQAlarm0CEB9B3D",
    "VectorIndexingVectorReindexerAsyncDLQB60B9EFF",
    "VectorIndexingVectorReindexerAsyncDLQPolicy8CFED122",
    "VectorIndexingsystemWorkflowLauncher46522329",
    "VectorIndexingsystemWorkflowLauncherServiceRole50656F80",
    "VectorIndexingsystemWorkflowLauncherServiceRoleDefaultPolicy4C679549",
    "VectorIndexingvectorReindexer805C9493",
    "VectorIndexingvectorReindexerEventInvokeConfig6911179E",
    "VectorIndexingvectorReindexerSelfInvokePolicyE1546957",
    "VectorIndexingvectorReindexerServiceRoleDefaultPolicy3B1CBD49",
    "VectorIndexingvectorReindexerServiceRoleF04D0FFC",
];

/** The vector-only part of the construct, present only with vector search on. */
const VECTOR_ONLY = [
    "VectorIndexingVectorEmbeddingReadyRule76031045",
    "VectorIndexingVectorEmbeddingReadyRuleDLQ8539AF40",
    "VectorIndexingVectorEmbeddingReadyRuleDLQAlarm07F1FE0D",
    "VectorIndexingVectorEmbeddingReadyRuleDLQPolicy4D3DEB61",
    "VectorIndexingVectorIndexerDLQ43D88FAB",
    "VectorIndexingVectorIndexerDLQAlarmBFF53C8B",
    "VectorIndexingVectorIndexerDLQPolicyF127573B",
    "VectorIndexingVectorIndexerQueue61252D31",
    "VectorIndexingVectorIndexerQueuePolicyF3922C9A",
    "VectorIndexingvectorIndexer522F4BF2",
    "VectorIndexingvectorIndexerServiceRole05B2229A",
    "VectorIndexingvectorIndexerServiceRoleDefaultPolicy65313BF6",
];

/** Logical ids of the construct and the parameter, without the stack-named mappings and subscriptions. */
const familyIds = (s: SynthResult): string[] =>
    s.resources
        .filter(
            (r) =>
                inSearchStack(r) &&
                (r.logicalId.startsWith("VectorIndexing") ||
                    r.logicalId.startsWith("ResourceNameParamVectorReindexer")) &&
                r.type !== "AWS::Lambda::EventSourceMapping" &&
                r.type !== "AWS::SNS::Subscription"
        )
        .map((r) => r.logicalId)
        .sort();

const lambdaByModule = (s: SynthResult, module: string) =>
    s.where(
        "AWS::Lambda::Function",
        (f) =>
            SynthResult.flatten(f.properties.Handler) ===
            `handlers.osVectorSearch.${module}.lambda_handler`
    );

const mappingsOnto = (s: SynthResult, fn: { logicalId: string }) =>
    s.where("AWS::Lambda::EventSourceMapping", (m) =>
        JSON.stringify(m.properties.FunctionName).includes(fn.logicalId)
    );

const allStatements = (s: SynthResult, fn: { properties: any }): any[] => {
    const roleRef = SynthResult.flatten(fn.properties.Role).replace(/^\$\{|\.Arn\}$/g, "");
    return [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")]
        .filter((p) => SynthResult.flatten(p.properties.Roles).includes(roleRef))
        .flatMap((p) => {
            const st = p.properties.PolicyDocument.Statement;
            return Array.isArray(st) ? st : [st];
        });
};

const vectorOff = (c: any) => {
    c.app.vectorSearch.enabled = false;
};
const pipelineOff = (c: any) => {
    c.app.vectorSearch.enabled = false;
    c.app.pipelines.useSystemGenAiMetadata.enabled = false;
};

describe("vector search on: the reindexer family keeps its logical ids", () => {
    test("every pinned id is emitted, and the construct emits nothing else", () => {
        const s = synthTemplate("commercial");
        expect(familyIds(s)).toEqual([...REINDEXER_FAMILY, ...VECTOR_ONLY].sort());
    });
});

describe("system pipeline on, vector search off: the reindexer family without the vector index", () => {
    let s: SynthResult;
    beforeAll(() => {
        s = synthTemplate("commercial", { mutate: vectorOff, mutateKey: "reindexer-vector-off" });
    });

    test("emits the reindexer family with the vector-on logical ids and nothing vector-only", () => {
        expect(familyIds(s)).toEqual([...REINDEXER_FAMILY].sort());
    });

    test("the two alarms watch the launch DLQ and the reindexer's async DLQ", () => {
        const alarms = s
            .where("AWS::CloudWatch::Alarm", (a) => a.logicalId.startsWith("VectorIndexing"))
            .map((a) => SynthResult.flatten(a.properties.AlarmName).replace(/^.*-/, ""))
            .sort();
        expect(alarms).toEqual(["systemWorkflowLaunchDLQ", "vectorReindexerAsyncDLQ"]);
    });

    test("no vector indexer, embedding-ready rule, /search/nlp route or vector index exists", () => {
        expect(lambdaByModule(s, "vectorIndexer")).toHaveLength(0);
        expect(lambdaByModule(s, "vectorSearchService")).toHaveLength(0);
        expect(
            s.where("AWS::Events::Rule", (r) =>
                JSON.stringify(r.properties.EventPattern ?? {}).includes("vector.embedding.ready")
            )
        ).toHaveLength(0);
        expect(s.grep("/search/nlp")).toEqual([]);
        expect(
            s.where("AWS::DynamoDB::Table", (t) => t.properties.VectorIndexes !== undefined)
        ).toHaveLength(0);
    });

    test("the reindexer says vector search is off, carries no vector index environment and no vector table grant", () => {
        const [reindexer] = lambdaByModule(s, "vectorReindexer");
        const env = reindexer.properties.Environment.Variables;
        expect(env.VECTOR_SEARCH_ENABLED).toBe("false");
        for (const key of ["VECTOR_INDEX_NAME", "EMBEDDING_MODEL_ID", "EMBEDDING_DIMENSIONS"]) {
            expect({ key, value: env[key] }).toEqual({ key, value: undefined });
        }
        // The vector table is in the storage stack, so the search stack's statements name it through
        // the nested-stack parameter whose name carries the table's construct id.
        const grants = allStatements(s, reindexer)
            .filter((st: any) =>
                JSON.stringify(st.Resource).includes("VectorEmbeddingsStorageTable")
            )
            .flatMap((st: any) => [].concat(st.Action));
        expect(grants).toEqual([]);
        // Control: the reindexer's statements are read (its asset table grant is there).
        expect(
            allStatements(s, reindexer).some((st: any) =>
                JSON.stringify(st.Resource).includes("AssetStorageTable")
            )
        ).toBe(true);
    });

    test("the launcher consumes the launch queue at the configured indexingConcurrency", () => {
        const [launcher] = lambdaByModule(s, "systemWorkflowLauncher");
        const mappings = mappingsOnto(s, launcher);
        expect(mappings).toHaveLength(1);
        expect(mappings[0].properties.ScalingConfig.MaximumConcurrency).toBe(5);
        expect(SynthResult.flatten(mappings[0].properties.EventSourceArn)).toContain(
            "VectorIndexingSystemWorkflowLaunchQueue49D54D8C"
        );
    });

    test("no vector reindex runs on deploy, even with reindexOnCdkDeploy set", () => {
        const flagged = synthTemplate("commercial", {
            mutate: (c: any) => {
                vectorOff(c);
                c.app.vectorSearch.reindexOnCdkDeploy = true;
            },
            mutateKey: "reindexer-vector-off-reindex-on-deploy",
        });
        expect(
            flagged.where("AWS::CloudFormation::CustomResource", (r) =>
                r.logicalId.startsWith("VectorReindexTrigger")
            )
        ).toHaveLength(0);
    });
});

describe("system pipeline off: no reindexer family", () => {
    test("emits none of the construct, the parameter or the vector Lambdas", () => {
        const s = synthTemplate("commercial", {
            mutate: pipelineOff,
            mutateKey: "reindexer-pipeline-off",
        });
        expect(familyIds(s)).toEqual([]);
        expect(
            s.where("AWS::Lambda::Function", (f) =>
                SynthResult.flatten(f.properties.Handler).startsWith("handlers.osVectorSearch.")
            )
        ).toHaveLength(0);
    });
});

describe("vector search on: the reindexer keeps its vector environment and table grant", () => {
    test("VECTOR_SEARCH_ENABLED is true beside the vector index environment", () => {
        const s = synthTemplate("commercial");
        const [reindexer] = lambdaByModule(s, "vectorReindexer");
        const env = reindexer.properties.Environment.Variables;
        expect(env.VECTOR_SEARCH_ENABLED).toBe("true");
        expect(SynthResult.flatten(env.VECTOR_INDEX_NAME)).not.toBe("");
        const actions = allStatements(s, reindexer)
            .filter((st: any) =>
                JSON.stringify(st.Resource).includes("VectorEmbeddingsStorageTable")
            )
            .flatMap((st: any) => [].concat(st.Action));
        expect(actions).toEqual(
            expect.arrayContaining(["dynamodb:BatchWriteItem", "dynamodb:Scan"])
        );
    });
});

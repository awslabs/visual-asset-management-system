/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: the vector indexing construct is emitted in the search stack exactly when
 * `app.vectorSearch.enabled`, its reindexer name is published to SSM, and its Lambdas address the index
 * the storage stack declares.
 *
 * The commercial template enables the feature; the two restricted templates disable it, and their
 * absence assertions are controlled by the commercial counts. `VECTOR_INDEX_NAME` is compared with the
 * `IndexName` of the storage stack's `VectorIndexes` override, so the two stacks cannot name different
 * indexes and pass.
 */

import {
    RESTRICTED_TEMPLATES,
    SynthResult,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";

jest.setTimeout(900_000);

const inSearchStack = (r: { stack: string }) => /searchbuilder/i.test(r.stack);

// Children of the `VectorIndexing` construct synthesize as `VectorIndexing<ChildId><8HEX>`.
const vectorQueues = (s: SynthResult) =>
    s.where(
        "AWS::SQS::Queue",
        (q) =>
            inSearchStack(q) &&
            (q.logicalId.startsWith("VectorIndexingVectorIndexerQueue") ||
                q.logicalId.startsWith("VectorIndexingSystemWorkflowLaunchQueue"))
    );

const vectorLambdas = (s: SynthResult) =>
    s.where("AWS::Lambda::Function", (f) =>
        SynthResult.flatten(f.properties.Handler).startsWith("handlers.vectorsearch.")
    );

const embeddingRules = (s: SynthResult) =>
    s.where("AWS::Events::Rule", (r) =>
        JSON.stringify(r.properties.EventPattern ?? {}).includes("vector.embedding.ready")
    );

const reindexerParams = (s: SynthResult) =>
    s.where("AWS::SSM::Parameter", (p) =>
        SynthResult.flatten(p.properties.Name).endsWith(
            "/resourceNames/lambdaFunctions/vectorReindexer"
        )
    );

const handlerOf = (s: SynthResult, module: string) => {
    const found = vectorLambdas(s).filter(
        (f) =>
            SynthResult.flatten(f.properties.Handler) ===
            `handlers.vectorsearch.${module}.lambda_handler`
    );
    expect(found).toHaveLength(1);
    return found[0];
};

/**
 * Every statement of every policy attached to a Lambda's role, inline AND managed: CDK spills into a
 * managed policy once an inline policy nears the size limit, and scanning only `AWS::IAM::Policy` reports
 * a correctly-granted permission as missing.
 */
const statementsOf = (s: SynthResult, fn: { properties: any }): any[] => {
    const roleRef = SynthResult.flatten(fn.properties.Role).replace(/^\$\{|\.Arn\}$/g, "");
    return [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")]
        .filter((p) => SynthResult.flatten(p.properties.Roles).includes(roleRef))
        .flatMap((p) => {
            const st = p.properties.PolicyDocument.Statement;
            return Array.isArray(st) ? st : [st];
        });
};

describe("commercial: vector indexing is wired into the search stack", () => {
    let s: SynthResult;
    beforeAll(() => {
        s = synthTemplate("commercial");
    });

    test("emits the two source queues, the embedding-ready rule and the four Lambdas", () => {
        expect(
            vectorQueues(s)
                .map((q) => q.logicalId.replace(/[0-9A-F]{8}$/, ""))
                .sort()
        ).toEqual(["VectorIndexingSystemWorkflowLaunchQueue", "VectorIndexingVectorIndexerQueue"]);
        expect(embeddingRules(s)).toHaveLength(1);
        expect(
            vectorLambdas(s)
                .map((f) => SynthResult.flatten(f.properties.Handler))
                .sort()
        ).toEqual([
            "handlers.vectorsearch.systemWorkflowLauncher.lambda_handler",
            "handlers.vectorsearch.vectorIndexer.lambda_handler",
            "handlers.vectorsearch.vectorReindexer.lambda_handler",
            "handlers.vectorsearch.vectorSearchService.lambda_handler",
        ]);
        expect(vectorLambdas(s).every(inSearchStack)).toBe(true);
    });

    test("publishes the reindexer function name under lambdaFunctions/vectorReindexer", () => {
        const params = reindexerParams(s);
        expect(params).toHaveLength(1);
        expect(SynthResult.flatten(params[0].properties.Value)).toContain(
            handlerOf(s, "vectorReindexer").logicalId
        );
        expect(params[0].properties.Type).toBe("String");
    });

    test("VECTOR_INDEX_NAME is the index the storage stack declares on the vector table", () => {
        const tables = s.where(
            "AWS::DynamoDB::Table",
            (t) => t.properties.VectorIndexes !== undefined
        );
        expect(tables).toHaveLength(1);
        const indexName = SynthResult.flatten(tables[0].properties.VectorIndexes[0].IndexName);
        expect(indexName).not.toBe("");
        for (const module of ["vectorIndexer", "vectorReindexer"]) {
            const env = handlerOf(s, module).properties.Environment.Variables;
            expect(SynthResult.flatten(env.VECTOR_INDEX_NAME)).toBe(indexName);
        }
    });

    test("the launcher's InvokeFunction resource names the execute-workflow Lambda in the aws partition", () => {
        const statements = statementsOf(s, handlerOf(s, "systemWorkflowLauncher")).filter(
            (st: any) => JSON.stringify(st.Action).includes("lambda:InvokeFunction")
        );
        expect(statements).toHaveLength(1);
        const resource = SynthResult.flatten(statements[0].Resource);
        expect(resource).toMatch(/^arn:aws:lambda:us-east-1:123456789012:function:/);
        expect(resource).toContain("executeWorkflow");
    });

    test("the indexer addresses its own queue and may send continuation messages to it", () => {
        const indexer = handlerOf(s, "vectorIndexer");
        const queue = vectorQueues(s).find((q) =>
            q.logicalId.startsWith("VectorIndexingVectorIndexerQueue")
        )!;
        expect(
            SynthResult.flatten(indexer.properties.Environment.Variables.VECTOR_INDEXER_QUEUE_URL)
        ).toContain(queue.logicalId);
        const send = statementsOf(s, indexer).filter((st: any) =>
            JSON.stringify(st.Action).includes("sqs:SendMessage")
        );
        expect(send).toHaveLength(1);
        expect(SynthResult.flatten(send[0].Resource)).toContain(queue.logicalId);
    });

    test("both indexer topics subscribe the vector indexer queue", () => {
        const queue = vectorQueues(s).find((q) =>
            q.logicalId.startsWith("VectorIndexingVectorIndexerQueue")
        )!;
        const subscriptions = s.where(
            "AWS::SNS::Subscription",
            (sub) =>
                sub.properties.Protocol === "sqs" &&
                SynthResult.flatten(sub.properties.Endpoint).includes(queue.logicalId)
        );
        expect(subscriptions).toHaveLength(2);
        expect(
            new Set(subscriptions.map((sub) => SynthResult.flatten(sub.properties.TopicArn))).size
        ).toBe(2);
    });
});

describe.each(RESTRICTED_TEMPLATES)(
    "%s: vectorSearch disabled emits no vector indexing",
    (name) => {
        test("no queue, rule, Lambda or SSM parameter is emitted", () => {
            const s = synthTemplate(name);
            const control = synthTemplate("commercial");
            expectAbsent(
                `vector source queues in ${name}`,
                vectorQueues(s).map((q) => q.logicalId),
                {
                    description: "commercial emits the vector source queues",
                    count: vectorQueues(control).length,
                }
            );
            expectAbsent(
                `vectorsearch Lambdas in ${name}`,
                vectorLambdas(s).map((f) => f.logicalId),
                {
                    description: "commercial emits the vectorsearch Lambdas",
                    count: vectorLambdas(control).length,
                }
            );
            expectAbsent(
                `embedding-ready rules in ${name}`,
                embeddingRules(s).map((r) => r.logicalId),
                {
                    description: "commercial emits the embedding-ready rule",
                    count: embeddingRules(control).length,
                }
            );
            expectAbsent(
                `vectorReindexer SSM parameters in ${name}`,
                reindexerParams(s).map((p) => p.logicalId),
                {
                    description: "commercial publishes the reindexer name",
                    count: reindexerParams(control).length,
                }
            );
        });
    }
);

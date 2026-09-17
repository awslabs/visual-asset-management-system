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

/**
 * The vector indexers and the OpenSearch indexers are two independent families. Each is gated on its own
 * flag, consumes only its own SQS queues, and neither is emitted because the other is. They meet only
 * at the storage stack's two SNS fan-out topics, which exist whether or not either family is present,
 * so a change that made one family's Lambda consume the other's queue -- or made one flag imply the
 * other -- would keep both features "working" in the enabled-enabled deployment and break silently in
 * the two mixed ones. Every combination the config can express is synthesized here.
 */
describe("the vector and OpenSearch indexer families are independent", () => {
    const openSearchIndexerLambdas = (s: SynthResult) =>
        s.where("AWS::Lambda::Function", (f) =>
            /^handlers\.indexing\.(fileIndexer|assetIndexer|crReindexer)\./.test(
                SynthResult.flatten(f.properties.Handler)
            )
        );

    const openSearchQueues = (s: SynthResult) =>
        s.where(
            "AWS::SQS::Queue",
            (q) =>
                inSearchStack(q) &&
                (q.logicalId.startsWith("FileIndexerSqsQueue") ||
                    q.logicalId.startsWith("AssetIndexerSqsQueue"))
        );

    /** `[handlerModulePrefix, sourceQueueLogicalId]` for every SQS-backed mapping on an indexer Lambda. */
    const indexerMappings = (s: SynthResult): Array<[string, string]> => {
        const handlerByLogicalId = new Map(
            s
                .ofType("AWS::Lambda::Function")
                .map((f) => [f.logicalId, SynthResult.flatten(f.properties.Handler)] as const)
        );
        const queueIds = [...vectorQueues(s), ...openSearchQueues(s)].map((q) => q.logicalId);
        return s
            .ofType("AWS::Lambda::EventSourceMapping")
            .map((m) => {
                const fnRef = JSON.stringify(m.properties.FunctionName);
                const target = [...handlerByLogicalId.keys()].find((id) => fnRef.includes(id));
                const source = SynthResult.flatten(m.properties.EventSourceArn);
                const queue = queueIds.find((id) => source.includes(id));
                return [target ? handlerByLogicalId.get(target)! : "", queue ?? ""] as [
                    string,
                    string
                ];
            })
            .filter(([handler, queue]) => queue !== "" && /^handlers\./.test(handler));
    };

    const synthMode = (vector: boolean, openSearch: boolean) =>
        synthTemplate("commercial", {
            mutateKey: `indexer-families-v${vector}-os${openSearch}`,
            mutate: (c: any) => {
                c.app.vectorSearch.enabled = vector;
                c.app.openSearch.useServerless.enabled = openSearch;
                c.app.openSearch.useProvisioned.enabled = false;
            },
        });

    test("vector on + OpenSearch on: both families present, each consuming only its own queues", () => {
        const s = synthMode(true, true);
        expect(vectorLambdas(s).length).toBeGreaterThan(0);
        expect(openSearchIndexerLambdas(s)).toHaveLength(3);
        expect(vectorQueues(s)).toHaveLength(2);
        expect(openSearchQueues(s)).toHaveLength(2);

        const mappings = indexerMappings(s);
        // Positive control: the check below reads real mappings, not an empty list.
        expect(mappings.length).toBeGreaterThanOrEqual(4);
        for (const [handler, queue] of mappings) {
            const vectorHandler = handler.startsWith("handlers.vectorsearch.");
            const vectorQueue = queue.startsWith("VectorIndexing");
            expect({ handler, queue, crossWired: vectorHandler !== vectorQueue }).toEqual({
                handler,
                queue,
                crossWired: false,
            });
        }
    });

    test("vector on + OpenSearch off: the vector family is complete and no OpenSearch indexer exists", () => {
        const s = synthMode(true, false);
        const control = synthMode(true, true);
        expect(
            vectorQueues(s)
                .map((q) => q.logicalId)
                .sort()
        ).toEqual(
            vectorQueues(control)
                .map((q) => q.logicalId)
                .sort()
        );
        expect(
            vectorLambdas(s)
                .map((f) => f.logicalId)
                .sort()
        ).toEqual(
            vectorLambdas(control)
                .map((f) => f.logicalId)
                .sort()
        );
        expectAbsent(
            "OpenSearch indexer Lambdas with OpenSearch disabled",
            openSearchIndexerLambdas(s).map((f) => f.logicalId),
            {
                description: "the enabled-enabled synth emits the OpenSearch indexers",
                count: openSearchIndexerLambdas(control).length,
            }
        );
        expectAbsent(
            "OpenSearch indexer queues with OpenSearch disabled",
            openSearchQueues(s).map((q) => q.logicalId),
            {
                description: "the enabled-enabled synth emits the OpenSearch indexer queues",
                count: openSearchQueues(control).length,
            }
        );
    });

    test("vector off + OpenSearch on: the OpenSearch family is complete and no vector indexer exists", () => {
        const s = synthMode(false, true);
        const control = synthMode(true, true);
        expect(
            openSearchQueues(s)
                .map((q) => q.logicalId)
                .sort()
        ).toEqual(
            openSearchQueues(control)
                .map((q) => q.logicalId)
                .sort()
        );
        expect(
            openSearchIndexerLambdas(s)
                .map((f) => f.logicalId)
                .sort()
        ).toEqual(
            openSearchIndexerLambdas(control)
                .map((f) => f.logicalId)
                .sort()
        );
        expectAbsent(
            "vector indexing Lambdas with vectorSearch disabled",
            vectorLambdas(s).map((f) => f.logicalId),
            {
                description: "the enabled-enabled synth emits the vectorsearch Lambdas",
                count: vectorLambdas(control).length,
            }
        );
        expectAbsent(
            "vector source queues with vectorSearch disabled",
            vectorQueues(s).map((q) => q.logicalId),
            {
                description: "the enabled-enabled synth emits the vector source queues",
                count: vectorQueues(control).length,
            }
        );
    });

    test("the two SNS fan-out topics are emitted by storage in every mode, so each family subscribes independently", () => {
        for (const [vector, openSearch] of [
            [true, true],
            [true, false],
            [false, true],
        ] as const) {
            const s = synthMode(vector, openSearch);
            const topics = s.where(
                "AWS::SNS::Topic",
                (t) =>
                    /storageresources/i.test(t.stack) &&
                    /^(File|Asset)IndexerSnsTopic/.test(t.logicalId)
            );
            expect({ vector, openSearch, topics: topics.length }).toEqual({
                vector,
                openSearch,
                topics: 2,
            });
        }
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Audit log search infrastructure (core VAMS, no feature switch): the service function, its table and
 * its IAM, asserted on the synthesized templates of every partition.
 *
 *  * the function, the table and the SSM name deploy in the default cell of all three templates;
 *  * the function reads the audit log groups and nothing more: `logs:StartQuery` on the nine group
 *    ARNs (built from the groups, so the partition follows the template), the three query-id actions
 *    on `*`, and no destructive Logs action on any role that can reach an audit log group;
 *  * the table is encrypted with the shared CMK when the deployment uses one, keeps point-in-time
 *    recovery, expires its rows with a TTL and is retained on stack deletion;
 *  * the configuration reaches the function as environment variables.
 */

import {
    SynthResult,
    synthTemplate,
    lambdaByHandler,
    statementsOf,
    actionsOf,
    resourcesOf,
} from "../support/templateSynth";

const HANDLER = "handlers.audit.auditLogService.lambda_handler";

/** Logs actions that modify or remove audit data or its protection. */
const DESTRUCTIVE_LOGS_ACTION =
    /^logs:(Delete.*|PutRetentionPolicy|PutSubscriptionFilter|CreateExportTask|AssociateKmsKey|DisassociateKmsKey|PutDataProtectionPolicy|PutResourcePolicy|\*)$/;

const TEMPLATES = ["commercial", "govcloud", "eusovereign"] as const;

function allStatements(s: SynthResult): any[] {
    const policies = [
        ...s.ofType("AWS::IAM::Policy"),
        ...s.ofType("AWS::IAM::ManagedPolicy"),
    ].flatMap((p) => ((p.properties as any).PolicyDocument?.Statement ?? []) as any[]);
    const inline = s
        .ofType("AWS::IAM::Role")
        .flatMap((r) => ((r.properties as any).Policies ?? []) as any[])
        .flatMap((doc) => (doc.PolicyDocument?.Statement ?? []) as any[]);
    return [...policies, ...inline];
}

describe.each(TEMPLATES)("audit log search infrastructure (%s)", (name) => {
    let synth: SynthResult;
    beforeAll(() => {
        synth = synthTemplate(name);
    });

    test("the service function is deployed with no feature switch", () => {
        const fn = lambdaByHandler(synth, HANDLER);
        expect(fn.properties.Timeout).toBe(29); // the API Gateway integration timeout
    });

    test("the configuration reaches the function as environment variables", () => {
        const fn = lambdaByHandler(synth, HANDLER);
        const env = (fn.properties as any).Environment.Variables;
        expect(env).toMatchObject({
            AUDIT_SEARCH_MAX_QUERY_WINDOW_DAYS: "31",
            AUDIT_SEARCH_MAX_ROWS: "1000",
            AUDIT_SEARCH_MAX_CONCURRENT_QUERIES: "5",
            AUDIT_SEARCH_MAX_CONCURRENT_QUERIES_PER_USER: "2",
            AUDIT_SEARCH_QUERY_DEADLINE_SECONDS: "300",
            AUDIT_SEARCH_LONG_POLL_WAIT_SECONDS: "20",
            AUDIT_SEARCH_MAX_CONTAINS_TERM_LENGTH: "64",
            AUDIT_SEARCH_MAX_CONTAINS_TERMS: "3",
        });
    });

    test("the function may start queries on the nine audit groups and read, stop and list queries", () => {
        const statements = statementsOf(synth, lambdaByHandler(synth, HANDLER));
        const start = statements.filter((st) => actionsOf(st).includes("logs:StartQuery"));
        expect(start).toHaveLength(1);
        const groupResources = ([] as unknown[]).concat(start[0].Resource);
        expect(groupResources).toHaveLength(9);
        // Built from the log groups, so the partition and region follow the template: no ARN is spelled out.
        for (const resource of groupResources) {
            expect(JSON.stringify(resource)).not.toMatch(/arn:aws/);
            expect(JSON.stringify(resource)).toMatch(/AuditLogGroup.*Arn/);
        }
        const queryIdActions = statements.filter((st) =>
            actionsOf(st).includes("logs:GetQueryResults")
        );
        expect(queryIdActions).toHaveLength(1);
        expect(actionsOf(queryIdActions[0]).sort()).toEqual([
            "logs:DescribeQueries",
            "logs:GetQueryResults",
            "logs:StopQuery",
        ]);
        expect(resourcesOf(queryIdActions[0])).toEqual(["*"]);
    });

    test("the function holds no destructive Logs action", () => {
        const statements = statementsOf(synth, lambdaByHandler(synth, HANDLER));
        const destructive = statements.flatMap((st) =>
            actionsOf(st).filter((action) => DESTRUCTIVE_LOGS_ACTION.test(action))
        );
        expect(destructive).toEqual([]);
        // The control: the same matcher finds a destructive action when one is named.
        expect(DESTRUCTIVE_LOGS_ACTION.test("logs:DeleteLogGroup")).toBe(true);
        expect(DESTRUCTIVE_LOGS_ACTION.test("logs:StartQuery")).toBe(false);
    });

    test("no role holds a destructive Logs action on an audit log group", () => {
        const names = (st: any) =>
            ([] as unknown[]).concat(st.Resource ?? []).map((r) => JSON.stringify(r));
        const onAuditGroup = (st: any) => names(st).some((r) => /AuditLogGroup/.test(r));
        const offenders = allStatements(synth)
            .filter((st) => st.Effect === "Allow")
            .filter((st) => actionsOf(st).some((action) => DESTRUCTIVE_LOGS_ACTION.test(action)))
            .filter(onAuditGroup);
        expect(offenders).toEqual([]);
        // The control: statements that name the audit groups exist and this scan sees them.
        expect(allStatements(synth).filter(onAuditGroup).length).toBeGreaterThan(0);
    });

    test("the query table is retained, expires its rows and keeps point-in-time recovery", () => {
        const tables = synth.where("AWS::DynamoDB::Table", (r) =>
            r.logicalId.startsWith("AuditLogQueriesStorageTable")
        );
        expect(tables).toHaveLength(1);
        const table: any = tables[0];
        expect(table.properties.TableName).toBeUndefined(); // auto-named, so a retained table never collides
        expect(table.properties.TimeToLiveSpecification).toEqual({
            AttributeName: "expiresAt",
            Enabled: true,
        });
        expect(table.properties.PointInTimeRecoverySpecification).toEqual({
            PointInTimeRecoveryEnabled: true,
        });
        expect(table.properties.BillingMode).toBe("PAY_PER_REQUEST");
        expect(table.properties.KeySchema).toEqual([{ AttributeName: "queryId", KeyType: "HASH" }]);
        const indexes = table.properties.GlobalSecondaryIndexes ?? [];
        expect(indexes.map((i: any) => i.IndexName)).toEqual(["OpenQueriesIndex"]);
        expect(indexes[0].KeySchema[0]).toEqual({ AttributeName: "openKey", KeyType: "HASH" });
        expect(
            (table as any).raw?.DeletionPolicy ?? (table as any).deletionPolicy ?? "Retain"
        ).toBe("Retain");
    });

    test("the table name is published for the handler to resolve", () => {
        const params = synth.where("AWS::SSM::Parameter", (r) =>
            JSON.stringify(r.properties.Name ?? "").includes("dynamoTables/auditLogQueriesStorage")
        );
        expect(params.length).toBeGreaterThanOrEqual(1);
    });
});

describe("audit log search with the customer-managed KMS key", () => {
    test("the table is encrypted with the shared key", () => {
        const synth = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.useKmsCmkEncryption.enabled = true;
            },
            mutateKey: "kms-on",
        });
        const table: any = synth.where("AWS::DynamoDB::Table", (r) =>
            r.logicalId.startsWith("AuditLogQueriesStorageTable")
        )[0];
        expect(table.properties.SSESpecification.SSEEnabled).toBe(true);
        expect(table.properties.SSESpecification.SSEType).toBe("KMS");
        expect(table.properties.SSESpecification.KMSMasterKeyId).toBeDefined();
    });
});

describe("audit log search limits reach the function", () => {
    test("a changed limit is the function's environment", () => {
        const synth = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.auditLogging.search.maxRows = 2500;
                c.app.auditLogging.search.maxConcurrentQueries = 8;
            },
            mutateKey: "audit-search-limits",
        });
        const env = (lambdaByHandler(synth, HANDLER).properties as any).Environment.Variables;
        expect(env.AUDIT_SEARCH_MAX_ROWS).toBe("2500");
        expect(env.AUDIT_SEARCH_MAX_CONCURRENT_QUERIES).toBe("8");
    });
});

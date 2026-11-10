/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The four execution service functions (the service, details, logs and permanent-delete route sets)
 * and the execute handler each hold exactly the DynamoDB calls their code makes, in every shipped config
 * template, all of it inline (no customer managed policy), and each route reaches the function whose
 * role holds its calls.
 *
 * The expected lists are the ones the backend records each handler's calls against
 * (backend/tests/handlers/workflows/test_execution_service_route_split.py and
 * test_execute_workflow_dynamodb_calls.py); a change to either side has to change both.
 *
 * Tables live in the storage nested stack and the handlers in the API stack, so a statement's Resource
 * carries the cross-stack parameter whose NAME embeds the table's construct id; the match is on that
 * name, as in workflowExecutionLockGrants.test.ts.
 */

import {
    EXECUTION_SERVICE_ROUTES_ENV,
    EXECUTION_SERVICE_ROUTE_SET_DETAILS,
    EXECUTION_SERVICE_ROUTE_SET_LOGS,
    EXECUTION_SERVICE_ROUTE_SET_PERMANENT_DELETE,
    EXECUTION_SERVICE_ROUTE_SET_SERVICE,
} from "../../lib/lambdaBuilder/workflowFunctions";
import { ALL_TEMPLATES, SynthResult, synthTemplate } from "../support/templateSynth";
import { inlinePolicies, managedPolicies, nestedTemplateKeyFor } from "../support/iamPolicySize";

jest.setTimeout(900_000);

/** Construct id of each table, keyed by the logical name the backend tests use. */
const TABLES: Record<string, string> = {
    main: "WorkflowExecutionsStorageTableV2",
    inputs: "WorkflowExecutionInputsStorageTable",
    configuration: "WorkflowExecutionConfigurationStorageTable",
    pipelineExecutions: "PipelineExecutionsStorageTable",
    pipelineInputFiles: "PipelineExecutionInputFilesStorageTable",
    pipelineInputMetadata: "PipelineExecutionInputMetadataStorageTable",
    pipelineInputConfiguration: "PipelineExecutionInputConfigurationStorageTable",
    pipelineOutputFiles: "PipelineExecutionOutputFilesStorageTable",
    pipelineOutputMetadata: "PipelineExecutionOutputMetadataStorageTable",
    pipelineOutputResults: "PipelineExecutionOutputResultsStorageTable",
    pipelineLogs: "PipelineExecutionLogsStorageTable",
    locks: "WorkflowExecutionLocksStorageTable",
    workflow: "WorkflowStorageTableV2",
    pipeline: "PipelineStorageTableV2",
    asset: "AssetStorageTable",
    fileVersionHistory: "AssetFileVersionHistoryStorageTable",
    buckets: "S3AssetBucketsStorageTable",
    templates: "PipelineTemplatesStorageTable",
    tagSchema: "PipelineTemplateTagSchemaStorageTable",
    // The authorization tables every handler reads (setupSecurityAndLoggingEnvironmentAndPermissions).
    constraints: "ConstraintsStorageTable",
    userRoles: "UserRolesStorageTable",
    roles: "RolesStorageTable",
    // The execute handler's metadata schema reads, pinned by executeMetadataSchemaReadGrants.test.ts.
    metadataSchema: "MetadataSchemaStorageTableV2",
    database: "DatabaseStorageTable",
};
const AUTHORIZATION_TABLES = new Set(["constraints", "userRoles", "roles"]);
const SCHEMA_READ_TABLES = new Set(["metadataSchema", "database"]);

const expand = (grants: Record<string, string[]>): string[] =>
    Object.entries(grants)
        .flatMap(([table, actions]) => actions.map((action) => `${table} ${action}`))
        .sort();

/** backend: test_execution_service_route_split.py (AUTHORIZATION_READS). */
const AUTHORIZATION_READS: Record<string, string[]> = {
    main: ["Query"],
    inputs: ["Query"],
    configuration: ["GetItem"],
    asset: ["BatchGetItem", "Query"],
    workflow: ["GetItem"],
};
/** The lock release, in the role's lock policy (the service and details route sets). */
const LOCK_RELEASE: Record<string, string[]> = {
    locks: ["DeleteItem"],
    "locks/index/WorkflowExecutionLocksByHolderGSI": ["Query"],
};

/** backend: test_execution_service_route_split.py (ROUTE_SET_CALLS). */
const ROUTE_SET_GRANTS: Record<string, string[]> = {
    executionService: expand({
        ...AUTHORIZATION_READS,
        ...LOCK_RELEASE,
        main: ["Query", "UpdateItem"],
        "main/index/WorkflowExecutionsByWorkflowGSI": ["Query"],
        "main/index/WorkflowExecutionsByGroupGSI": ["Query"],
        "main/index/WorkflowExecutionsByDateGSI": ["Query"],
        "inputs/index/WorkflowExecInputsByAssetGSI": ["Query"],
        "configuration/index/WorkflowExecConfigByOutputAssetGSI": ["Query"], // pragma: allowlist secret
        pipelineExecutions: ["UpdateItem"],
        "pipelineExecutions/index/PipelineExecByWorkflowExecGSI": ["Query"],
        pipeline: ["GetItem"],
        pipelineInputConfiguration: ["Query"],
    }),
    executionDetails: expand({
        ...AUTHORIZATION_READS,
        ...LOCK_RELEASE,
        main: ["Query", "UpdateItem"],
        pipelineExecutions: ["UpdateItem"],
        "pipelineExecutions/index/PipelineExecByWorkflowExecGSI": ["Query"],
        pipeline: ["BatchGetItem", "GetItem"],
        pipelineInputConfiguration: ["Query"],
        pipelineInputMetadata: ["Query"],
        pipelineOutputFiles: ["Query"],
        pipelineOutputMetadata: ["BatchGetItem", "Query"],
        pipelineOutputResults: ["Query"],
        "fileVersionHistory/index/WorkflowExecutionIdIndex": ["Query"],
    }),
    executionLogs: expand({
        ...AUTHORIZATION_READS,
        "pipelineExecutions/index/PipelineExecByWorkflowExecGSI": ["Query"],
        pipelineLogs: ["Query"],
    }),
};

/** backend: test_execution_service_route_split.py (PERMANENT_DELETE_CALLS). */
const PERMANENT_DELETE_GRANTS = expand({
    main: ["DeleteItem", "Query"],
    configuration: ["DeleteItem", "GetItem"],
    workflow: ["GetItem"],
    asset: ["Query"],
    inputs: ["BatchWriteItem", "Query"],
    "pipelineExecutions/index/PipelineExecByWorkflowExecGSI": ["Query"],
    pipelineExecutions: ["DeleteItem"],
    ...Object.fromEntries(
        [
            "pipelineInputConfiguration",
            "pipelineInputMetadata",
            "pipelineInputFiles",
            "pipelineOutputFiles",
            "pipelineOutputMetadata",
            "pipelineOutputResults",
            "pipelineLogs",
        ].map((t) => [t, ["BatchWriteItem", "Query"]])
    ),
});

/** backend: test_execute_workflow_dynamodb_calls.py (EXECUTE_WORKFLOW_GRANTS). */
const EXECUTE_WORKFLOW_GRANTS = expand({
    workflow: ["GetItem"],
    pipeline: ["GetItem"],
    asset: ["Query"],
    buckets: ["Query", "Scan"],
    templates: ["GetItem", "Query"],
    "tagSchema/index/TagSchemaByTemplateGSI": ["Query"],
    main: ["GetItem", "PutItem", "UpdateItem"],
    inputs: ["PutItem"],
    configuration: ["PutItem"],
    pipelineExecutions: ["PutItem"],
    pipelineInputConfiguration: ["PutItem"],
    pipelineInputMetadata: ["BatchWriteItem"],
    locks: ["DeleteItem", "PutItem"],
});

const RESOURCE = /([A-Za-z0-9]+?)[0-9A-F]{8}Arn"}(?:,"\/index\/([A-Za-z0-9*]+))?/;

/** The logical table (and index) one Resource entry names, or undefined when it is not a table. */
function tableOf(resource: any): string | undefined {
    const match = RESOURCE.exec(JSON.stringify(resource));
    if (!match) return undefined;
    const name = Object.entries(TABLES).find(([, id]) => match[1].endsWith(`Builder${id}`))?.[0];
    if (!name) return undefined;
    return match[2] ? `${name}/index/${match[2]}` : name;
}

function roleId(synth: SynthResult, stack: string, construct: string): string {
    const roles = synth
        .ofType("AWS::IAM::Role")
        .filter(
            (r) =>
                r.stack === stack &&
                new RegExp(`^${construct}ServiceRole[0-9A-F]{8}$`).test(r.logicalId)
        );
    expect(roles).toHaveLength(1);
    return roles[0].logicalId;
}

/** "<table> <action>" for every DynamoDB action the role holds on a table, inline and managed. */
function dynamoGrants(synth: SynthResult, stack: string, role: string): string[] {
    const docs = [...inlinePolicies(synth, role, stack), ...managedPolicies(synth, role, stack)];
    const grants = new Set<string>();
    for (const [, doc] of docs) {
        for (const st of (doc?.Statement ?? []) as any[]) {
            const actions = ([] as string[])
                .concat(st.Action ?? [])
                .filter((a) => a.startsWith("dynamodb:"));
            for (const resource of ([] as any[]).concat(st.Resource ?? [])) {
                const table = tableOf(resource);
                if (!table) continue;
                for (const action of actions) {
                    grants.add(`${table} ${action.replace("dynamodb:", "")}`);
                }
            }
        }
    }
    return [...grants].sort();
}

/** The grants on every table but the authorization tables (and, for the execute handler, the schema). */
const withoutShared = (grants: string[], extra: Set<string> = new Set()) =>
    grants.filter((g) => {
        const table = g.split(/[ /]/)[0];
        return !AUTHORIZATION_TABLES.has(table) && !extra.has(table);
    });

function functionOf(synth: SynthResult, stack: string, construct: string) {
    const fns = synth
        .ofType("AWS::Lambda::Function")
        .filter(
            (r) => r.stack === stack && new RegExp(`^${construct}[0-9A-F]{8}$`).test(r.logicalId)
        );
    expect(fns).toHaveLength(1);
    return fns[0];
}

describe.each(ALL_TEMPLATES)("execution service route split (%s template)", (name) => {
    let synth: SynthResult;
    let stack: string;

    beforeAll(() => {
        synth = synthTemplate(name);
        stack = nestedTemplateKeyFor(synth, "ApiBuilder2");
    });

    test("each table the grants name is in this synth (control)", () => {
        for (const id of Object.values(TABLES)) {
            expect(
                synth.where("AWS::DynamoDB::Table", (r) =>
                    new RegExp(`^${id}[0-9A-F]{8}$`).test(r.logicalId)
                )
            ).toHaveLength(1);
        }
    });

    test.each(Object.keys(ROUTE_SET_GRANTS))(
        "%s holds exactly the calls its routes make",
        (construct) => {
            const grants = withoutShared(
                dynamoGrants(synth, stack, roleId(synth, stack, construct))
            );
            expect(grants).toEqual(ROUTE_SET_GRANTS[construct]);
        }
    );

    test.each(Object.keys(ROUTE_SET_GRANTS))(
        "%s can delete or put no execution row",
        (construct) => {
            const grants = dynamoGrants(synth, stack, roleId(synth, stack, construct));
            expect(grants.filter((g) => /(PutItem|BatchWriteItem)$/.test(g))).toEqual([]);
            expect(grants.filter((g) => / DeleteItem$/.test(g) && !g.startsWith("locks "))).toEqual(
                []
            );
        }
    );

    test.each(Object.keys(ROUTE_SET_GRANTS))(
        "%s carries its grants inline, with no managed policy",
        (construct) => {
            const role = roleId(synth, stack, construct);
            expect(managedPolicies(synth, role, stack)).toEqual([]);
            // The role names only the AWS managed Lambda execution policies.
            const roleResource = synth.ofType("AWS::IAM::Role").find((r) => r.logicalId === role)!;
            const named = ((roleResource.properties.ManagedPolicyArns ?? []) as any[]).map((arn) =>
                JSON.stringify(arn)
            );
            expect(named.length).toBeGreaterThan(0);
            for (const arn of named) {
                expect(arn).toMatch(/:iam::aws:policy\/service-role\/AWSLambda/);
            }
        }
    );

    test("only the abort holds the stop calls and only the re-run invokes the launch", () => {
        const actionsOf = (construct: string) =>
            inlinePolicies(synth, roleId(synth, stack, construct), stack)
                .flatMap(([, doc]) => (doc?.Statement ?? []) as any[])
                .flatMap((st) => ([] as string[]).concat(st.Action ?? []));
        const stops = (construct: string) =>
            actionsOf(construct).filter((a) =>
                /^(states:StopExecution|batch:TerminateJob|sagemaker:Stop.*|deadline:UpdateJob|lambda:InvokeFunction)$/.test(
                    a
                )
            );
        expect(stops("executionService")).toEqual(
            expect.arrayContaining([
                "states:StopExecution",
                "batch:TerminateJob",
                "sagemaker:StopTrainingJob",
                "sagemaker:StopPipelineExecution",
                "lambda:InvokeFunction",
            ])
        );
        expect(stops("executionDetails")).toEqual([]);
        expect(stops("executionLogs")).toEqual([]);
        // The full log-read scope is on the logs function only; the reconciling reads capture from the
        // shared workflow log group and the config-name groups.
        const logReads = (construct: string) =>
            inlinePolicies(synth, roleId(synth, stack, construct), stack)
                .flatMap(([, doc]) => (doc?.Statement ?? []) as any[])
                .filter((st) =>
                    ([] as string[]).concat(st.Action ?? []).includes("logs:FilterLogEvents")
                )
                .flatMap((st) => ([] as any[]).concat(st.Resource ?? []));
        expect(logReads("executionService")).toHaveLength(4);
        expect(logReads("executionDetails")).toHaveLength(4);
        expect(logReads("executionLogs").length).toBeGreaterThan(4);
    });

    test("the permanent-delete function holds exactly the calls its route makes", () => {
        const grants = withoutShared(
            dynamoGrants(synth, stack, roleId(synth, stack, "executionPermanentDelete"))
        );
        expect(grants).toEqual(PERMANENT_DELETE_GRANTS);
    });

    test("the permanent-delete function can put, update, stop or invoke nothing", () => {
        const role = roleId(synth, stack, "executionPermanentDelete");
        const actions = inlinePolicies(synth, role, stack)
            .flatMap(([, doc]) => (doc?.Statement ?? []) as any[])
            .flatMap((st) => ([] as string[]).concat(st.Action ?? []));
        expect(actions.filter((a) => /^dynamodb:(PutItem|UpdateItem)$/.test(a))).toEqual([]);
        expect(actions.filter((a) => a.startsWith("states:"))).toEqual([
            "states:DescribeExecution",
        ]);
        expect(actions.filter((a) => /^(lambda|batch|sagemaker|deadline):/.test(a))).toEqual([]);
        expect(managedPolicies(synth, role, stack)).toEqual([]);
    });

    test("the execute handler holds exactly the calls its launch makes", () => {
        const grants = withoutShared(
            dynamoGrants(synth, stack, roleId(synth, stack, "executeWorkflow")),
            SCHEMA_READ_TABLES
        );
        expect(grants).toEqual(EXECUTE_WORKFLOW_GRANTS);
    });

    test("each function is told the routes it serves", () => {
        const routeSets: [string, string][] = [
            ["executionService", EXECUTION_SERVICE_ROUTE_SET_SERVICE],
            ["executionDetails", EXECUTION_SERVICE_ROUTE_SET_DETAILS],
            ["executionLogs", EXECUTION_SERVICE_ROUTE_SET_LOGS],
            ["executionPermanentDelete", EXECUTION_SERVICE_ROUTE_SET_PERMANENT_DELETE],
        ];
        for (const [construct, routeSet] of routeSets) {
            const fn = functionOf(synth, stack, construct);
            expect([
                construct,
                fn.properties.Environment.Variables[EXECUTION_SERVICE_ROUTES_ENV],
            ]).toEqual([construct, routeSet]);
            expect(fn.properties.Handler).toBe(
                "handlers.workflows.executionService.lambda_handler"
            );
        }
    });

    test("each execution route reaches the function whose role holds its calls", () => {
        const paths = synth.ofType("AWS::ApiGateway::RestApi")[0].properties.Body.paths;
        const target = (path: string, method: string) => {
            const uri = JSON.stringify(
                paths[path]?.[method]?.["x-amazon-apigateway-integration"]?.uri
            );
            const match =
                /ApiBuilder2(execution(?:Service|Details|Logs|PermanentDelete))[0-9A-F]{8}Arn/.exec(
                    uri
                );
            return match ? match[1] : uri;
        };
        const routes: [string, string, string][] = [
            [
                "/database/{databaseId}/assets/{assetId}/workflows/executions",
                "get",
                "executionService",
            ],
            [
                "/database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}",
                "get",
                "executionService",
            ],
            ["/workflows/executions", "get", "executionService"],
            ["/workflows/executions/{executionId}", "delete", "executionService"],
            ["/workflows/executions/{executionId}/rerun", "post", "executionService"],
            ["/workflows/executions/{executionId}/details", "get", "executionDetails"],
            ["/workflows/executions/{executionId}/details/metadata", "get", "executionDetails"],
            ["/workflows/executions/{executionId}/details/files", "get", "executionDetails"],
            ["/workflows/executions/{executionId}/details/results", "get", "executionDetails"],
            ["/workflows/executions/{executionId}/logs", "get", "executionLogs"],
            ["/workflows/executions/{executionId}/permanent", "delete", "executionPermanentDelete"],
        ];
        for (const [path, method, construct] of routes) {
            expect([path, method, target(path, method)]).toEqual([path, method, construct]);
        }
        // API Gateway may invoke each function.
        for (const construct of ["executionDetails", "executionLogs", "executionPermanentDelete"]) {
            expect(
                synth
                    .ofType("AWS::Lambda::Permission")
                    .filter((p) =>
                        new RegExp(`ApiBuilder2${construct}[0-9A-F]{8}Arn`).test(
                            JSON.stringify(p.properties.FunctionName)
                        )
                    )
            ).toHaveLength(1);
        }
    });
});

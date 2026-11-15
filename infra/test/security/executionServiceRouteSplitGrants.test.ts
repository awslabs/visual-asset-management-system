/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The execution service, the permanent-delete function and the execute handler each hold exactly the
 * DynamoDB calls their code makes, in every shipped config template, and the routes reach the function
 * whose role holds them.
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
    EXECUTION_SERVICE_ROUTE_SET_PERMANENT_DELETE,
    EXECUTION_SERVICE_ROUTE_SET_SERVICE,
    EXECUTION_SERVICE_TABLE_READ_POLICY_ID,
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

/** backend: test_execution_service_route_split.py (EXECUTION_SERVICE_WRITES and the read paths). */
const EXECUTION_SERVICE_READ_TABLES = [
    "asset",
    "main",
    "inputs",
    "configuration",
    "pipelineExecutions",
    "pipelineInputMetadata",
    "pipelineInputConfiguration",
    "pipelineOutputFiles",
    "pipelineOutputMetadata",
    "pipelineOutputResults",
    "pipelineLogs",
    "workflow",
    "pipeline",
    "fileVersionHistory",
];
const EXECUTION_SERVICE_READ_INDEXES = [
    "main/index/WorkflowExecutionsByWorkflowGSI",
    "main/index/WorkflowExecutionsByGroupGSI",
    "main/index/WorkflowExecutionsByDateGSI",
    "inputs/index/WorkflowExecInputsByAssetGSI",
    "configuration/index/WorkflowExecConfigByOutputAssetGSI", // pragma: allowlist secret
    "pipelineExecutions/index/PipelineExecByWorkflowExecGSI",
    "fileVersionHistory/index/WorkflowExecutionIdIndex",
];
const EXECUTION_SERVICE_GRANTS = expand({
    ...Object.fromEntries(
        [...EXECUTION_SERVICE_READ_TABLES, ...EXECUTION_SERVICE_READ_INDEXES].map((t) => [
            t,
            ["BatchGetItem", "GetItem", "Query"],
        ])
    ),
    main: ["BatchGetItem", "GetItem", "Query", "UpdateItem"],
    pipelineExecutions: ["BatchGetItem", "GetItem", "Query", "UpdateItem"],
    // The lock release, in the role's lock policy.
    locks: ["DeleteItem"],
    "locks/index/WorkflowExecutionLocksByHolderGSI": ["Query"],
});

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

    test("the execution service holds exactly the calls its routes make", () => {
        const grants = withoutShared(
            dynamoGrants(synth, stack, roleId(synth, stack, "executionService"))
        );
        expect(grants).toEqual(EXECUTION_SERVICE_GRANTS);
    });

    test("the execution service can delete or put no execution row", () => {
        const grants = dynamoGrants(synth, stack, roleId(synth, stack, "executionService"));
        expect(grants.filter((g) => /(PutItem|BatchWriteItem)$/.test(g))).toEqual([]);
        expect(grants.filter((g) => / DeleteItem$/.test(g))).toEqual(["locks DeleteItem"]);
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

    test("the execution service's table reads are in its managed policy, which its function waits for", () => {
        const role = roleId(synth, stack, "executionService");
        const managed = managedPolicies(synth, role, stack);
        expect(managed.map(([id]) => id)).toEqual([
            expect.stringMatching(
                new RegExp(`^executionService${EXECUTION_SERVICE_TABLE_READ_POLICY_ID}`)
            ),
        ]);
        const fn = functionOf(synth, stack, "executionService");
        expect(fn.raw.DependsOn).toEqual(expect.arrayContaining([managed[0][0]]));
        // The role names the policy, so the role (and the default policy that names the role) is
        // updated only once the policy exists.
        const roleResource = synth.ofType("AWS::IAM::Role").find((r) => r.logicalId === role)!;
        expect(JSON.stringify(roleResource.properties.ManagedPolicyArns)).toContain(managed[0][0]);
    });

    test("each function is told the routes it serves", () => {
        expect(
            functionOf(synth, stack, "executionService").properties.Environment.Variables[
                EXECUTION_SERVICE_ROUTES_ENV
            ]
        ).toBe(EXECUTION_SERVICE_ROUTE_SET_SERVICE);
        const permanentDelete = functionOf(synth, stack, "executionPermanentDelete");
        expect(permanentDelete.properties.Environment.Variables[EXECUTION_SERVICE_ROUTES_ENV]).toBe(
            EXECUTION_SERVICE_ROUTE_SET_PERMANENT_DELETE
        );
        expect(permanentDelete.properties.Handler).toBe(
            "handlers.workflows.executionService.lambda_handler"
        );
    });

    test("permanent delete reaches the permanent-delete function and every other execution route the service", () => {
        const paths = synth.ofType("AWS::ApiGateway::RestApi")[0].properties.Body.paths;
        const target = (path: string, method: string) => {
            const uri = JSON.stringify(
                paths[path]?.[method]?.["x-amazon-apigateway-integration"]?.uri
            );
            return /ApiBuilder2executionPermanentDelete[0-9A-F]{8}Arn/.test(uri)
                ? "executionPermanentDelete"
                : /ApiBuilder2executionService[0-9A-F]{8}Arn/.test(uri)
                ? "executionService"
                : uri;
        };
        const serviceRoutes: [string, string][] = [
            ["/database/{databaseId}/assets/{assetId}/workflows/executions", "get"],
            ["/database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}", "get"],
            ["/workflows/executions", "get"],
            ["/workflows/executions/{executionId}", "delete"],
            ["/workflows/executions/{executionId}/details", "get"],
            ["/workflows/executions/{executionId}/details/metadata", "get"],
            ["/workflows/executions/{executionId}/details/files", "get"],
            ["/workflows/executions/{executionId}/details/results", "get"],
            ["/workflows/executions/{executionId}/logs", "get"],
            ["/workflows/executions/{executionId}/rerun", "post"],
        ];
        for (const [path, method] of serviceRoutes) {
            expect([path, method, target(path, method)]).toEqual([
                path,
                method,
                "executionService",
            ]);
        }
        expect(target("/workflows/executions/{executionId}/permanent", "delete")).toBe(
            "executionPermanentDelete"
        );
        // API Gateway may invoke the new function.
        expect(
            synth
                .ofType("AWS::Lambda::Permission")
                .filter((p) =>
                    /ApiBuilder2executionPermanentDelete[0-9A-F]{8}Arn/.test(
                        JSON.stringify(p.properties.FunctionName)
                    )
                )
        ).toHaveLength(1);
    });
});

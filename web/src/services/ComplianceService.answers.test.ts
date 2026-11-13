/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every compliance service call decides success and failure from what the API answers (D188,
 * web/CLAUDE.md 4.1): a non-2xx status, which apiClient throws with the API's message, or a 2xx
 * answer that says `success: false` or is not a JSON object. The message text decides nothing:
 * a success message repeats names the caller chose, so a schema named `error-budget` or an asset
 * named `Error-Log-Scan` must still read as a success.
 */

jest.mock("./apiClient", () => ({
    ...jest.requireActual("./apiClient"),
    apiClient: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
}));

import { apiClient, ApiError } from "./apiClient";
import {
    approveCascade,
    bindSchemaToAsset,
    bindSchemaToDatabase,
    createComplianceSchema,
    evaluateAssetCompliance,
    evaluateDatabaseCompliance,
    fetchAssetAuditHistory,
    fetchAuditLog,
    fetchCascade,
    fetchCascades,
    fetchComplianceSchema,
    fetchComplianceSchemaListing,
    fetchComplianceSchemas,
    fetchComplianceState,
    fetchDatabaseComplianceOverview,
    fetchEvaluationHistory,
    fetchQuarantinedAssets,
    getDatabaseBindings,
    grantException,
    rejectCascade,
    releaseFromQuarantine,
    revokeException,
    sweepSchema,
    unbindSchemaFromAsset,
    unbindSchemaFromDatabase,
    updateComplianceSchema,
} from "./ComplianceService";

type Method = "get" | "post" | "put" | "del";

interface Call {
    name: string;
    method: Method;
    call: () => Promise<any[]>;
    /** A 2xx answer of the route, with a message that holds "error" and "Error". */
    answer: Record<string, any>;
}

const NAMED = "error-budget";
const MESSAGE = `Error-Log-Scan: schema '${NAMED}' saved`;
const schema = { schemaName: NAMED, schemaBody: { schemaFormat: "vams-rules-v1", rules: {} } };

const CALLS: Call[] = [
    {
        name: "fetchComplianceSchemas",
        method: "get",
        call: () => fetchComplianceSchemas("db1"),
        answer: { message: MESSAGE, schemas: [schema] },
    },
    {
        name: "fetchComplianceSchemaListing",
        method: "get",
        call: () => fetchComplianceSchemaListing(),
        answer: { message: MESSAGE, schemas: [schema] },
    },
    {
        name: "fetchComplianceSchema",
        method: "get",
        call: () => fetchComplianceSchema(NAMED),
        answer: { message: MESSAGE, ...schema },
    },
    {
        name: "createComplianceSchema",
        method: "post",
        call: () => createComplianceSchema(schema),
        answer: { message: `Schema '${NAMED}' registered as v1`, schemaName: NAMED, version: 1 },
    },
    {
        name: "updateComplianceSchema",
        method: "put",
        call: () => updateComplianceSchema(NAMED, schema),
        answer: { message: `Schema '${NAMED}' registered as v2`, schemaName: NAMED, version: 2 },
    },
    {
        name: "evaluateAssetCompliance",
        method: "post",
        call: () => evaluateAssetCompliance("db1", "Error-Log-Scan"),
        answer: { message: MESSAGE, evaluationId: "ev1", verdict: "compliant" },
    },
    {
        name: "evaluateDatabaseCompliance",
        method: "post",
        call: () => evaluateDatabaseCompliance("errors-db"),
        answer: { message: MESSAGE, databaseId: "errors-db", assetsTriggered: [] },
    },
    {
        name: "sweepSchema",
        method: "post",
        call: () => sweepSchema(NAMED),
        answer: { message: MESSAGE, assetsTriggered: [] },
    },
    {
        name: "fetchEvaluationHistory",
        method: "get",
        call: () => fetchEvaluationHistory("db1", "Error-Log-Scan"),
        answer: { message: MESSAGE, evaluations: [] },
    },
    {
        name: "fetchComplianceState",
        method: "get",
        call: () => fetchComplianceState("db1", "Error-Log-Scan"),
        answer: { message: MESSAGE, complianceState: "compliant", schemaName: NAMED },
    },
    {
        name: "fetchDatabaseComplianceOverview",
        method: "get",
        call: () => fetchDatabaseComplianceOverview("errors-db"),
        answer: { message: MESSAGE, databaseId: "errors-db", totalAssets: 0, assets: [] },
    },
    {
        name: "fetchQuarantinedAssets",
        method: "get",
        call: () => fetchQuarantinedAssets(),
        answer: { message: MESSAGE, quarantinedAssets: [] },
    },
    {
        name: "releaseFromQuarantine",
        method: "post",
        call: () => releaseFromQuarantine("db1", "Error-Log-Scan", "fixed"),
        answer: { message: "Asset db1:Error-Log-Scan released from quarantine" },
    },
    {
        name: "grantException",
        method: "post",
        call: () => grantException("db1", "Error-Log-Scan", "accepted"),
        answer: { message: "Exception granted for db1:Error-Log-Scan" },
    },
    {
        name: "revokeException",
        method: "del",
        call: () => revokeException("db1", "Error-Log-Scan"),
        answer: { message: MESSAGE, complianceState: "quarantined" },
    },
    {
        name: "fetchCascades",
        method: "get",
        call: () => fetchCascades(),
        answer: { message: MESSAGE, cascades: [] },
    },
    {
        name: "fetchCascade",
        method: "get",
        call: () => fetchCascade("c1"),
        answer: { message: MESSAGE, cascadeId: "c1", state: "completed" },
    },
    {
        name: "approveCascade",
        method: "post",
        call: () => approveCascade("c1"),
        answer: { message: MESSAGE, cascadeId: "c1", state: "executing" },
    },
    {
        name: "rejectCascade",
        method: "post",
        call: () => rejectCascade("c1"),
        answer: { message: MESSAGE, cascadeId: "c1" },
    },
    {
        name: "bindSchemaToDatabase",
        method: "put",
        call: () => bindSchemaToDatabase("db1", NAMED),
        answer: {
            message: `Schema '${NAMED}' bound to database 'db1'; the binding is being applied to 3 assets`,
            changed: true,
            applyStatus: "applying",
            applyId: "apply-1",
            assetCount: 3,
        },
    },
    {
        name: "unbindSchemaFromDatabase",
        method: "del",
        call: () => unbindSchemaFromDatabase("errors-db"),
        answer: {
            message:
                "Schema binding removed from database 'errors-db'; the removal is being applied to 3 assets",
            changed: true,
        },
    },
    {
        name: "bindSchemaToAsset",
        method: "put",
        call: () => bindSchemaToAsset("db1", "Error-Log-Scan", NAMED),
        answer: { message: `Schema '${NAMED}' bound to asset` },
    },
    {
        name: "unbindSchemaFromAsset",
        method: "del",
        call: () => unbindSchemaFromAsset("db1", "Error-Log-Scan"),
        answer: { message: MESSAGE },
    },
    {
        name: "getDatabaseBindings",
        method: "get",
        call: () => getDatabaseBindings("errors-db"),
        answer: { message: MESSAGE, databaseId: "errors-db", databaseSchema: NAMED },
    },
    {
        name: "fetchAssetAuditHistory",
        method: "get",
        call: () => fetchAssetAuditHistory("db1", "Error-Log-Scan"),
        answer: { message: MESSAGE, entries: [] },
    },
    {
        name: "fetchAuditLog",
        method: "get",
        call: () => fetchAuditLog({ text: "error" }),
        answer: { message: MESSAGE, entries: [] },
    },
];

const mocked = (method: Method) => apiClient[method] as jest.Mock;

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("a compliance call reads the API's answer, not its message text (D188)", () => {
    it("covers every compliance service call", () => {
        // Every async export of the service calls the API; a new one gets a row in CALLS.
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const source: string = require("fs").readFileSync(
            require.resolve("./ComplianceService.ts"),
            "utf8"
        );
        const exported = Array.from(source.matchAll(/^export const (\w+) = async/gm)).map(
            (match) => match[1]
        );
        expect(CALLS.map((entry) => entry.name).sort()).toEqual(exported.sort());
        // No call decides from the message text.
        expect(source).not.toMatch(/message\.(includes|indexOf)\(/);
    });

    it.each(CALLS.map((entry) => [entry.name, entry]))(
        "%s: a 2xx answer whose message holds 'error' and 'Error' reads as a success",
        async (_name, entry) => {
            mocked(entry.method).mockResolvedValue(entry.answer);
            const result = await entry.call();
            expect(result[0]).toBe(true);
        }
    );

    it.each(CALLS.map((entry) => [entry.name, entry]))(
        "%s: a 2xx answer that says success: false reads as a failure with its message",
        async (_name, entry) => {
            mocked(entry.method).mockResolvedValue({ success: false, message: "Not allowed" });
            expect(await entry.call()).toEqual([false, "Not allowed"]);
        }
    );

    it.each(CALLS.map((entry) => [entry.name, entry]))(
        "%s: an answer that is not a JSON object reads as a failure with the call's own text",
        async (_name, entry) => {
            mocked(entry.method).mockResolvedValue(null);
            const result = await entry.call();
            expect(result[0]).toBe(false);
            expect(result[1]).toMatch(/^Failed to /);
        }
    );

    it.each(CALLS.map((entry) => [entry.name, entry]))(
        "%s: a refused request (non-2xx) reads as a failure with the API's message",
        async (_name, entry) => {
            mocked(entry.method).mockRejectedValue(new ApiError("Not Authorized", 403));
            expect(await entry.call()).toEqual([false, "Not Authorized"]);
        }
    );
});

describe("binding a schema named error-budget", () => {
    it("reports the 202 answer as a success, with the apply the page follows", async () => {
        const answer = CALLS.find((entry) => entry.name === "bindSchemaToDatabase")!.answer;
        mocked("put").mockResolvedValue(answer);
        const result = await bindSchemaToDatabase("db1", NAMED);
        expect(result).toEqual([true, answer.message, answer]);
    });

    it("reports a change refused while another is applied (409) as a failure", async () => {
        const busy =
            "The compliance schema binding of this database is still being applied; try again when it finishes";
        mocked("put").mockRejectedValue(new ApiError(busy, 409));
        expect(await bindSchemaToDatabase("db1", NAMED)).toEqual([false, busy]);
    });

    it("reports a schema the database may not bind (400) as a failure", async () => {
        mocked("put").mockRejectedValue(
            new ApiError("Schema body must be a vams-rules-v1 document", 400)
        );
        expect(await bindSchemaToDatabase("db1", NAMED)).toEqual([
            false,
            "Schema body must be a vams-rules-v1 document",
        ]);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    findRunningMirisUpload,
    getMirisStreamStatus,
    getMirisUploadRun,
    isExecutionInProgress,
    MIRIS_REQUEST_BLOCKED,
    mintMirisViewerKey,
    startMirisUpload,
} from "./mirisAPIService";
import { ApiError, apiClient } from "../apiClient";

jest.mock("../apiClient", () => {
    const actual = jest.requireActual("../apiClient");
    return {
        ApiError: actual.ApiError,
        isRequestBlocked: actual.isRequestBlocked,
        apiClient: { get: jest.fn(), post: jest.fn() },
    };
});

const mockGet = apiClient.get as jest.Mock;
const mockPost = apiClient.post as jest.Mock;

const ARGS = { databaseId: "db-1", assetId: "asset-1", filePath: "/folder/scene.usd" };

const STATUS = {
    filePath: "/folder/scene.usd",
    mirisAssetUuid: "8d65af7c-5412-404b-a494-fbd2cdb62442",
    sourceVersionId: "v-abc",
    uploadedAt: "2026-10-14T12:00:00Z",
    displayName: "Scene",
    state: "streamable",
};

const wafBlocked = () =>
    new ApiError("Request blocked (request ID abc-123)", 403, {
        message: "Request blocked",
        blockedBy: "waf",
        requestId: "abc-123",
    });

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => {
    jest.restoreAllMocks();
});

const failure = (message: string, status?: number, state?: string) => [
    false,
    message,
    status,
    state,
];

describe("getMirisStreamStatus", () => {
    it.each([
        ["streamable", STATUS],
        ["notStreamed", { ...STATUS, mirisAssetUuid: null, state: "notStreamed" }],
    ])("returns a %s status from the add-on status route", async (_state, response) => {
        mockGet.mockResolvedValue(response);
        expect(await getMirisStreamStatus(ARGS)).toEqual([true, response]);
        expect(mockGet).toHaveBeenCalledWith("addon/miris/asset-status", {
            queryStringParameters: ARGS,
        });
    });

    it.each([
        ["without a state", { message: "Validation Error" }, "Validation Error"],
        ["that is empty", undefined, "Unknown response shape"],
    ])("returns an error for a response %s", async (_label, response, message) => {
        mockGet.mockResolvedValue(response);
        expect(await getMirisStreamStatus(ARGS)).toEqual(failure(message));
    });
});

const KEY = {
    filePath: "/folder/scene.usd",
    mirisAssetUuid: "8d65af7c-5412-404b-a494-fbd2cdb62442",
    viewerKey: "minted-viewer-key",
    expiresAt: "2026-10-15T19:04:28Z",
};

describe("mintMirisViewerKey", () => {
    it("returns the minted key from the add-on viewer key route", async () => {
        mockPost.mockResolvedValue(KEY);
        expect(await mintMirisViewerKey(ARGS)).toEqual([true, KEY]);
        expect(mockPost).toHaveBeenCalledWith("addon/miris/viewer-key", { body: ARGS });
    });

    it.each([
        ["the state the error body reports", "notBound", "notBound"],
        ["no state for a non-string one", 7, undefined],
    ])("returns %s", async (_label, state, expected) => {
        mockPost.mockRejectedValue(new ApiError("not linked", 403, { state }));
        expect(await mintMirisViewerKey(ARGS)).toEqual(failure("not linked", 403, expected));
    });

    it.each([
        ["an empty viewer key", { ...KEY, viewerKey: "" }],
        ["no asset UUID", { ...KEY, mirisAssetUuid: undefined }],
        ["no body", undefined],
    ])("treats a response with %s as a failure", async (_label, response) => {
        mockPost.mockResolvedValue(response);
        expect(await mintMirisViewerKey(ARGS)).toEqual(failure("Unknown response shape"));
    });

    it("never logs the key material", async () => {
        mockPost.mockResolvedValue(KEY);
        await mintMirisViewerKey(ARGS);
        expect(JSON.stringify((console.log as jest.Mock).mock.calls)).not.toContain(KEY.viewerKey);
    });
});

describe("startMirisUpload", () => {
    it("executes the GLOBAL miris-upload workflow on the one file as a manual run", async () => {
        const response = { executionId: "exec-1", warnings: ["note"] };
        mockPost.mockResolvedValue({ message: response });
        expect(await startMirisUpload(ARGS)).toEqual([true, response]);
        expect(mockPost).toHaveBeenCalledWith("workflows/GLOBAL/miris-upload/execute", {
            body: {
                inputFiles: [
                    {
                        databaseId: "db-1",
                        assetId: "asset-1",
                        relativeFileKey: "/folder/scene.usd",
                    },
                ],
                triggerType: "manual",
            },
        });
    });
});

describe("request failures", () => {
    const CALLS = [
        ["getMirisStreamStatus", mockGet, () => getMirisStreamStatus(ARGS)],
        ["mintMirisViewerKey", mockPost, () => mintMirisViewerKey(ARGS)],
        ["startMirisUpload", mockPost, () => startMirisUpload(ARGS)],
    ] as const;

    it.each(CALLS)("%s returns the message and HTTP status", async (_name, mock, call) => {
        for (const status of [403, 503]) {
            mock.mockRejectedValueOnce(new ApiError(`failed ${status}`, status));
            expect(await call()).toEqual(failure(`failed ${status}`, status));
        }
    });

    it.each(CALLS)(
        "%s reports a request the AWS WAF web ACL blocked apart from a permission denial",
        async (_name, mock, call) => {
            mock.mockRejectedValue(wafBlocked());
            expect(await call()).toEqual(
                failure("Request blocked (request ID abc-123)", undefined, MIRIS_REQUEST_BLOCKED)
            );
        }
    );

    it.each(CALLS)(
        "%s returns an undefined status when the request did not reach the API",
        async (_name, mock, call) => {
            mock.mockRejectedValue(new TypeError("Failed to fetch"));
            expect(await call()).toEqual(failure("Failed to fetch"));
        }
    );

    it("[control] a 403 without the WAF marker is still a permission denial", async () => {
        mockPost.mockRejectedValue(new ApiError("Forbidden", 403, { blockedBy: "other" }));
        expect(await mintMirisViewerKey(ARGS)).toEqual(failure("Forbidden", 403));
    });
});

describe("findRunningMirisUpload", () => {
    const ASSET = { databaseId: "db-1", assetId: "asset-1" };
    const PATH = "database/db-1/assets/asset-1/workflows/executions";
    const execution = (overrides: Record<string, unknown> = {}) => ({
        workflowExecutionId: "exec-1",
        workflowId: "miris-upload",
        workflowDatabaseId: "GLOBAL",
        executionStatus: "RUNNING",
        executionStartDate: "2026-10-29T10:00:00Z",
        executionStopDate: "",
        ...overrides,
    });
    const page = (items: unknown[], nextToken?: string) => ({
        message: {
            Items: items,
            filterStartDate: "2026-07-31T10:00:00Z",
            ...(nextToken ? { NextToken: nextToken } : {}),
        },
    });

    it("lists the asset's executions filtered to the GLOBAL miris-upload workflow", async () => {
        mockGet.mockResolvedValue(page([]));
        await findRunningMirisUpload(ASSET);
        expect(mockGet).toHaveBeenCalledTimes(1);
        expect(mockGet).toHaveBeenCalledWith(PATH, {
            queryStringParameters: { workflowDatabaseId: "GLOBAL", workflowId: "miris-upload" },
        });
    });

    it.each(["RUNNING", "NEW"])("returns a %s run", async (executionStatus) => {
        const run = execution({ executionStatus });
        mockGet.mockResolvedValue(page([run]));
        expect(await findRunningMirisUpload(ASSET)).toEqual([true, run]);
    });

    it("returns null when every miris-upload run has finished", async () => {
        mockGet.mockResolvedValue(
            page([
                execution({ workflowExecutionId: "a", executionStatus: "SUCCEEDED" }),
                execution({ workflowExecutionId: "b", executionStatus: "FAILED" }),
                execution({ workflowExecutionId: "c", executionStatus: "ABORTED" }),
                execution({ workflowExecutionId: "d", executionStatus: "TIMED_OUT" }),
                execution({
                    workflowExecutionId: "e",
                    executionStopDate: "2026-10-29T11:00:00Z",
                }),
                execution({ workflowExecutionId: "f", workflowId: "other" }),
            ])
        );
        expect(await findRunningMirisUpload(ASSET)).toEqual([true, null]);
    });

    it.each(["RUNNING", "FAILED", "SKIPPED"])(
        "returns the named run while %s, finished or not",
        async (executionStatus) => {
            const named = execution({ executionStatus, executionError: "" });
            mockGet.mockResolvedValue(
                page([
                    execution({ workflowExecutionId: "other", executionStatus: "RUNNING" }),
                    named,
                ])
            );
            expect(await findRunningMirisUpload({ ...ASSET, executionId: "exec-1" })).toEqual([
                true,
                named,
            ]);
        }
    );

    it("returns null when the named run is not listed", async () => {
        mockGet.mockResolvedValue(page([execution({ workflowExecutionId: "other" })]));
        expect(await findRunningMirisUpload({ ...ASSET, executionId: "exec-1" })).toEqual([
            true,
            null,
        ]);
    });

    it("follows NextToken until it finds a run", async () => {
        const run = execution({ workflowExecutionId: "exec-2" });
        mockGet
            .mockResolvedValueOnce(page([], "token-1"))
            .mockResolvedValueOnce(page([run], "token-2"));
        expect(await findRunningMirisUpload(ASSET)).toEqual([true, run]);
        expect(mockGet).toHaveBeenCalledTimes(2);
        expect(mockGet).toHaveBeenLastCalledWith(PATH, {
            queryStringParameters: {
                workflowDatabaseId: "GLOBAL",
                workflowId: "miris-upload",
                startingToken: "token-1",
            },
        });
    });

    it("stops on a repeated NextToken", async () => {
        mockGet.mockResolvedValue(page([], "token-1"));
        expect(await findRunningMirisUpload(ASSET)).toEqual([true, null]);
        expect(mockGet).toHaveBeenCalledTimes(2);
    });

    it("stops after its page bound", async () => {
        let n = 0;
        mockGet.mockImplementation(async () => page([], `token-${++n}`));
        expect(await findRunningMirisUpload(ASSET)).toEqual([true, null]);
        expect(mockGet).toHaveBeenCalledTimes(10);
    });

    it.each([
        [
            "a failed request",
            () => mockGet.mockRejectedValue(new ApiError("Forbidden", 403)),
            "Forbidden",
        ],
        [
            "an unknown response shape",
            () => mockGet.mockResolvedValue({ message: "nope" }),
            "Unknown response shape",
        ],
    ])("reports %s", async (_label, arrange, message) => {
        arrange();
        expect(await findRunningMirisUpload(ASSET)).toEqual([false, message]);
    });
});

describe("isExecutionInProgress", () => {
    it.each([
        [{ executionStatus: "RUNNING" }, true],
        [{ executionStatus: "NEW" }, true],
        [{ executionStatus: "SUCCEEDED" }, false],
        [{ executionStatus: "COMPLETE" }, false],
        [{ executionStatus: "SKIPPED" }, false],
        [{ executionStatus: "RUNNING", executionStopDate: "2026-10-29T11:00:00Z" }, false],
    ])("%o -> %s", (execution, expected) => {
        expect(isExecutionInProgress(execution as any)).toBe(expected);
    });
});

describe("getMirisUploadRun", () => {
    it.each([
        ["an unfinished run", { executionStatus: "RUNNING" }, { finished: false }],
        [
            "a run that succeeded",
            { executionStatus: "SUCCEEDED" },
            { finished: true, status: "SUCCEEDED", failure: null },
        ],
        [
            "a failed run with its recorded error",
            { executionStatus: "FAILED", executionError: "exit code 5: unresolved reference" },
            { finished: true, status: "FAILED", failure: "exit code 5: unresolved reference" },
        ],
        [
            "a run that ended without an error",
            { executionStatus: "ABORTED" },
            { finished: true, status: "ABORTED", failure: "The run ended with status ABORTED." },
        ],
    ])("reads %s from the execution details", async (_label, execution, run) => {
        mockGet.mockResolvedValueOnce({ message: { workflowExecutionId: "exec-1", ...execution } });
        expect(await getMirisUploadRun("exec-1")).toEqual([true, run]);
        expect(mockGet).toHaveBeenCalledWith("workflows/executions/exec-1/details");
    });

    it.each([
        [
            "the request fails",
            () => mockGet.mockRejectedValueOnce(new Error("Forbidden")),
            "Forbidden",
        ],
        [
            "the response has no status",
            () => mockGet.mockResolvedValueOnce({}),
            "Unknown response shape",
        ],
    ])("fails when %s", async (_label, arrange, message) => {
        arrange();
        expect(await getMirisUploadRun("exec-1")).toEqual([false, message]);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient, isRequestBlocked } from "../apiClient";
import { unwrapMessage } from "../../features/orchestration/api/client";
import { getExecutionDetails } from "../../features/orchestration/api/executions";
import type {
    ExecuteRequest,
    ExecuteResponse,
    Execution,
} from "../../features/orchestration/types";

/**
 * Miris stream recorded on one asset file, as returned by `GET /addon/miris/asset-status`.
 * `state` is `notStreamed`, `invalidUuid`, `notBound`, `unknown`, or the Miris processing state
 * (`preview`, `streamable`, `error`, `failed`, ...). `stale` is true when a file in the stream's
 * dependency manifest has changed or is missing, false when none has, and null when the file
 * records no manifest or the check could not run.
 */
export interface MirisStreamStatus {
    filePath: string;
    mirisAssetUuid: string | null;
    sourceVersionId: string | null;
    uploadedAt: string | null;
    displayName: string | null;
    state: string;
    stale?: boolean | null;
}

/**
 * Miris viewer key for one asset file, as returned by `POST /addon/miris/viewer-key`. The key
 * streams only `mirisAssetUuid`, and `expiresAt` is when it stops authorizing new streams.
 */
export interface MirisViewerKey {
    filePath: string;
    mirisAssetUuid: string;
    viewerKey: string;
    expiresAt: string;
}

/** `state` reported for a request the deployment's AWS WAF web ACL blocked. */
export const MIRIS_REQUEST_BLOCKED = "requestBlocked";

/** The GLOBAL workflow the Miris Upload pipeline registers, which uploads one USD file to Miris. */
export const MIRIS_UPLOAD_WORKFLOW_DATABASE_ID = "GLOBAL";
export const MIRIS_UPLOAD_WORKFLOW_ID = "miris-upload";

/** Tier-1 route a caller must be allowed to call to start the upload workflow. */
export const EXECUTE_WORKFLOW_API_ROUTE = "/workflows/{workflowDatabaseId}/{workflowId}/execute";

/** Tier-1 route a caller must be allowed to call to list an asset's workflow executions. */
export const ASSET_EXECUTIONS_API_ROUTE =
    "/database/{databaseId}/assets/{assetId}/workflows/executions";

/** Execution statuses that mean a run has finished; any other status is still in progress. */
const TERMINAL_EXECUTION_STATUSES = new Set([
    "SUCCEEDED",
    "FAILED",
    "ABORTED",
    "TIMED_OUT",
    "COMPLETE",
    "SKIPPED",
]);

/** Terminal execution statuses of a run that succeeded. */
const SUCCEEDED_EXECUTION_STATUSES = new Set(["SUCCEEDED", "COMPLETE"]);

/** Upper bound on the execution list pages one in-progress lookup reads. */
const RUNNING_UPLOAD_MAX_PAGES = 10;

/** True when a listed execution has not finished: no stop date and no terminal status. */
export function isExecutionInProgress(execution: Partial<Execution>): boolean {
    if (execution.executionStopDate) return false;
    return !TERMINAL_EXECUTION_STATUSES.has(String(execution.executionStatus || ""));
}

/**
 * Failure tuple for a request that threw: the message, the HTTP status when the request reached
 * the API, and the error body's `state`. A WAF-blocked request carries no status, so it is not
 * read as a permission denial.
 */
function requestFailure(
    error: any,
    fallbackMessage: string
): [false, string, number | undefined, string | undefined] {
    const message = error?.message || fallbackMessage;
    if (isRequestBlocked(error)) {
        return [false, message, undefined, MIRIS_REQUEST_BLOCKED];
    }
    return [
        false,
        message,
        typeof error?.status === "number" ? error.status : undefined,
        typeof error?.body?.state === "string" ? error.body.state : undefined,
    ];
}

interface MirisFileRequest {
    databaseId: string;
    assetId: string;
    filePath: string;
}

/**
 * Fetch the Miris stream status of a file. The server reads the Miris asset UUID from the
 * file's metadata, so the caller supplies only the asset-relative file path or object key.
 *
 * Returns [true, MirisStreamStatus] on success, or [false, errorMessage, status, state] on
 * failure, where `status` is the HTTP status when the request reached the API (403 for a
 * permission denial) and undefined otherwise. A request the AWS WAF web ACL blocked reports an
 * undefined status and the state `requestBlocked`.
 */
export const getMirisStreamStatus = async ({
    databaseId,
    assetId,
    filePath,
}: MirisFileRequest): Promise<
    [true, MirisStreamStatus] | [false, string, number | undefined, string | undefined]
> => {
    try {
        const response = await apiClient.get("addon/miris/asset-status", {
            queryStringParameters: { databaseId, assetId, filePath },
        });
        if (response && typeof response === "object" && typeof response.state === "string") {
            return [true, response as MirisStreamStatus];
        }
        return [false, response?.message || "Unknown response shape", undefined, undefined];
    } catch (error: any) {
        console.log(error);
        return requestFailure(error, "Failed to fetch Miris stream status");
    }
};

/**
 * Get a viewer key minted for the file's one Miris asset. The server reads the Miris asset UUID
 * from the file's metadata, so the caller supplies only the file path. The key is returned to the
 * caller only; do not cache or persist it.
 *
 * Returns [true, MirisViewerKey] on success, or [false, errorMessage, status, state] on failure,
 * where `status` is the HTTP status when the request reached the API (400 for an asset that is not
 * distributable, or is quarantined while the deployment's quarantine block is on, with the API's
 * reason as the message; 403 for a permission denial or an unbound Miris asset, 404 when the file
 * records no stream, 502 when Miris could not be
 * read or did not issue a key, 503 when the add-on is not configured, 504 when Miris did not
 * answer in time) and undefined otherwise, and `state` is the error body's `state` (`notBound`
 * when the Miris asset lacks the `vams-asset-{assetId}` or `vams-database-{databaseId}` tag), when
 * it reports one. A request the AWS WAF web ACL blocked is not a permission denial: it reports an
 * undefined status and the state `requestBlocked`.
 */
export const mintMirisViewerKey = async ({
    databaseId,
    assetId,
    filePath,
}: MirisFileRequest): Promise<
    [true, MirisViewerKey] | [false, string, number | undefined, string | undefined]
> => {
    try {
        const response = await apiClient.post("addon/miris/viewer-key", {
            body: { databaseId, assetId, filePath },
        });
        if (
            response &&
            typeof response === "object" &&
            typeof response.viewerKey === "string" &&
            response.viewerKey !== "" &&
            typeof response.mirisAssetUuid === "string"
        ) {
            return [true, response as MirisViewerKey];
        }
        return [false, "Unknown response shape", undefined, undefined];
    } catch (error: any) {
        console.log(error?.message);
        return requestFailure(error, "Failed to get a Miris viewer key");
    }
};

/**
 * Start the Miris Spatial Streaming Upload workflow on one file, as a manual run of the GLOBAL
 * `miris-upload` workflow. `filePath` is the asset-relative path the status endpoint returns. The
 * request names no template, so the pipeline runs with its default template, which the execute
 * endpoint applies to a pipeline that requires one. No file version is pinned: the pipeline
 * uploads the asset's current files.
 *
 * Returns [true, ExecuteResponse] on success, or [false, errorMessage, status, state] on failure,
 * where `status` is the HTTP status when the request reached the API (403 for a permission
 * denial) and undefined otherwise. A request the AWS WAF web ACL blocked reports an undefined
 * status and the state `requestBlocked`.
 */
export const startMirisUpload = async ({
    databaseId,
    assetId,
    filePath,
}: MirisFileRequest): Promise<
    [true, ExecuteResponse] | [false, string, number | undefined, string | undefined]
> => {
    const body: ExecuteRequest = {
        inputFiles: [{ databaseId, assetId, relativeFileKey: filePath }],
        triggerType: "manual",
    };
    try {
        const response = await apiClient.post(
            `workflows/${MIRIS_UPLOAD_WORKFLOW_DATABASE_ID}/${MIRIS_UPLOAD_WORKFLOW_ID}/execute`,
            { body }
        );
        const result = unwrapMessage(response);
        return [true, result && typeof result === "object" ? (result as ExecuteResponse) : {}];
    } catch (error: any) {
        console.log(error?.message);
        return requestFailure(error, "Failed to start the Miris upload");
    }
};

interface MirisAssetRequest {
    databaseId: string;
    assetId: string;
}

/**
 * Find a Miris Spatial Streaming Upload run on an asset that has not finished. Lists the asset's
 * workflow executions (`GET /database/{databaseId}/assets/{assetId}/workflows/executions`) filtered
 * server side to the GLOBAL `miris-upload` workflow, newest first, and returns the first one whose
 * status is not terminal. The match is asset-level rather than per file, since the pipeline uploads
 * the asset's root file whichever file started it. The listing covers the endpoint's default
 * look-back window, and its filters apply after each page is read, so pages are followed until a
 * run is found, the listing ends, or `RUNNING_UPLOAD_MAX_PAGES` pages have been read. Given an
 * `executionId`, it returns that run instead, finished or not.
 *
 * Returns [true, Execution] for a run in progress (or the named run), [true, null] when none is
 * found, or [false, errorMessage] on failure.
 */
export const findRunningMirisUpload = async ({
    databaseId,
    assetId,
    executionId,
}: MirisAssetRequest & { executionId?: string }): Promise<
    [true, Execution | null] | [false, string]
> => {
    const requested = new Set<string>();
    let startingToken: string | undefined;
    try {
        for (let page = 0; page < RUNNING_UPLOAD_MAX_PAGES; page++) {
            const response = await apiClient.get(
                `database/${databaseId}/assets/${assetId}/workflows/executions`,
                {
                    queryStringParameters: {
                        workflowDatabaseId: MIRIS_UPLOAD_WORKFLOW_DATABASE_ID,
                        workflowId: MIRIS_UPLOAD_WORKFLOW_ID,
                        ...(startingToken ? { startingToken } : {}),
                    },
                }
            );
            const result = unwrapMessage(response);
            if (!result || typeof result !== "object" || !Array.isArray(result.Items)) {
                return [false, "Unknown response shape"];
            }
            const running = (result.Items as Execution[]).find(
                (execution) =>
                    execution.workflowId === MIRIS_UPLOAD_WORKFLOW_ID &&
                    execution.workflowDatabaseId === MIRIS_UPLOAD_WORKFLOW_DATABASE_ID &&
                    (executionId
                        ? execution.workflowExecutionId === executionId
                        : isExecutionInProgress(execution))
            );
            if (running) return [true, running];
            const next = typeof result.NextToken === "string" ? result.NextToken : "";
            if (!next || requested.has(next)) return [true, null];
            requested.add(next);
            startingToken = next;
        }
        return [true, null];
    } catch (error: any) {
        console.log(error?.message);
        return [false, error?.message || "Failed to list the asset's Miris uploads"];
    }
};

/**
 * A Miris upload run: unfinished, or finished with its execution status and failure reason (null
 * when it succeeded).
 */
export type MirisUploadRun =
    | { finished: false }
    | { finished: true; status: string; failure: string | null };

/**
 * Read one Miris Spatial Streaming Upload run from its execution details
 * (`GET /workflows/executions/{executionId}/details`). A finished run that did not succeed reports
 * the error the workflow recorded (`executionError`), or its status when it recorded none.
 *
 * Returns [true, MirisUploadRun], or [false, errorMessage] on failure.
 */
export const getMirisUploadRun = async (
    executionId: string
): Promise<[true, MirisUploadRun] | [false, string]> => {
    const [ok, execution] = await getExecutionDetails(executionId);
    if (!ok || typeof execution !== "object" || typeof execution?.executionStatus !== "string") {
        return [false, typeof execution === "string" ? execution : "Unknown response shape"];
    }
    return [true, mirisUploadRunOf(execution)];
};

/**
 * The run an execution record describes: unfinished, or finished with its status and the error the
 * workflow recorded (`executionError`), or its status when it recorded none (null when it succeeded).
 */
export function mirisUploadRunOf(execution: Partial<Execution>): MirisUploadRun {
    if (isExecutionInProgress(execution)) return { finished: false };
    const status = String(execution.executionStatus || "");
    const failure = SUCCEEDED_EXECUTION_STATUSES.has(status)
        ? null
        : execution.executionError || `The run ended with status ${status}.`;
    return { finished: true, status, failure };
}

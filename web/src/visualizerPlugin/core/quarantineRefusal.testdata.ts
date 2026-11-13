/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Test data: what every file route answers for a quarantined asset while
 * `app.compliance.quarantineBlocksDownload` is on (D169). The body is read from
 * `quarantineRefusalBody.testdata.json`, which the backend handler tests compare with each route's
 * real response (`backend/tests/quarantineRefusalBody.py`), so the web tests render the body the
 * API sends.
 */
import refusal from "./quarantineRefusalBody.testdata.json";

/** The HTTP status of the refusal. */
export const QUARANTINE_REFUSAL_STATUS: number = refusal.statusCode;

/** The refusal's JSON body. */
export const QUARANTINE_REFUSAL_BODY: { message: string } = refusal.body;

/** The refusal's `message`, the sentence the documentation quotes. */
export const QUARANTINE_REFUSAL_MESSAGE: string = refusal.body.message;

/**
 * The tuple `downloadAsset` returns for the refusal (`APIService.quarantineRefusal.test.ts` checks
 * it against the real `downloadAsset` and `apiClient` over this body).
 */
export const QUARANTINE_REFUSED_DOWNLOAD = [
    false,
    QUARANTINE_REFUSAL_MESSAGE,
    QUARANTINE_REFUSAL_STATUS,
    QUARANTINE_REFUSAL_MESSAGE,
];

/**
 * A stand-in for the streaming route's `fetch` answer to the refusal, with what the viewers read
 * from it (jsdom has no fetch `Response`).
 */
export function quarantineRefusalResponse(): Response {
    const json = async () => ({ ...QUARANTINE_REFUSAL_BODY });
    return {
        ok: false,
        status: QUARANTINE_REFUSAL_STATUS,
        statusText: "Bad Request",
        headers: { get: () => "application/json" },
        json,
        text: async () => JSON.stringify(QUARANTINE_REFUSAL_BODY),
        clone: () => ({ json }),
        arrayBuffer: async () => new ArrayBuffer(0),
    } as unknown as Response;
}

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Upload retries cover transient server errors (429 and 5xx) only, and the status code is read
 * from the error's status fields, never guessed from its message text.
 */

import { RETRY_CONFIG, extractStatusCode, isRetryableError, retryWithBackoff } from "./uploadRetry";

function httpError(status: number, message = `HTTP ${status}`) {
    return Object.assign(new Error(message), { status });
}

describe("uploadRetry", () => {
    beforeEach(() => {
        jest.spyOn(console, "error").mockImplementation(() => {});
        jest.spyOn(console, "log").mockImplementation(() => {});
    });

    afterEach(() => {
        jest.useRealTimers();
        jest.restoreAllMocks();
    });

    describe("extractStatusCode", () => {
        it("reads status, response.status and statusCode", () => {
            expect(extractStatusCode({ status: 502 })).toBe(502);
            expect(extractStatusCode({ response: { status: 503 } })).toBe(503);
            expect(extractStatusCode({ statusCode: 504 })).toBe(504);
        });

        it("does not infer a status from the message text", () => {
            expect(extractStatusCode(new Error("File type .exe is not allowed (400)"))).toBeNull();
            expect(extractStatusCode(new Error("Rate limit exceeded"))).toBeNull();
            expect(extractStatusCode(new Error("object 404.glb missing"))).toBeNull();
        });
    });

    describe("isRetryableError", () => {
        it.each([429, 500, 502, 503, 504])("retries %i by default", (status) => {
            expect(isRetryableError(httpError(status))).toBe(true);
        });

        it.each([400, 403, 404, 409])("does not retry %i by default", (status) => {
            expect(isRetryableError(httpError(status))).toBe(false);
        });

        it("uses a caller-supplied list", () => {
            expect(isRetryableError(httpError(504), [429, 500, 502, 503])).toBe(false);
            expect(isRetryableError(httpError(500), [429, 500, 502, 503])).toBe(true);
        });

        it("defaults to RETRY_CONFIG.RETRYABLE_STATUS_CODES", () => {
            expect(RETRY_CONFIG.RETRYABLE_STATUS_CODES).toEqual([429, 500, 502, 503, 504]);
        });
    });

    describe("retryWithBackoff", () => {
        it("fails a 400 at once without retrying", async () => {
            const operation = jest.fn().mockRejectedValue(httpError(400));

            await expect(retryWithBackoff(operation, "Initialize")).rejects.toMatchObject({
                status: 400,
            });
            expect(operation).toHaveBeenCalledTimes(1);
        });

        it("retries a single 502 and returns the result", async () => {
            jest.useFakeTimers();
            const operation = jest
                .fn()
                .mockRejectedValueOnce(httpError(502))
                .mockResolvedValue("ok");

            const pending = retryWithBackoff(operation, "Initialize");
            await jest.advanceTimersByTimeAsync(RETRY_CONFIG.STANDARD_BACKOFF_MS);

            await expect(pending).resolves.toBe("ok");
            expect(operation).toHaveBeenCalledTimes(2);
        });
    });
});

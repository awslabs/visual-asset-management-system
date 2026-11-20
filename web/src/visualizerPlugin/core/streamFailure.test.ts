/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The sentence a streaming viewer shows when a file request fails. The viewer libraries (Potree,
 * Cesium) reject without the HTTP status, so a failure is probed and named by what the API answered:
 * no readable response (a network failure, or a request the deployment's AWS WAF web ACL or its rate
 * limit refused before the API answered the CORS preflight), a WAF block the API stage rendered, a
 * permission denial, a missing file, a throttle, or another status.
 */
import {
    describeStreamFailure,
    DOWNLOAD_ROUTE_FILE_NOT_FOUND,
    isFileNotFoundAnswer,
    probeStreamFailure,
} from "./streamFailure";
import fileNotFound from "./fileNotFoundBody.testdata.json";

const SUBJECT = "the point cloud preview";
const WAF_BODY = {
    message: "Request blocked by the deployment's AWS WAF web ACL",
    blockedBy: "waf",
    requestId: "r-1",
};

describe("describeStreamFailure", () => {
    it("names an unreachable request, which is what a refused preflight looks like", () => {
        expect(describeStreamFailure(SUBJECT, {})).toBe(
            "Unable to reach the point cloud preview. The request failed before VAMS answered it: a network error, a block by the deployment's AWS WAF web ACL, or a rate limit."
        );
    });

    it("names a WAF block with the request ID the administrator looks up", () => {
        expect(describeStreamFailure(SUBJECT, { status: 403, body: WAF_BODY })).toBe(
            "Unable to load the point cloud preview: Request blocked by the deployment's AWS WAF web ACL (request ID r-1)."
        );
    });

    it("names a WAF block without a request ID", () => {
        const { requestId, ...body } = WAF_BODY;
        expect(requestId).toBe("r-1");
        expect(describeStreamFailure(SUBJECT, { status: 403, body })).toBe(
            "Unable to load the point cloud preview: Request blocked by the deployment's AWS WAF web ACL."
        );
    });

    it("names a permission denial: the authorization error, API Gateway's denial, or no message", () => {
        const denial =
            "Unable to load the point cloud preview: you don't have permission to view it.";
        expect(describeStreamFailure(SUBJECT, { status: 403 })).toBe(denial);
        expect(
            describeStreamFailure(SUBJECT, { status: 403, body: { message: "Not Authorized" } })
        ).toBe(denial);
        expect(
            describeStreamFailure(SUBJECT, {
                status: 403,
                body: { message: "User is not authorized to access this resource" },
            })
        ).toBe(denial);
    });

    it("shows the API's own reason for any other 403, such as an asset that is not distributable", () => {
        expect(
            describeStreamFailure(SUBJECT, {
                status: 403,
                body: { message: "Asset is not currently allowing file distribution" },
            })
        ).toBe(
            "Unable to load the point cloud preview: Asset is not currently allowing file distribution."
        );
    });

    it("names an expired session", () => {
        expect(describeStreamFailure(SUBJECT, { status: 401 })).toBe(
            "Unable to load the point cloud preview: your session is no longer valid. Sign in again."
        );
    });

    it("names a missing file", () => {
        expect(describeStreamFailure(SUBJECT, { status: 404 })).toBe(
            "Unable to load the point cloud preview: it was not found."
        );
    });

    it("names a throttle", () => {
        expect(describeStreamFailure(SUBJECT, { status: 429 })).toBe(
            "Unable to load the point cloud preview: too many requests. Try again shortly."
        );
    });

    it("names any other status by its number", () => {
        expect(describeStreamFailure(SUBJECT, { status: 502 })).toBe(
            "Unable to load the point cloud preview (HTTP 502)."
        );
    });

    it("names a file the API served that the viewer could not read", () => {
        expect(describeStreamFailure(SUBJECT, { status: 200 })).toBe(
            "Unable to display the point cloud preview: the viewer could not read the file."
        );
    });
});

describe("probeStreamFailure", () => {
    const URL = "https://api.example/api/database/db/assets/a/auxiliaryPreviewAssets/stream/x";
    const HEADERS = { Authorization: "Bearer t" };
    let fetchMock: jest.Mock;

    beforeEach(() => {
        fetchMock = jest.fn();
        (global as any).fetch = fetchMock;
    });

    afterEach(() => {
        delete (global as any).fetch;
    });

    it("requests the URL with the viewer's headers", async () => {
        fetchMock.mockResolvedValue({ ok: true, status: 200 });
        await probeStreamFailure(URL, HEADERS);
        expect(fetchMock).toHaveBeenCalledWith(URL, { headers: HEADERS });
    });

    it("returns the status and the JSON body of a failed response", async () => {
        fetchMock.mockResolvedValue({
            ok: false,
            status: 403,
            json: () => Promise.resolve(WAF_BODY),
        });
        await expect(probeStreamFailure(URL, HEADERS)).resolves.toEqual({
            status: 403,
            body: WAF_BODY,
        });
    });

    it("returns the status alone when the body is not JSON", async () => {
        fetchMock.mockResolvedValue({
            ok: false,
            status: 502,
            json: () => Promise.reject(new SyntaxError("Unexpected token <")),
        });
        await expect(probeStreamFailure(URL, HEADERS)).resolves.toEqual({ status: 502 });
    });

    it("returns the status of a response that succeeded", async () => {
        fetchMock.mockResolvedValue({ ok: true, status: 200 });
        await expect(probeStreamFailure(URL, HEADERS)).resolves.toEqual({ status: 200 });
    });

    it("returns no status when the request itself fails", async () => {
        fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
        await expect(probeStreamFailure(URL, HEADERS)).resolves.toEqual({});
    });
});

// The body is the download route's real answer for a missing key: the backend handler test
// `test_missing_single_file_answer_is_the_web_test_data` compares the route's response with the same
// file, so a rewording on either side fails a test instead of turning missing files into refusals.
describe("isFileNotFoundAnswer", () => {
    it("reads the download route's missing-file answer as a missing file", () => {
        expect(fileNotFound.body.message.endsWith(DOWNLOAD_ROUTE_FILE_NOT_FOUND)).toBe(true);
        expect(isFileNotFoundAnswer(fileNotFound.statusCode, fileNotFound.body.message)).toBe(true);
    });

    it("[control] reads no other refusal or status as a missing file", () => {
        expect(isFileNotFoundAnswer(400, "Asset is quarantined and cannot be downloaded")).toBe(
            false
        );
        expect(isFileNotFoundAnswer(403, fileNotFound.body.message)).toBe(false);
        expect(isFileNotFoundAnswer(undefined, fileNotFound.body.message)).toBe(false);
    });
});

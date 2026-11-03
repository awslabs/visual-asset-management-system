/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the Cesium viewer shows when a tileset or its tiles fail to load. A tileset request Cesium
 * rejects carries the HTTP status (a Cesium `RequestErrorEvent`), so it is named like any other
 * stream failure; a tileset that fails for another reason keeps Cesium's own message; failed tiles of
 * a tileset that did load, and tilesets that failed beside others that loaded, are counted rather
 * than left as silent holes.
 */
import { describeTilesetFailure, partialTilesetsNotice, tileFailureNotice } from "./tilesetFailure";

const KEY = "city/tileset.json";
const WAF_BODY = {
    message: "Request blocked by the deployment's AWS WAF web ACL",
    blockedBy: "waf",
    requestId: "r-1",
};

/** The shape of a Cesium RequestErrorEvent: the status, the response text and the headers. */
const requestError = (statusCode: number | undefined, response?: unknown) => ({
    statusCode,
    response,
    responseHeaders: undefined,
});

describe("describeTilesetFailure", () => {
    it("names a WAF block from the response text Cesium keeps", () => {
        expect(describeTilesetFailure(KEY, requestError(403, JSON.stringify(WAF_BODY)))).toBe(
            'Unable to load the tileset "city/tileset.json": Request blocked by the deployment\'s AWS WAF web ACL (request ID r-1).'
        );
    });

    it("names a WAF block from a parsed response", () => {
        expect(describeTilesetFailure(KEY, requestError(403, WAF_BODY))).toBe(
            'Unable to load the tileset "city/tileset.json": Request blocked by the deployment\'s AWS WAF web ACL (request ID r-1).'
        );
    });

    it("shows the API's reason for a tileset in an asset that is not distributable", () => {
        expect(
            describeTilesetFailure(
                KEY,
                requestError(403, JSON.stringify({ message: "Asset not distributable" }))
            )
        ).toBe('Unable to load the tileset "city/tileset.json": Asset not distributable.');
    });

    it("names a request that got no readable response", () => {
        expect(describeTilesetFailure(KEY, requestError(undefined))).toMatch(
            /^Unable to reach the tileset "city\/tileset\.json"\. The request failed before VAMS answered it/
        );
    });

    it("names a missing tileset, and a response that is not JSON is not a block", () => {
        expect(describeTilesetFailure(KEY, requestError(404, "<html>not found</html>"))).toBe(
            'Unable to load the tileset "city/tileset.json": it was not found.'
        );
    });

    it("keeps Cesium's message for a failure that is not a request error", () => {
        expect(describeTilesetFailure(KEY, new Error("Unsupported tileset version"))).toBe(
            'Tileset loading failed for "city/tileset.json": Unsupported tileset version'
        );
        expect(describeTilesetFailure(KEY, undefined)).toBe(
            'Tileset loading failed for "city/tileset.json": Unknown error'
        );
    });
});

describe("tileFailureNotice", () => {
    it("counts failed tiles and names the last error", () => {
        expect(tileFailureNotice(1, "Request has failed. Status Code: 429")).toBe(
            "1 tile request failed, so the model may be incomplete. Last error: Request has failed. Status Code: 429"
        );
        expect(tileFailureNotice(12)).toBe(
            "12 tile requests failed, so the model may be incomplete."
        );
    });
});

describe("partialTilesetsNotice", () => {
    it("counts the tilesets that failed beside the ones that loaded, naming the first", () => {
        expect(
            partialTilesetsNotice(['Unable to load the tileset "b.json": it was not found.'], 3)
        ).toBe(
            '1 of 3 tilesets failed to load. Unable to load the tileset "b.json": it was not found.'
        );
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A '#' or '?' in a value placed into an API path stays part of the value (#1476).
 *
 * The services build paths such as `database/${databaseId}/assets/${assetId}`, and `buildUrl`
 * resolves them with `new URL()`. An asset id may end in '#', which `new URL()` read as the start of
 * a fragment, so asset `x-q#` was read and updated as asset `x-q`. `buildUrl` now percent-encodes
 * '#' and '?' in the path, and a '%' that does not start an escape (an asset id may end in '%');
 * a path without any of them is sent exactly as before.
 */

import { apiClient } from "./apiClient";
import {
    archiveAssetDelete,
    deleteAssetPermanentDelete,
    fetchAsset,
    fetchAssetS3Files,
    updateAsset,
} from "./APIService";

jest.mock("../utils/sessionManager", () => ({
    ensureValidSession: jest.fn(),
    logoutExpired: jest.fn(),
}));
jest.mock("../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(async () => "Bearer good"),
}));

const BASE = "https://api.example.test/";

function okResponse(body: any = { message: "ok" }): Response {
    return {
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => null },
        json: async () => body,
        text: async () => JSON.stringify(body),
    } as unknown as Response;
}

let fetchMock: jest.Mock;

beforeEach(() => {
    localStorage.setItem("api_path", BASE);
    fetchMock = jest.fn(async () => okResponse());
    (global as any).fetch = fetchMock;
});

function sent(): Array<[string, string]> {
    return fetchMock.mock.calls.map(([url, init]: [string, RequestInit]) => [
        String(init.method),
        String(url),
    ]);
}

// [service call, HTTP method, route suffix after the asset id, query string the call adds]
const ASSET_CALLS: Array<[string, (assetId: string) => Promise<any>, string, string, string]> = [
    [
        "read",
        (id) => fetchAsset({ databaseId: "db-1", assetId: id }),
        "GET",
        "",
        "?showArchived=false",
    ],
    [
        "update",
        (id) => updateAsset({ databaseId: "db-1", assetId: id, updateData: { description: "d" } }),
        "PUT",
        "",
        "",
    ],
    [
        "archive",
        (id) =>
            archiveAssetDelete({ databaseId: "db-1", assetId: id, body: { confirmArchive: true } }),
        "DELETE",
        "/archiveAsset",
        "",
    ],
    [
        "delete",
        (id) =>
            deleteAssetPermanentDelete({
                databaseId: "db-1",
                assetId: id,
                body: { confirmPermanentDelete: true },
            }),
        "DELETE",
        "/deleteAsset",
        "",
    ],
    [
        "list files",
        (id) => fetchAssetS3Files({ databaseId: "db-1", assetId: id }),
        "GET",
        "/listFiles",
        "?includeArchived=false",
    ],
];

describe.each(ASSET_CALLS)("%s", (_name, call, method, suffix, query) => {
    it.each([
        ["an id ending in '#'", "x-q#", "x-q%23"],
        ["an id with '#' inside", "a#b", "a%23b"],
        ["an id with '?'", "a?b", "a%3Fb"],
        ["an id ending in '%'", "x-q%", "x-q%25"],
    ])("addresses %s as itself", async (_label, assetId, segment) => {
        await call(assetId);
        expect(sent()).toEqual([
            [method, `${BASE}database/db-1/assets/${segment}${suffix}${query}`],
        ]);
        const url = new URL(sent()[0][1]);
        expect(url.hash).toBe("");
        expect(decodeURIComponent(url.pathname.split("/")[4])).toBe(assetId);
    });

    it("never reaches the sibling asset without the '#'", async () => {
        await call("x-q#");
        expect(new URL(sent()[0][1]).pathname).not.toBe(`/database/db-1/assets/x-q${suffix}`);
    });

    it.each(["x-q", "a b", "résumé", "o'neil,1"])(
        "sends %s exactly as new URL() did before",
        async (assetId) => {
            await call(assetId);
            const before = new URL(`database/db-1/assets/${assetId}${suffix}`, BASE);
            expect(sent()[0][1].split("?")[0]).toBe(before.toString());
        }
    );
});

describe("buildUrl", () => {
    it("keeps query parameters in the query string", async () => {
        await apiClient.get("database/db-1/assets/x-q#/listFiles", {
            queryStringParameters: { basic: "true", startingToken: "a#b?c" },
        });
        const url = new URL(sent()[0][1]);
        expect(url.pathname).toBe("/database/db-1/assets/x-q%23/listFiles");
        expect(url.searchParams.get("basic")).toBe("true");
        expect(url.searchParams.get("startingToken")).toBe("a#b?c");
    });

    it("does not encode an escape a second time", async () => {
        await apiClient.get("database/db-1/assets/x-q%23");
        expect(sent()[0][1]).toBe(`${BASE}database/db-1/assets/x-q%23`);
    });

    it("encodes a '%' that does not start an escape", async () => {
        await apiClient.get("database/db-1/assets/x-q%/listFiles");
        await apiClient.get("database/db-1/assets/a%zz");
        expect(sent().map(([, url]) => url)).toEqual([
            `${BASE}database/db-1/assets/x-q%25/listFiles`,
            `${BASE}database/db-1/assets/a%25zz`,
        ]);
    });

    // Callers that encode a segment themselves (EDH image, session, recipe, job and capacity
    // reservation ids; workflow trigger types) pass encodeURIComponent output, in which every '%'
    // starts an escape, so buildUrl sends it unchanged.
    it.each(["x-q%", "100%", "a%2Fb", "fileUpload#t-1", "img 1/2", "%%", "résumé%"])(
        "sends encodeURIComponent(%j) unchanged",
        async (value) => {
            const segment = encodeURIComponent(value);
            await apiClient.get(`addon/edh/admin/images/vdi/${segment}`);
            expect(sent()[0][1]).toBe(`${BASE}addon/edh/admin/images/vdi/${segment}`);
            const sentSegment = new URL(sent()[0][1]).pathname.split("/")[6];
            expect(decodeURIComponent(sentSegment)).toBe(value);
        }
    );

    // shortcut pinned: a raw value with '%' and two hex digits cannot be told from an escape, so
    // it is sent as one. No id the backend accepts has it ('%' only as an asset id's last
    // character); a value that could would need encoding in its service.
    it("sends a raw '%' followed by two hex digits as that escape", async () => {
        await apiClient.get("database/db-1/assets/x-%41");
        expect(sent()[0][1]).toBe(`${BASE}database/db-1/assets/x-%41`);
    });
});

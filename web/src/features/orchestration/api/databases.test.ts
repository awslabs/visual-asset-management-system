/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("../../../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
}));

import { fetchAllDatabases } from "../../../services/APIService";
import { listAllDatabases } from "./databases";

describe("listAllDatabases (delegates to the standard APIService)", () => {
    beforeEach(() => jest.clearAllMocks());

    it("returns [true, items] when the standard function returns an array", async () => {
        (fetchAllDatabases as jest.Mock).mockResolvedValue([
            { databaseId: "db1", description: "d" },
            { databaseId: "db2" },
        ]);
        const r = await listAllDatabases();
        expect(fetchAllDatabases).toHaveBeenCalledTimes(1);
        expect(r).toEqual([true, [{ databaseId: "db1", description: "d" }, { databaseId: "db2" }]]);
    });

    it("returns [false, message] when the standard function returns an error string", async () => {
        (fetchAllDatabases as jest.Mock).mockResolvedValue("Failed to load databases.");
        const r = await listAllDatabases();
        expect(r).toEqual([false, "Failed to load databases."]);
    });

    it("returns [false, fallback] when the standard function returns false", async () => {
        (fetchAllDatabases as jest.Mock).mockResolvedValue(false);
        const [ok, data] = await listAllDatabases();
        expect(ok).toBe(false);
        expect(typeof data).toBe("string");
    });
});

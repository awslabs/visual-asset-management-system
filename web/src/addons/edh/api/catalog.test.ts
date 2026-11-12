/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("../../../services/apiClient", () => ({
    apiClient: {
        get: jest.fn(),
        post: jest.fn(),
        put: jest.fn(),
        del: jest.fn(),
    },
}));

import { apiClient } from "../../../services/apiClient";
import { getEdhLeasePolicy, listEdhImages, listEdhInstanceTypes } from "./catalog";

const IMAGE = {
    imageId: "42",
    name: "Ubuntu GPU desktop",
    baseOs: "ubuntu2204",
    osFamily: "linux",
    rootSize: 120,
    description: null,
};

const LEASE_POLICY = { enabled: true, leaseHours: 8, forced: false, graceHours: 24 };

describe("catalog service", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    describe("listEdhImages", () => {
        it("reads addon/edh/catalog/images and returns the Items array", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ Items: [IMAGE] });
            const r = await listEdhImages();
            expect(apiClient.get).toHaveBeenCalledWith("addon/edh/catalog/images");
            expect(r).toEqual([true, [IMAGE]]);
        });

        it("returns an empty array when the response carries no Items", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({});
            expect(await listEdhImages()).toEqual([true, []]);
        });
    });

    describe("listEdhInstanceTypes", () => {
        it("reads addon/edh/catalog/instance-types and returns the Items array", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                Items: [{ instanceType: "g5.xlarge" }, { instanceType: "g5.2xlarge" }],
            });
            const r = await listEdhInstanceTypes();
            expect(apiClient.get).toHaveBeenCalledWith("addon/edh/catalog/instance-types");
            expect(r).toEqual([
                true,
                [{ instanceType: "g5.xlarge" }, { instanceType: "g5.2xlarge" }],
            ]);
        });

        it("returns the failure tuple when the request is rejected", async () => {
            (apiClient.get as jest.Mock).mockRejectedValue(new Error("EDH is unavailable"));
            expect(await listEdhInstanceTypes()).toEqual([false, "EDH is unavailable"]);
        });
    });

    describe("getEdhLeasePolicy", () => {
        it("reads addon/edh/catalog/lease and returns the payload as the route wraps it", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ lease: LEASE_POLICY });
            const r = await getEdhLeasePolicy();
            expect(apiClient.get).toHaveBeenCalledWith("addon/edh/catalog/lease");
            expect(r).toEqual([true, { lease: LEASE_POLICY }]);
        });

        it("returns the failure tuple when the request is rejected", async () => {
            (apiClient.get as jest.Mock).mockRejectedValue(new Error("Forbidden"));
            expect(await getEdhLeasePolicy()).toEqual([false, "Forbidden"]);
        });
    });
});

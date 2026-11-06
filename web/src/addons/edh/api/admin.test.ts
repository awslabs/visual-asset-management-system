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
import {
    listAdminImages,
    registerVdiImage,
    registerSimulationImage,
    deleteVdiImage,
    deleteSimulationImage,
    listAdminInstances,
    listStaleInstances,
    stopAdminInstance,
    deactivateAdminInstance,
    getCapacity,
    getHealth,
    getAdminSettings,
    updateAdminSettings,
} from "./admin";
import { EdhRequestError } from "./outcome";

describe("edh admin service", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("listAdminImages hits addon/edh/admin/images and returns the {Items} page", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ Items: [{ kind: "vdi", imageId: "7" }] });
        const r = await listAdminImages();
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/admin/images");
        expect(r).toEqual([true, { Items: [{ kind: "vdi", imageId: "7" }] }]);
    });

    it("registerVdiImage posts the JSON body to addon/edh/admin/images/vdi", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue({ kind: "vdi", imageId: "42" });
        const body = {
            amiId: "ami-0123456789abcdef0",
            stackName: "linux-cad",
            baseOs: "amazonlinux2023",
            rootSize: 100,
            virtualDesktopProfileId: 1,
        };
        const r = await registerVdiImage(body);
        expect(apiClient.post).toHaveBeenCalledWith("addon/edh/admin/images/vdi", { body });
        expect(r).toEqual([true, { kind: "vdi", imageId: "42" }]);
    });

    it("registerSimulationImage posts to addon/edh/admin/images/simulation", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue({ kind: "simulation", imageId: "3" });
        const body = {
            amiId: "ami-0aaaaaaaaaaaaaaaa",
            stackName: "sim",
            rootSize: 50,
            targetNodeProfileId: 4,
        };
        await registerSimulationImage(body);
        expect(apiClient.post).toHaveBeenCalledWith("addon/edh/admin/images/simulation", { body });
    });

    it("deleteVdiImage / deleteSimulationImage DELETE the kind-discriminated path with an encoded id", async () => {
        (apiClient.del as jest.Mock).mockResolvedValue({ deleted: true });
        await deleteVdiImage("7");
        expect(apiClient.del).toHaveBeenCalledWith("addon/edh/admin/images/vdi/7");
        await deleteSimulationImage("a/b");
        expect(apiClient.del).toHaveBeenCalledWith("addon/edh/admin/images/simulation/a%2Fb");
    });

    it("listAdminInstances and listStaleInstances hit their routes; stale carries idleMinutes", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ Items: [] });
        await listAdminInstances();
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/admin/instances");
        await listStaleInstances(45);
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/admin/instances/stale", {
            queryStringParameters: { idleMinutes: "45" },
        });
    });

    it("stopAdminInstance / deactivateAdminInstance POST the session action routes", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue({ sessionId: "s-1", state: "STOPPED" });
        const r = await stopAdminInstance("s-1");
        expect(apiClient.post).toHaveBeenCalledWith("addon/edh/admin/instances/s-1/stop");
        expect(r).toEqual([true, { sessionId: "s-1", state: "STOPPED" }]);
        await deactivateAdminInstance("s-1");
        expect(apiClient.post).toHaveBeenCalledWith("addon/edh/admin/instances/s-1/deactivate");
    });

    it.each([
        ["stopAdminInstance", () => stopAdminInstance("s-1"), "post"],
        ["deactivateAdminInstance", () => deactivateAdminInstance("s-1"), "post"],
        ["deleteVdiImage", () => deleteVdiImage("7"), "del"],
        ["deleteSimulationImage", () => deleteSimulationImage("3"), "del"],
    ] as const)("%s keeps the status and parsed body of a rejection", async (_name, call, verb) => {
        (apiClient[verb] as jest.Mock).mockRejectedValue(
            Object.assign(new Error("Endpoint request timed out"), { status: 504, body: null })
        );
        const [ok, err] = await call();
        expect(ok).toBe(false);
        expect(err).toBeInstanceOf(EdhRequestError);
        expect(err).toMatchObject({ message: "Endpoint request timed out", status: 504 });
    });

    it("the registrations keep the status and parsed body of a rejection", async () => {
        const body = { message: "EDH did not confirm the request in time.", outcomeUnknown: true };
        (apiClient.post as jest.Mock).mockRejectedValue(
            Object.assign(new Error(body.message), { status: 502, body })
        );
        const [, vdi] = await registerVdiImage({
            amiId: "ami-0123456789abcdef0",
            stackName: "linux-cad",
            baseOs: "amazonlinux2023",
            rootSize: 100,
            virtualDesktopProfileId: 1,
        });
        const [, simulation] = await registerSimulationImage({
            amiId: "ami-0aaaaaaaaaaaaaaaa",
            stackName: "sim",
            rootSize: 50,
            targetNodeProfileId: 4,
        });
        expect(vdi).toMatchObject({ status: 502, body });
        expect(simulation).toMatchObject({ status: 502, body });
    });

    it("getCapacity and getHealth hit their routes", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ status: "ok" });
        await getCapacity();
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/admin/capacity");
        await getHealth();
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/admin/health");
    });

    it("getHealth passes a 502 health body through as the answer", async () => {
        const body = {
            status: "error",
            reason: "credentialRejected",
            edhClusterId: "edh-dev",
            message: "EDH rejected the deployment credential.",
        };
        (apiClient.get as jest.Mock).mockRejectedValue(
            Object.assign(new Error(body.message), { status: 502, body })
        );
        const r = await getHealth();
        expect(r).toEqual([true, body]);
    });

    it("a thrown ApiError becomes a [false, message] tuple", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(new Error("Bad Gateway"));
        const r = await getHealth();
        expect(r).toEqual([false, "Bad Gateway"]);
    });

    it("getAdminSettings hits addon/edh/admin/settings", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({
            lease: {
                enabled: true,
                leaseHours: 8,
                forced: false,
                graceHours: 24,
                updatedAt: "2026-09-17T10:00:00Z",
                updatedBy: "admin",
            },
        });
        const r = await getAdminSettings();
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/admin/settings");
        expect(r).toEqual([
            true,
            {
                lease: {
                    enabled: true,
                    leaseHours: 8,
                    forced: false,
                    graceHours: 24,
                    updatedAt: "2026-09-17T10:00:00Z",
                    updatedBy: "admin",
                },
            },
        ]);
    });

    it("updateAdminSettings puts the body to addon/edh/admin/settings", async () => {
        (apiClient.put as jest.Mock).mockResolvedValue({
            lease: {
                enabled: true,
                leaseHours: 12,
                forced: true,
                graceHours: 48,
                updatedAt: "2026-09-17T10:05:00Z",
                updatedBy: "admin",
            },
        });
        const body = { enabled: true, leaseHours: 12, forced: true, graceHours: 48 };
        const r = await updateAdminSettings(body);
        expect(apiClient.put).toHaveBeenCalledWith("addon/edh/admin/settings", { body });
        expect(r).toEqual([
            true,
            {
                lease: {
                    enabled: true,
                    leaseHours: 12,
                    forced: true,
                    graceHours: 48,
                    updatedAt: "2026-09-17T10:05:00Z",
                    updatedBy: "admin",
                },
            },
        ]);
    });

    it("updateAdminSettings returns an error tuple on failure", async () => {
        (apiClient.put as jest.Mock).mockRejectedValue(new Error("Invalid lease hours"));
        const r = await updateAdminSettings({ leaseHours: 1000 });
        expect(r).toEqual([false, "Invalid lease hours"]);
    });
});

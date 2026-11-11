/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as adminService from "./admin";
import {
    adminQk,
    useEdhAdminMutations,
    useEdhAdminSettings,
    useUpdateEdhAdminSettings,
} from "./adminQueries";
import { EdhRequestError } from "./outcome";

jest.mock("./admin", () => ({
    listAdminImages: jest.fn(),
    registerVdiImage: jest.fn(),
    registerSimulationImage: jest.fn(),
    deleteVdiImage: jest.fn(),
    deleteSimulationImage: jest.fn(),
    listAdminInstances: jest.fn(),
    listStaleInstances: jest.fn(),
    stopAdminInstance: jest.fn(),
    deactivateAdminInstance: jest.fn(),
    getCapacity: jest.fn(),
    getHealth: jest.fn(),
    getAdminSettings: jest.fn(),
    updateAdminSettings: jest.fn(),
}));

const wrapperFor = (client: QueryClient) => {
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    Wrapper.displayName = "QueryWrapper";
    return Wrapper;
};

const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

// The bodies the add-on's handlers answer with, as the service layer rejects with them.
const UNCONFIRMED =
    "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.";
const UNAVAILABLE = "EDH is unavailable. Try again later.";

describe("adminQk", () => {
    it("keys every stale view under one prefix, so invalidating the prefix reaches them all", () => {
        expect(adminQk.staleInstances(120)).toEqual([...adminQk.staleInstancesAll(), 120]);
        expect(adminQk.staleInstances(60).slice(0, 1)).toEqual([...adminQk.staleInstancesAll()]);
        expect(adminQk.staleInstancesAll()).not.toEqual(adminQk.instances());
    });
});

describe("useEdhAdminMutations", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("invalidates the session views, every stale view and the capacity after a stop", async () => {
        (adminService.stopAdminInstance as jest.Mock).mockResolvedValue([
            true,
            { sessionId: "s-1" },
        ]);
        const client = newClient();
        // A stale view an administrator has looked at, cached under its own threshold.
        client.setQueryData(adminQk.staleInstances(120), []);
        client.setQueryData(adminQk.instances(), []);
        const invalidate = jest.spyOn(client, "invalidateQueries");
        const { result } = renderHook(() => useEdhAdminMutations(), {
            wrapper: wrapperFor(client),
        });

        await result.current.stopInstance.mutateAsync("s-1");

        expect(adminService.stopAdminInstance).toHaveBeenCalledWith("s-1");
        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: adminQk.staleInstancesAll() })
        );
        expect(invalidate).toHaveBeenCalledWith({ queryKey: adminQk.instances() });
        expect(invalidate).toHaveBeenCalledWith({ queryKey: adminQk.capacity() });
        expect(client.getQueryState(adminQk.staleInstances(120))?.isInvalidated).toBe(true);
        expect(client.getQueryState(adminQk.instances())?.isInvalidated).toBe(true);
    });

    it("rejects with the service message and leaves the views alone when a deactivate is refused", async () => {
        (adminService.deactivateAdminInstance as jest.Mock).mockResolvedValue([false, "Forbidden"]);
        const client = newClient();
        const invalidate = jest.spyOn(client, "invalidateQueries");
        const { result } = renderHook(() => useEdhAdminMutations(), {
            wrapper: wrapperFor(client),
        });

        await expect(result.current.deactivateInstance.mutateAsync("s-1")).rejects.toThrow(
            "Forbidden"
        );
        expect(invalidate).not.toHaveBeenCalled();
    });

    it("re-reads the session views after a stop EDH did not confirm", async () => {
        (adminService.stopAdminInstance as jest.Mock).mockResolvedValue([
            false,
            new EdhRequestError(UNCONFIRMED, 502, { message: UNCONFIRMED, outcomeUnknown: true }),
        ]);
        const client = newClient();
        const invalidate = jest.spyOn(client, "invalidateQueries");
        const { result } = renderHook(() => useEdhAdminMutations(), {
            wrapper: wrapperFor(client),
        });

        await expect(result.current.stopInstance.mutateAsync("s-1")).rejects.toMatchObject({
            status: 502,
        });
        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: adminQk.instances() })
        );
    });

    it("re-reads the images after the gateway's 504 on a registration, not after a plain 502", async () => {
        const client = newClient();
        const invalidate = jest.spyOn(client, "invalidateQueries");
        const { result } = renderHook(() => useEdhAdminMutations(), {
            wrapper: wrapperFor(client),
        });
        const body = {
            amiId: "ami-0123456789abcdef0",
            stackName: "linux-cad",
            baseOs: "amazonlinux2023",
            rootSize: 100,
            virtualDesktopProfileId: 1,
        };

        (adminService.registerVdiImage as jest.Mock).mockResolvedValue([
            false,
            new EdhRequestError(UNAVAILABLE, 502, { message: UNAVAILABLE }),
        ]);
        await expect(result.current.registerVdi.mutateAsync(body)).rejects.toThrow(UNAVAILABLE);
        expect(invalidate).not.toHaveBeenCalled();

        (adminService.registerVdiImage as jest.Mock).mockResolvedValue([
            false,
            new EdhRequestError("Endpoint request timed out", 504),
        ]);
        await expect(result.current.registerVdi.mutateAsync(body)).rejects.toMatchObject({
            status: 504,
        });
        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: adminQk.images() })
        );
    });
});

describe("useEdhAdminSettings", () => {
    it("returns the lease policy from getAdminSettings", async () => {
        (adminService.getAdminSettings as jest.Mock).mockResolvedValue([
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
        const { result } = renderHook(() => useEdhAdminSettings(), {
            wrapper: wrapperFor(newClient()),
        });

        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(result.current.data).toEqual({
            enabled: true,
            leaseHours: 8,
            forced: false,
            graceHours: 24,
            updatedAt: "2026-09-17T10:00:00Z",
            updatedBy: "admin",
        });
    });
});

describe("useUpdateEdhAdminSettings", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("invalidates the settings key on success", async () => {
        (adminService.updateAdminSettings as jest.Mock).mockResolvedValue([
            true,
            { lease: { enabled: true, leaseHours: 12, forced: true, graceHours: 48 } },
        ]);
        const client = newClient();
        const invalidate = jest.spyOn(client, "invalidateQueries");
        const { result } = renderHook(() => useUpdateEdhAdminSettings(), {
            wrapper: wrapperFor(client),
        });

        await result.current.mutateAsync({ leaseHours: 12 });

        expect(adminService.updateAdminSettings).toHaveBeenCalledWith({ leaseHours: 12 });
        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: adminQk.settings() })
        );
    });

    it("rejects with the service message when the update fails", async () => {
        (adminService.updateAdminSettings as jest.Mock).mockResolvedValue([
            false,
            "Invalid lease hours",
        ]);
        const client = newClient();
        const { result } = renderHook(() => useUpdateEdhAdminSettings(), {
            wrapper: wrapperFor(client),
        });

        await expect(result.current.mutateAsync({ leaseHours: 1000 })).rejects.toThrow(
            "Invalid lease hours"
        );
    });
});

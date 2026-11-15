/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as workstationService from "./workstations";
import * as catalogService from "./catalog";
import {
    WORKSTATION_POLL_STABLE_MS,
    WORKSTATION_POLL_TRANSITIONAL_MS,
    computeWorkstationRefetchInterval,
    isTransitionalWorkstation,
    qk,
    useEdhCatalogImages,
    useEdhCatalogLease,
    useEdhInstanceTypes,
    useOsSignIn,
    useWorkstationConnect,
    useWorkstationMutations,
    useWorkstations,
} from "./queries";
import { EdhRequestError } from "./outcome";
import type { WorkstationRecord } from "../types";

jest.mock("./workstations", () => ({
    listWorkstations: jest.fn(),
    createWorkstation: jest.fn(),
    stopWorkstation: jest.fn(),
    startWorkstation: jest.fn(),
    deleteWorkstation: jest.fn(),
    renewWorkstation: jest.fn(),
    getWorkstationConnection: jest.fn(),
    getOsSignIn: jest.fn(),
}));
jest.mock("./catalog", () => ({
    listEdhImages: jest.fn(),
    listEdhInstanceTypes: jest.fn(),
    getEdhLeasePolicy: jest.fn(),
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

const row = (overrides: Partial<WorkstationRecord>): WorkstationRecord => ({
    bootstrapId: "b-1",
    sessionId: "s-1",
    userId: "user@example.com",
    edhUid: "vabcdefghij23456",
    state: "ACTIVE",
    edhState: "running",
    name: "Dev box",
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.xlarge",
    createdAt: "2026-09-08T10:00:00Z",
    updatedAt: "2026-09-08T10:05:00Z",
    connectionAvailable: true,
    ...overrides,
});

const LEASE_POLICY = { enabled: true, leaseHours: 8, forced: false, graceHours: 24 };

describe("computeWorkstationRefetchInterval", () => {
    it("returns false for a missing or empty list", () => {
        expect(computeWorkstationRefetchInterval(undefined)).toBe(false);
        expect(computeWorkstationRefetchInterval([])).toBe(false);
    });

    it("polls every 5 s while a workstation is still creating", () => {
        expect(
            computeWorkstationRefetchInterval([
                row({ state: "CREATING", sessionId: null, connectionAvailable: false }),
            ])
        ).toBe(WORKSTATION_POLL_TRANSITIONAL_MS);
    });

    it("polls every 5 s while an active workstation has no connection yet", () => {
        expect(
            computeWorkstationRefetchInterval([
                row({ state: "ACTIVE", connectionAvailable: false }),
            ])
        ).toBe(5000);
    });

    it("polls every 20 s once every workstation is settled", () => {
        expect(
            computeWorkstationRefetchInterval([
                row({ state: "ACTIVE", connectionAvailable: true }),
                row({ bootstrapId: "b-2", state: "STOPPED", connectionAvailable: false }),
                row({ bootstrapId: "b-3", state: "ORPHANED", connectionAvailable: false }),
            ])
        ).toBe(WORKSTATION_POLL_STABLE_MS);
    });

    it("lets one transitional row set the cadence for the whole list", () => {
        expect(
            computeWorkstationRefetchInterval([
                row({ state: "STOPPED", connectionAvailable: false }),
                row({
                    bootstrapId: "b-2",
                    state: "CREATING",
                    sessionId: null,
                    connectionAvailable: false,
                }),
            ])
        ).toBe(5000);
    });

    it.each(["starting", "stopping"] as const)(
        "polls every 5 s while a stopped row reports the activity %s, and every 20 s once it is cleared",
        (activity) => {
            const stopped = row({
                state: "STOPPED",
                edhState: "stopped",
                connectionAvailable: false,
            });
            expect(computeWorkstationRefetchInterval([{ ...stopped, activity }])).toBe(
                WORKSTATION_POLL_TRANSITIONAL_MS
            );
            expect(computeWorkstationRefetchInterval([{ ...stopped, activity: null }])).toBe(
                WORKSTATION_POLL_STABLE_MS
            );
            // A start that ended stopped with its reason is settled: nothing moves until the user acts.
            expect(
                computeWorkstationRefetchInterval([
                    {
                        ...stopped,
                        activity: null,
                        lastError: "CAPACITY_UNAVAILABLE",
                        startFailure: { observedAt: "2026-10-23T15:19:30Z" },
                    },
                ])
            ).toBe(WORKSTATION_POLL_STABLE_MS);
        }
    );

    it("polls every 20 s for a creating row whose start the sweep found stalled: nothing moves until the user acts", () => {
        const booting = row({
            state: "CREATING",
            edhState: "pending",
            sessionId: "s-1",
            connectionAvailable: false,
        });
        expect(computeWorkstationRefetchInterval([{ ...booting, activity: "starting" }])).toBe(
            WORKSTATION_POLL_TRANSITIONAL_MS
        );
        expect(
            computeWorkstationRefetchInterval([
                {
                    ...booting,
                    activity: null,
                    lastError: "START_STALLED",
                    startFailure: { observedAt: "2026-10-24T11:40:00Z" },
                },
            ])
        ).toBe(WORKSTATION_POLL_STABLE_MS);
    });
});

describe("isTransitionalWorkstation", () => {
    it.each([
        ["STOPPED", "starting", true],
        ["STOPPED", "stopping", true],
        ["STOPPED", null, false],
        ["ACTIVE", "starting", true],
        ["CREATING", "starting", true],
    ] as const)("%s with activity=%s -> %s", (state, activity, expected) => {
        expect(
            isTransitionalWorkstation({ state, connectionAvailable: state === "ACTIVE", activity })
        ).toBe(expected);
    });

    it.each([
        ["CREATING", false, true],
        ["ACTIVE", false, true],
        ["ACTIVE", true, false],
        ["STOPPED", false, false],
        ["DELETED", false, false],
        ["ORPHANED", false, false],
    ] as const)("%s with connectionAvailable=%s -> %s", (state, connectionAvailable, expected) => {
        expect(isTransitionalWorkstation({ state, connectionAvailable })).toBe(expected);
    });

    it("treats a running workstation whose display session ended as settled", () => {
        // ACTIVE and not connectable, but not starting: nothing changes until the user stops it.
        expect(
            isTransitionalWorkstation({
                state: "ACTIVE",
                connectionAvailable: false,
                activity: null,
                connectionUnavailableReason: "DISPLAY_SESSION_ENDED",
            })
        ).toBe(false);
    });

    it("treats a stalled start as settled, and a creating row with any other code as transitional", () => {
        const creating = { state: "CREATING", connectionAvailable: false, activity: null } as const;
        expect(isTransitionalWorkstation({ ...creating, lastError: "START_STALLED" })).toBe(false);
        expect(isTransitionalWorkstation({ ...creating, lastError: null })).toBe(true);
        // The service reports no activity on a stalled row; were it to report one, the row moves.
        expect(
            isTransitionalWorkstation({
                ...creating,
                activity: "starting",
                lastError: "START_STALLED",
            })
        ).toBe(true);
    });
});

describe("qk", () => {
    it("namespaces every key under edh so none collides with the orchestration factory", () => {
        expect(qk.workstations()[0]).toBe("edh");
        expect(qk.catalogImages()[0]).toBe("edh");
        expect(qk.instanceTypes()[0]).toBe("edh");
        expect(qk.catalogLease()[0]).toBe("edh");
    });
});

describe("useWorkstations", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("polls the live query at the cadence the listed rows call for: 5 s, 20 s, or not at all", async () => {
        (workstationService.listWorkstations as jest.Mock).mockResolvedValue([
            true,
            [row({ state: "CREATING", sessionId: null, connectionAvailable: false })],
        ]);
        const client = newClient();
        const { result } = renderHook(() => useWorkstations(), { wrapper: wrapperFor(client) });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(workstationService.listWorkstations).toHaveBeenCalledTimes(1);

        // The cadence is a function of the query's own data, read the way the library calls it. The
        // observer's options sit on the query at run time; the query's static type omits them.
        const query = client.getQueryCache().find({ queryKey: qk.workstations() });
        if (!query) throw new Error("the workstations query is not in the cache");
        const { refetchInterval: interval } = query.options as {
            refetchInterval?: (q: typeof query) => number | false;
        };
        if (!interval) throw new Error("refetchInterval is not set on the live query");
        expect(typeof interval).toBe("function");
        expect(interval(query)).toBe(5000);
        expect(WORKSTATION_POLL_TRANSITIONAL_MS).toBe(5000);

        client.setQueryData(qk.workstations(), [
            row({}),
            row({ bootstrapId: "b-2", state: "STOPPED" }),
        ]);
        expect(interval(query)).toBe(20000);
        expect(WORKSTATION_POLL_STABLE_MS).toBe(20000);

        client.setQueryData(qk.workstations(), []);
        expect(interval(query)).toBe(false);
    });

    it("surfaces a service refusal as the query error", async () => {
        (workstationService.listWorkstations as jest.Mock).mockResolvedValue([false, "Forbidden"]);
        const { result } = renderHook(() => useWorkstations(), {
            wrapper: wrapperFor(newClient()),
        });
        await waitFor(() => expect(result.current.isError).toBe(true));
        expect((result.current.error as Error).message).toBe("Forbidden");
    });
});

describe("useEdhCatalogImages / useEdhInstanceTypes", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("issue nothing while disabled, as the wizard keeps them until it opens", () => {
        const client = newClient();
        const images = renderHook(() => useEdhCatalogImages(false), {
            wrapper: wrapperFor(client),
        });
        const types = renderHook(() => useEdhInstanceTypes(false), { wrapper: wrapperFor(client) });
        expect(images.result.current.fetchStatus).toBe("idle");
        expect(types.result.current.fetchStatus).toBe("idle");
        expect(catalogService.listEdhImages).not.toHaveBeenCalled();
        expect(catalogService.listEdhInstanceTypes).not.toHaveBeenCalled();
    });

    it("resolve the catalog and the instance types through the service once enabled", async () => {
        (catalogService.listEdhImages as jest.Mock).mockResolvedValue([
            true,
            [{ imageId: "42", name: "Ubuntu GPU desktop" }],
        ]);
        (catalogService.listEdhInstanceTypes as jest.Mock).mockResolvedValue([
            true,
            [{ instanceType: "g5.xlarge" }],
        ]);
        const client = newClient();
        const images = renderHook(() => useEdhCatalogImages(), { wrapper: wrapperFor(client) });
        const types = renderHook(() => useEdhInstanceTypes(true), { wrapper: wrapperFor(client) });
        await waitFor(() => expect(images.result.current.isSuccess).toBe(true));
        await waitFor(() => expect(types.result.current.isSuccess).toBe(true));
        expect(images.result.current.data).toEqual([{ imageId: "42", name: "Ubuntu GPU desktop" }]);
        expect(types.result.current.data).toEqual([{ instanceType: "g5.xlarge" }]);
        expect(client.getQueryData(qk.catalogImages())).toHaveLength(1);
        expect(client.getQueryData(qk.instanceTypes())).toHaveLength(1);
    });
});

describe("useEdhCatalogLease", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("issues nothing while disabled, as the wizard keeps it until it opens", () => {
        const { result } = renderHook(() => useEdhCatalogLease(false), {
            wrapper: wrapperFor(newClient()),
        });
        expect(result.current.fetchStatus).toBe("idle");
        expect(catalogService.getEdhLeasePolicy).not.toHaveBeenCalled();
    });

    it("keeps the policy the route wraps, not the wrapper", async () => {
        (catalogService.getEdhLeasePolicy as jest.Mock).mockResolvedValue([
            true,
            { lease: LEASE_POLICY },
        ]);
        const client = newClient();
        const { result } = renderHook(() => useEdhCatalogLease(), { wrapper: wrapperFor(client) });
        await waitFor(() => expect(result.current.isSuccess).toBe(true));
        expect(result.current.data).toEqual(LEASE_POLICY);
        expect(client.getQueryData(qk.catalogLease())).toEqual(LEASE_POLICY);
    });

    it("surfaces a refusal as the query error", async () => {
        (catalogService.getEdhLeasePolicy as jest.Mock).mockResolvedValue([false, "Forbidden"]);
        const { result } = renderHook(() => useEdhCatalogLease(), {
            wrapper: wrapperFor(newClient()),
        });
        await waitFor(() => expect(result.current.isError).toBe(true));
        expect((result.current.error as Error).message).toBe("Forbidden");
    });
});

describe("useWorkstationConnect", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("requests nothing until fired, then resolves the connection for the given session", async () => {
        (workstationService.getWorkstationConnection as jest.Mock).mockResolvedValue([
            true,
            {
                sessionId: "s-1",
                connectionUrl: "https://dcv.example.internal/?t=x",
                osFamily: "linux",
            },
        ]);
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const { result } = renderHook(() => useWorkstationConnect(), {
            wrapper: wrapperFor(client),
        });
        expect(workstationService.getWorkstationConnection).not.toHaveBeenCalled();

        const connection = await result.current.mutateAsync("s-1");

        expect(workstationService.getWorkstationConnection).toHaveBeenCalledWith("s-1");
        expect(connection.connectionUrl).toBe("https://dcv.example.internal/?t=x");
        // Nothing is cached: the URL carries a credential and is asked for per click.
        expect(client.getQueryCache().getAll()).toHaveLength(0);
    });

    it("rejects with the service message when the workstation is not ready", async () => {
        (workstationService.getWorkstationConnection as jest.Mock).mockResolvedValue([
            false,
            "Workstation is not ready to connect",
        ]);
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const { result } = renderHook(() => useWorkstationConnect(), {
            wrapper: wrapperFor(client),
        });

        await expect(result.current.mutateAsync("s-1")).rejects.toThrow(
            "Workstation is not ready to connect"
        );
    });

    it("re-reads the list after a refused connect, so the row shows why", async () => {
        (workstationService.getWorkstationConnection as jest.Mock).mockResolvedValue([
            false,
            "The workstation is running but its remote display session has ended. Stop it, then start it again, to reconnect.",
        ]);
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const invalidate = jest.spyOn(client, "invalidateQueries");
        const { result } = renderHook(() => useWorkstationConnect(), {
            wrapper: wrapperFor(client),
        });

        await expect(result.current.mutateAsync("s-1")).rejects.toThrow(
            /display session has ended/
        );

        expect(invalidate).toHaveBeenCalledWith({ queryKey: ["edh", "workstations"] });
    });
});

describe("useOsSignIn", () => {
    const SIGN_IN = {
        sessionId: "s-6",
        username: "vabcdefghij23456",
        osFamily: "windows",
        credential: "desktopPassword",
        password: "winpw-" + "x".repeat(12),
    };

    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("requests nothing until fired and keeps nothing in the query cache", async () => {
        (workstationService.getOsSignIn as jest.Mock).mockResolvedValue([true, SIGN_IN]);
        const client = newClient();
        const { result } = renderHook(() => useOsSignIn(), {
            wrapper: wrapperFor(client),
        });
        expect(workstationService.getOsSignIn).not.toHaveBeenCalled();

        let signIn: unknown;
        await act(async () => {
            signIn = await result.current.mutateAsync("s-6");
        });

        expect(workstationService.getOsSignIn).toHaveBeenCalledWith("s-6");
        expect(signIn).toEqual(SIGN_IN);
        expect(client.getQueryCache().getAll()).toHaveLength(0);
    });

    it("leaves the mutation cache once no component observes the settled call", async () => {
        (workstationService.getOsSignIn as jest.Mock).mockResolvedValue([true, SIGN_IN]);
        const client = newClient();
        const { result, unmount } = renderHook(() => useOsSignIn(), {
            wrapper: wrapperFor(client),
        });

        act(() => {
            result.current.mutate("s-6");
        });
        await waitFor(() => expect(result.current.data).toEqual(SIGN_IN));
        expect(client.getMutationCache().getAll()).toHaveLength(1);

        unmount();
        await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0));
    });

    it("keeps a call still in flight at unmount only until it settles", async () => {
        let resolve: (value: [boolean, typeof SIGN_IN]) => void = () => undefined;
        (workstationService.getOsSignIn as jest.Mock).mockReturnValue(
            new Promise<[boolean, typeof SIGN_IN]>((settle) => {
                resolve = settle;
            })
        );
        const client = newClient();
        const { result, unmount } = renderHook(() => useOsSignIn(), {
            wrapper: wrapperFor(client),
        });

        act(() => {
            result.current.mutate("s-6");
        });
        await waitFor(() => expect(workstationService.getOsSignIn).toHaveBeenCalled());
        unmount();
        expect(client.getMutationCache().getAll()).toHaveLength(1);

        await act(async () => resolve([true, SIGN_IN]));
        await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0));
    });

    it("rejects with the service message when the sign-in is refused", async () => {
        (workstationService.getOsSignIn as jest.Mock).mockResolvedValue([
            false,
            "Windows password is not available yet",
        ]);
        const { result } = renderHook(() => useOsSignIn(), {
            wrapper: wrapperFor(newClient()),
        });

        await act(async () => {
            await expect(result.current.mutateAsync("s-6")).rejects.toThrow(
                "Windows password is not available yet"
            );
        });
    });

    it("shows a reopened observer of the same workstation nothing of the earlier answer", async () => {
        const client = newClient();
        (workstationService.getOsSignIn as jest.Mock).mockResolvedValueOnce([true, SIGN_IN]);
        const first = renderHook(() => useOsSignIn(), { wrapper: wrapperFor(client) });
        act(() => {
            first.result.current.mutate("s-6");
        });
        await waitFor(() => expect(first.result.current.data).toEqual(SIGN_IN));
        first.unmount();

        // The same workstation again, answering another password: a per-session cache would show the
        // earlier one while this call is pending.
        const later = { ...SIGN_IN, password: "winpw-" + "y".repeat(12) };
        let resolve: (value: [boolean, typeof SIGN_IN]) => void = () => undefined;
        (workstationService.getOsSignIn as jest.Mock).mockReturnValue(
            new Promise<[boolean, typeof SIGN_IN]>((settle) => {
                resolve = settle;
            })
        );
        const second = renderHook(() => useOsSignIn(), { wrapper: wrapperFor(client) });
        expect(second.result.current.data).toBeUndefined();
        act(() => {
            second.result.current.mutate("s-6");
        });
        await waitFor(() => expect(second.result.current.isPending).toBe(true));
        expect(second.result.current.data).toBeUndefined();

        await act(async () => resolve([true, later]));
        await waitFor(() => expect(second.result.current.data?.password).toBe(later.password));
    });
});

describe("useWorkstationMutations", () => {
    const setup = () => {
        const client = newClient();
        const invalidate = jest.spyOn(client, "invalidateQueries");
        const { result } = renderHook(() => useWorkstationMutations(), {
            wrapper: wrapperFor(client),
        });
        return { result, invalidate };
    };

    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("invalidates the workstations list after a stop succeeds", async () => {
        (workstationService.stopWorkstation as jest.Mock).mockResolvedValue([
            true,
            row({ state: "STOPPED", connectionAvailable: false }),
        ]);
        const { result, invalidate } = setup();

        await result.current.stopWorkstation.mutateAsync({ sessionId: "s-1" });

        expect(workstationService.stopWorkstation).toHaveBeenCalledWith("s-1");
        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.workstations() })
        );
    });

    it("invalidates the list even when a launch outcome is unknown", async () => {
        // The launch may have gone through; only a re-read of the list can say.
        (workstationService.createWorkstation as jest.Mock).mockResolvedValue([
            false,
            "The launch request did not receive a reply.",
        ]);
        const { result, invalidate } = setup();

        await expect(
            result.current.createWorkstation.mutateAsync({
                bootstrapId: "11111111-1111-4111-8111-111111111111",
                imageId: "42",
                instanceType: "g5.xlarge",
                name: "Dev box",
                rootSizeGb: 100,
                osFamily: "linux",
            })
        ).rejects.toThrow("The launch request did not receive a reply.");

        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.workstations() })
        );
    });

    it("rejects with the service message and leaves the list alone when a stop is refused", async () => {
        (workstationService.stopWorkstation as jest.Mock).mockResolvedValue([
            false,
            "Workstation not found",
        ]);
        const { result, invalidate } = setup();

        await expect(
            result.current.stopWorkstation.mutateAsync({ sessionId: "s-x" })
        ).rejects.toThrow("Workstation not found");

        expect(invalidate).not.toHaveBeenCalled();
    });

    it.each(["stopWorkstation", "startWorkstation", "deleteWorkstation"] as const)(
        "re-reads the list after a %s EDH did not confirm, and rejects with its status and body",
        async (name) => {
            const body = { message: UNCONFIRMED, outcomeUnknown: true };
            (workstationService[name] as jest.Mock).mockResolvedValue([
                false,
                new EdhRequestError(UNCONFIRMED, 502, body),
            ]);
            const { result, invalidate } = setup();

            await expect(
                result.current[name].mutateAsync({ sessionId: "s-1" })
            ).rejects.toMatchObject({ message: UNCONFIRMED, status: 502, body });

            await waitFor(() =>
                expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.workstations() })
            );
        }
    );

    it("re-reads the list after the gateway's 504 on a start", async () => {
        (workstationService.startWorkstation as jest.Mock).mockResolvedValue([
            false,
            new EdhRequestError("Endpoint request timed out", 504),
        ]);
        const { result, invalidate } = setup();

        await expect(
            result.current.startWorkstation.mutateAsync({ sessionId: "s-1" })
        ).rejects.toMatchObject({ status: 504 });

        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.workstations() })
        );
    });

    it("puts the record a start answers (202) into the cached list at once, then re-reads it", async () => {
        const client = newClient();
        const stopped = row({ state: "STOPPED", edhState: "stopped", connectionAvailable: false });
        const other = row({ bootstrapId: "b-2", sessionId: "s-2", name: "Other box" });
        client.setQueryData(qk.workstations(), [stopped, other]);
        const invalidate = jest.spyOn(client, "invalidateQueries");
        (workstationService.startWorkstation as jest.Mock).mockResolvedValue([
            true,
            { ...stopped, activity: "starting", sessionBootstrap: "provisioned" },
        ]);
        const { result } = renderHook(() => useWorkstationMutations(), {
            wrapper: wrapperFor(client),
        });

        await result.current.startWorkstation.mutateAsync({ sessionId: "s-1" });

        // The badge reads Starting before the next poll answers; the other row is untouched.
        const cached = client.getQueryData<WorkstationRecord[]>(qk.workstations());
        expect(cached?.[0].activity).toBe("starting");
        expect(cached?.[0]).not.toHaveProperty("sessionBootstrap");
        expect(cached?.[1]).toBe(other);
        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.workstations() })
        );
    });

    it("leaves the list alone after a plain 502, which is a failure", async () => {
        (workstationService.startWorkstation as jest.Mock).mockResolvedValue([
            false,
            new EdhRequestError(UNAVAILABLE, 502, { message: UNAVAILABLE }),
        ]);
        const { result, invalidate } = setup();

        await expect(
            result.current.startWorkstation.mutateAsync({ sessionId: "s-1" })
        ).rejects.toThrow(UNAVAILABLE);

        expect(invalidate).not.toHaveBeenCalled();
    });

    it("invalidates the workstations list after a renew succeeds", async () => {
        (workstationService.renewWorkstation as jest.Mock).mockResolvedValue([
            true,
            row({ leaseExpiresAt: "2026-09-18T12:00:00+00:00" }),
        ]);
        const { result, invalidate } = setup();

        await result.current.renewWorkstation.mutateAsync({ sessionId: "s-1" });

        expect(workstationService.renewWorkstation).toHaveBeenCalledWith("s-1");
        await waitFor(() =>
            expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.workstations() })
        );
    });

    it("rejects with the service message and leaves the list alone when a renew is refused", async () => {
        (workstationService.renewWorkstation as jest.Mock).mockResolvedValue([
            false,
            "Workstation has no lease to renew",
        ]);
        const { result, invalidate } = setup();

        await expect(
            result.current.renewWorkstation.mutateAsync({ sessionId: "s-1" })
        ).rejects.toThrow("Workstation has no lease to renew");

        expect(invalidate).not.toHaveBeenCalled();
    });

    it("passes the session id through start and delete", async () => {
        (workstationService.startWorkstation as jest.Mock).mockResolvedValue([true, row({})]);
        (workstationService.deleteWorkstation as jest.Mock).mockResolvedValue([
            true,
            row({ state: "DELETED", connectionAvailable: false }),
        ]);
        const { result } = setup();

        await result.current.startWorkstation.mutateAsync({ sessionId: "s-1" });
        await result.current.deleteWorkstation.mutateAsync({ sessionId: "s-1" });

        expect(workstationService.startWorkstation).toHaveBeenCalledWith("s-1");
        expect(workstationService.deleteWorkstation).toHaveBeenCalledWith("s-1");
    });
});

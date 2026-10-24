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
    LAUNCH_OUTCOME_UNKNOWN_MESSAGE,
    createWorkstation,
    deleteWorkstation,
    getWindowsSignIn,
    getWorkstationConnection,
    isUnknownOutcomeStatus,
    listWorkstations,
    renewWorkstation,
    startWorkstation,
    stopWorkstation,
} from "./workstations";
import { EdhRequestError } from "./outcome";

const LAUNCH_BODY = {
    bootstrapId: "11111111-1111-4111-8111-111111111111",
    imageId: "42",
    instanceType: "g5.xlarge",
    name: "Dev box",
    rootSizeGb: 100,
    osFamily: "linux" as const,
};

const rejection = (message: string, status: number) =>
    Object.assign(new Error(message), { status });

describe("workstations service", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    describe("listWorkstations", () => {
        it("reads addon/edh/workstations and returns the Items array", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({ Items: [{ bootstrapId: "b-1" }] });
            const r = await listWorkstations();
            expect(apiClient.get).toHaveBeenCalledWith("addon/edh/workstations");
            expect(r).toEqual([true, [{ bootstrapId: "b-1" }]]);
        });

        it("returns an empty array when the response carries no Items", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({});
            expect(await listWorkstations()).toEqual([true, []]);
        });

        it("returns the failure tuple when the request is rejected", async () => {
            (apiClient.get as jest.Mock).mockRejectedValue(new Error("Forbidden"));
            expect(await listWorkstations()).toEqual([false, "Forbidden"]);
        });
    });

    describe("createWorkstation", () => {
        it("posts the launch body to addon/edh/workstations and returns the record", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({
                bootstrapId: LAUNCH_BODY.bootstrapId,
                state: "CREATING",
            });
            const r = await createWorkstation(LAUNCH_BODY);
            expect(apiClient.post).toHaveBeenCalledWith("addon/edh/workstations", {
                body: LAUNCH_BODY,
            });
            expect(r).toEqual([true, { bootstrapId: LAUNCH_BODY.bootstrapId, state: "CREATING" }]);
        });

        it.each([502, 504])(
            "reports an unknown outcome for a %i instead of a failure message",
            async (status) => {
                (apiClient.post as jest.Mock).mockRejectedValue(
                    rejection("Gateway Timeout", status)
                );
                expect(await createWorkstation(LAUNCH_BODY)).toEqual([
                    false,
                    LAUNCH_OUTCOME_UNKNOWN_MESSAGE,
                ]);
            }
        );

        it("returns the backend's message for any other rejection", async () => {
            (apiClient.post as jest.Mock).mockRejectedValue(
                rejection("instanceType is not permitted in this deployment", 400)
            );
            expect(await createWorkstation(LAUNCH_BODY)).toEqual([
                false,
                "instanceType is not permitted in this deployment",
            ]);
        });
    });

    describe("isUnknownOutcomeStatus", () => {
        it("names exactly the two gateway statuses that lose the reply", () => {
            expect(isUnknownOutcomeStatus(502)).toBe(true);
            expect(isUnknownOutcomeStatus(504)).toBe(true);
            expect(isUnknownOutcomeStatus(500)).toBe(false);
            expect(isUnknownOutcomeStatus(400)).toBe(false);
            expect(isUnknownOutcomeStatus(undefined)).toBe(false);
        });
    });

    describe("stopWorkstation / startWorkstation", () => {
        it("posts to the stop action path", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ state: "STOPPED" });
            const r = await stopWorkstation("s-1");
            expect(apiClient.post).toHaveBeenCalledWith("addon/edh/workstations/s-1/stop", {});
            expect(r).toEqual([true, { state: "STOPPED" }]);
        });

        it("posts to the start action path", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({ state: "ACTIVE" });
            const r = await startWorkstation("s-1");
            expect(apiClient.post).toHaveBeenCalledWith("addon/edh/workstations/s-1/start", {});
            expect(r).toEqual([true, { state: "ACTIVE" }]);
        });
    });

    describe("stop / start / delete failures", () => {
        it.each([
            ["stopWorkstation", stopWorkstation, "post"],
            ["startWorkstation", startWorkstation, "post"],
            ["deleteWorkstation", deleteWorkstation, "del"],
        ] as const)(
            "%s keeps the status and parsed body of a rejection",
            async (_name, call, verb) => {
                const body = {
                    message: "EDH did not confirm the request in time.",
                    outcomeUnknown: true,
                };
                (apiClient[verb] as jest.Mock).mockRejectedValue(
                    Object.assign(new Error(body.message), { status: 502, body })
                );
                const [ok, err] = await call("s-1");
                expect(ok).toBe(false);
                expect(err).toBeInstanceOf(EdhRequestError);
                expect(err).toMatchObject({ message: body.message, status: 502, body });
            }
        );
    });

    describe("renewWorkstation", () => {
        it("posts to the renew action path and returns the renewed record", async () => {
            (apiClient.post as jest.Mock).mockResolvedValue({
                state: "ACTIVE",
                leaseExpiresAt: "2026-09-18T12:00:00+00:00",
                leaseStoppedAt: null,
            });
            const r = await renewWorkstation("s-1");
            expect(apiClient.post).toHaveBeenCalledWith("addon/edh/workstations/s-1/renew", {});
            expect(r).toEqual([
                true,
                {
                    state: "ACTIVE",
                    leaseExpiresAt: "2026-09-18T12:00:00+00:00",
                    leaseStoppedAt: null,
                },
            ]);
        });

        it("returns the failure tuple when the workstation carries no lease", async () => {
            (apiClient.post as jest.Mock).mockRejectedValue(
                new Error("Workstation has no lease to renew")
            );
            expect(await renewWorkstation("s-1")).toEqual([
                false,
                "Workstation has no lease to renew",
            ]);
        });
    });

    describe("deleteWorkstation", () => {
        it("uses DELETE on addon/edh/workstations/{sessionId}", async () => {
            (apiClient.del as jest.Mock).mockResolvedValue({ state: "DELETED" });
            const r = await deleteWorkstation("s-1");
            expect(apiClient.del).toHaveBeenCalledWith("addon/edh/workstations/s-1", {});
            expect(apiClient.post).not.toHaveBeenCalled();
            expect(r).toEqual([true, { state: "DELETED" }]);
        });
    });

    describe("getWorkstationConnection", () => {
        it("reads addon/edh/workstations/{sessionId}/connect", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue({
                sessionId: "s-1",
                connectionUrl: "https://dcv.example.internal/?authToken=x#s-1",
                osFamily: "linux",
            });
            const r = await getWorkstationConnection("s-1");
            expect(apiClient.get).toHaveBeenCalledWith("addon/edh/workstations/s-1/connect");
            expect(r).toEqual([
                true,
                {
                    sessionId: "s-1",
                    connectionUrl: "https://dcv.example.internal/?authToken=x#s-1",
                    osFamily: "linux",
                },
            ]);
        });
    });

    describe("getWindowsSignIn", () => {
        const signIn = {
            sessionId: "s-1",
            username: "vabcdefghij23456",
            password: "winpw-" + "x".repeat(12),
            directoryAccount: false,
        };

        it("reads addon/edh/workstations/{sessionId}/windows-sign-in", async () => {
            (apiClient.get as jest.Mock).mockResolvedValue(signIn);
            const r = await getWindowsSignIn("s-1");
            expect(apiClient.get).toHaveBeenCalledWith(
                "addon/edh/workstations/s-1/windows-sign-in"
            );
            expect(r).toEqual([true, signIn]);
        });

        it("answers the service message when the sign-in is refused", async () => {
            (apiClient.get as jest.Mock).mockRejectedValue(
                new Error("Windows password is not available yet")
            );
            expect(await getWindowsSignIn("s-1")).toEqual([
                false,
                "Windows password is not available yet",
            ]);
        });
    });
});

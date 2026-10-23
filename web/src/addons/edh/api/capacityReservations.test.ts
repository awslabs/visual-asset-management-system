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
import { listEdhCapacityReservations } from "./catalog";
import {
    listAdminCapacityReservations,
    registerCapacityReservation,
    unregisterCapacityReservation,
    updateCapacityReservation,
} from "./admin";
import { createWorkstation, startWorkstation } from "./workstations";
import {
    EdhRequestError,
    RESERVATION_FULL_LAUNCH_MESSAGE,
    isReservationFull,
    unconfirmedNotice,
} from "./outcome";

const CR_ID = "cr-0123456789abcdef0";

const LAUNCH_BODY = {
    bootstrapId: "11111111-1111-4111-8111-111111111111",
    imageId: "42",
    instanceType: "g5.16xlarge",
    name: "Dev box",
    rootSizeGb: 100,
    osFamily: "linux" as const,
    capacityReservationId: CR_ID,
};

const rejection = (message: string, status: number, body?: unknown) =>
    Object.assign(new Error(message), { status, body });

const FULL_BODY = {
    message: "The capacity reservation has no free capacity",
    code: "RESERVATION_FULL",
};

describe("capacity reservation services", () => {
    beforeEach(() => jest.clearAllMocks());

    it("reads the catalog's reservations without a filter and returns the Items", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({
            Items: [{ capacityReservationId: CR_ID }],
        });
        expect(await listEdhCapacityReservations()).toEqual([
            true,
            [{ capacityReservationId: CR_ID }],
        ]);
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/catalog/capacity-reservations");
        (apiClient.get as jest.Mock).mockResolvedValue({});
        expect(await listEdhCapacityReservations()).toEqual([true, []]);
    });

    it("lists, registers, edits and unregisters through the admin routes, the id encoded", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ Items: [] });
        (apiClient.post as jest.Mock).mockResolvedValue({ capacityReservationId: CR_ID });
        (apiClient.put as jest.Mock).mockResolvedValue({ capacityReservationId: CR_ID });
        (apiClient.del as jest.Mock).mockResolvedValue({ capacityReservationId: CR_ID });

        expect(await listAdminCapacityReservations()).toEqual([true, { Items: [] }]);
        expect(apiClient.get).toHaveBeenCalledWith("addon/edh/admin/capacity-reservations");

        const body = { capacityReservationId: CR_ID, label: "Render pool" };
        await registerCapacityReservation(body);
        expect(apiClient.post).toHaveBeenCalledWith("addon/edh/admin/capacity-reservations", {
            body,
        });

        await updateCapacityReservation(CR_ID, { enabled: false });
        expect(apiClient.put).toHaveBeenCalledWith(
            `addon/edh/admin/capacity-reservations/${CR_ID}`,
            { body: { enabled: false } }
        );

        await unregisterCapacityReservation("cr-x/../y");
        expect(apiClient.del).toHaveBeenCalledWith(
            "addon/edh/admin/capacity-reservations/cr-x%2F..%2Fy"
        );
    });

    it("returns a refused registration's message", async () => {
        (apiClient.post as jest.Mock).mockRejectedValue(
            rejection("Capacity reservation not found in this account and Region", 400)
        );
        expect(
            await registerCapacityReservation({ capacityReservationId: CR_ID, label: "x" })
        ).toEqual([false, "Capacity reservation not found in this account and Region"]);
    });

    it("sends the reservation with the launch body", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue({ bootstrapId: LAUNCH_BODY.bootstrapId });
        await createWorkstation(LAUNCH_BODY);
        expect(apiClient.post).toHaveBeenCalledWith("addon/edh/workstations", {
            body: LAUNCH_BODY,
        });
    });

    it("maps a launch refused RESERVATION_FULL to the wizard's sentence", async () => {
        (apiClient.post as jest.Mock).mockRejectedValue(
            rejection(FULL_BODY.message, 409, FULL_BODY)
        );
        expect(await createWorkstation(LAUNCH_BODY)).toEqual([
            false,
            RESERVATION_FULL_LAUNCH_MESSAGE,
        ]);
    });

    it("passes every other launch refusal through, the reservation's own included", async () => {
        for (const [message, status] of [
            ["The capacity reservation could not be read; try again", 503],
            ["The capacity reservation is not active", 400],
            ["capacityReservationId is not registered", 400],
            ["A workstation with this bootstrapId belongs to another user", 409],
        ] as const) {
            (apiClient.post as jest.Mock).mockRejectedValue(rejection(message, status));
            expect(await createWorkstation(LAUNCH_BODY)).toEqual([false, message]);
        }
    });

    it("keeps a start's RESERVATION_FULL code, and does not read it as unconfirmed", async () => {
        const startFull = {
            message: "The capacity reservation has no free capacity; the workstation stays stopped",
            code: "RESERVATION_FULL",
        };
        (apiClient.post as jest.Mock).mockRejectedValue(
            rejection(startFull.message, 409, startFull)
        );
        const [ok, err] = await startWorkstation("s-1");
        expect(ok).toBe(false);
        expect(err).toBeInstanceOf(EdhRequestError);
        expect(isReservationFull(err)).toBe(true);
        expect(unconfirmedNotice("Start", err)).toBeNull();
    });

    it("recognises only a 409 coded RESERVATION_FULL", () => {
        expect(isReservationFull(new EdhRequestError("x", 409, FULL_BODY))).toBe(true);
        expect(isReservationFull(new EdhRequestError("x", 409, { startInProgress: true }))).toBe(
            false
        );
        expect(isReservationFull(new EdhRequestError("x", 400, FULL_BODY))).toBe(false);
        expect(isReservationFull(null)).toBe(false);
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { EdhAdminInstance } from "../api/admin";
import type { CapacityReservationView } from "../types";

// Route templates as `backend/backend/common/addonApiRoutes.py` declares them.
export const CAPACITY_RESERVATIONS_ROUTE = "/addon/edh/admin/capacity-reservations";
export const CAPACITY_RESERVATION_ROUTE =
    "/addon/edh/admin/capacity-reservations/{capacityReservationId}";
export const ADMIN_INSTANCES_ROUTE = "/addon/edh/admin/instances";

/** What the Reservations tab says the registry is for. */
export const RESERVATIONS_DESCRIPTION =
    "Register EC2 capacity reservations so users can launch workstations into reserved capacity. " +
    "A user sees a reservation when their role allows it and the reservation is enabled, active, " +
    "and in an Availability Zone the cluster can launch into. Launches use on-demand capacity otherwise.";

/** What both admin reservation tables say when nothing is registered. */
export const NO_RESERVATIONS_TEXT = "No reservations registered. Launches use on-demand capacity.";

/** The words the State column shows for the two states VAMS adds to EC2's own. */
const STATE_TEXT: Record<string, string> = {
    notFound: "Not found in EC2",
    unknown: "Unknown (EC2 read failed)",
};

/** A reservation's end date in the reader's locale, or that it has none. */
export const formatEndDate = (iso: string | null | undefined): string => {
    if (!iso) return "No end date";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleString();
};

/** A reservation's state as the admin tables show it; EC2's own states as EC2 names them. */
export const reservationStateText = (state: string): string => STATE_TEXT[state] ?? state;

/**
 * The instance counts of a reservation EC2 still holds open, or null. EC2 keeps the last counts on
 * an expired or cancelled reservation, and they say nothing once it no longer reserves capacity.
 */
const liveCounts = (
    r: Pick<CapacityReservationView, "state" | "availableInstanceCount" | "totalInstanceCount">
): { available: number; total: number } | null =>
    r.state === "active" && r.availableInstanceCount != null && r.totalInstanceCount != null
        ? { available: r.availableInstanceCount, total: r.totalInstanceCount }
        : null;

/** The Reservations table's Free / total cell: `<free>/<total>` for an active reservation, else `—`. */
export const freeOfTotal = (r: CapacityReservationView): string => {
    const counts = liveCounts(r);
    return counts ? `${counts.available}/${counts.total}` : "—";
};

/**
 * The Capacity tab's In use / total cell: `<in use>/<total>` for an active reservation, else `—`.
 * In use is every occupied slot EC2 counts, whether a VAMS workstation holds it or not.
 */
export const inUseOfTotal = (r: CapacityReservationView): string => {
    const counts = liveCounts(r);
    return counts ? `${counts.total - counts.available}/${counts.total}` : "—";
};

/** The slots in use and in total across the active reservations; null when none is active. */
export function reservedSlots(
    reservations: CapacityReservationView[]
): { inUse: number; total: number } | null {
    const live = reservations.map(liveCounts).filter((c): c is NonNullable<typeof c> => !!c);
    if (live.length === 0) return null;
    return live.reduce(
        (sum, c) => ({ inUse: sum.inUse + c.total - c.available, total: sum.total + c.total }),
        { inUse: 0, total: 0 }
    );
}

/** The Reserved slots card: `<in use> of <total> in use` across the active reservations, else `—`. */
export const reservedSlotsText = (reservations: CapacityReservationView[]): string => {
    const slots = reservedSlots(reservations);
    return slots ? `${slots.inUse} of ${slots.total} in use` : "—";
};

/** Whether a session is running or on its way there: ACTIVE, or a start in progress. */
export const isRunningSession = (session: Pick<EdhAdminInstance, "state" | "activity">) =>
    session.state === "ACTIVE" || session.activity === "starting";

/** The running sessions launched into the reservation. */
export const workstationsInReservation = (
    sessions: EdhAdminInstance[],
    capacityReservationId: string
): number =>
    sessions.filter((s) => isRunningSession(s) && s.capacityReservationId === capacityReservationId)
        .length;

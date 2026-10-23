/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { CapacityReservationView, EdhOsFamily, WorkstationRecord } from "./types";

/** The Capacity choice that names no reservation. */
export const ON_DEMAND_LABEL = "On-demand";

/** What the Capacity detail says for a launch that asked for a full reservation and ran on-demand. */
export const ON_DEMAND_FALLBACK_TEXT = "On-demand (reservation full)";

/** The EC2 platform prefix each image OS family launches on; the service applies the same rule. */
const PLATFORM_PREFIX: Record<EdhOsFamily, string> = {
    linux: "Linux/UNIX",
    windows: "Windows",
};

/**
 * Whether a launch of `instanceType` with an image of `osFamily` can go into the reservation: the
 * types are equal and the reservation's platform suits the family. Nothing matches before both are
 * chosen.
 */
export function reservationSuitsLaunch(
    reservation: Pick<CapacityReservationView, "instanceType" | "instancePlatform">,
    instanceType: string,
    osFamily: EdhOsFamily | undefined
): boolean {
    if (!instanceType || !osFamily || reservation.instanceType !== instanceType) return false;
    const prefix = PLATFORM_PREFIX[osFamily];
    return !!prefix && (reservation.instancePlatform ?? "").startsWith(prefix);
}

/** The catalog's reservations a launch of the type and family can go into, in the catalog's order. */
export function matchingReservations(
    reservations: CapacityReservationView[] | undefined,
    instanceType: string,
    osFamily: EdhOsFamily | undefined
): CapacityReservationView[] {
    return (reservations ?? []).filter((r) => reservationSuitsLaunch(r, instanceType, osFamily));
}

export const reservationHasFreeSlot = (reservation: CapacityReservationView): boolean =>
    (reservation.availableInstanceCount ?? 0) >= 1;

/** A full reservation is offered only when its launch falls back to on-demand. */
export const reservationSelectable = (reservation: CapacityReservationView): boolean =>
    reservationHasFreeSlot(reservation) || reservation.fallbackToOnDemand;

/** What a full reservation does with a launch, as the Capacity choice and Review say it. */
const fullSuffix = (reservation: CapacityReservationView) =>
    reservation.fallbackToOnDemand ? "full, will launch on-demand" : "full";

/** The Capacity option's text: the free slots, or what a full reservation does with a launch. */
export function reservationOptionLabel(reservation: CapacityReservationView): string {
    if (reservationHasFreeSlot(reservation)) {
        return `${reservation.label} (${reservation.availableInstanceCount}/${reservation.totalInstanceCount} free)`;
    }
    return `${reservation.label} (${fullSuffix(reservation)})`;
}

/** What Review says about a chosen reservation: its label and id, and that it is full when it is. */
export function reservationReviewText(reservation: CapacityReservationView): string {
    const named = `${reservation.label} (${reservation.capacityReservationId})`;
    return reservationHasFreeSlot(reservation) ? named : `${named}, ${fullSuffix(reservation)}`;
}

/** The reservation preselected: the first with a free slot, else on-demand (`""`). */
export function preselectReservation(matching: CapacityReservationView[]): string {
    return matching.find(reservationHasFreeSlot)?.capacityReservationId ?? "";
}

/**
 * The Capacity detail of a workstation: the reservation it launched into, by label and id (the id
 * alone once unregistered), that it ran on-demand because the reservation was full, or nothing for
 * an on-demand launch. My Workstations and the admin Sessions tab both show it.
 */
export function describeCapacity(
    row: Partial<
        Pick<
            WorkstationRecord,
            "capacityReservationId" | "capacityReservationLabel" | "capacityReservationFallback"
        >
    >
): string | null {
    if (row.capacityReservationFallback) return ON_DEMAND_FALLBACK_TEXT;
    if (!row.capacityReservationId) return null;
    return row.capacityReservationLabel
        ? `${row.capacityReservationLabel} (${row.capacityReservationId})`
        : row.capacityReservationId;
}

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { CapacityReservationView, EdhOsFamily, WorkstationRecord } from "./types";

/** The Capacity choice that names no reservation. */
export const ON_DEMAND_LABEL = "On-demand";

/** What the Capacity detail says for a launch that asked for a full reservation and ran on-demand. */
export const ON_DEMAND_FALLBACK_TEXT = "On-demand (reservation full)";

/**
 * What the Capacity detail says for a stopped workstation VAMS moved to on-demand because its
 * reservation ended: the reservation by label, else by id, else unnamed.
 */
export const onDemandEndedText = (reservation?: string | null): string =>
    reservation ? `On-demand (reservation ${reservation} ended)` : "On-demand (reservation ended)";

/** What the launch wizard's Capacity option says of a reservation the role may see but not use. */
export const VIEW_ONLY_SUFFIX = "view only";

/** The hint's sentence when the Capacity choice lists a view-only reservation. */
export const VIEW_ONLY_HINT =
    "A reservation marked (view only) is one your role may see but not launch into; ask an administrator to grant launching into it.";

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

/**
 * Whether EDH has no private subnet in the reservation's Availability Zone, so it cannot launch
 * into it. Unknown (null or absent) is not a known lack.
 */
const lacksEdhSubnet = (reservation: Pick<CapacityReservationView, "edhSubnetAvailable">) =>
    reservation.edhSubnetAvailable === false;

/**
 * The catalog's reservations a launch of the type and family can go into, in the catalog's order.
 * One in an Availability Zone with no EDH subnet is left out: the service lists none, and refuses a
 * launch into one.
 */
export function matchingReservations(
    reservations: CapacityReservationView[] | undefined,
    instanceType: string,
    osFamily: EdhOsFamily | undefined
): CapacityReservationView[] {
    return (reservations ?? []).filter(
        (r) => !lacksEdhSubnet(r) && reservationSuitsLaunch(r, instanceType, osFamily)
    );
}

export const reservationHasFreeSlot = (reservation: CapacityReservationView): boolean =>
    (reservation.availableInstanceCount ?? 0) >= 1;

/**
 * Whether the user's role may launch into the reservation, not only see it. A catalog item from a
 * service that predates `launchAllowed` is launchable, as it always was; the launch decides.
 */
export const reservationLaunchAllowed = (reservation: CapacityReservationView): boolean =>
    reservation.launchAllowed !== false;

/**
 * A reservation is offered only when the role may launch into it, and, when full, only when its
 * launch falls back to on-demand.
 */
export const reservationSelectable = (reservation: CapacityReservationView): boolean =>
    reservationLaunchAllowed(reservation) &&
    (reservationHasFreeSlot(reservation) || reservation.fallbackToOnDemand);

/** What a full reservation does with a launch, as the Capacity choice and Review say it. */
const fullSuffix = (reservation: CapacityReservationView) =>
    reservation.fallbackToOnDemand ? "full, will launch on-demand" : "full";

/**
 * The Capacity option's text: that the role may only view the reservation, else its free slots, or
 * what a full reservation does with a launch. A disabled option always says why.
 */
export function reservationOptionLabel(reservation: CapacityReservationView): string {
    if (!reservationLaunchAllowed(reservation)) {
        return `${reservation.label} (${VIEW_ONLY_SUFFIX})`;
    }
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

/**
 * The reservation preselected: the first with a free slot the role may launch into, else on-demand
 * (`""`). A view-only reservation is never preselected, whatever its free slots.
 */
export function preselectReservation(matching: CapacityReservationView[]): string {
    return (
        matching.find((r) => reservationLaunchAllowed(r) && reservationHasFreeSlot(r))
            ?.capacityReservationId ?? ""
    );
}

/**
 * The Settings table's note on a reservation EDH cannot launch into for want of a private subnet in
 * its Availability Zone (`No EDH subnet in <az>`); null when EDH has one, or when VAMS could not
 * read EDH's subnets. `vamscli edh admin capacity-reservation list` prints the same note.
 */
export function edhSubnetNote(
    reservation: Pick<CapacityReservationView, "edhSubnetAvailable" | "availabilityZone">
): string | null {
    if (!lacksEdhSubnet(reservation)) return null;
    return `No EDH subnet in ${reservation.availabilityZone || "its Availability Zone"}`;
}

/** The record fields the Capacity detail is read from. */
export type CapacityDetailFields = Partial<
    Pick<
        WorkstationRecord,
        | "capacityReservationId"
        | "capacityReservationLabel"
        | "capacityReservationFallback"
        | "capacityReservationFallbackReason"
        | "capacityReservationRequested"
    >
>;

/**
 * The Capacity detail of a workstation: the reservation it launched into, by label and id (the id
 * alone once unregistered); that it ran on-demand because the reservation was full; that VAMS moved
 * it to on-demand because its reservation ended (`On-demand (reservation <label or id> ended)`); or
 * nothing for an on-demand launch. A fallback without a reason predates the field and was a full
 * reservation's. My Workstations and the admin Sessions tab both show it, and `vamscli edh
 * workstation get` prints the same (capacityDisplayCases.json).
 */
export function describeCapacity(row: CapacityDetailFields): string | null {
    if (row.capacityReservationFallback) {
        return row.capacityReservationFallbackReason === "ended"
            ? onDemandEndedText(row.capacityReservationLabel || row.capacityReservationRequested)
            : ON_DEMAND_FALLBACK_TEXT;
    }
    if (!row.capacityReservationId) return null;
    return row.capacityReservationLabel
        ? `${row.capacityReservationLabel} (${row.capacityReservationId})`
        : row.capacityReservationId;
}

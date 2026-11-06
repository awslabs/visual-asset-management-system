/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    ON_DEMAND_FALLBACK_TEXT,
    describeCapacity,
    edhSubnetNote,
    matchingReservations,
    preselectReservation,
    reservationOptionLabel,
    reservationSelectable,
    reservationSuitsLaunch,
} from "./capacityReservations";
import type { CapacityReservationView } from "./types";

const view = (overrides: Partial<CapacityReservationView> = {}): CapacityReservationView => ({
    capacityReservationId: "cr-0123456789abcdef0",
    label: "Render pool",
    fallbackToOnDemand: true,
    enabled: true,
    instanceType: "g5.16xlarge",
    availabilityZone: "us-east-1a",
    instancePlatform: "Linux/UNIX",
    state: "active",
    totalInstanceCount: 4,
    availableInstanceCount: 3,
    instanceMatchCriteria: "targeted",
    endDate: null,
    reservationType: "default",
    ...overrides,
});

describe("reservationSuitsLaunch", () => {
    it("matches the instance type exactly and the platform by the image's OS family", () => {
        expect(reservationSuitsLaunch(view(), "g5.16xlarge", "linux")).toBe(true);
        expect(reservationSuitsLaunch(view(), "g5.12xlarge", "linux")).toBe(false);
        expect(reservationSuitsLaunch(view(), "g5.16xlarge", "windows")).toBe(false);
        const windows = view({ instancePlatform: "Windows with SQL Server Standard" });
        expect(reservationSuitsLaunch(windows, "g5.16xlarge", "windows")).toBe(true);
        expect(reservationSuitsLaunch(windows, "g5.16xlarge", "linux")).toBe(false);
        // EC2 names RHEL and SUSE platforms apart from Linux/UNIX, so they do not suit.
        expect(
            reservationSuitsLaunch(
                view({ instancePlatform: "Red Hat Enterprise Linux" }),
                "g5.16xlarge",
                "linux"
            )
        ).toBe(false);
    });

    it("matches nothing before an instance type and an image are chosen", () => {
        expect(reservationSuitsLaunch(view(), "", "linux")).toBe(false);
        expect(reservationSuitsLaunch(view(), "g5.16xlarge", undefined)).toBe(false);
        expect(
            reservationSuitsLaunch(view({ instancePlatform: null }), "g5.16xlarge", "linux")
        ).toBe(false);
    });
});

describe("the Capacity choices", () => {
    const free = view({ capacityReservationId: "cr-00000000000000001", label: "Free" });
    const fullFallback = view({
        capacityReservationId: "cr-00000000000000002",
        label: "Full fallback",
        availableInstanceCount: 0,
    });
    const fullStrict = view({
        capacityReservationId: "cr-00000000000000003",
        label: "Full strict",
        availableInstanceCount: 0,
        fallbackToOnDemand: false,
    });
    const otherType = view({
        capacityReservationId: "cr-00000000000000004",
        label: "Other type",
        instanceType: "g6e.xlarge",
    });

    it("labels each reservation by its free slots, or what a full one will do", () => {
        expect(reservationOptionLabel(free)).toBe("Free (3/4 free)");
        expect(reservationOptionLabel(fullFallback)).toBe(
            "Full fallback (full, will launch on-demand)"
        );
        expect(reservationOptionLabel(fullStrict)).toBe("Full strict (full)");
    });

    it("offers a full reservation only when it falls back to on-demand", () => {
        expect(reservationSelectable(free)).toBe(true);
        expect(reservationSelectable(fullFallback)).toBe(true);
        expect(reservationSelectable(fullStrict)).toBe(false);
    });

    it("filters by type and family, keeping the catalog's order", () => {
        expect(
            matchingReservations(
                [fullStrict, otherType, free, fullFallback],
                "g5.16xlarge",
                "linux"
            ).map((r) => r.label)
        ).toEqual(["Full strict", "Free", "Full fallback"]);
        expect(matchingReservations(undefined, "g5.16xlarge", "linux")).toEqual([]);
    });

    it("preselects the first reservation with a free slot, else on-demand", () => {
        expect(preselectReservation([fullFallback, free])).toBe(free.capacityReservationId);
        expect(preselectReservation([fullFallback, fullStrict])).toBe("");
        expect(preselectReservation([])).toBe("");
    });

    const viewOnly = view({
        capacityReservationId: "cr-00000000000000005",
        label: "Viewers",
        launchAllowed: false,
    });
    const noSubnet = view({
        capacityReservationId: "cr-00000000000000006",
        label: "No subnet",
        availabilityZone: "us-east-1f",
        edhSubnetAvailable: false,
    });

    it("labels a view-only reservation so and never offers or preselects it", () => {
        expect(reservationOptionLabel(viewOnly)).toBe("Viewers (view only)");
        expect(reservationSelectable(viewOnly)).toBe(false);
        expect(preselectReservation([viewOnly, free])).toBe(free.capacityReservationId);
        expect(preselectReservation([viewOnly])).toBe("");
        // A catalog item from a service that predates the field is launchable, as it always was.
        expect(reservationSelectable(view({ launchAllowed: undefined }))).toBe(true);
    });

    it("leaves out a reservation whose Availability Zone has no EDH subnet, keeping an unknown one", () => {
        const unknown = view({
            capacityReservationId: "cr-00000000000000007",
            edhSubnetAvailable: null,
        });
        expect(
            matchingReservations([noSubnet, unknown, free], "g5.16xlarge", "linux").map(
                (r) => r.capacityReservationId
            )
        ).toEqual([unknown.capacityReservationId, free.capacityReservationId]);
    });

    it("notes a reservation whose Availability Zone has no EDH subnet", () => {
        expect(edhSubnetNote(noSubnet)).toBe("No EDH subnet in us-east-1f");
        expect(edhSubnetNote(view({ availabilityZone: null, edhSubnetAvailable: false }))).toBe(
            "No EDH subnet in its Availability Zone"
        );
        expect(edhSubnetNote(view({ edhSubnetAvailable: null }))).toBeNull();
        expect(edhSubnetNote(view({ edhSubnetAvailable: true }))).toBeNull();
        expect(edhSubnetNote(view())).toBeNull();
    });
});

describe("describeCapacity", () => {
    it("names the reservation a launch was sent into, by label and id", () => {
        expect(
            describeCapacity({
                capacityReservationId: "cr-0123456789abcdef0",
                capacityReservationLabel: "Render pool",
            })
        ).toBe("Render pool (cr-0123456789abcdef0)");
    });

    it("falls back to the id for a reservation unregistered since", () => {
        expect(
            describeCapacity({
                capacityReservationId: "cr-0123456789abcdef0",
                capacityReservationLabel: null,
            })
        ).toBe("cr-0123456789abcdef0");
    });

    it("says a full reservation's launch ran on-demand", () => {
        expect(ON_DEMAND_FALLBACK_TEXT).toBe("On-demand (reservation full)");
        expect(
            describeCapacity({
                capacityReservationId: null,
                capacityReservationLabel: null,
                capacityReservationFallback: true,
            })
        ).toBe(ON_DEMAND_FALLBACK_TEXT);
    });

    it("says a stopped workstation whose reservation ended was moved to on-demand", () => {
        const ended = {
            capacityReservationId: null,
            capacityReservationFallback: true,
            capacityReservationFallbackReason: "ended",
        };
        expect(
            describeCapacity({
                ...ended,
                capacityReservationLabel: "Render pool",
                capacityReservationRequested: "cr-0123456789abcdef0",
            })
        ).toBe("On-demand (reservation Render pool ended)");
        expect(
            describeCapacity({
                ...ended,
                capacityReservationLabel: null,
                capacityReservationRequested: "cr-0123456789abcdef0",
            })
        ).toBe("On-demand (reservation cr-0123456789abcdef0 ended)");
        expect(describeCapacity(ended)).toBe("On-demand (reservation ended)");
        // A fallback row without the reason predates it and was a full reservation's.
        expect(
            describeCapacity({
                capacityReservationFallback: true,
                capacityReservationFallbackReason: "full",
            })
        ).toBe(ON_DEMAND_FALLBACK_TEXT);
    });

    it("says nothing for an on-demand launch", () => {
        expect(describeCapacity({})).toBeNull();
        expect(
            describeCapacity({
                capacityReservationId: null,
                capacityReservationLabel: null,
                capacityReservationFallback: false,
            })
        ).toBeNull();
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CapacityTab, {
    instanceTypesByFamily,
    profileInstanceTypes,
    profileInstanceTypesByArchitecture,
} from "./CapacityTab";
import * as adminQueries from "../api/adminQueries";
import type { EdhAdminInstance, EdhDesktopProfile } from "../api/admin";
import type { CapacityReservationView } from "../types";

jest.mock("../api/adminQueries", () => ({
    useEdhCapacity: jest.fn(),
    useEdhAdminInstances: jest.fn(),
    useEdhAdminCapacityReservations: jest.fn(),
}));

// A profile's instance types as EDH lists them: a JSON string keyed by architecture, with an empty group.
const GROUPED = '{"x86_64":["m7i.large","g5.xlarge"],"arm64":["m7g.large"],"i386":[]}';

const SESSIONS_ROUTE = "/addon/edh/admin/instances";
const RESERVATIONS_ROUTE = "/addon/edh/admin/capacity-reservations";

const profile = (fields: Partial<EdhDesktopProfile> = {}): EdhDesktopProfile => ({
    id: 1,
    ...fields,
});

const query = (data: any, overrides: any = {}) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
    ...overrides,
});

const reservation = (fields: Partial<CapacityReservationView>): CapacityReservationView => ({
    capacityReservationId: "cr-00000000000000001",
    label: "Render pool",
    fallbackToOnDemand: true,
    enabled: true,
    instanceType: "g5.xlarge",
    availabilityZone: "us-east-1a",
    instancePlatform: "Linux/UNIX",
    state: "active",
    totalInstanceCount: 4,
    availableInstanceCount: 1,
    instanceMatchCriteria: "targeted",
    endDate: null,
    reservationType: "default",
    ...fields,
});

const RENDER_POOL = reservation({});
// EC2 keeps an ended reservation's last counts; they are not shown or summed.
const ENDED = reservation({
    capacityReservationId: "cr-00000000000000002",
    label: "Ended pool",
    instanceType: "m7i.large",
    state: "expired",
    totalInstanceCount: 2,
    availableInstanceCount: 2,
});
const SMALL_POOL = reservation({
    capacityReservationId: "cr-00000000000000003",
    label: "Small pool",
    instanceType: "m7i.large",
    totalInstanceCount: 2,
    availableInstanceCount: 2,
});

const session = (fields: Partial<EdhAdminInstance>): EdhAdminInstance => ({
    sessionId: "s-1",
    edhState: "running",
    owner: "vowner1",
    name: "desk",
    createdAt: null,
    updatedAt: null,
    userId: "alice",
    bootstrapId: "b-1",
    state: "ACTIVE",
    activity: null,
    lastError: null,
    imageId: "7",
    osFamily: "linux",
    instanceType: "g5.xlarge",
    ...fields,
});

const SESSIONS = [
    session({ sessionId: "s-1", capacityReservationId: RENDER_POOL.capacityReservationId }),
    // A start in progress counts as running.
    session({
        sessionId: "s-2",
        state: "STOPPED",
        activity: "starting",
        capacityReservationId: RENDER_POOL.capacityReservationId,
    }),
    // A stopped desktop launched into the reservation holds no slot.
    session({
        sessionId: "s-3",
        state: "STOPPED",
        edhState: "stopped",
        capacityReservationId: RENDER_POOL.capacityReservationId,
    }),
    session({ sessionId: "s-4", instanceType: "m7i.large" }),
    session({ sessionId: "s-5", instanceType: "t3.large" }),
];

const CAPACITY = {
    profiles: [
        profile({ id: 1, profile_name: "grouped", allowed_instance_types: GROUPED }),
        profile({
            id: 2,
            profile_name: "listed",
            allowed_instance_types: "m5.large,m5.xlarge",
            allowed_subnet_ids: "subnet-00000abcdefabcdef,subnet-00000bcdefabcdefa",
        }),
        profile({ id: 3, profile_name: "unset" }),
    ],
    totalSessions: 5,
    byState: { running: 4, stopped: 1 },
    distinctOwners: 2,
};

function mount(
    can: (method: string, route: string) => boolean = () => true,
    {
        sessions = query(SESSIONS),
        reservations = query([RENDER_POOL, ENDED, SMALL_POOL]),
        capacity = query(CAPACITY),
    }: { sessions?: any; reservations?: any; capacity?: any } = {}
) {
    (adminQueries.useEdhCapacity as jest.Mock).mockReturnValue(capacity);
    (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(sessions);
    (adminQueries.useEdhAdminCapacityReservations as jest.Mock).mockReturnValue(reservations);
    const onManageReservations = jest.fn();
    render(<CapacityTab can={can} onManageReservations={onManageReservations} />);
    return { onManageReservations };
}

const tile = (label: string) =>
    screen.getByRole("group", { name: label }).lastElementChild as HTMLElement;

/** The cells of a table as {first-column text: {header: cell}}. */
const tableCells = (name: string) => {
    const table = screen.getByRole("table", { name });
    const headers = within(table)
        .getAllByRole("columnheader")
        .map((header) => header.textContent?.trim() ?? "");
    const rows: Record<string, Record<string, HTMLElement>> = {};
    for (const row of within(table).getAllByRole("row").slice(1)) {
        const cells = within(row).getAllByRole("cell");
        rows[cells[0].textContent ?? ""] = Object.fromEntries(
            headers.map((header, i) => [header, cells[i]])
        );
    }
    return { headers, rows };
};

describe("profileInstanceTypesByArchitecture", () => {
    it("names each architecture that lists a type, in the value's key order, and skips empty groups", () => {
        expect(
            profileInstanceTypesByArchitecture(profile({ allowed_instance_types: GROUPED }))
        ).toEqual([
            { architecture: "x86_64", instanceTypes: ["m7i.large", "g5.xlarge"] },
            { architecture: "arm64", instanceTypes: ["m7g.large"] },
        ]);
        // Not sorted: i386 follows x86_64, as the value lists them.
        expect(
            profileInstanceTypesByArchitecture(
                profile({ allowed_instance_types: '{"x86_64":["t3.large"],"i386":["t2.micro"]}' })
            )
        ).toEqual([
            { architecture: "x86_64", instanceTypes: ["t3.large"] },
            { architecture: "i386", instanceTypes: ["t2.micro"] },
        ]);
    });

    it("reads a mapping as it reads the JSON string", () => {
        expect(
            profileInstanceTypesByArchitecture(
                profile({ allowed_instance_types: { arm64: ["m7g.large"], x86_64: [] } })
            )
        ).toEqual([{ architecture: "arm64", instanceTypes: ["m7g.large"] }]);
    });

    it.each<[string, EdhDesktopProfile["allowed_instance_types"]]>([
        ["a comma-separated string", "m5.large, m5.xlarge"],
        ["a list", ["m5.large", "m5.xlarge"]],
        ["a JSON list", '["m5.large","m5.xlarge"]'],
    ])("gives one group of no architecture for %s", (_case, allowed) => {
        expect(
            profileInstanceTypesByArchitecture(profile({ allowed_instance_types: allowed }))
        ).toEqual([{ architecture: null, instanceTypes: ["m5.large", "m5.xlarge"] }]);
    });

    it("gives the pattern EDH derived the types from when no group lists one", () => {
        expect(
            profileInstanceTypesByArchitecture(
                profile({
                    allowed_instance_types: '{"x86_64":[],"arm64":[]}',
                    pattern_allowed_instance_types: "m5.large,m5.xlarge",
                })
            )
        ).toEqual([{ architecture: null, instanceTypes: ["m5.large", "m5.xlarge"] }]);
    });
});

describe("profileInstanceTypes", () => {
    it("lists the types of every group", () => {
        expect(profileInstanceTypes(profile({ allowed_instance_types: GROUPED }))).toEqual([
            "m7i.large",
            "g5.xlarge",
            "m7g.large",
        ]);
    });
});

describe("instanceTypesByFamily", () => {
    it("puts the GPU families first and each family's sizes in size order", () => {
        expect(
            instanceTypesByFamily([
                "m7i.2xlarge",
                "g6e.12xlarge",
                "c7i.large",
                "p5.48xlarge",
                "g6e.xlarge",
                "m7i.metal-24xl",
                "gr6.4xlarge",
                "m7i.large",
                "g6e.2xlarge",
                "g5.48xlarge",
                "m7i.metal-48xl",
                "m7i.medium",
                "g6e.metal",
                "g6e.24xlarge",
                "m7i.xlarge",
            ])
        ).toEqual([
            { family: "g5", sizes: ["48xlarge"] },
            { family: "g6e", sizes: ["xlarge", "2xlarge", "12xlarge", "24xlarge", "metal"] },
            { family: "gr6", sizes: ["4xlarge"] },
            { family: "p5", sizes: ["48xlarge"] },
            { family: "c7i", sizes: ["large"] },
            {
                family: "m7i",
                sizes: ["medium", "large", "xlarge", "2xlarge", "metal-24xl", "metal-48xl"],
            },
        ]);
    });

    it("orders the families by number within their part and keeps a name without a size", () => {
        expect(
            instanceTypesByFamily(["m10.large", "m7i.large", "m5", "g10.xlarge", "g4dn.xlarge"])
        ).toEqual([
            { family: "g4dn", sizes: ["xlarge"] },
            { family: "g10", sizes: ["xlarge"] },
            { family: "m5", sizes: [] },
            { family: "m7i", sizes: ["large"] },
            { family: "m10", sizes: ["large"] },
        ]);
    });
});

describe("CapacityTab", () => {
    beforeEach(() => jest.clearAllMocks());

    it("sums sessions, running workstations, owners, reservations and the reserved slots in use", () => {
        mount();
        expect(tile("Total sessions")).toHaveTextContent("5");
        // Two ACTIVE, one starting; the stopped one is not running.
        expect(tile("Running")).toHaveTextContent(/^4$/);
        expect(tile("Distinct owners")).toHaveTextContent("2");
        expect(tile("Registered reservations")).toHaveTextContent("3");
        // Render pool: 3 of 4 in use; Small pool: 0 of 2; the expired pool's last counts are left out.
        expect(tile("Reserved slots")).toHaveTextContent("3 of 6 in use");
        expect(screen.getByText("Running: 4")).toBeInTheDocument();
        expect(screen.getByText("Stopped: 1")).toBeInTheDocument();
    });

    it("lists each reservation with EC2's in-use count and the VAMS workstations running in it", () => {
        mount();
        const { headers, rows } = tableCells("Capacity reservation use");
        expect(headers).toEqual([
            "Label",
            "Instance type",
            "Availability Zone",
            "State",
            "In use / total",
            "VAMS workstations",
            "End date",
            "Enabled",
        ]);
        expect(rows["Render pool"]["In use / total"]).toHaveTextContent("3/4");
        // The ACTIVE row and the start in progress; the stopped one launched into it holds no slot.
        expect(rows["Render pool"]["VAMS workstations"]).toHaveTextContent(/^2$/);
        expect(rows["Small pool"]["In use / total"]).toHaveTextContent("0/2");
        expect(rows["Small pool"]["VAMS workstations"]).toHaveTextContent(/^0$/);
        expect(rows["Ended pool"]["State"]).toHaveTextContent("expired");
        expect(rows["Ended pool"]["In use / total"]).toHaveTextContent(/^—$/);
        expect(rows["Ended pool"]["End date"]).toHaveTextContent("No end date");
    });

    it("counts the running workstations by instance type and names the active reservation of each", () => {
        mount();
        const { headers, rows } = tableCells("Running workstations by instance type");
        expect(headers).toEqual(["Instance type", "Running", "Active reservation"]);
        expect(Object.keys(rows)).toEqual(["g5.xlarge", "m7i.large", "t3.large"]);
        expect(rows["g5.xlarge"]["Running"]).toHaveTextContent(/^2$/);
        expect(rows["g5.xlarge"]["Active reservation"]).toHaveTextContent(
            "Render pool (3/4 in use)"
        );
        // The expired reservation of the type covers nothing.
        expect(rows["m7i.large"]["Active reservation"]).toHaveTextContent(
            "Small pool (0/2 in use)"
        );
        expect(rows["t3.large"]["Active reservation"]).toHaveTextContent("No active reservation");
    });

    it("opens the Reservations tab from Manage reservations", async () => {
        const user = userEvent.setup();
        const { onManageReservations } = mount();
        await user.click(screen.getByRole("button", { name: "Manage reservations" }));
        expect(onManageReservations).toHaveBeenCalledTimes(1);
    });

    it("says when nothing is registered", () => {
        mount(() => true, { reservations: query([]) });
        expect(
            screen.getByText("No reservations registered. Launches use on-demand capacity.")
        ).toBeInTheDocument();
        expect(tile("Registered reservations")).toHaveTextContent("0");
        expect(tile("Reserved slots")).toHaveTextContent("—");
        expect(screen.queryByRole("table", { name: "Capacity reservation use" })).toBeNull();
    });

    it("leaves out every reservation part for a role that may not list reservations", () => {
        mount((method, route) => route !== RESERVATIONS_ROUTE);
        expect(adminQueries.useEdhAdminCapacityReservations).toHaveBeenLastCalledWith(false);
        expect(screen.queryByRole("group", { name: "Registered reservations" })).toBeNull();
        expect(screen.queryByRole("group", { name: "Reserved slots" })).toBeNull();
        expect(screen.queryByRole("heading", { name: "Capacity reservations" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Manage reservations" })).toBeNull();
        expect(tableCells("Running workstations by instance type").headers).toEqual([
            "Instance type",
            "Running",
        ]);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("leaves out every session part for a role that may not list sessions", () => {
        mount((method, route) => route !== SESSIONS_ROUTE);
        expect(adminQueries.useEdhAdminInstances).toHaveBeenLastCalledWith(false);
        expect(screen.queryByRole("group", { name: "Running" })).toBeNull();
        expect(
            screen.queryByRole("heading", { name: "Running workstations by instance type" })
        ).toBeNull();
        expect(tableCells("Capacity reservation use").headers).not.toContain("VAMS workstations");
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("reports a failed reservation read in its own part and keeps the rest", () => {
        mount(() => true, {
            reservations: query(undefined, { error: new Error("EC2 could not be read") }),
        });
        expect(screen.getByRole("alert")).toHaveTextContent(
            "Error loading capacity reservations: EC2 could not be read"
        );
        expect(tile("Registered reservations")).toHaveTextContent("—");
        expect(tile("Total sessions")).toHaveTextContent("5");
        expect(
            tableCells("Running workstations by instance type").rows["g5.xlarge"][
                "Active reservation"
            ]
        ).toHaveTextContent("—");
        expect(screen.getByRole("table", { name: "Virtual desktop profiles" })).toBeInTheDocument();
    });

    it("reports a failed session read in its own part and keeps the rest", () => {
        mount(() => true, { sessions: query(undefined, { error: new Error("boom") }) });
        expect(screen.getByRole("alert")).toHaveTextContent("Error loading sessions: boom");
        expect(tile("Running")).toHaveTextContent("—");
        expect(
            tableCells("Capacity reservation use").rows["Render pool"]["VAMS workstations"]
        ).toHaveTextContent("—");
        expect(tile("Reserved slots")).toHaveTextContent("3 of 6 in use");
    });

    it("reports a failed capacity read in its own part and keeps the reservations", () => {
        mount(() => true, { capacity: query(undefined, { error: new Error("EDH refused") }) });
        expect(screen.getByRole("alert")).toHaveTextContent("Error loading capacity: EDH refused");
        expect(tile("Total sessions")).toHaveTextContent("—");
        expect(screen.queryByRole("table", { name: "Virtual desktop profiles" })).toBeNull();
        expect(screen.getByRole("table", { name: "Capacity reservation use" })).toBeInTheDocument();
    });

    it("refreshes every part it reads from the one button", async () => {
        const user = userEvent.setup();
        const parts = {
            capacity: query(CAPACITY),
            sessions: query(SESSIONS),
            reservations: query([RENDER_POOL]),
        };
        mount(() => true, parts);
        await user.click(screen.getByRole("button", { name: "Refresh capacity" }));
        expect(parts.capacity.refetch).toHaveBeenCalled();
        expect(parts.sessions.refetch).toHaveBeenCalled();
        expect(parts.reservations.refetch).toHaveBeenCalled();
    });

    describe("the virtual desktop profiles", () => {
        /** The `header` cell of the profile named `name`. */
        const profileCell = (name: string, header: string) =>
            tableCells("Virtual desktop profiles").rows[
                String(CAPACITY.profiles.find((p) => p.profile_name === name)?.id)
            ][header];

        it("shows a count of the allowed types per architecture, collapsed", () => {
            mount();
            const grouped = within(profileCell("grouped", "Allowed instance types"));
            expect(grouped.getByRole("button", { name: "x86_64: 2 types" })).toHaveAttribute(
                "aria-expanded",
                "false"
            );
            expect(grouped.getByRole("button", { name: "arm64: 1 type" })).toBeInTheDocument();
            expect(grouped.queryByText("m7i")).not.toBeInTheDocument();
            expect(
                within(profileCell("listed", "Allowed instance types")).getByRole("button", {
                    name: "2 types",
                })
            ).toBeInTheDocument();
            expect(profileCell("unset", "Allowed instance types")).toHaveTextContent(/^—$/);
        });

        it("expands an architecture into its types by family, GPU first, by keyboard", async () => {
            const user = userEvent.setup();
            mount();
            const button = within(profileCell("grouped", "Allowed instance types")).getByRole(
                "button",
                { name: "x86_64: 2 types" }
            );
            button.focus();
            await user.keyboard("{Enter}");
            expect(button).toHaveAttribute("aria-expanded", "true");
            const list = document.getElementById(
                button.getAttribute("aria-controls") as string
            ) as HTMLElement;
            expect(
                within(list)
                    .getAllByRole("listitem")
                    .map((item) => item.textContent)
            ).toEqual(["g5: xlarge", "m7i: large"]);
            await user.keyboard("{Enter}");
            expect(button).toHaveAttribute("aria-expanded", "false");
            expect(
                document.getElementById(button.getAttribute("aria-controls") as string)
            ).toBeNull();
        });

        it("lists one subnet per line", () => {
            mount();
            const subnets = within(profileCell("listed", "Subnets"))
                .getAllByRole("listitem")
                .map((item) => item.textContent);
            expect(subnets).toEqual(["subnet-00000abcdefabcdef", "subnet-00000bcdefabcdefa"]);
            expect(profileCell("grouped", "Subnets")).toHaveTextContent(/^—$/);
        });
    });
});

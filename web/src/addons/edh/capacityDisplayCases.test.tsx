/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The web half of the capacity-display consistency check: every record in capacityDisplayCases.json,
 * which tools/VamsCLI/tests/addon/test_edh_capacity_display.py reads too, shows the agreed Capacity
 * detail; every catalog option the agreed launch-wizard option (offered or not, disabled or not, and
 * never preselected when the user cannot launch into it) and the agreed Settings State cell. The CLI
 * test asserts its `Capacity:` line and its STATE and LAUNCH columns against the same file.
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fixture from "./capacityDisplayCases.json";
import { describeCapacity } from "./capacityReservations";
import CapacityDetail from "./components/CapacityDetail";
import CapacityReservationsSection from "./admin/CapacityReservationsSection";
import LaunchWorkstationWizard from "./workstations/LaunchWorkstationWizard";
import * as queries from "./api/queries";
import * as adminQueries from "./api/adminQueries";
import type { CapacityReservationView, WorkstationRecord } from "./types";

jest.mock("./api/queries", () => ({
    useEdhCatalogImages: jest.fn(),
    useEdhInstanceTypes: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
    useEdhCatalogCapacityReservations: jest.fn(),
}));

jest.mock("./api/adminQueries", () => ({
    useEdhAdminCapacityReservations: jest.fn(),
    useEdhCapacityReservationMutations: jest.fn(),
}));

interface RecordCase {
    name: string;
    record: Partial<WorkstationRecord>;
    capacity: string | null;
}

interface OptionCase {
    name: string;
    view: CapacityReservationView;
    wizardOption: string | null;
    wizardDisabled: boolean | null;
    stateCell: string;
    subnetNote: string | null;
    cliState: string;
    cliLaunch: string;
}

const RECORDS = fixture.records as RecordCase[];
const OPTIONS = fixture.catalogOptions as unknown as OptionCase[];
const VIEWS = OPTIONS.map((c) => c.view);

const loaded = (data: any) => ({ data, isLoading: false, error: null });

describe("capacityDisplayCases.json (shared with the CLI tests)", () => {
    it("covers every Capacity detail and every catalog option the contract lists (control)", () => {
        const capacities = RECORDS.map((c) => c.capacity);
        expect(capacities).toEqual(
            expect.arrayContaining([
                "Render pool (cr-0123456789abcdef0)",
                "cr-0123456789abcdef0",
                "On-demand (reservation full)",
                "On-demand (reservation Render pool ended)",
                "On-demand (reservation cr-0123456789abcdef0 ended)",
                null,
            ])
        );
        expect(new Set(RECORDS.map((c) => c.record.capacityReservationFallbackReason))).toEqual(
            new Set(["full", "ended", null, undefined])
        );
        // Free, full that falls back, full that refuses, view only, and no EDH subnet (not offered).
        expect(OPTIONS.some((c) => c.view.launchAllowed === false)).toBe(true);
        expect(OPTIONS.some((c) => c.view.edhSubnetAvailable === false)).toBe(true);
        expect(OPTIONS.some((c) => c.view.edhSubnetAvailable === null)).toBe(true);
        expect(OPTIONS.filter((c) => c.wizardOption === null)).toHaveLength(1);
        expect(OPTIONS.find((c) => c.view.capacityReservationId === fixture.preselected)).toBe(
            OPTIONS[OPTIONS.length - 1]
        );
    });

    describe("the Capacity detail of a workstation", () => {
        it.each(RECORDS.map((c) => [c.name, c] as const))("%s", (_name, c) => {
            expect(describeCapacity(c.record)).toBe(c.capacity);
            const { container } = render(<CapacityDetail row={c.record} />);
            if (c.capacity === null) {
                expect(container).toBeEmptyDOMElement();
            } else {
                expect(container).toHaveTextContent(`Capacity: ${c.capacity}`);
            }
        });
    });

    describe("the launch wizard's Capacity options", () => {
        const IMAGE = {
            imageId: "42",
            name: "Ubuntu GPU desktop",
            baseOs: "ubuntu2204",
            osFamily: fixture.launch.osFamily,
            rootSize: 120,
            description: null,
            architecture: "x86_64",
        };

        async function openCapacity(user: ReturnType<typeof userEvent.setup>) {
            (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded([IMAGE]));
            (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(
                loaded([{ instanceType: fixture.launch.instanceType, architectures: ["x86_64"] }])
            );
            (queries.useEdhCatalogCapacityReservations as jest.Mock).mockReturnValue(loaded(VIEWS));
            (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
                createWorkstation: { mutateAsync: jest.fn(), isPending: false },
            });
            render(
                <LaunchWorkstationWizard
                    open={true}
                    onClose={jest.fn()}
                    generateBootstrapId={() => "11111111-1111-4111-8111-111111111111"}
                />
            );
            await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
            await user.click(screen.getByRole("button", { name: "Next" }));
            await user.selectOptions(
                screen.getByLabelText("Instance type"),
                fixture.launch.instanceType
            );
            return screen.getByLabelText("Capacity") as HTMLSelectElement;
        }

        beforeEach(() => jest.clearAllMocks());

        it("offers each option as agreed, in the catalog's order, after On-demand", async () => {
            const user = userEvent.setup();
            const select = await openCapacity(user);
            const shown = Array.from(select.options).map((o) => ({
                label: o.textContent,
                value: o.value,
                disabled: o.disabled,
            }));
            expect(shown).toEqual([
                { label: "On-demand", value: "", disabled: false },
                ...OPTIONS.filter((c) => c.wizardOption !== null).map((c) => ({
                    label: c.wizardOption,
                    value: c.view.capacityReservationId,
                    disabled: c.wizardDisabled,
                })),
            ]);
        });

        it("preselects the agreed option, never one the user cannot launch into", async () => {
            const user = userEvent.setup();
            const select = await openCapacity(user);
            expect(select).toHaveValue(fixture.preselected);
            const preselected = OPTIONS.find(
                (c) => c.view.capacityReservationId === fixture.preselected
            ) as OptionCase;
            expect(preselected.view.launchAllowed).toBe(true);
            expect(preselected.view.edhSubnetAvailable).toBe(true);
            expect(preselected.wizardDisabled).toBe(false);
        });

        it("says in the hint why a view-only reservation cannot be chosen", async () => {
            const user = userEvent.setup();
            await openCapacity(user);
            expect(screen.getByText(/marked \(view only\)/)).toHaveTextContent(
                "A reservation marked (view only) is one your role may see but not launch into; ask an administrator to grant launching into it."
            );
        });
    });

    describe("the Settings table's State cell", () => {
        it.each(OPTIONS.map((c) => [c.name, c] as const))("%s", (_name, c) => {
            (adminQueries.useEdhAdminCapacityReservations as jest.Mock).mockReturnValue({
                ...loaded([c.view]),
                refetch: jest.fn(),
                isFetching: false,
            });
            (adminQueries.useEdhCapacityReservationMutations as jest.Mock).mockReturnValue({
                register: { mutateAsync: jest.fn(), isPending: false },
                update: { mutateAsync: jest.fn(), isPending: false },
                unregister: { mutateAsync: jest.fn(), isPending: false },
            });
            render(<CapacityReservationsSection can={() => false} />);
            const table = screen.getByRole("table", { name: "Capacity reservations" });
            const headers = within(table)
                .getAllByRole("columnheader")
                .map((h) => h.textContent?.trim());
            const [, row] = within(table).getAllByRole("row");
            const cell = within(row).getAllByRole("cell")[headers.indexOf("State")];
            expect(within(cell).getByText(c.stateCell)).toBeInTheDocument();
            if (c.subnetNote === null) {
                expect(cell).toHaveTextContent(new RegExp(`^${c.stateCell}$`));
            } else {
                // Words, not only the warning colour, carry the note.
                expect(within(cell).getByText(c.subnetNote)).toBeInTheDocument();
                expect(cell.textContent).toContain(c.subnetNote);
            }
        });
    });
});

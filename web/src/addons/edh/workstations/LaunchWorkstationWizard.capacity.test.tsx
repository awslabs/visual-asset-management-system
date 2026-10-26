/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LaunchWorkstationWizard from "./LaunchWorkstationWizard";
import * as queries from "../api/queries";
import { RESERVATION_FULL_LAUNCH_MESSAGE } from "../api/outcome";
import type { CapacityReservationView } from "../types";

jest.mock("../api/queries", () => ({
    useEdhCatalogImages: jest.fn(),
    useEdhInstanceTypes: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
    useEdhCatalogCapacityReservations: jest.fn(),
}));

const BOOTSTRAP_ID = "11111111-1111-4111-8111-111111111111";

const IMAGES = [
    {
        imageId: "42",
        name: "Ubuntu GPU desktop",
        baseOs: "ubuntu2204",
        osFamily: "linux",
        rootSize: 120,
        description: null,
        architecture: "x86_64",
    },
    {
        imageId: "43",
        name: "Windows CAD desktop",
        baseOs: "windows2022",
        osFamily: "windows",
        rootSize: null,
        description: null,
        architecture: null,
    },
];

const TYPES = [
    { instanceType: "g5.xlarge", architectures: ["x86_64"] },
    { instanceType: "g5.2xlarge", architectures: ["x86_64"] },
];

const reservation = (
    id: string,
    label: string,
    overrides: Partial<CapacityReservationView> = {}
): CapacityReservationView => ({
    capacityReservationId: id,
    label,
    fallbackToOnDemand: true,
    enabled: true,
    instanceType: "g5.2xlarge",
    availabilityZone: "us-east-1a",
    instancePlatform: "Linux/UNIX",
    state: "active",
    totalInstanceCount: 2,
    availableInstanceCount: 1,
    instanceMatchCriteria: "targeted",
    endDate: null,
    reservationType: "default",
    ...overrides,
});

const FULL_FALLBACK = reservation("cr-00000000000000001", "Overflow", {
    availableInstanceCount: 0,
    totalInstanceCount: 1,
});
const FULL_STRICT = reservation("cr-00000000000000002", "Reserved only", {
    availableInstanceCount: 0,
    totalInstanceCount: 1,
    fallbackToOnDemand: false,
});
const RENDER_POOL = reservation("cr-00000000000000003", "Render pool");
const SPARE = reservation("cr-00000000000000004", "Spare", {
    instanceType: "g5.xlarge",
    availableInstanceCount: 2,
});
const WINDOWS_POOL = reservation("cr-00000000000000005", "Windows pool", {
    instancePlatform: "Windows",
    totalInstanceCount: 1,
});

const RESERVATIONS = [FULL_FALLBACK, FULL_STRICT, RENDER_POOL, SPARE, WINDOWS_POOL];

const loaded = (data: any) => ({ data, isLoading: false, error: null });

function mount(
    reservations: any = loaded(RESERVATIONS),
    mutateAsync = jest.fn().mockResolvedValue({ bootstrapId: BOOTSTRAP_ID, state: "CREATING" })
) {
    (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded(IMAGES));
    (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(loaded(TYPES));
    (queries.useEdhCatalogCapacityReservations as jest.Mock).mockReturnValue(reservations);
    (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
        createWorkstation: { mutateAsync, isPending: false },
    });
    render(
        <LaunchWorkstationWizard
            open={true}
            onClose={jest.fn()}
            generateBootstrapId={() => BOOTSTRAP_ID}
        />
    );
    return mutateAsync;
}

async function sizeStepFor(user: ReturnType<typeof userEvent.setup>, image: RegExp) {
    await user.click(screen.getByRole("radio", { name: image }));
    await user.click(screen.getByRole("button", { name: "Next" }));
}

const capacity = () => screen.getByLabelText("Capacity") as HTMLSelectElement;
const capacityOptions = () =>
    Array.from(capacity().options).map((o) => ({
        label: o.textContent,
        value: o.value,
        disabled: o.disabled,
    }));
const reviewSummary = () => screen.getByRole("dialog").querySelector("dl") as HTMLElement;

describe("LaunchWorkstationWizard capacity", () => {
    beforeEach(() => jest.clearAllMocks());

    it("asks for the catalog's reservations only while the wizard is open", () => {
        mount();
        expect(queries.useEdhCatalogCapacityReservations).toHaveBeenCalledWith(true);
    });

    it("offers On-demand and each reservation of the chosen type and the image's family, labelled by free slots", async () => {
        const user = userEvent.setup();
        mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        // Before an instance type is chosen no reservation matches, so the field is not shown.
        expect(screen.queryByLabelText("Capacity")).not.toBeInTheDocument();

        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        expect(capacityOptions()).toEqual([
            { label: "On-demand", value: "", disabled: false },
            {
                label: "Overflow (full, will launch on-demand)",
                value: FULL_FALLBACK.capacityReservationId,
                disabled: false,
            },
            {
                label: "Reserved only (full)",
                value: FULL_STRICT.capacityReservationId,
                disabled: true,
            },
            {
                label: "Render pool (1/2 free)",
                value: RENDER_POOL.capacityReservationId,
                disabled: false,
            },
        ]);
    });

    it("preselects the first matching reservation with a free slot", async () => {
        const user = userEvent.setup();
        mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        expect(capacity()).toHaveValue(RENDER_POOL.capacityReservationId);
    });

    it("preselects On-demand when no matching reservation has a free slot", async () => {
        const user = userEvent.setup();
        mount(loaded([FULL_FALLBACK, FULL_STRICT]));
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        expect(capacity()).toHaveValue("");
    });

    it("hides the field when the catalog lists no reservation, or cannot be read", async () => {
        const user = userEvent.setup();
        mount(loaded([]));
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        expect(screen.queryByLabelText("Capacity")).not.toBeInTheDocument();

        (queries.useEdhCatalogCapacityReservations as jest.Mock).mockReturnValue({
            data: undefined,
            isLoading: false,
            error: new Error("Forbidden"),
        });
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        expect(screen.queryByLabelText("Capacity")).not.toBeInTheDocument();
        expect(screen.queryByText(/Forbidden/)).not.toBeInTheDocument();
    });

    it("re-filters on a change of instance type and clears a choice that no longer matches", async () => {
        const user = userEvent.setup();
        mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        await user.selectOptions(capacity(), FULL_FALLBACK.capacityReservationId);

        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        expect(capacityOptions().map((o) => o.label)).toEqual(["On-demand", "Spare (2/2 free)"]);
        // The choice went with the type; the new type's first free reservation is preselected.
        expect(capacity()).toHaveValue(SPARE.capacityReservationId);
    });

    it("keeps an explicit On-demand across a change of instance type", async () => {
        const user = userEvent.setup();
        mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        await user.selectOptions(capacity(), "");
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        expect(capacity()).toHaveValue("");
    });

    it("re-filters on a change of image by its OS family and clears a choice it does not suit", async () => {
        const user = userEvent.setup();
        mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        expect(capacity()).toHaveValue(RENDER_POOL.capacityReservationId);

        await user.click(screen.getByRole("button", { name: "Back" }));
        await user.click(screen.getByRole("radio", { name: /Windows CAD desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(capacityOptions().map((o) => o.label)).toEqual([
            "On-demand",
            "Windows pool (1/1 free)",
        ]);
        expect(capacity()).toHaveValue(WINDOWS_POOL.capacityReservationId);
    });

    it("shows the reservation on Review and sends its id with the launch", async () => {
        const user = userEvent.setup();
        const mutateAsync = mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        await user.click(screen.getByRole("button", { name: "Next" }));

        const summary = within(reviewSummary());
        expect(summary.getByText("Capacity")).toBeInTheDocument();
        expect(summary.getByText("Render pool (cr-00000000000000003)")).toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: "Launch" }));
        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
        expect(mutateAsync).toHaveBeenCalledWith({
            bootstrapId: BOOTSTRAP_ID,
            imageId: "42",
            instanceType: "g5.2xlarge",
            name: "Ubuntu GPU desktop",
            rootSizeGb: 120,
            osFamily: "linux",
            capacityReservationId: RENDER_POOL.capacityReservationId,
        });
    });

    it("says on Review that a full reservation will launch on-demand, and still sends its id", async () => {
        const user = userEvent.setup();
        const mutateAsync = mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        await user.selectOptions(capacity(), FULL_FALLBACK.capacityReservationId);
        await user.click(screen.getByRole("button", { name: "Next" }));

        expect(
            within(reviewSummary()).getByText(
                "Overflow (cr-00000000000000001), full, will launch on-demand"
            )
        ).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Launch" }));
        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
        expect(mutateAsync.mock.calls[0][0].capacityReservationId).toBe(
            FULL_FALLBACK.capacityReservationId
        );
    });

    it("sends no reservation for On-demand and says so on Review", async () => {
        const user = userEvent.setup();
        const mutateAsync = mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        await user.selectOptions(capacity(), "");
        await user.click(screen.getByRole("button", { name: "Next" }));

        expect(within(reviewSummary()).getByText("On-demand")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Launch" }));
        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
        expect(mutateAsync.mock.calls[0][0]).not.toHaveProperty("capacityReservationId");
    });

    it("leaves Capacity off Review and the body when no reservation was offered", async () => {
        const user = userEvent.setup();
        const mutateAsync = mount(loaded([]));
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        await user.click(screen.getByRole("button", { name: "Next" }));

        expect(within(reviewSummary()).queryByText("Capacity")).not.toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Launch" }));
        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
        expect(mutateAsync.mock.calls[0][0]).not.toHaveProperty("capacityReservationId");
    });

    it("shows a full reservation's refusal on Review and stays open", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest.fn().mockRejectedValue(new Error(RESERVATION_FULL_LAUNCH_MESSAGE));
        mount(loaded(RESERVATIONS), mutateAsync);
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Launch" }));

        expect(await screen.findByRole("alert")).toHaveTextContent(RESERVATION_FULL_LAUNCH_MESSAGE);
        expect(screen.getByRole("button", { name: "Launch" })).toBeInTheDocument();
    });

    it("disables a view-only reservation, labels it so, and never preselects it", async () => {
        const user = userEvent.setup();
        const viewOnly = reservation("cr-00000000000000006", "Viewers", {
            availableInstanceCount: 2,
            launchAllowed: false,
        });
        mount(loaded([viewOnly, FULL_FALLBACK]));
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        expect(capacityOptions()).toEqual([
            { label: "On-demand", value: "", disabled: false },
            { label: "Viewers (view only)", value: viewOnly.capacityReservationId, disabled: true },
            {
                label: "Overflow (full, will launch on-demand)",
                value: FULL_FALLBACK.capacityReservationId,
                disabled: false,
            },
        ]);
        // Its free slot does not make it the default: the user cannot launch into it.
        expect(capacity()).toHaveValue("");
        expect(screen.getByText(/marked \(view only\)/)).toBeInTheDocument();
    });

    it("leaves the view-only sentence out of the hint when no option is view only", async () => {
        const user = userEvent.setup();
        mount();
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        expect(screen.queryByText(/view only/)).not.toBeInTheDocument();
    });

    it("does not offer a reservation in an Availability Zone with no EDH subnet", async () => {
        const user = userEvent.setup();
        const noSubnet = reservation("cr-00000000000000007", "No subnet", {
            availabilityZone: "us-east-1f",
            edhSubnetAvailable: false,
        });
        const unknownSubnet = reservation("cr-00000000000000008", "Subnet unknown", {
            edhSubnetAvailable: null,
        });
        mount(loaded([noSubnet, unknownSubnet]));
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        expect(capacityOptions().map((o) => o.label)).toEqual([
            "On-demand",
            "Subnet unknown (1/2 free)",
        ]);
        expect(capacity()).toHaveValue(unknownSubnet.capacityReservationId);
    });

    it("shows the service's refusal of a view-only reservation on Review", async () => {
        const user = userEvent.setup();
        const message = "Your role may view this capacity reservation but not launch into it";
        const mutateAsync = jest.fn().mockRejectedValue(new Error(message));
        mount(loaded(RESERVATIONS), mutateAsync);
        await sizeStepFor(user, /Ubuntu GPU desktop/);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.2xlarge");
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Launch" }));

        expect(await screen.findByRole("alert")).toHaveTextContent(message);
        expect(screen.getByRole("button", { name: "Launch" })).toBeInTheDocument();
    });
});

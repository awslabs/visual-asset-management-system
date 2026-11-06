/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LaunchWorkstationWizard, { sessionBootstrapNotice } from "./LaunchWorkstationWizard";
import * as queries from "../api/queries";

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };

jest.mock("../api/queries", () => ({
    useEdhCatalogImages: jest.fn(),
    useEdhInstanceTypes: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
    useEdhCatalogCapacityReservations: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const BOOTSTRAP_ID = "11111111-1111-4111-8111-111111111111";
const IMAGES = [
    {
        imageId: "42",
        name: "Ubuntu GPU desktop",
        baseOs: "ubuntu2204",
        osFamily: "linux",
        rootSize: 120,
        description: "CUDA 12, Blender, Isaac Sim",
    },
    {
        imageId: "43",
        name: "Windows Server desktop",
        baseOs: "windows2022",
        osFamily: "windows",
        rootSize: 60,
        description: null,
    },
];
const loaded = (data: any[]) => ({ data, isLoading: false, error: null });

function mountWizard(mutateAsync: jest.Mock) {
    (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded(IMAGES));
    (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(
        loaded([{ instanceType: "g5.xlarge" }])
    );
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
}

async function launch(user: ReturnType<typeof userEvent.setup>, image = /Ubuntu GPU desktop/) {
    await user.click(screen.getByRole("radio", { name: image }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.clear(screen.getByLabelText("Workstation name"));
    await user.type(screen.getByLabelText("Workstation name"), "Dev box");
    await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Launch" }));
}

beforeEach(() => {
    for (const fn of Object.values(mockToast)) fn.mockReset();
});

describe("sessionBootstrapNotice", () => {
    it.each([
        ["skipped", /no roles/],
        ["unavailable", /could not be provisioned/],
    ] as const)("%s yields a notice", (outcome, expected) => {
        expect(sessionBootstrapNotice(outcome)).toMatch(expected);
    });

    it.each(["provisioned", "disabled", undefined] as const)("%s yields none", (outcome) => {
        expect(sessionBootstrapNotice(outcome)).toBeNull();
    });
});

describe("LaunchWorkstationWizard session-bootstrap notice", () => {
    it("warns after a launch whose credential was skipped", async () => {
        const user = userEvent.setup();
        mountWizard(
            jest.fn().mockResolvedValue({
                bootstrapId: BOOTSTRAP_ID,
                state: "CREATING",
                sessionBootstrap: "skipped",
            })
        );
        await launch(user);
        await waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
        expect(mockToast.warning).toHaveBeenCalledWith(
            "Workstation sign-in not provisioned",
            expect.objectContaining({
                description: expect.stringContaining("stop and start"),
                duration: 0,
            })
        );
    });

    it("stays quiet after a provisioned launch", async () => {
        const user = userEvent.setup();
        mountWizard(
            jest.fn().mockResolvedValue({
                bootstrapId: BOOTSTRAP_ID,
                state: "CREATING",
                sessionBootstrap: "provisioned",
            })
        );
        await launch(user);
        await waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
        expect(mockToast.warning).not.toHaveBeenCalled();
    });
});

describe("sessionBootstrapNotice for a Windows workstation", () => {
    it.each(["skipped", "unavailable"] as const)("%s yields none for Windows", (outcome) => {
        expect(sessionBootstrapNotice(outcome, "start", "windows")).toBeNull();
        expect(sessionBootstrapNotice(outcome, "start", "linux")).not.toBeNull();
    });

    it("stays quiet after a Windows launch, which takes no VAMS sign-in", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest.fn().mockResolvedValue({
            bootstrapId: BOOTSTRAP_ID,
            state: "CREATING",
            sessionBootstrap: "unavailable",
        });
        mountWizard(mutateAsync);
        await launch(user, /Windows Server desktop/);
        await waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
        expect(mutateAsync.mock.calls[0][0].osFamily).toBe("windows");
        expect(mockToast.warning).toHaveBeenCalledTimes(0);
    });
});

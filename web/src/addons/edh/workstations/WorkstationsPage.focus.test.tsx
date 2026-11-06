/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Where focus goes when a dialog of My Workstations closes. The launch wizard returns to the Launch
 * Workstation button, and once a launch is sent to the search box, since the list gains a row. The
 * Delete confirmation and the desktop sign-in, opened from a row's menu in the table or on a card,
 * return to that row's actions, and a delete that removes the row hands focus to the search box; a
 * confirmation opened again on a row the last delete left listed returns to the row's actions.
 */

import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkstationsPage from "./WorkstationsPage";
import * as queries from "../api/queries";
import { EdhRequestError } from "../api/outcome";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import type { WorkstationRecord } from "../types";
import { expectHeldOpen } from "../components/heldDialogTestSupport";

jest.mock("../api/queries", () => ({
    WORKSTATION_POLL_TRANSITIONAL_MS: 5000,
    WORKSTATION_POLL_STABLE_MS: 20000,
    useWorkstations: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useWorkstationConnect: jest.fn(() => ({ mutateAsync: jest.fn(), isPending: false })),
    useOsSignIn: jest.fn(() => ({
        mutate: jest.fn(),
        reset: jest.fn(),
        data: undefined,
        error: null,
        isPending: false,
    })),
    useEdhCatalogImages: jest.fn(),
    useEdhInstanceTypes: jest.fn(),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
    useEdhCatalogCapacityReservations: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: ["EDH_WORKSTATIONS"] })) },
}));

const activeRow: WorkstationRecord = {
    bootstrapId: "b-1",
    sessionId: "s-1",
    userId: "user@example.com",
    edhUid: "vabcdefghij23456",
    state: "ACTIVE",
    edhState: "running",
    name: "Dev box",
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.xlarge",
    createdAt: "2026-11-01T10:00:00Z",
    updatedAt: "2026-11-01T10:05:00Z",
    connectionAvailable: true,
};

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
];

const loaded = (data: unknown) => ({ data, isLoading: false, error: null });

let mutations: Record<string, { mutateAsync: jest.Mock; isPending: boolean }>;

/** A viewport `width` px wide, answering min-width media queries. */
const viewport = (width: number) => {
    window.matchMedia = jest.fn(
        (query: string) =>
            ({
                matches: width >= Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0),
                media: query,
                onchange: null,
                addEventListener: jest.fn(),
                removeEventListener: jest.fn(),
                addListener: jest.fn(),
                removeListener: jest.fn(),
                dispatchEvent: () => true,
            } as unknown as MediaQueryList)
    );
};

const renderPage = () =>
    render(
        <QueryClientProvider client={new QueryClient()}>
            <MemoryRouter>
                <WorkstationsPage />
            </MemoryRouter>
        </QueryClientProvider>
    );

const originalMatchMedia = window.matchMedia;

beforeEach(() => {
    jest.clearAllMocks();
    viewport(1600);
    mutations = {
        createWorkstation: {
            mutateAsync: jest.fn().mockResolvedValue({ ...activeRow, bootstrapId: "b-9" }),
            isPending: false,
        },
        stopWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
        startWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
        deleteWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
        renewWorkstation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    };
    (queries.useWorkstationMutations as jest.Mock).mockReturnValue(mutations);
    (queries.useWorkstations as jest.Mock).mockReturnValue({
        data: [activeRow],
        isLoading: false,
        error: null,
        refetch: jest.fn(),
        isFetching: false,
        dataUpdatedAt: undefined,
    });
    (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded(IMAGES));
    (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(
        loaded([{ instanceType: "g5.xlarge", architectures: ["x86_64"] }])
    );
    (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
        loading: false,
        can: () => true,
    });
});

afterEach(() => {
    window.matchMedia = originalMatchMedia;
});

// Hidden ones included: the page is hidden from the accessibility tree while a dialog is open.
const control = (name: string) => screen.getByRole("button", { name, hidden: true });
const searchBox = () => screen.getByRole("textbox", { name: "Search workstations", hidden: true });

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

/** Opens the row's menu from its trigger and picks `item`; returns the dialog it opens. */
const openFromMenu = async (user: ReturnType<typeof userEvent.setup>, item: string) => {
    control("Actions for Dev box").focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("menuitem", { name: item }));
    return screen.findByRole("dialog");
};

/**
 * Holds `mutation`'s next request in flight, reading as pending, until the returned function
 * answers it with `value`.
 */
const holdRequest = (
    mutation: { mutateAsync: jest.Mock; isPending: boolean },
    value: unknown = {}
) => {
    let answer = () => undefined as void;
    mutation.mutateAsync = jest.fn(() => {
        mutation.isPending = true;
        return new Promise((resolve) => {
            answer = () => {
                mutation.isPending = false;
                resolve(value);
            };
        });
    });
    return () => act(async () => answer());
};

/**
 * Presses Enter on the dialog's `label` button and checks the busy window: the button reads
 * `busyLabel`, keeps focus (aria-disabled, not disabled), and a second Enter, Escape and Cancel
 * leave the request and the dialog as they are.
 */
const pressAndHold = async (
    user: ReturnType<typeof userEvent.setup>,
    dialog: HTMLElement,
    label: string,
    busyLabel: string
) => {
    within(dialog).getByRole("button", { name: label }).focus();
    await user.keyboard("{Enter}");
    const busy = within(dialog).getByRole("button", { name: busyLabel });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    expect(busy).not.toBeDisabled();
    expect(busy).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.keyboard("{Escape}");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog")).toBe(dialog);
    busy.focus();
};

/** Closes the dialog by Escape or by the footer button named `how`. */
const dismiss = async (user: ReturnType<typeof userEvent.setup>, how: string) => {
    const dialog = await screen.findByRole("dialog");
    if (how === "Escape") {
        await user.keyboard("{Escape}");
    } else {
        await user.click(within(dialog).getByRole("button", { name: how }));
    }
    await closed();
};

describe("focus when the launch wizard closes", () => {
    const openWizard = async (user: ReturnType<typeof userEvent.setup>) => {
        control("Launch Workstation").focus();
        await user.keyboard("{Enter}");
        return screen.findByRole("dialog", { name: "Launch workstation" });
    };

    it.each(["Escape", "Cancel"])(
        "returns to Launch Workstation after %s, and not between steps",
        async (how) => {
            const user = userEvent.setup({ delay: null });
            renderPage();
            const wizard = await openWizard(user);
            await user.click(within(wizard).getByRole("radio", { name: /Ubuntu GPU desktop/ }));
            await user.click(within(wizard).getByRole("button", { name: "Next" }));
            // A step change keeps the wizard open and focus inside it.
            expect(screen.getByRole("dialog", { name: "Launch workstation" })).toBeInTheDocument();
            expect(control("Launch Workstation")).not.toHaveFocus();

            await dismiss(user, how);
            await waitFor(() => expect(control("Launch Workstation")).toHaveFocus());
            expect(mutations.createWorkstation.mutateAsync).not.toHaveBeenCalled();
        }
    );

    it("goes to the search box once the launch is requested", async () => {
        const user = userEvent.setup({ delay: null });
        renderPage();
        const wizard = await openWizard(user);
        await user.click(within(wizard).getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(within(wizard).getByRole("button", { name: "Next" }));
        await user.selectOptions(within(wizard).getByLabelText("Instance type"), "g5.xlarge");
        await user.click(within(wizard).getByRole("button", { name: "Next" }));
        await user.click(within(wizard).getByRole("button", { name: "Launch" }));

        await closed();
        expect(mutations.createWorkstation.mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(searchBox()).toHaveFocus());
    });

    it("keeps focus on Launching... and the wizard open while the launch is in flight", async () => {
        const answer = holdRequest(mutations.createWorkstation, {
            ...activeRow,
            bootstrapId: "b-9",
        });
        const user = userEvent.setup({ delay: null });
        renderPage();
        const wizard = await openWizard(user);
        await user.click(within(wizard).getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(within(wizard).getByRole("button", { name: "Next" }));
        await user.selectOptions(within(wizard).getByLabelText("Instance type"), "g5.xlarge");
        await user.click(within(wizard).getByRole("button", { name: "Next" }));

        await pressAndHold(user, wizard, "Launch", "Launching...");
        expect(mutations.createWorkstation.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
        await waitFor(() => expect(searchBox()).toHaveFocus());
    });

    it("holds the wizard while the launch is in flight: the close button reads unavailable and the backdrop keeps focus", async () => {
        const answer = holdRequest(mutations.createWorkstation, {
            ...activeRow,
            bootstrapId: "b-9",
        });
        const user = userEvent.setup({ delay: null });
        renderPage();
        const wizard = await openWizard(user);
        await user.click(within(wizard).getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(within(wizard).getByRole("button", { name: "Next" }));
        await user.selectOptions(within(wizard).getByLabelText("Instance type"), "g5.xlarge");
        await user.click(within(wizard).getByRole("button", { name: "Next" }));
        await user.click(within(wizard).getByRole("button", { name: "Launch" }));

        await expectHeldOpen(user, wizard);
        expect(mutations.createWorkstation.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
    });

    it("holds Back on the review step while the launch is in flight", async () => {
        const answer = holdRequest(mutations.createWorkstation, {
            ...activeRow,
            bootstrapId: "b-9",
        });
        const user = userEvent.setup({ delay: null });
        renderPage();
        const wizard = await openWizard(user);
        await user.click(within(wizard).getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(within(wizard).getByRole("button", { name: "Next" }));
        await user.selectOptions(within(wizard).getByLabelText("Instance type"), "g5.xlarge");
        await user.click(within(wizard).getByRole("button", { name: "Next" }));
        await pressAndHold(user, wizard, "Launch", "Launching...");

        const back = within(wizard).getByRole("button", { name: "Back" });
        expect(back).toHaveAttribute("aria-disabled", "true");
        expect(back).not.toBeDisabled();
        back.focus();
        await user.keyboard("{Enter}");
        await user.click(back);
        // Still the review step: the launch under way is shown, not the editable size step.
        expect(back).toHaveFocus();
        expect(within(wizard).getByRole("button", { name: "Launching..." })).toBeInTheDocument();
        expect(within(wizard).queryByLabelText("Instance type")).not.toBeInTheDocument();
        expect(mutations.createWorkstation.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
        await waitFor(() => expect(searchBox()).toHaveFocus());
    });
});

describe("focus when the Delete confirmation closes", () => {
    it.each(["Escape", "Cancel"])("returns to the row's actions after %s", async (how) => {
        const user = userEvent.setup({ delay: null });
        renderPage();
        await openFromMenu(user, "Delete");
        await dismiss(user, how);
        await waitFor(() => expect(control("Actions for Dev box")).toHaveFocus());
        expect(mutations.deleteWorkstation.mutateAsync).not.toHaveBeenCalled();
    });

    it("goes to the search box once the delete removes the workstation", async () => {
        const user = userEvent.setup({ delay: null });
        renderPage();
        const dialog = await openFromMenu(user, "Delete");
        await user.click(within(dialog).getByRole("button", { name: "Delete" }));
        await closed();
        expect(mutations.deleteWorkstation.mutateAsync).toHaveBeenCalledWith({ sessionId: "s-1" });
        await waitFor(() => expect(searchBox()).toHaveFocus());
    });

    it("keeps focus on Deleting... and the confirmation open while the delete is in flight", async () => {
        const answer = holdRequest(mutations.deleteWorkstation);
        const user = userEvent.setup({ delay: null });
        renderPage();
        const dialog = await openFromMenu(user, "Delete");

        await pressAndHold(user, dialog, "Delete", "Deleting...");
        expect(mutations.deleteWorkstation.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
        await waitFor(() => expect(searchBox()).toHaveFocus());
    });

    it("returns to the row's actions when the delete fails and the row stays", async () => {
        mutations.deleteWorkstation.mutateAsync = jest.fn().mockRejectedValue(new Error("refused"));
        const user = userEvent.setup({ delay: null });
        renderPage();
        const dialog = await openFromMenu(user, "Delete");
        await user.click(within(dialog).getByRole("button", { name: "Delete" }));
        await closed();
        await waitFor(() => expect(control("Actions for Dev box")).toHaveFocus());
    });

    it.each(["Escape", "Cancel"])(
        "returns to the card's actions after %s below 640 px",
        async (how) => {
            viewport(414);
            const user = userEvent.setup({ delay: null });
            renderPage();
            expect(screen.getByRole("listitem", { name: "Dev box" })).toBeInTheDocument();
            await openFromMenu(user, "Delete");
            await dismiss(user, how);
            await waitFor(() => expect(control("Actions for Dev box")).toHaveFocus());
        }
    );

    it.each([1600, 414])(
        "returns to the row's actions when opened again on a row a delete EDH did not confirm left listed, at %i px",
        async (width) => {
            viewport(width);
            mutations.deleteWorkstation.mutateAsync = jest
                .fn()
                .mockRejectedValue(new EdhRequestError("Endpoint request timed out", 504));
            const user = userEvent.setup({ delay: null });
            renderPage();
            const dialog = await openFromMenu(user, "Delete");
            await user.click(within(dialog).getByRole("button", { name: "Delete" }));
            await closed();
            await waitFor(() => expect(searchBox()).toHaveFocus());

            // The row is still listed; the user opens it again and thinks better of it.
            await openFromMenu(user, "Delete");
            await dismiss(user, "Cancel");
            await waitFor(() => expect(control("Actions for Dev box")).toHaveFocus());
        }
    );
});

describe("focus when the desktop sign-in closes", () => {
    it.each(["Escape", "Close"])("returns to the row's actions after %s", async (how) => {
        const user = userEvent.setup({ delay: null });
        renderPage();
        const dialog = await openFromMenu(user, "Show desktop sign-in");
        expect(dialog).toHaveAccessibleName("Linux sign-in for Dev box");
        await dismiss(user, how);
        await waitFor(() => expect(control("Actions for Dev box")).toHaveFocus());
    });

    it("returns to the card's actions below 640 px", async () => {
        viewport(414);
        const user = userEvent.setup({ delay: null });
        renderPage();
        await openFromMenu(user, "Show desktop sign-in");
        await dismiss(user, "Escape");
        await waitFor(() => expect(control("Actions for Dev box")).toHaveFocus());
    });
});

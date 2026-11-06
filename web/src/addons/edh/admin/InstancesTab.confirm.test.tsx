/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Where focus goes when the Sessions tab's Stop or Deactivate confirmation closes: back to the
 * session's actions button, in the table and on the cards, unless a deactivation removed the
 * session, when it goes to the search box. A confirmation opened again on a session the last
 * deactivation left listed returns to the session's actions.
 */

import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InstancesTab from "./InstancesTab";
import * as adminQueries from "../api/adminQueries";
import { EdhRequestError } from "../api/outcome";
import type { EdhAdminInstance } from "../api/admin";

jest.mock("../api/adminQueries", () => ({
    useEdhAdminInstances: jest.fn(),
    useEdhStaleInstances: jest.fn(),
    useEdhAdminMutations: jest.fn(),
}));

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const session = (overrides: Partial<EdhAdminInstance>): EdhAdminInstance => ({
    sessionId: "s-1",
    edhState: "running",
    owner: "vabcdefghij23456",
    name: "cad-1",
    createdAt: "2026-10-23T10:00:00Z",
    updatedAt: "2026-10-23T10:05:00Z",
    userId: "user@example.com",
    bootstrapId: "b-1",
    state: "ACTIVE",
    lastError: null,
    imageId: "42",
    osFamily: "linux",
    instanceType: "g5.2xlarge",
    ...overrides,
});

// Two sessions of one name, as two owners may have: focus must reach the session's own button.
const SESSIONS = [
    session({}),
    session({ sessionId: "s-2", bootstrapId: "b-2", userId: "other@example.com" }),
];

const query = (data: EdhAdminInstance[]) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

let mutations: Record<string, { mutateAsync: jest.Mock; isPending: boolean }>;

/** A viewport `width` px wide for the tab's min-width query. */
const viewport = (width: number) => {
    window.matchMedia = jest.fn((mediaQuery: string) => ({
        matches: width >= Number(/min-width:\s*(\d+)px/.exec(mediaQuery)?.[1] ?? 0),
        media: mediaQuery,
        addEventListener: jest.fn(),
        removeEventListener: jest.fn(),
    })) as unknown as typeof window.matchMedia;
};

const originalMatchMedia = window.matchMedia;

beforeEach(() => {
    jest.clearAllMocks();
    mutations = {
        stopInstance: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
        deactivateInstance: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    };
    (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(query(SESSIONS));
    (adminQueries.useEdhStaleInstances as jest.Mock).mockReturnValue(query([]));
    (adminQueries.useEdhAdminMutations as jest.Mock).mockReturnValue(mutations);
});

afterEach(() => {
    window.matchMedia = originalMatchMedia;
});

// Hidden ones included: the list is hidden from the accessibility tree while a dialog is open.
const triggers = () => screen.getAllByRole("button", { name: "Actions for cad-1", hidden: true });

const open = async (user: ReturnType<typeof userEvent.setup>, index: number, action: string) => {
    triggers()[index].focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("menuitem", { name: action }));
    return screen.findByRole("dialog", { name: `${action} cad-1` });
};

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

describe.each(["Stop", "Deactivate"])("the %s confirmation", (action) => {
    it.each(["Escape", "Cancel"])(
        "returns focus to the session's actions after %s",
        async (how) => {
            const user = userEvent.setup();
            render(<InstancesTab can={() => true} />);
            const dialog = await open(user, 1, action);
            if (how === "Escape") {
                await user.keyboard("{Escape}");
            } else {
                await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
            }
            await closed();
            await waitFor(() => expect(triggers()[1]).toHaveFocus());
        }
    );

    it("returns focus to the session's card actions after Escape below 640 px", async () => {
        viewport(414);
        const user = userEvent.setup();
        render(<InstancesTab can={() => true} />);
        expect(screen.getByRole("list", { name: "EDH sessions" })).toBeInTheDocument();
        await open(user, 1, action);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(triggers()[1]).toHaveFocus());
    });
});

it("returns focus to the session's actions once a stop is answered, the session keeping its row", async () => {
    const user = userEvent.setup();
    render(<InstancesTab can={() => true} />);
    const dialog = await open(user, 0, "Stop");
    await user.click(within(dialog).getByRole("button", { name: "Stop" }));
    await closed();
    expect(mutations.stopInstance.mutateAsync).toHaveBeenCalledWith("s-1");
    await waitFor(() => expect(triggers()[0]).toHaveFocus());
});

it("sends focus to the search box once a deactivation removes the session", async () => {
    const user = userEvent.setup();
    render(<InstancesTab can={() => true} />);
    const dialog = await open(user, 0, "Deactivate");
    await user.click(within(dialog).getByRole("button", { name: "Deactivate" }));
    await closed();
    expect(mutations.deactivateInstance.mutateAsync).toHaveBeenCalledWith("s-1");
    await waitFor(() =>
        expect(screen.getByRole("textbox", { name: "Search sessions" })).toHaveFocus()
    );
});

describe.each<[string, string, string, () => HTMLElement]>([
    ["Stop", "stopInstance", "Stopping...", () => triggers()[0]],
    [
        "Deactivate",
        "deactivateInstance",
        "Deactivating...",
        () => screen.getByRole("textbox", { name: "Search sessions" }),
    ],
])("while a %s is in flight", (action, mutationName, busyLabel, target) => {
    it(`keeps focus on ${busyLabel} and the confirmation open, then focus goes where the answer sends it`, async () => {
        const mutation = mutations[mutationName];
        let answer = () => undefined as void;
        mutation.mutateAsync = jest.fn(() => {
            mutation.isPending = true;
            return new Promise((resolve) => {
                answer = () => {
                    mutation.isPending = false;
                    resolve({});
                };
            });
        });
        const user = userEvent.setup({ delay: null });
        render(<InstancesTab can={() => true} />);
        const dialog = await open(user, 0, action);
        within(dialog).getByRole("button", { name: action }).focus();
        await user.keyboard("{Enter}");

        const busy = within(dialog).getByRole("button", { name: busyLabel });
        expect(busy).toHaveAttribute("aria-disabled", "true");
        expect(busy).not.toBeDisabled();
        expect(busy).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.keyboard("{Escape}");
        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        expect(screen.getByRole("dialog", { name: `${action} cad-1` })).toBe(dialog);
        expect(mutation.mutateAsync).toHaveBeenCalledTimes(1);

        await act(async () => answer());
        await closed();
        await waitFor(() => expect(target()).toHaveFocus());
    });
});

describe("a confirmation opened again on a session a deactivation EDH did not confirm left listed", () => {
    it.each<[string, string, number]>([
        ["Stop", "Escape", 414],
        ["Deactivate", "Cancel", 1600],
    ])(
        "returns focus to the session's actions after %s and %s, at %i px",
        async (action, how, width) => {
            viewport(width);
            mutations.deactivateInstance.mutateAsync = jest
                .fn()
                .mockRejectedValue(new EdhRequestError("Endpoint request timed out", 504));
            const user = userEvent.setup({ delay: null });
            render(<InstancesTab can={() => true} />);
            const first = await open(user, 0, "Deactivate");
            await user.click(within(first).getByRole("button", { name: "Deactivate" }));
            await closed();
            await waitFor(() =>
                expect(screen.getByRole("textbox", { name: "Search sessions" })).toHaveFocus()
            );

            // The session is still listed; the user opens a confirmation on it again.
            const again = await open(user, 0, action);
            if (how === "Escape") {
                await user.keyboard("{Escape}");
            } else {
                await user.click(within(again).getByRole("button", { name: "Cancel" }));
            }
            await closed();
            await waitFor(() => expect(triggers()[0]).toHaveFocus());
        }
    );
});

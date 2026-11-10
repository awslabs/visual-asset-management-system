/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OsSignInDialog from "./OsSignInDialog";
import * as queries from "../api/queries";
import type { EdhOsFamily } from "../types";

jest.mock("../api/queries", () => ({ useOsSignIn: jest.fn() }));

const mockToast = { error: jest.fn(), success: jest.fn(), warning: jest.fn(), info: jest.fn() };

jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const PASSWORD = "winpw-" + "x".repeat(12);
const SIGN_IN = {
    sessionId: "s-6",
    osFamily: "windows",
    username: "vabcdefghij23456",
    credential: "desktopPassword",
    password: PASSWORD,
};
const DIRECTORY_SIGN_IN = {
    ...SIGN_IN,
    username: "CORP\\alice",
    credential: "directoryPassword",
    password: null,
};
const LINUX_SIGN_IN = {
    sessionId: "s-6",
    osFamily: "linux",
    username: "vabcdefghij23456",
    credential: "none",
    password: null,
};

interface SignInState {
    mutate: jest.Mock;
    reset: jest.Mock;
    data: unknown;
    error: Error | null;
    isPending: boolean;
}

const signInState = (overrides: Partial<SignInState> = {}): SignInState => ({
    mutate: jest.fn(),
    reset: jest.fn(),
    data: SIGN_IN,
    error: null,
    isPending: false,
    ...overrides,
});

const dialog = (osFamily: EdhOsFamily, onClose: jest.Mock, name = "Win box") => (
    <OsSignInDialog sessionId="s-6" workstationName={name} osFamily={osFamily} onClose={onClose} />
);

function mount(state: SignInState, onClose = jest.fn(), osFamily: EdhOsFamily = "windows") {
    (queries.useOsSignIn as jest.Mock).mockReturnValue(state);
    const view = render(dialog(osFamily, onClose));
    return { ...view, onClose };
}

// user-event's setup() installs its own clipboard, so the stub goes in after it.
const stubClipboard = (writeText: jest.Mock) =>
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

beforeEach(() => {
    jest.clearAllMocks();
});

describe("OsSignInDialog", () => {
    it("requests the sign-in of its workstation when it opens", () => {
        const state = signInState();
        mount(state);

        expect(state.mutate).toHaveBeenCalledWith("s-6", expect.anything());
        expect(
            screen.getByRole("dialog", { name: "Windows sign-in for Win box" })
        ).toBeInTheDocument();
        expect(screen.getByText("vabcdefghij23456")).toBeInTheDocument();
    });

    it("keeps the password masked until it is revealed, and hides it again", async () => {
        const user = userEvent.setup();
        mount(signInState());

        expect(screen.queryByText(PASSWORD)).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Hide password" })).not.toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Show password" }));
        expect(screen.getByText(PASSWORD)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Show password" })).not.toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Hide password" }));
        expect(screen.queryByText(PASSWORD)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Show password" })).toBeInTheDocument();
    });

    it("copies the user name and the password without revealing the password", async () => {
        const user = userEvent.setup();
        const writeText = jest.fn().mockResolvedValue(undefined);
        stubClipboard(writeText);
        mount(signInState());

        await user.click(screen.getByRole("button", { name: "Copy user name" }));
        await user.click(screen.getByRole("button", { name: "Copy password" }));

        expect(writeText.mock.calls).toEqual([["vabcdefghij23456"], [PASSWORD]]);
        await waitFor(() => expect(mockToast.success).toHaveBeenCalledWith("Password copied"));
        expect(mockToast.success).toHaveBeenCalledWith("User name copied");
        expect(screen.queryByText(PASSWORD)).not.toBeInTheDocument();
    });

    it("says so when the browser refuses the clipboard", async () => {
        const user = userEvent.setup();
        stubClipboard(jest.fn().mockRejectedValue(new Error("denied")));
        mount(signInState());

        await user.click(screen.getByRole("button", { name: "Copy password" }));

        await waitFor(() =>
            expect(mockToast.error).toHaveBeenCalledWith("Password could not be copied", {
                description: "The browser refused clipboard access.",
            })
        );
    });

    it("drops the sign-in when it closes", () => {
        const state = signInState();
        const { unmount } = mount(state);

        expect(state.reset).not.toHaveBeenCalled();
        unmount();
        expect(state.reset).toHaveBeenCalledTimes(1);
    });

    it("closes from its Close button", async () => {
        const user = userEvent.setup();
        const { onClose } = mount(signInState());

        await user.click(screen.getByRole("button", { name: "Close" }));

        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("shows no password for a Windows directory account", () => {
        mount(signInState({ data: DIRECTORY_SIGN_IN }));

        expect(screen.getByText("CORP\\alice")).toBeInTheDocument();
        expect(screen.getByText("Sign in with your directory password.")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Show password" })).not.toBeInTheDocument();
        expect(screen.getByText(/Send Ctrl \+ Alt \+ Del/)).toBeInTheDocument();
        expect(screen.getByText(/then enter your directory password/)).toBeInTheDocument();
    });

    it("tells a Linux owner that no password is needed and shows none", () => {
        mount(signInState({ data: LINUX_SIGN_IN }), jest.fn(), "linux");

        expect(
            screen.getByRole("dialog", { name: "Linux sign-in for Win box" })
        ).toBeInTheDocument();
        expect(screen.getByText("vabcdefghij23456")).toBeInTheDocument();
        expect(screen.getByText("No password is needed.")).toBeInTheDocument();
        expect(screen.getByText(/Amazon DCV signs the session in/)).toBeInTheDocument();
        expect(screen.getByText(/does not lock its screen/)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Show password" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Copy password" })).not.toBeInTheDocument();
        expect(screen.queryByText(/Send Ctrl \+ Alt \+ Del/)).not.toBeInTheDocument();
        expect(screen.queryByText(/directory password/)).not.toBeInTheDocument();
    });

    it("titles a Linux dialog from the row while the sign-in is fetched", () => {
        mount(signInState({ data: undefined }), jest.fn(), "linux");

        expect(
            screen.getByRole("dialog", { name: "Linux sign-in for Win box" })
        ).toBeInTheDocument();
        expect(screen.getByRole("status")).toHaveTextContent("Fetching the desktop sign-in…");
    });

    it("no longer says Amazon DCV locks Windows when the last connection closes", () => {
        mount(signInState());

        expect(screen.queryByText(/locks Windows/)).not.toBeInTheDocument();
        expect(
            screen.getByText(/does not lock while idle or when the last connection closes/)
        ).toBeInTheDocument();
        expect(screen.getByText(/sign-in screen again after each start/)).toBeInTheDocument();
    });

    it("shows and toasts the reason the sign-in is refused", () => {
        const message = "Windows password is not available yet";
        const refuse = (_sessionId: string, options: { onError: (err: Error) => void }) =>
            options.onError(new Error(message));
        mount(
            signInState({
                mutate: jest.fn(refuse),
                data: undefined,
                error: new Error(message),
            })
        );

        expect(screen.getByRole("alert")).toHaveTextContent(message);
        expect(mockToast.error).toHaveBeenCalledWith("Desktop sign-in unavailable", {
            description: message,
        });
    });

    it("says it is fetching until the sign-in answers", () => {
        mount(signInState({ data: undefined }));

        expect(screen.getByRole("status")).toHaveTextContent("Fetching the desktop sign-in…");
    });

    it("gives the Windows sign-in steps once the sign-in answers", () => {
        const state = signInState({ data: undefined });
        const { rerender, onClose } = mount(state);

        expect(screen.queryByText(/Send Ctrl \+ Alt \+ Del/)).not.toBeInTheDocument();
        (queries.useOsSignIn as jest.Mock).mockReturnValue({ ...state, data: SIGN_IN });
        rerender(dialog("windows", onClose));

        expect(screen.getByText(/Send Ctrl \+ Alt \+ Del/)).toBeInTheDocument();
        expect(screen.getByText(/lower-left corner/)).toBeInTheDocument();
        expect(screen.getByText(/at the Windows sign-in screen/)).toBeInTheDocument();
    });
});

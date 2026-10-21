/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConnectLink, { connectUnavailableReason } from "./ConnectLink";
import * as queries from "../api/queries";
import type { WorkstationRecord } from "../types";

jest.mock("../api/queries", () => ({
    useWorkstationConnect: jest.fn(),
}));

const mockToast = { error: jest.fn(), success: jest.fn(), warning: jest.fn(), info: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const row = (overrides: Partial<WorkstationRecord>): WorkstationRecord => ({
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
    createdAt: "2026-09-08T10:00:00Z",
    updatedAt: "2026-09-08T10:05:00Z",
    connectionAvailable: true,
    ...overrides,
});

const CONNECTION_URL = "https://dcv.example.internal/?authToken=x#s-1";

/** A stand-in for the blank tab `window.open` hands back inside the click. */
const fakeTab = () => ({ location: { replace: jest.fn() }, close: jest.fn(), opener: {} });

let connect: { mutateAsync: jest.Mock; isPending: boolean };
let openSpy: jest.SpyInstance;

describe("ConnectLink", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        connect = { mutateAsync: jest.fn(), isPending: false };
        (queries.useWorkstationConnect as jest.Mock).mockReturnValue(connect);
        openSpy = jest.spyOn(window, "open");
    });

    afterEach(() => {
        openSpy.mockRestore();
    });

    it("renders Connect as a button and requests nothing before it is clicked", () => {
        render(<ConnectLink row={row({})} />);

        const control = screen.getByRole("button", { name: "Connect to Dev box" });
        expect(control).toHaveTextContent("Connect");
        expect(control).toHaveAccessibleDescription("Opens in a new tab.");
        expect(screen.queryByRole("link")).not.toBeInTheDocument();
        expect(document.querySelector("a[href]")).toBeNull();
        expect(connect.mutateAsync).not.toHaveBeenCalled();
        expect(openSpy).not.toHaveBeenCalled();
    });

    it("opens a tab inside the click, then points it at the connection URL", async () => {
        const user = userEvent.setup();
        const tab = fakeTab();
        openSpy.mockReturnValue(tab as unknown as Window);
        connect.mutateAsync.mockResolvedValue({
            sessionId: "s-1",
            connectionUrl: CONNECTION_URL,
            osFamily: "linux",
        });
        render(<ConnectLink row={row({})} />);

        await user.click(screen.getByRole("button", { name: "Connect to Dev box" }));

        // The tab is opened before the request is made, and cannot reach back into this window.
        expect(openSpy).toHaveBeenCalledWith("", "_blank");
        expect(tab.opener).toBeNull();
        expect(connect.mutateAsync).toHaveBeenCalledWith("s-1");
        await waitFor(() => expect(tab.location.replace).toHaveBeenCalledWith(CONNECTION_URL));
        expect(tab.close).not.toHaveBeenCalled();
        expect(mockToast.error).not.toHaveBeenCalled();
    });

    it("opens one tab and sends one request for a double-click", async () => {
        openSpy.mockImplementation(() => fakeTab());
        connect.mutateAsync.mockReturnValue(new Promise<never>(() => undefined));
        render(<ConnectLink row={row({})} />);

        // Both clicks land before a re-render, as a double-click's do.
        const control = screen.getByRole("button", { name: "Connect to Dev box" });
        fireEvent.click(control);
        fireEvent.click(control);
        await waitFor(() => expect(connect.mutateAsync).toHaveBeenCalledWith("s-1"));
        expect(connect.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
        expect(openSpy.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("closes the tab and raises a toast when the connection request fails", async () => {
        const user = userEvent.setup();
        const tab = fakeTab();
        openSpy.mockReturnValue(tab as unknown as Window);
        connect.mutateAsync.mockRejectedValue(new Error("Workstation is not ready to connect"));
        render(<ConnectLink row={row({})} />);

        await user.click(screen.getByRole("button", { name: "Connect to Dev box" }));

        await waitFor(() => expect(tab.close).toHaveBeenCalled());
        expect(tab.location.replace).not.toHaveBeenCalled();
        expect(mockToast.error).toHaveBeenCalledWith("Connect failed", {
            description: "Dev box: Workstation is not ready to connect",
        });
    });

    it("raises a toast and requests nothing when the browser blocks the tab", async () => {
        const user = userEvent.setup();
        openSpy.mockReturnValue(null);
        render(<ConnectLink row={row({})} />);

        await user.click(screen.getByRole("button", { name: "Connect to Dev box" }));

        expect(connect.mutateAsync).not.toHaveBeenCalled();
        expect(mockToast.error).toHaveBeenCalledWith("Connect failed", {
            description: expect.stringMatching(/^Dev box: the browser blocked the new tab/),
        });
    });

    it("is operable from the keyboard", async () => {
        const user = userEvent.setup();
        const tab = fakeTab();
        openSpy.mockReturnValue(tab as unknown as Window);
        connect.mutateAsync.mockResolvedValue({
            sessionId: "s-1",
            connectionUrl: CONNECTION_URL,
            osFamily: "linux",
        });
        render(<ConnectLink row={row({})} />);

        screen.getByRole("button", { name: "Connect to Dev box" }).focus();
        await user.keyboard("{Enter}");

        await waitFor(() => expect(tab.location.replace).toHaveBeenCalledWith(CONNECTION_URL));
    });

    it("shows that the tab is being opened while the request is in flight", () => {
        connect.isPending = true;
        render(<ConnectLink row={row({})} />);
        const button = screen.getByRole("button", { name: "Connect to Dev box" });
        expect(button).toHaveTextContent("Opening…");
        expect(button).toBeDisabled();
    });

    it("renders an inert control with the reason under it while the connection is not available", async () => {
        const user = userEvent.setup();
        render(<ConnectLink row={row({ state: "ACTIVE", connectionAvailable: false })} />);

        const control = screen.getByRole("button", { name: "Connect" });
        expect(control).toHaveAttribute("aria-disabled", "true");
        expect(control).toHaveAccessibleDescription("Available once the workstation is running.");
        expect(screen.getByText("Available once the workstation is running.")).toBeVisible();
        expect(control).not.toHaveAttribute("title");

        await user.click(control);
        expect(connect.mutateAsync).not.toHaveBeenCalled();
        expect(openSpy).not.toHaveBeenCalled();
    });

    it("describes a creating workstation that has no session id yet the same way", () => {
        render(
            <ConnectLink
                row={row({ state: "CREATING", sessionId: null, connectionAvailable: false })}
            />
        );
        expect(screen.queryByRole("button", { name: /Connect to/ })).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Connect" })).toHaveAccessibleDescription(
            "Available once the workstation is running."
        );
    });

    it("tells the owner of a stopped workstation to start it", () => {
        render(<ConnectLink row={row({ state: "STOPPED", connectionAvailable: false })} />);
        expect(screen.getByRole("button", { name: "Connect" })).toHaveAccessibleDescription(
            "Start the workstation to connect."
        );
        expect(screen.getByText("Start the workstation to connect.")).toBeVisible();
    });

    it("labels an orphaned workstation's Connect with the failed launch", () => {
        render(
            <ConnectLink
                row={row({
                    state: "ORPHANED",
                    sessionId: null,
                    connectionAvailable: false,
                    lastError: "EDH_REFUSED",
                })}
            />
        );

        expect(screen.getByRole("button", { name: "Connect" })).toHaveAccessibleDescription(
            "The launch did not complete."
        );
        expect(screen.getByText("The launch did not complete.")).toBeVisible();
    });

    it("gives every non-connectable state a reason", () => {
        expect(
            connectUnavailableReason(row({ state: "DELETED", connectionAvailable: false }))
        ).toBe("The workstation has been deleted.");
        expect(connectUnavailableReason(row({ state: "CREATING", sessionId: null }))).toBe(
            "Available once the workstation is running."
        );
    });
});

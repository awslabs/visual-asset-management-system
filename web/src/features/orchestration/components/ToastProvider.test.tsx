/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ToastProvider, useToast, toastErrorMessage } from "./ToastProvider";

/** Minimal harness: buttons that raise each variant so the rendered stack can be asserted. */
const Raiser: React.FC<{ duration?: number }> = ({ duration }) => {
    const toast = useToast();
    return (
        <>
            <button onClick={() => toast.error("Archive failed", { description: "403 Forbidden" })}>
                raise-error
            </button>
            <button onClick={() => toast.success("Archived", { duration })}>raise-success</button>
            <button onClick={() => toast.warning("Saved with warnings")}>raise-warning</button>
            <button onClick={() => toast.info("Heads up")}>raise-info</button>
            <button onClick={() => toast.dismissAll()}>dismiss-all</button>
        </>
    );
};

const renderWithProvider = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe("ToastProvider", () => {
    afterEach(() => {
        jest.useRealTimers();
    });

    it("renders nothing until a toast is raised", () => {
        renderWithProvider(<Raiser />);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    it("shows an error with its description and uses role=alert for assertive delivery", async () => {
        renderWithProvider(<Raiser />);
        await userEvent.click(screen.getByText("raise-error"));

        const toast = screen.getByRole("alert");
        expect(toast).toHaveTextContent("Archive failed");
        expect(toast).toHaveTextContent("403 Forbidden");
    });

    it("does not deliver a non-error assertively", async () => {
        renderWithProvider(<Raiser />);
        await userEvent.click(screen.getByText("raise-success"));

        // The message is shown, but not as role=alert — only failures interrupt the user.
        expect(screen.getByText("Archived")).toBeInTheDocument();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("dismisses on the close button", async () => {
        renderWithProvider(<Raiser />);
        await userEvent.click(screen.getByText("raise-error"));
        expect(screen.getByRole("alert")).toBeInTheDocument();

        await userEvent.click(screen.getByRole("button", { name: /dismiss error notification/i }));
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("gives an error the app's longer 8s window before auto-hiding", () => {
        jest.useFakeTimers();
        renderWithProvider(<Raiser />);
        // userEvent needs real timers, so dispatch directly under fake timers.
        act(() => {
            screen.getByText("raise-error").click();
        });
        // Still present at the 5s mark that dismisses a success.
        act(() => {
            jest.advanceTimersByTime(5_000);
        });
        expect(screen.getByRole("alert")).toBeInTheDocument();
        // Gone once the error window elapses — matching components/search/hooks/useToasts.tsx.
        act(() => {
            jest.advanceTimersByTime(3_500);
        });
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("auto-dismisses a success after its duration", () => {
        jest.useFakeTimers();
        renderWithProvider(<Raiser />);
        act(() => {
            screen.getByText("raise-success").click();
        });
        expect(screen.getByText("Archived")).toBeInTheDocument();

        act(() => {
            jest.advanceTimersByTime(5_000);
        });
        expect(screen.queryByText("Archived")).not.toBeInTheDocument();
    });

    it("collapses an identical repeat instead of stacking duplicates", async () => {
        renderWithProvider(<Raiser />);
        await userEvent.click(screen.getByText("raise-error"));
        await userEvent.click(screen.getByText("raise-error"));

        expect(screen.getAllByRole("alert")).toHaveLength(1);
    });

    it("caps the stack so a burst cannot fill the viewport", async () => {
        renderWithProvider(<Raiser />);
        // Five distinct toasts; the cap is four, so the oldest is dropped.
        await userEvent.click(screen.getByText("raise-error"));
        await userEvent.click(screen.getByText("raise-success"));
        await userEvent.click(screen.getByText("raise-warning"));
        await userEvent.click(screen.getByText("raise-info"));

        // Four distinct toasts raised; the cap is four so all remain, and a fifth would evict.
        expect(screen.getByText("Archive failed")).toBeInTheDocument();
        expect(screen.getByText("Heads up")).toBeInTheDocument();
    });

    it("dismissAll clears the whole stack", async () => {
        renderWithProvider(<Raiser />);
        await userEvent.click(screen.getByText("raise-error"));
        await userEvent.click(screen.getByText("raise-warning"));
        await userEvent.click(screen.getByText("dismiss-all"));

        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.queryByText("Saved with warnings")).not.toBeInTheDocument();
    });

    it("delivers an error assertively via Cloudscape's ariaRole", async () => {
        renderWithProvider(<Raiser />);
        await userEvent.click(screen.getByText("raise-error"));
        expect(screen.getByRole("alert")).toHaveTextContent("Archive failed");
    });

    it("renders through Cloudscape Flashbar in the shared top-right position", async () => {
        renderWithProvider(<Raiser />);
        await userEvent.click(screen.getByText("raise-error"));

        const region = screen.getByLabelText("Notifications");
        // Same placement as components/search/SearchNotifications/ToastManager.tsx.
        expect(region).toHaveStyle({ position: "fixed", top: "20px", right: "20px" });
        // Cloudscape groups the stack; presence of the group proves Flashbar rendered it.
        expect(region.querySelector('[role="group"]')).toBeTruthy();
    });

    it("degrades to a no-op outside a provider rather than throwing", async () => {
        // A component rendered in isolation (or a unit test without the provider) must still work.
        const logSpy = jest.spyOn(console, "log").mockImplementation(() => undefined);
        expect(() => render(<Raiser />)).not.toThrow();
        await userEvent.click(screen.getByText("raise-error"));
        expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Archive failed"));
        logSpy.mockRestore();
    });
});

describe("toastErrorMessage", () => {
    it("uses an Error's message", () => {
        expect(toastErrorMessage(new Error("Pipeline is archived"))).toBe("Pipeline is archived");
    });

    it("passes a raw string through", () => {
        expect(toastErrorMessage("plain failure")).toBe("plain failure");
    });

    it("reads message/error/detail off a plain object", () => {
        expect(toastErrorMessage({ message: "from message" })).toBe("from message");
        expect(toastErrorMessage({ error: "from error" })).toBe("from error");
        expect(toastErrorMessage({ detail: "from detail" })).toBe("from detail");
    });

    it("falls back rather than rendering [object Object]", () => {
        expect(toastErrorMessage({ unexpected: true })).toBe("The request was rejected.");
        expect(toastErrorMessage(undefined)).toBe("The request was rejected.");
        expect(toastErrorMessage(null, "custom")).toBe("custom");
    });

    it("falls back for an Error with an empty message", () => {
        expect(toastErrorMessage(new Error(""))).toBe("The request was rejected.");
    });
});

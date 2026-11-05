/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TriggerList from "./TriggerList";
import type { WorkflowTrigger } from "../types";
import { TRIGGER_TYPES } from "../types";

const renderList = (over: Partial<React.ComponentProps<typeof TriggerList>> = {}) => {
    const props = {
        triggers: [] as WorkflowTrigger[],
        isLoading: false,
        onAdd: jest.fn(),
        onEdit: jest.fn(),
        onDelete: jest.fn(),
        onRetry: jest.fn(),
        ...over,
    };
    render(<TriggerList {...props} />);
    return props;
};

const nightly: WorkflowTrigger = {
    triggerType: "fileUpload#nightly",
    enabled: true,
    inputFileFilters: { allow: ["*.glb"] },
};

describe("TriggerList", () => {
    it("shows the loading line, not the empty state, while loading", () => {
        renderList({ isLoading: true });
        expect(screen.getByText(/Loading triggers/i)).toBeInTheDocument();
        expect(screen.queryByText("No triggers configured")).not.toBeInTheDocument();
    });

    it("shows the empty state once loaded with nothing", () => {
        renderList();
        expect(screen.getByText("No triggers configured")).toBeInTheDocument();
        expect(screen.getByText(/started explicitly/i)).toBeInTheDocument();
    });

    it("offers one add button per configurable type and reports the type", async () => {
        const { onAdd } = renderList();
        for (const t of TRIGGER_TYPES) {
            await userEvent.click(
                screen.getByRole("button", { name: new RegExp(`add ${t.label} trigger`, "i") })
            );
            expect(onAdd).toHaveBeenCalledWith(t.type);
        }
    });

    it("renders a row per trigger whose Edit and Delete name the key and hand back the row", async () => {
        const { onEdit, onDelete } = renderList({ triggers: [nightly] });

        expect(screen.getByText("*.glb")).toBeInTheDocument();
        expect(screen.getByText("nightly")).toBeInTheDocument();
        await userEvent.click(
            screen.getByRole("button", { name: "Edit trigger fileUpload#nightly" })
        );
        expect(onEdit).toHaveBeenCalledWith(nightly);
        await userEvent.click(
            screen.getByRole("button", { name: "Delete trigger fileUpload#nightly" })
        );
        expect(onDelete).toHaveBeenCalledWith(nightly);
    });

    it("lists pending drafts as Not saved rows with their reason and a Retry action", async () => {
        const item = { draft: nightly, error: "This workflow restricts concurrency per asset" };
        const { onRetry } = renderList({ pending: [item] });

        expect(screen.getByText("Not saved")).toBeInTheDocument();
        expect(screen.getByText(/restricts concurrency per asset/)).toBeInTheDocument();
        // A pending row is not "no triggers": the empty state would contradict the row above it.
        expect(screen.queryByText("No triggers configured")).not.toBeInTheDocument();

        await userEvent.click(
            screen.getByRole("button", { name: "Retry trigger fileUpload#nightly" })
        );
        expect(onRetry).toHaveBeenCalledWith(item);
    });
});

/**
 * A system workflow's triggers can be switched on and off but not added or deleted (the API answers
 * 400), so the list keeps Add and Delete in place, disabled with the reason, and Edit enabled.
 */
describe("TriggerList for a system workflow", () => {
    const ADD_REASON = "Triggers cannot be added to a system workflow.";
    const DELETE_REASON =
        "Triggers of a system workflow cannot be deleted. Use Edit to switch the trigger off.";

    it("lists Add and Delete disabled with their reasons and keeps Edit enabled", () => {
        renderList({ triggers: [nightly], locked: true });
        for (const t of TRIGGER_TYPES) {
            const add = screen.getByRole("button", {
                name: new RegExp(`add ${t.label} trigger`, "i"),
            });
            expect(add).toHaveAttribute("aria-disabled", "true");
            expect(add).toHaveAttribute("aria-description", ADD_REASON);
            // The type description gives way to the reason, so a hover shows one tooltip.
            expect(add).not.toHaveAttribute("title");
        }
        const del = screen.getByRole("button", { name: "Delete trigger fileUpload#nightly" });
        expect(del).toHaveAttribute("aria-disabled", "true");
        expect(del).toHaveAttribute("aria-description", DELETE_REASON);
        expect(
            screen.getByRole("button", { name: "Edit trigger fileUpload#nightly" })
        ).not.toHaveAttribute("aria-disabled");
    });

    it("reaches the disabled controls by keyboard, shows each reason and calls neither handler", async () => {
        const user = userEvent.setup();
        const { onAdd, onDelete } = renderList({ triggers: [nightly], locked: true });

        // Tab order: the add button(s), then the row's Edit, then its Delete.
        await user.tab();
        const add = screen.getByRole("button", { name: /add file upload trigger/i });
        expect(add).toHaveFocus();
        expect(await screen.findByRole("tooltip")).toHaveTextContent(ADD_REASON);
        await user.keyboard("{Enter}");
        expect(onAdd).not.toHaveBeenCalled();

        await user.tab();
        await user.tab();
        const del = screen.getByRole("button", { name: "Delete trigger fileUpload#nightly" });
        expect(del).toHaveFocus();
        expect(await screen.findByRole("tooltip")).toHaveTextContent(DELETE_REASON);
        await user.click(del);
        expect(onDelete).not.toHaveBeenCalled();
    });

    it("leaves every control enabled for an ordinary workflow", () => {
        renderList({ triggers: [nightly] });
        const add = screen.getByRole("button", { name: /add file upload trigger/i });
        expect(add).not.toHaveAttribute("aria-disabled");
        expect(add).toHaveAttribute("title");
        expect(
            screen.getByRole("button", { name: "Delete trigger fileUpload#nightly" })
        ).not.toHaveAttribute("aria-disabled");
    });
});

/**
 * The route check comes first: an action the role may not call is hidden, on an ordinary workflow
 * and on a system one, rather than offered and then refused by the API.
 */
describe("TriggerList route check", () => {
    const pendingItem = { draft: nightly, error: "Not saved yet" };

    it.each([
        ["an ordinary", false],
        ["a system", true],
    ])(
        "shows no Add, Edit, Delete or Retry on %s workflow to a role that may call neither route",
        (_, locked) => {
            renderList({
                triggers: [nightly],
                pending: [pendingItem],
                locked,
                canSet: false,
                canDelete: false,
            });
            // The stored row and the unsaved one stay readable.
            expect(screen.getByText("*.glb")).toBeInTheDocument();
            expect(screen.getByText("Not saved")).toBeInTheDocument();
            expect(screen.queryAllByRole("button")).toHaveLength(0);
            expect(screen.queryByRole("columnheader", { name: "Actions" })).not.toBeInTheDocument();
        }
    );

    it("hides only Delete from a role that may set triggers but not delete them", () => {
        renderList({ triggers: [nightly], canSet: true, canDelete: false });
        expect(
            screen.getByRole("button", { name: /add file upload trigger/i })
        ).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: "Edit trigger fileUpload#nightly" })
        ).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Delete trigger fileUpload#nightly" })
        ).not.toBeInTheDocument();
    });

    it("hides Add, Edit and Retry from a role that may delete triggers but not set them", () => {
        renderList({ triggers: [nightly], pending: [pendingItem], canSet: false, canDelete: true });
        expect(screen.queryByRole("button", { name: /add .* trigger/i })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /^Edit trigger/ })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /^Retry trigger/ })).not.toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: "Delete trigger fileUpload#nightly" })
        ).toBeInTheDocument();
    });

    it("scrolls the trigger table inside its own box and lets the header wrap", () => {
        // jsdom has no layout, so the classes are the contract: without the scroll box a phone-width
        // page widens to the table and pushes Edit and Delete off-screen.
        renderList({ triggers: [nightly] });
        const table = screen.getByRole("table");
        expect(table.parentElement).toHaveClass("overflow-x-auto");
        const heading = screen.getByRole("heading", { name: "Triggers" });
        expect(heading.parentElement).toHaveClass("flex-wrap");
    });

    it("lets a keyboard user reach the scroll box when the role has no row actions", async () => {
        renderList({ triggers: [nightly], canSet: false, canDelete: false });
        expect(screen.queryAllByRole("button")).toHaveLength(0);
        const box = screen.getByRole("region", { name: "Triggers" });
        expect(box).toContainElement(screen.getByRole("table"));
        await userEvent.tab();
        expect(box).toHaveFocus();
    });

    it("adds no extra tab stop when the rows hold Edit and Delete", () => {
        renderList({ triggers: [nightly] });
        expect(screen.getByRole("region", { name: "Triggers" })).not.toHaveAttribute("tabindex");
    });
});

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

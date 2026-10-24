/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EdhFeatureTabs, { EDH_FEATURE_PANEL_ID, edhFeatureTabId } from "./EdhFeatureTabs";

describe("EdhFeatureTabs", () => {
    it("renders both tabs with the active one selected", () => {
        render(<EdhFeatureTabs active="workstations" onChange={jest.fn()} />);
        const tabs = screen.getAllByRole("tab");
        expect(tabs.map((t) => t.textContent)).toEqual(["Workstations", "Simulations"]);
        expect(screen.getByRole("tab", { name: "Workstations" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(screen.getByRole("tab", { name: "Simulations" })).toHaveAttribute(
            "aria-selected",
            "false"
        );
    });

    it("wires each tab to the page's panel by the ids the page uses", () => {
        render(<EdhFeatureTabs active="workstations" onChange={jest.fn()} />);
        const tab = screen.getByRole("tab", { name: "Simulations" });
        expect(tab).toHaveAttribute("id", edhFeatureTabId("simulations"));
        expect(tab).toHaveAttribute("aria-controls", EDH_FEATURE_PANEL_ID);
    });

    it("reports a click and an arrow key as a change", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();
        render(<EdhFeatureTabs active="workstations" onChange={onChange} />);

        await user.click(screen.getByRole("tab", { name: "Simulations" }));
        expect(onChange).toHaveBeenLastCalledWith("simulations");

        screen.getByRole("tab", { name: "Workstations" }).focus();
        await user.keyboard("{ArrowRight}");
        expect(onChange).toHaveBeenLastCalledWith("simulations");
    });

    it("moves focus with the selection on the arrow, Home and End keys", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();
        const { rerender } = render(<EdhFeatureTabs active="workstations" onChange={onChange} />);

        screen.getByRole("tab", { name: "Workstations" }).focus();
        await user.keyboard("{ArrowRight}");
        expect(onChange).toHaveBeenLastCalledWith("simulations");
        expect(screen.getByRole("tab", { name: "Simulations" })).toHaveFocus();

        rerender(<EdhFeatureTabs active="simulations" onChange={onChange} />);
        await user.keyboard("{Home}");
        expect(onChange).toHaveBeenLastCalledWith("workstations");
        expect(screen.getByRole("tab", { name: "Workstations" })).toHaveFocus();

        rerender(<EdhFeatureTabs active="workstations" onChange={onChange} />);
        await user.keyboard("{End}");
        expect(onChange).toHaveBeenLastCalledWith("simulations");
        expect(screen.getByRole("tab", { name: "Simulations" })).toHaveFocus();

        // Left from the first tab wraps to the last.
        rerender(<EdhFeatureTabs active="workstations" onChange={onChange} />);
        screen.getByRole("tab", { name: "Workstations" }).focus();
        await user.keyboard("{ArrowLeft}");
        expect(onChange).toHaveBeenLastCalledWith("simulations");
        expect(screen.getByRole("tab", { name: "Simulations" })).toHaveFocus();
    });

    it("reports nothing when the key lands on the tab already selected", async () => {
        const user = userEvent.setup();
        const onChange = jest.fn();
        render(<EdhFeatureTabs active="workstations" onChange={onChange} />);

        screen.getByRole("tab", { name: "Workstations" }).focus();
        await user.keyboard("{Home}");
        expect(onChange).not.toHaveBeenCalled();
        expect(screen.getByRole("tab", { name: "Workstations" })).toHaveFocus();
    });
});

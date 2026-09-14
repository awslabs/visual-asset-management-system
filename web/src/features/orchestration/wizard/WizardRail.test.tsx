/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WizardRail, { RailStep } from "./WizardRail";

const STEPS: RailStep[] = [
    { id: "workflow", label: "Workflow", done: true },
    { id: "input", label: "Inputs", status: "Incomplete" },
    { id: "pipeline-0", label: "Alpha Pipeline", status: "Needs template" },
    { id: "review", label: "Review" },
];

describe("WizardRail", () => {
    it("is the one navigation landmark and renders each label exactly once", () => {
        render(<WizardRail steps={STEPS} currentId="input" onJumpTo={jest.fn()} />);
        expect(screen.getAllByRole("navigation")).toHaveLength(1);
        expect(screen.getByRole("navigation", { name: "Execution steps" })).toBeInTheDocument();
        // Exact-text queries in the wizard suites rely on a single label node per step.
        expect(screen.getAllByText("Alpha Pipeline")).toHaveLength(1);
        expect(screen.getAllByText("Inputs")).toHaveLength(1);
    });

    it("marks the current step and shows the status chips", () => {
        render(<WizardRail steps={STEPS} currentId="input" />);
        expect(screen.getByText("Inputs").closest("li")).toHaveAttribute("aria-current", "step");
        expect(screen.getByText("Review").closest("li")).not.toHaveAttribute("aria-current");
        expect(screen.getByText("Incomplete")).toBeInTheDocument();
        expect(screen.getByText("Needs template")).toBeInTheDocument();
    });

    it("lets a visited step be jumped back to, and leaves future steps inert", async () => {
        const onJumpTo = jest.fn();
        render(<WizardRail steps={STEPS} currentId="pipeline-0" onJumpTo={onJumpTo} />);
        // Visited: the leading `done` step and every step before the current one.
        await userEvent.click(screen.getByRole("button", { name: /Workflow/ }));
        expect(onJumpTo).toHaveBeenCalledWith("workflow");
        await userEvent.click(screen.getByRole("button", { name: /Inputs/ }));
        expect(onJumpTo).toHaveBeenCalledWith("input");
        // The current row and the future row are not buttons.
        expect(screen.queryByRole("button", { name: /Alpha Pipeline/ })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Review/ })).not.toBeInTheDocument();
    });

    it("renders no buttons at all without a jump handler", () => {
        render(<WizardRail steps={STEPS} currentId="review" />);
        expect(screen.queryAllByRole("button")).toHaveLength(0);
    });

    it("collapses to a horizontal strip below md with CSS, not matchMedia", () => {
        render(<WizardRail steps={STEPS} currentId="input" />);
        const list = screen.getByRole("list");
        expect(list.className).toContain("flex-row");
        expect(list.className).toContain("md:flex-col");
        // The rule is about the component, not the environment: jsdom has no matchMedia whatever
        // the component does, so only the source can show that none is consulted.
        const src = require("fs").readFileSync(
            require("path").join(__dirname, "WizardRail.tsx"),
            "utf-8"
        );
        expect(src).not.toContain("matchMedia");
    });
});

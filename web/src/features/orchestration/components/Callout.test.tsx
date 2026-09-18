/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import Callout from "./Callout";
import { btnSuccess } from "./controlStyles";

describe("Callout", () => {
    it("renders no implicit role, so a step never gains a second alert by accident", () => {
        render(
            <Callout tone="info" title="To continue">
                <p>pick a file</p>
            </Callout>
        );
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
        expect(screen.getByText("To continue")).toBeInTheDocument();
        expect(screen.getByText("pick a file")).toBeInTheDocument();
    });

    it("passes an explicit role and live region through", () => {
        render(
            <Callout tone="error" role="alert">
                failed
            </Callout>
        );
        expect(screen.getByRole("alert")).toHaveTextContent("failed");

        render(
            <Callout tone="warning" aria-live="polite" title="Blockers">
                one
            </Callout>
        );
        expect(screen.getByText("one").closest("[aria-live]")).toHaveAttribute(
            "aria-live",
            "polite"
        );
    });

    it("paints a border and a tone-specific fill", () => {
        render(<Callout tone="success">done</Callout>);
        const box = screen.getByText("done");
        expect(box.className).toContain("orch-outline");
        expect(box.className).toContain("border");
        expect(box.className).toContain("green");
    });
});

describe("btnSuccess", () => {
    it("shares btnPrimary's geometry with a green fill", () => {
        expect(btnSuccess).toContain("px-4 py-1.5");
        expect(btnSuccess).toContain("rounded-lg");
        expect(btnSuccess).toContain("bg-green-600");
        expect(btnSuccess).toContain("disabled:opacity-50");
    });
});

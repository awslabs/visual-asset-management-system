/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A long explanation (the workflow builder's Job Name help) is taller than the room above its icon on
 * a phone-width screen. The tooltip body is held to the height Radix measures on the side it opens and
 * scrolls, so its first lines are never above the top of the viewport. jsdom does no layout, so these
 * tests pin the bound itself; the live check is the narrow-viewport smoke.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import InfoTooltip from "./InfoTooltip";

async function openTooltip(text: React.ReactNode) {
    render(<InfoTooltip text={text} label="About this field" />);
    await userEvent.hover(screen.getByLabelText("About this field"));
    // role="tooltip" is Radix's visually hidden copy of the content for screen readers; the body on
    // screen is the one outside it.
    const a11yCopy = await screen.findByRole("tooltip");
    const bodies = screen
        .getAllByTestId("info-tooltip-body")
        .filter((el) => !a11yCopy.contains(el));
    expect(bodies).toHaveLength(1);
    return bodies[0];
}

describe("InfoTooltip body height", () => {
    it("is bounded by the height available on the side it opens", async () => {
        const body = await openTooltip("explanation text");
        expect(body.style.maxHeight).toContain("var(--radix-tooltip-content-available-height)");
        expect(body.className).toContain("overflow-y-auto");
    });

    it("holds the whole explanation inside the bounded body", async () => {
        const long = (
            <>
                <p>first paragraph</p>
                <p>last paragraph</p>
            </>
        );
        const body = await openTooltip(long);
        expect(body.textContent).toBe("first paragraphlast paragraph");
    });
});

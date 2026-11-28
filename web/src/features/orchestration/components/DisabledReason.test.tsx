/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A disabled control's reason must be readable on a phone-width screen as well as a desktop one. jsdom
 * does no layout, so these tests give the viewport, the control and the tooltip their sizes and read
 * the position Radix computes for the tooltip from them.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DisabledReasonButton, DisabledReasonTooltip } from "./DisabledReason";

const REASON = "System pipelines are managed by the deployment and cannot be archived.";
const VIEWPORT = { width: 390, height: 844 };
// A long reason fills the tooltip's 20rem width cap.
const TOOLTIP = { width: 320, height: 48 };

let controlRect = { left: 0, top: 0, width: 0, height: 0 };

const rectOf = ({ left, top, width, height }: typeof controlRect) =>
    ({
        x: left,
        y: top,
        left,
        top,
        width,
        height,
        right: left + width,
        bottom: top + height,
        toJSON: () => ({}),
    } as DOMRect);

beforeEach(() => {
    jest.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(VIEWPORT.width);
    jest.spyOn(document.documentElement, "clientHeight", "get").mockReturnValue(VIEWPORT.height);
    const rect = Element.prototype.getBoundingClientRect;
    jest.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
        this: Element
    ) {
        return this.getAttribute("data-testid") === "control"
            ? rectOf(controlRect)
            : rect.call(this);
    });
    // Radix measures the element it positions, the wrapper around the tooltip content.
    const isTooltip = (el: HTMLElement) => el.hasAttribute("data-radix-popper-content-wrapper");
    jest.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (
        this: HTMLElement
    ) {
        return isTooltip(this) ? TOOLTIP.width : 0;
    });
    jest.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (
        this: HTMLElement
    ) {
        return isTooltip(this) ? TOOLTIP.height : 0;
    });
});

afterEach(() => jest.restoreAllMocks());

/** Focuses the control and returns the tooltip's top-left corner once Radix has placed it. */
async function openAt(rect: typeof controlRect) {
    controlRect = rect;
    render(
        <DisabledReasonTooltip reason={REASON}>
            <button data-testid="control" aria-disabled="true">
                Archive
            </button>
        </DisabledReasonTooltip>
    );
    await userEvent.tab();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(REASON);
    const wrapper = tooltipWrapper();
    let corner = { x: NaN, y: NaN };
    await waitFor(() => {
        const match = /translate\((-?[\d.]+)px, (-?[\d.]+)px\)/.exec(wrapper.style.transform);
        if (!match) throw new Error(`tooltip not placed yet: ${wrapper.style.transform}`);
        corner = { x: Number(match[1]), y: Number(match[2]) };
    });
    return corner;
}

function tooltipWrapper(): HTMLElement {
    const wrapper = document.querySelector<HTMLElement>("[data-radix-popper-content-wrapper]");
    if (!wrapper) throw new Error("tooltip is not open");
    return wrapper;
}

const expectOnScreen = ({ x, y }: { x: number; y: number }) => {
    expect(x).toBeGreaterThanOrEqual(0);
    expect(x + TOOLTIP.width).toBeLessThanOrEqual(VIEWPORT.width);
    expect(y).toBeGreaterThanOrEqual(0);
    expect(y + TOOLTIP.height).toBeLessThanOrEqual(VIEWPORT.height);
};

describe("DisabledReasonTooltip at a 390 px viewport", () => {
    it("stays on screen for a control at the right edge (a menu item)", async () => {
        expectOnScreen(await openAt({ left: 182, top: 400, width: 200, height: 36 }));
    });

    it("stays on screen for a control at the left edge", async () => {
        expectOnScreen(await openAt({ left: 16, top: 400, width: 120, height: 32 }));
    });

    it("stays on screen for a control in the middle", async () => {
        expectOnScreen(await openAt({ left: 150, top: 400, width: 90, height: 32 }));
    });

    it("stays on screen for a control at the top edge", async () => {
        expectOnScreen(await openAt({ left: 270, top: 4, width: 110, height: 32 }));
    });

    it("is no wider than the viewport less its margins", async () => {
        await openAt({ left: 182, top: 400, width: 200, height: 36 });
        const content = tooltipWrapper().firstElementChild as HTMLElement;
        expect(content.style.maxWidth).toBe("min(20rem, calc(100vw - 16px))");
    });
});

describe("DisabledReasonButton", () => {
    it("is announced with its reason and runs nothing when activated", async () => {
        const onClick = jest.fn();
        render(
            <DisabledReasonButton disabledReason={REASON} onClick={onClick}>
                Delete
            </DisabledReasonButton>
        );
        const button = screen.getByRole("button", { name: "Delete" });
        expect(button).toHaveAttribute("aria-disabled", "true");
        expect(button).toHaveAttribute("aria-description", REASON);
        await userEvent.click(button);
        expect(onClick).not.toHaveBeenCalled();
    });

    it("is an ordinary button without a reason", async () => {
        const onClick = jest.fn();
        render(<DisabledReasonButton onClick={onClick}>Delete</DisabledReasonButton>);
        const button = screen.getByRole("button", { name: "Delete" });
        expect(button).not.toHaveAttribute("aria-disabled");
        await userEvent.click(button);
        expect(onClick).toHaveBeenCalledTimes(1);
    });
});

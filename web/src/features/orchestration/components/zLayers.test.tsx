/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Stacking order for portalled overlays.
 *
 * Radix portals tooltip/menu content to `document.body`, which removes it from its parent's stacking
 * context. A tooltip opened from inside a dialog is therefore a SIBLING of that dialog, and z-index
 * alone decides which paints on top. Both the info-icon tooltip and the template-instructions
 * tooltip were left at Tailwind's `z-50` against a dialog at 3001, so on the execute modal they
 * opened behind it and were invisible.
 *
 * These tests assert the ORDERING relationships rather than the literals, plus one guard that the
 * constants still match the values Dialog/Drawer actually render.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fs from "fs";
import path from "path";
import { Z } from "./zLayers";
import InfoTooltip from "./InfoTooltip";
import InstructionsPanel from "./InstructionsPanel";

describe("Z layer ordering", () => {
    it("puts tooltips above the modal layer", () => {
        // The whole bug: a tooltip opened from a dialog must paint over it.
        expect(Z.tooltip).toBeGreaterThan(Z.modal);
    });

    it("puts tooltips below toasts", () => {
        // A failure message must never be hidden behind a hover.
        expect(Z.tooltip).toBeLessThan(Z.toast);
    });

    it("keeps the modal above its own scrim and above the app nav", () => {
        expect(Z.modal).toBeGreaterThan(Z.overlay);
        expect(Z.overlay).toBeGreaterThan(Z.appNav);
    });

    it("matches the values Dialog and Drawer actually render", () => {
        // Those two still use Tailwind literals. If someone changes them there, this constant
        // becomes a lie and the next portalled component gets layered against the wrong number.
        for (const file of ["Dialog.tsx", "Drawer.tsx"]) {
            const source = fs.readFileSync(path.join(__dirname, file), "utf-8");
            expect(source).toContain(`z-[${Z.overlay}]`);
            expect(source).toContain(`z-[${Z.modal}]`);
        }
    });
});

describe("portalled overlays carry an explicit z-index", () => {
    it("InfoTooltip content renders above the modal layer", async () => {
        render(<InfoTooltip text="explanation text" label="More info" />);
        await userEvent.hover(screen.getByLabelText("More info"));

        // role="tooltip" is Radix's visually-hidden a11y copy of the text; the element that carries
        // the positioning and z-index is its parent.
        const a11yNode = await screen.findByRole("tooltip");
        const positioned = a11yNode.parentElement as HTMLElement;
        // Read the inline style: asserting the computed number is what proves the ordering, rather
        // than merely that some class is present.
        expect(Number(positioned.style.zIndex)).toBeGreaterThan(Z.modal);
    });

    it("InstructionsPanel tooltip renders above the modal layer", async () => {
        // Long text forces the tooltip form rather than the inline form.
        const long = Array.from({ length: 20 }, (_, i) => `instruction line ${i}`).join("\n");
        render(<InstructionsPanel text={long} title="Instructions" />);

        await userEvent.hover(screen.getByTestId("instructions-tooltip-trigger"));
        const content = await screen.findByTestId("instructions-tooltip-content");
        expect(Number((content as HTMLElement).style.zIndex)).toBeGreaterThan(Z.modal);
    });

    it("no portalled overlay in the module is left on Tailwind's z-50", () => {
        // z-50 is far below the modal layer, so any portalled content still carrying it is invisible
        // when opened from a dialog — the exact defect, caught for future components too.
        const dir = __dirname;
        const offenders: string[] = [];
        for (const file of fs.readdirSync(dir)) {
            if (!file.endsWith(".tsx") || file.includes(".test.")) continue;
            const source = fs.readFileSync(path.join(dir, file), "utf-8");
            if (!source.includes("Portal")) continue;
            // className only — a `z-50` mentioned in a comment is not a rendered class.
            const classAttrs = source.match(/className=(?:"[^"]*"|\{`[^`]*`\})/g) || [];
            if (classAttrs.some((a) => /\bz-50\b/.test(a))) offenders.push(file);
        }
        expect(offenders).toEqual([]);
    });
});

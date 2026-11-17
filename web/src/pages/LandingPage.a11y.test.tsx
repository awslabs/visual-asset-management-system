/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The home page carries no list whose children are not list items (#1236).
 *
 * The carousel was a `ul` holding four hidden radio inputs and the dot strip beside its four
 * slides, which axe-core reports as a serious `list` violation: the CSS needs those controls as
 * siblings of the slides, so the element is not a list and now says so. The assertion is written
 * as the rule rather than as the element, so it also covers the page's real list of links and any
 * list added later.
 *
 * The carousel can be operated with the keyboard (#1573). Its four radios are the slide controls:
 * a keyboard user Tabs to the selected one and changes slides with the arrow keys, as in any radio
 * group. That needs each radio to be named after its slide, and to stay focusable, which
 * `display: none` prevented. Jest mocks the stylesheet, so the CSS half is checked by reading
 * `styles/index.scss`; the deployed page is checked in a browser.
 */

import fs from "fs";
import path from "path";
import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import LandingPage from "./LandingPage";
import Synonyms from "../synonyms";

jest.mock("../hooks/usePageTitle", () => ({ usePageTitle: jest.fn() }));

const SLIDE_NAMES = [
    `Upload & Manage ${Synonyms.Assets}`,
    "Visualize in 3d & VR",
    "Add Pipelines",
    "Build Workflows",
];

const renderPage = () =>
    render(
        <MemoryRouter>
            <LandingPage navigationOpen={true} />
        </MemoryRouter>
    );

describe("LandingPage list semantics", () => {
    it("gives every list only list items as element children", () => {
        const { container } = render(
            <MemoryRouter>
                <LandingPage navigationOpen={true} />
            </MemoryRouter>
        );

        const lists = Array.from(container.querySelectorAll("ul, ol"));
        expect(lists.length).toBeGreaterThan(0);

        const offenders = lists.flatMap((list) =>
            Array.from(list.children)
                .filter((child) => !["LI", "SCRIPT", "TEMPLATE"].includes(child.tagName))
                .map(
                    (child) => `${list.tagName.toLowerCase()}.${list.className} > ${child.tagName}`
                )
        );
        expect(offenders).toEqual([]);
    });

    it("renders the carousel's slides without list semantics", () => {
        const { container } = render(
            <MemoryRouter>
                <LandingPage navigationOpen={true} />
            </MemoryRouter>
        );

        const slides = container.querySelector(".carousel .slides");
        expect(slides).not.toBeNull();
        expect(slides?.tagName).toBe("DIV");
        // The CSS positions each slide by its sibling radio, so the controls stay inside.
        expect(slides?.querySelectorAll(".slide-container").length).toBe(4);
        expect(slides?.querySelectorAll('input[type="radio"]').length).toBe(4);
        expect(slides?.querySelector(".carousel-dots")).not.toBeNull();
    });
});

/** The declarations of every rule in `css` whose selector list contains `selector`, merged. */
function declarations(css: string, selector: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [, selectors, body] of Array.from(css.matchAll(/([^{}]+)\{([^{}]*)\}/g))) {
        const list = selectors
            .replace(/\/\*[\s\S]*?\*\//g, "")
            .split(",")
            .map((s) => s.trim().replace(/\s+/g, " "));
        if (!list.includes(selector)) continue;
        for (const decl of body.replace(/\/\*[\s\S]*?\*\//g, "").split(";")) {
            const [prop, ...value] = decl.split(":");
            if (prop.trim() && value.length) out[prop.trim()] = value.join(":").trim();
        }
    }
    return out;
}

describe("LandingPage carousel keyboard access (#1573)", () => {
    it("names each slide control after its slide", () => {
        renderPage();

        const radios = screen.getAllByRole("radio");
        expect(radios.map((radio) => radio.getAttribute("aria-label"))).toEqual(SLIDE_NAMES);
        SLIDE_NAMES.forEach((name) => {
            expect(screen.getByRole("radio", { name })).toBeInTheDocument();
        });
    });

    it("changes the slide with the arrow keys, as a radio group does", async () => {
        const user = userEvent.setup();
        renderPage();

        await user.tab();
        expect(screen.getByRole("radio", { name: SLIDE_NAMES[0] })).toHaveFocus();
        expect(screen.getByRole("radio", { name: SLIDE_NAMES[0] })).toBeChecked();

        await user.keyboard("{ArrowRight}");
        expect(screen.getByRole("radio", { name: SLIDE_NAMES[1] })).toHaveFocus();
        expect(screen.getByRole("radio", { name: SLIDE_NAMES[1] })).toBeChecked();
        expect(screen.getByRole("radio", { name: SLIDE_NAMES[0] })).not.toBeChecked();

        await user.keyboard("{ArrowLeft}");
        expect(screen.getByRole("radio", { name: SLIDE_NAMES[0] })).toBeChecked();
    });

    describe("stylesheet", () => {
        const indexScss = fs.readFileSync(path.join(__dirname, "../styles/index.scss"), "utf8");
        const radio = declarations(indexScss, ".carousel .slides input");

        it("[control] finds the carousel radio rule", () => {
            // An empty result would make the next check pass trivially.
            expect(Object.keys(radio).length).toBeGreaterThan(0);
        });

        it("keeps the radios focusable: hidden visually, not removed from the tab order", () => {
            expect(radio.display).not.toBe("none");
            expect(radio.visibility ?? "visible").not.toBe("hidden");
            expect(radio["clip-path"]).toBe("inset(50%)");
        });

        it("anchors the radios at the bottom edge, so focusing one scrolls the dots into view", () => {
            // At the top (their static position) a focused radio left the dots below the window.
            expect(radio.position).toBe("absolute");
            expect(radio.bottom).toBe("0");
            expect(radio.top).toBeUndefined();
        });

        it("outlines the dot of the focused slide control", () => {
            [1, 2, 3, 4].forEach((n) => {
                const focusRule = declarations(
                    indexScss,
                    `.carousel input#img-${n}:focus-visible ~ .carousel-dots label#img-dot-${n}`
                );
                expect(focusRule.outline).toMatch(/^2px solid /);
            });
        });
    });
});

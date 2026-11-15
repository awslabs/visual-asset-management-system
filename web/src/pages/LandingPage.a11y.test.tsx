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
 */

import React from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import LandingPage from "./LandingPage";

jest.mock("../hooks/usePageTitle", () => ({ usePageTitle: jest.fn() }));

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

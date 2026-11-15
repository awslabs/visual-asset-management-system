/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The layout every route renders names its navigation open and close buttons, so a screen reader
 * announces them instead of a bare "button". Renders Cloudscape's real AppLayout.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { AppRoutes } from "./routes";
import { checkWebRoutesAllowed, WebRouteCheck } from "./services/webRoutesCheck";

jest.mock("./services/webRoutesCheck", () => ({ checkWebRoutesAllowed: jest.fn() }));

jest.mock("./layout/Navigation", () => ({
    Navigation: () => <div data-testid="side-navigation" />,
}));

jest.mock("./pages/LandingPage", () => ({
    __esModule: true,
    default: () => <div data-testid="landing-page" />,
}));

const mockCheckWebRoutesAllowed = checkWebRoutesAllowed as jest.MockedFunction<
    typeof checkWebRoutesAllowed
>;

function renderAt(navigationOpen: boolean) {
    mockCheckWebRoutesAllowed.mockImplementation(async (routes: WebRouteCheck[]) => routes);
    return render(
        <MemoryRouter initialEntries={["/"]}>
            <AppRoutes
                navigationOpen={navigationOpen}
                setNavigationOpen={jest.fn()}
                user={undefined}
            />
        </MemoryRouter>
    );
}

describe("AppRoutes layout accessible names", () => {
    it("names the navigation close button while the navigation is open", async () => {
        const { container } = renderAt(true);
        await waitFor(() =>
            expect(container.querySelector("[data-testid='landing-page']")).not.toBeNull()
        );
        const close = createWrapper(container).findAppLayout()!.findNavigationClose();
        expect(close).not.toBeNull();
        expect(close!.getElement().getAttribute("aria-label")).toBe("Close side navigation");
    });

    it("names the navigation open button while the navigation is closed", async () => {
        const { container } = renderAt(false);
        await waitFor(() =>
            expect(container.querySelector("[data-testid='landing-page']")).not.toBeNull()
        );
        const toggle = createWrapper(container).findAppLayout()!.findNavigationToggle();
        expect(toggle).not.toBeNull();
        expect(toggle!.getElement().getAttribute("aria-label")).toBe("Open side navigation");
    });
});

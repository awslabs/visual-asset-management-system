/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * With a customer logo configured, the side navigation header links to the Home hash route, so
 * following it stays inside the single-page app instead of reloading the document.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { Navigation } from "./Navigation";
import { checkWebRoutesAllowed } from "../services/webRoutesCheck";

jest.mock("../config", () => ({
    __esModule: true,
    default: { APP_NAME: "VAMS", CUSTOMER_LOGO: "customer-logo.png" },
}));
jest.mock("../services/webRoutesCheck", () => ({ checkWebRoutesAllowed: jest.fn() }));

describe("Navigation header with a customer logo", () => {
    it("links the logo to #/", async () => {
        (checkWebRoutesAllowed as jest.Mock).mockImplementation(async (routes: any[]) => routes);

        const { container } = render(<Navigation activeHref="#/" user={undefined} />);

        await waitFor(() => {
            expect(createWrapper(container).findSideNavigation()).toBeTruthy();
        });
        const headerLink = createWrapper(container).findSideNavigation()!.findHeaderLink();
        expect(headerLink!.getElement().getAttribute("href")).toBe("#/");
    });
});

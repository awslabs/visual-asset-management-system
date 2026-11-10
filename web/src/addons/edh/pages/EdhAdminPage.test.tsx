/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// The feature component is stubbed — the shell's own wiring is the subject.
jest.mock("../admin/EdhAdminPage", () => ({
    __esModule: true,
    default: () => <div data-testid="edh-admin-page" />,
}));

import EdhAdminPage from "./EdhAdminPage";

describe("EdhAdminPage", () => {
    beforeEach(() => {
        // Control: the starting title is a DIFFERENT page's, so a shell that sets nothing is caught.
        document.title = "VAMS - Databases";
    });

    it("titles the page", () => {
        render(
            <MemoryRouter initialEntries={["/edh/admin"]}>
                <EdhAdminPage />
            </MemoryRouter>
        );
        expect(document.title).toBe("VAMS - Workstation Admin");
    });

    it("renders the admin feature component and nothing else", () => {
        const { container } = render(
            <MemoryRouter initialEntries={["/edh/admin"]}>
                <EdhAdminPage />
            </MemoryRouter>
        );
        expect(screen.getByTestId("edh-admin-page")).toBeInTheDocument();
        expect(container.firstChild).toBe(screen.getByTestId("edh-admin-page"));
    });
});

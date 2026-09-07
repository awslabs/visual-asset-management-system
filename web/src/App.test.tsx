/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shell used to log the signed-in user on every render and write a "email" localStorage key
 * that nothing reads — and, when no user was present, wrote the literal string "undefined".
 * Both happened during render, which React 18 StrictMode double-invokes.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import App from "./App";

jest.mock("@cloudscape-design/components", () => ({
    TopNavigation: ({ utilities }: any) => (
        <div data-testid="top-navigation">
            {(utilities || []).map((utility: any) => utility.text).join(",")}
        </div>
    ),
}));

jest.mock("./routes", () => ({ AppRoutes: () => <div data-testid="app-routes" /> }));
jest.mock("./authenticator/Footer", () => ({
    PageFooter: () => <div data-testid="page-footer" />,
}));
jest.mock("./features/orchestration/components/ToastProvider", () => ({
    ToastProvider: ({ children }: any) => <>{children}</>,
}));
jest.mock("./hooks/useThemeSettings", () => ({
    useThemeSettings: () => ({ theme: "dark", setTheme: jest.fn(), density: "comfortable" }),
}));
jest.mock("aws-amplify/auth", () => ({ signOut: jest.fn().mockResolvedValue(undefined) }));

describe("App shell", () => {
    beforeEach(() => {
        localStorage.clear();
        // The shell portals its top navigation into this node.
        document.body.innerHTML = '<div id="headerWrapper"></div>';
    });

    it("does not write the user's name to a localStorage 'email' key", () => {
        localStorage.setItem("user", JSON.stringify({ username: "u1" }));

        render(<App />);

        // Positive control: the tree rendered and still reads the user, so the assertion below
        // is not satisfied by a shell that failed to render.
        expect(screen.getByTestId("page-footer")).toBeInTheDocument();
        expect(screen.getByTestId("top-navigation")).toHaveTextContent("u1");

        expect(localStorage.getItem("email")).toBeNull();
    });

    it("writes no 'email' key at all when there is no signed-in user", () => {
        // This was the case that stored the string "undefined".
        render(<App />);

        expect(screen.getByTestId("page-footer")).toBeInTheDocument();
        expect(localStorage.getItem("email")).toBeNull();
    });

    it("does not log the signed-in user to the console", () => {
        const log = jest.spyOn(console, "log").mockImplementation(() => {});
        try {
            localStorage.setItem("user", JSON.stringify({ username: "u1" }));

            render(<App />);

            const logged = log.mock.calls.map((call) => call.map(String).join(" "));
            expect(logged.filter((line) => line.includes("current user is"))).toEqual([]);
        } finally {
            log.mockRestore();
        }
    });
});

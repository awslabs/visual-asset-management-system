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
import { render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { navigation } from "./utils/sessionManager";

let mockUtilities: any[] = [];
let mockIdentity: any;
jest.mock("@cloudscape-design/components", () => ({
    TopNavigation: ({ utilities, identity }: any) => {
        mockUtilities = utilities || [];
        mockIdentity = identity;
        return (
            <div data-testid="top-navigation">
                {(utilities || []).map((utility: any) => utility.text).join(",")}
            </div>
        );
    },
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
const mockSignOut = jest.fn().mockResolvedValue(undefined);
jest.mock("aws-amplify/auth", () => ({ signOut: (...a: any[]) => mockSignOut(...a) }));
const mockLocalforageClear = jest.fn().mockResolvedValue(undefined);
jest.mock("localforage", () => ({ clear: (...a: any[]) => mockLocalforageClear(...a) }));

/** A key in the shape Amplify v6 stores the Cognito refresh token under. */
const AMPLIFY_REFRESH_TOKEN_KEY = "CognitoIdentityServiceProvider.client-1.u1.refreshToken";

describe("App shell", () => {
    beforeEach(() => {
        localStorage.clear();
        mockSignOut.mockReset().mockResolvedValue(undefined);
        mockLocalforageClear.mockReset().mockResolvedValue(undefined);
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

    it("links the header logo to the hash route for Home", () => {
        render(<App />);

        expect(mockIdentity.href).toBe("#/");
    });

    it("keeps the theme preference and removes the session keys on header sign-out", async () => {
        // jsdom reports the "/" redirect as an unimplemented navigation; that is not under test.
        const error = jest.spyOn(console, "error").mockImplementation(() => {});
        const log = jest.spyOn(console, "log").mockImplementation(() => {});
        try {
            localStorage.setItem("vams-theme-preference", "light");
            localStorage.setItem("user", JSON.stringify({ username: "u1" }));

            render(<App />);
            const userMenu = mockUtilities.find((utility) => utility.text === "u1");
            userMenu.onItemClick({ detail: { id: "signout" } });

            await waitFor(() => expect(localStorage.getItem("user")).toBeNull());
            expect(localStorage.getItem("vams-theme-preference")).toBe("light");
            expect(mockLocalforageClear).toHaveBeenCalledTimes(1);
        } finally {
            error.mockRestore();
            log.mockRestore();
        }
    });

    describe("header sign-out order", () => {
        let redirect: jest.SpyInstance;
        let error: jest.SpyInstance;
        let log: jest.SpyInstance;

        beforeEach(() => {
            redirect = jest
                .spyOn(navigation, "redirectToLogin")
                .mockImplementation(() => undefined);
            error = jest.spyOn(console, "error").mockImplementation(() => undefined);
            log = jest.spyOn(console, "log").mockImplementation(() => undefined);
            localStorage.setItem("user", JSON.stringify({ username: "u1" }));
            localStorage.setItem(AMPLIFY_REFRESH_TOKEN_KEY, "refresh-1");
        });

        afterEach(() => {
            redirect.mockRestore();
            error.mockRestore();
            log.mockRestore();
        });

        const clickSignOut = () => {
            render(<App />);
            const userMenu = mockUtilities.find((utility) => utility.text === "u1");
            userMenu.onItemClick({ detail: { id: "signout" } });
        };

        it("lets Amplify read the stored tokens before any storage is cleared", async () => {
            const order: string[] = [];
            let seenByAmplify: string | null = "not read";
            mockSignOut.mockImplementation(async () => {
                order.push("signOut:start");
                // Amplify loads the tokens asynchronously before it calls RevokeToken.
                await Promise.resolve();
                seenByAmplify = localStorage.getItem(AMPLIFY_REFRESH_TOKEN_KEY);
                order.push("signOut:done");
            });
            mockLocalforageClear.mockImplementation(async () => {
                order.push("localforage.clear");
            });
            redirect.mockImplementation(() => {
                order.push("redirect");
            });

            clickSignOut();

            await waitFor(() => expect(redirect).toHaveBeenCalledTimes(1));
            expect(seenByAmplify).toBe("refresh-1");
            expect(order).toEqual([
                "signOut:start",
                "signOut:done",
                "localforage.clear",
                "redirect",
            ]);
            expect(localStorage.getItem(AMPLIFY_REFRESH_TOKEN_KEY)).toBeNull();
            expect(localStorage.getItem("user")).toBeNull();
        });

        it("still clears storage and redirects when Amplify sign-out fails", async () => {
            mockSignOut.mockRejectedValue(new Error("network down"));

            clickSignOut();

            await waitFor(() => expect(redirect).toHaveBeenCalledTimes(1));
            expect(localStorage.getItem("user")).toBeNull();
            expect(localStorage.getItem(AMPLIFY_REFRESH_TOKEN_KEY)).toBeNull();
            expect(mockLocalforageClear).toHaveBeenCalledTimes(1);
        });
    });
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Configuration Error screen shown when /api/amplify-config cannot be loaded. A deployment that
 * restricts callers by address, or a firewall rule, answers that request with HTTP 403, so the
 * screen names a 403 among its possible causes beside the server's own status line. The module
 * mocks are the ones Auth.secureConfig.test.tsx uses to render Auth.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { appCache } from "../services/appCache";
import Auth from "./Auth";

/**
 * src/setupTests.ts mocks @aws-amplify/ui-react as `{}` globally, which makes every export
 * undefined -- rendering <Authenticator> then fails with "type is invalid". Answer whatever
 * Auth asks for instead of enumerating symbols, so adding an import to Auth.tsx later cannot
 * silently break this suite. Names beginning with "use" are hooks, not components.
 */
jest.mock("@aws-amplify/ui-react", () => {
    const react = require("react");
    const stubs = new Map();
    const stubFor = (name: string) => {
        if (name.startsWith("use")) {
            // useTheme() is read as tokens.space.xl by the federated login box.
            return () => ({ tokens: { space: { xl: "0" } } });
        }
        const Stub = (props: any) => react.createElement("div", null, props?.children);
        Stub.displayName = `AmplifyUi(${name})`;
        return Stub;
    };
    const handler = {
        get(_target: any, property: any) {
            if (property === "__esModule") return true;
            if (typeof property !== "string") return undefined;
            if (!stubs.has(property)) stubs.set(property, stubFor(property));
            return stubs.get(property);
        },
    };
    return new Proxy({}, handler);
});

jest.mock("aws-amplify", () => ({ Amplify: { configure: jest.fn() } }));

const mockGetCurrentUser = jest.fn();
jest.mock("aws-amplify/auth", () => ({
    getCurrentUser: (...args: any[]) => mockGetCurrentUser(...args),
    signOut: jest.fn().mockResolvedValue(undefined),
    signInWithRedirect: jest.fn(),
    fetchAuthSession: jest.fn().mockResolvedValue({}),
    decodeJWT: jest.fn(() => ({ payload: {} })),
}));

jest.mock("aws-amplify/utils", () => ({ Hub: { listen: jest.fn(() => () => {}) } }));

const mockGetTokenFromCodeRedirect = jest.fn();
jest.mock("@badgateway/oauth2-client", () => ({
    OAuth2Client: class {
        authorizationCode = {
            getTokenFromCodeRedirect: (...args: any[]) => mockGetTokenFromCodeRedirect(...args),
        };
    },
    generateCodeVerifier: jest.fn(),
}));

const mockGetSecureConfig = jest.fn();
const mockGetAmplifyConfig = jest.fn();
jest.mock("../services/APIService", () => ({
    getSecureConfig: (...args: any[]) => mockGetSecureConfig(...args),
    getAmplifyConfig: (...args: any[]) => mockGetAmplifyConfig(...args),
    fetchLoginProfile: jest.fn().mockResolvedValue([true, { userId: "u1", email: "u1@test" }]),
    fetchAllowedApiRoutes: jest.fn().mockResolvedValue([true, { routes: [] }]),
}));

jest.mock("../utils/sessionManager", () => {
    // The sign-out steps run for real (Amplify's signOut is the mock above), so a sign-out
    // test sees localStorage cleared the way the app clears it.
    const actual = jest.requireActual("../utils/sessionManager");
    return {
        ensureValidSession: jest.fn().mockResolvedValue(true),
        scheduleExpiryTimer: jest.fn().mockResolvedValue(undefined),
        clearExpiryTimer: jest.fn(),
        registerFocusRevalidation: jest.fn(() => () => {}),
        clearLocalStore: jest.fn().mockResolvedValue(undefined),
        clearStoredSession: actual.clearStoredSession,
        revokeSession: actual.revokeSession,
        SESSION_EXPIRED_KEY: "session_expired",
        SESSION_RETURN_TO_KEY: "session_return_to",
    };
});

jest.mock("../hooks/useThemeSettings", () => ({
    useThemeSettings: () => ({ theme: "dark", setTheme: jest.fn() }),
}));

jest.mock("../common/GlobalHeader", () => ({
    GlobalHeader: () => <div data-testid="global-header" />,
}));
jest.mock("../authenticator/Header", () => ({ Header: () => null }));
jest.mock("../authenticator/Footer", () => ({ Footer: () => null, PageFooter: () => null }));
jest.mock("../authenticator/SignInHeader", () => ({ SignInHeader: () => null }));
jest.mock("../authenticator/SignInFooter", () => ({ SignInFooter: () => null }));
jest.mock("../components/loading/LoadingScreen", () => ({
    __esModule: true,
    default: () => <div data-testid="loading-screen" />,
}));

const renderAuth = () =>
    render(
        <Auth>
            <div data-testid="app-children" />
        </Auth>
    );

describe("Auth Configuration Error screen", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        appCache.removeItem("config");
    });

    it("names an HTTP 403 refusal among the possible causes", async () => {
        mockGetAmplifyConfig.mockResolvedValue({
            _configError: true,
            _errorMessage:
                "Unable to reach the API configuration endpoint. The server returned HTTP 403 (Forbidden).",
        });

        renderAuth();

        expect(await screen.findByText("Configuration Error")).toBeInTheDocument();
        expect(
            screen.getByText(/The server returned HTTP 403 \(Forbidden\)\./)
        ).toBeInTheDocument();
        expect(
            screen.getByText(
                /The API refused the request \(HTTP 403\): the deployment accepts only some network addresses, or a firewall rule blocked the request/
            )
        ).toBeInTheDocument();
        // The other causes stay listed, and nothing of the signed-in app renders.
        expect(screen.getByText("The backend API is not responding")).toBeInTheDocument();
        expect(screen.queryByTestId("app-children")).not.toBeInTheDocument();
    });
});

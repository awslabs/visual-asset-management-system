/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * External OAuth sign-in sends one redirect_uri, without the hash route, on both the authorize
 * request and the token request. An IdP compares the two (and against its registered value), so
 * a deep link such as #/databases/<id>/assets/<id> must not change what is sent.
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { appCache } from "../services/appCache";
import Auth from "./Auth";

const mockGetAuthorizeUri = jest.fn();
const mockGetTokenFromCodeRedirect = jest.fn();
jest.mock("@badgateway/oauth2-client", () => ({
    OAuth2Client: class {
        authorizationCode = {
            getAuthorizeUri: (...args: any[]) => mockGetAuthorizeUri(...args),
            getTokenFromCodeRedirect: (...args: any[]) => mockGetTokenFromCodeRedirect(...args),
        };
    },
    generateCodeVerifier: jest.fn().mockResolvedValue("verifier-1"),
}));

/** Same Amplify UI stand-in as Auth.secureConfig.test.tsx: every export resolves to a stub. */
jest.mock("@aws-amplify/ui-react", () => {
    const react = require("react");
    const stubs = new Map();
    const stubFor = (name: string) => {
        if (name.startsWith("use")) {
            return () => ({ tokens: { space: { xl: "0" } } });
        }
        const Stub = (props: any) => react.createElement("div", null, props?.children);
        Stub.displayName = `AmplifyUi(${name})`;
        return Stub;
    };
    return new Proxy(
        {},
        {
            get(_target: any, property: any) {
                if (property === "__esModule") return true;
                if (typeof property !== "string") return undefined;
                if (!stubs.has(property)) stubs.set(property, stubFor(property));
                return stubs.get(property);
            },
        }
    );
});

jest.mock("aws-amplify", () => ({ Amplify: { configure: jest.fn() } }));
jest.mock("aws-amplify/auth", () => ({
    getCurrentUser: jest.fn().mockRejectedValue(new Error("no user")),
    signOut: jest.fn().mockResolvedValue(undefined),
    signInWithRedirect: jest.fn(),
    fetchAuthSession: jest.fn().mockResolvedValue({}),
    decodeJWT: jest.fn(() => ({ payload: {} })),
}));
jest.mock("aws-amplify/utils", () => ({ Hub: { listen: jest.fn(() => () => {}) } }));

const mockGetAmplifyConfig = jest.fn();
jest.mock("../services/APIService", () => ({
    getSecureConfig: jest.fn().mockResolvedValue({}),
    getAmplifyConfig: (...args: any[]) => mockGetAmplifyConfig(...args),
    fetchLoginProfile: jest.fn().mockResolvedValue([true, {}]),
    fetchAllowedApiRoutes: jest.fn().mockResolvedValue([true, { routes: [] }]),
}));

const mockEnsureValidSession = jest.fn();
jest.mock("../utils/sessionManager", () => ({
    ensureValidSession: (...args: any[]) => mockEnsureValidSession(...args),
    scheduleExpiryTimer: jest.fn().mockResolvedValue(undefined),
    clearExpiryTimer: jest.fn(),
    registerFocusRevalidation: jest.fn(() => () => {}),
    clearLocalStore: jest.fn().mockResolvedValue(undefined),
    clearStoredSession: jest.fn(),
    SESSION_EXPIRED_KEY: "session_expired",
    SESSION_RETURN_TO_KEY: "session_return_to",
}));

jest.mock("../hooks/useThemeSettings", () => ({
    useThemeSettings: () => ({ theme: "dark", setTheme: jest.fn() }),
}));
jest.mock("../common/GlobalHeader", () => ({ GlobalHeader: () => null }));
jest.mock("../authenticator/Header", () => ({ Header: () => null }));
jest.mock("../authenticator/Footer", () => ({ Footer: () => null, PageFooter: () => null }));
jest.mock("../authenticator/SignInHeader", () => ({ SignInHeader: () => null }));
jest.mock("../authenticator/SignInFooter", () => ({ SignInFooter: () => null }));
jest.mock("../components/loading/LoadingScreen", () => ({ __esModule: true, default: () => null }));

/** An empty cognitoUserPoolId selects external OAuth mode. */
const OAUTH2_CONFIG = {
    api: "https://api.test/api/",
    region: "",
    cognitoUserPoolId: "",
    cognitoAppClientId: "",
    cognitoIdentityPoolId: "",
    stackName: "vams-test",
    externalOAuthIdpURL: "https://idp.test",
    externalOAuthIdpClientId: "client-1",
    externalOAuthIdpTokenEndpoint: "https://idp.test/token",
    externalOAuthIdpAuthorizationEndpoint: "https://idp.test/authorize",
    externalOAuthIdpDiscoveryEndpoint: "https://idp.test/.well-known/openid-configuration",
    externalOAuthIdpScope: "openid",
    externalOAuthIdpScopeMfa: "mfa",
};

const DEEP_LINK = "/#/databases/db1/assets/asset1";

const renderAuth = () =>
    render(
        <Auth>
            <div data-testid="app-children" />
        </Auth>
    );

describe("Auth external OAuth redirect_uri", () => {
    let consoleError: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        appCache.setItem("config", OAUTH2_CONFIG);
        mockGetAmplifyConfig.mockResolvedValue({ ...OAUTH2_CONFIG });
        // Signed out: no stored token validates, so the login screen renders.
        mockEnsureValidSession.mockResolvedValue(false);
        // jsdom reports document.location assignments as unimplemented navigation.
        consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
    });

    afterEach(() => {
        consoleError.mockRestore();
        window.history.replaceState(null, "", "/");
    });

    it("sends the page URL without the hash route to the authorize request", async () => {
        window.history.replaceState(null, "", DEEP_LINK);
        mockGetAuthorizeUri.mockResolvedValue("https://idp.test/authorize?x=1");

        renderAuth();
        fireEvent.click(await screen.findByText("Log in with SSO"));

        await waitFor(() => expect(mockGetAuthorizeUri).toHaveBeenCalledTimes(1));
        const request = mockGetAuthorizeUri.mock.calls[0][0];
        expect(request.redirectUri).toBe("http://localhost/");
        // The deep link still travels in state, so sign-in returns to it.
        expect(request.state).toBe(`http://localhost${DEEP_LINK}`);
    });

    it("sends the same redirect_uri to the token request", async () => {
        localStorage.setItem("auth_state", `http://localhost${DEEP_LINK}`);
        localStorage.setItem("code_verifier", "verifier-1");
        window.history.replaceState(null, "", "/?code=abc&state=xyz");
        mockGetTokenFromCodeRedirect.mockReturnValue(new Promise(() => {}));

        renderAuth();

        await waitFor(() => expect(mockGetTokenFromCodeRedirect).toHaveBeenCalledTimes(1));
        expect(mockGetTokenFromCodeRedirect.mock.calls[0][1].redirectUri).toBe("http://localhost/");
    });
});

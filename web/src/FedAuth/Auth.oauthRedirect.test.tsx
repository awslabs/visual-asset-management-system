/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * External OAuth sign-in sends one redirect_uri, without the hash route, on both the authorize
 * request and the token request. An IdP compares the two (and against its registered value), so
 * a deep link such as #/databases/<id>/assets/<id> must not change what is sent. `state` is an
 * opaque random value: an IdP that returns it without percent-encoding would cut a URL in it at
 * `#`. The deep link is stored separately, followed only on this origin, and used once. A code
 * exchange that fails signs the browser out, which also clears the browser asset store. Auth also
 * clears that store once per page load, signed in or not.
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { appCache } from "../services/appCache";
import { MISSING_USER_ID_CLAIM_KEY } from "../utils/authTokenUtils";
import Auth from "./Auth";
import { oauthNavigation } from "./oauthSignInState";

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
const mockClearLocalStore = jest.fn();
const mockRevokeSession = jest.fn();
const mockClearStoredSession = jest.fn();
jest.mock("../utils/sessionManager", () => ({
    ensureValidSession: (...args: any[]) => mockEnsureValidSession(...args),
    scheduleExpiryTimer: jest.fn().mockResolvedValue(undefined),
    clearExpiryTimer: jest.fn(),
    registerFocusRevalidation: jest.fn(() => () => {}),
    clearLocalStore: (...args: any[]) => mockClearLocalStore(...args),
    clearStoredSession: (...args: any[]) => mockClearStoredSession(...args),
    revokeSession: (...args: any[]) => mockRevokeSession(...args),
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

/** An opaque state as the sign-in stores it: 64 hex characters. */
const STATE = "0123456789abcdef".repeat(4);

/** A token whose access token parses as a JWT with a `sub`. */
const TOKEN = {
    accessToken: `header.${btoa(JSON.stringify({ sub: "user-1" }))}.signature`,
    refreshToken: "refresh-1",
    expiresAt: Date.now() + 60 * 60 * 1000,
};

/** The values a sign-in attempt that started on the deep link leaves in localStorage. */
const storeSignInAttempt = (returnTo = `http://localhost${DEEP_LINK}`) => {
    localStorage.setItem("auth_state", STATE);
    localStorage.setItem("auth_return_to", returnTo);
    localStorage.setItem("code_verifier", "verifier-1");
};

/** The three values a sign-in attempt stores, read back. */
const storedSignInValues = () => [
    localStorage.getItem("auth_state"),
    localStorage.getItem("auth_return_to"),
    localStorage.getItem("code_verifier"),
];

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
        mockClearLocalStore.mockResolvedValue(undefined);
        mockRevokeSession.mockReset().mockResolvedValue(undefined);
        mockClearStoredSession.mockReset();
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
    });

    it("sends the same redirect_uri to the token request", async () => {
        storeSignInAttempt();
        window.history.replaceState(null, "", `/?code=abc&state=${STATE}`);
        mockGetTokenFromCodeRedirect.mockReturnValue(new Promise(() => {}));

        renderAuth();

        await waitFor(() => expect(mockGetTokenFromCodeRedirect).toHaveBeenCalledTimes(1));
        expect(mockGetTokenFromCodeRedirect.mock.calls[0][1].redirectUri).toBe("http://localhost/");
    });
});

describe("Auth sign-out after a failed code exchange", () => {
    let consoleError: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        appCache.setItem("config", OAUTH2_CONFIG);
        mockGetAmplifyConfig.mockResolvedValue({ ...OAUTH2_CONFIG });
        mockEnsureValidSession.mockResolvedValue(false);
        mockClearLocalStore.mockResolvedValue(undefined);
        mockRevokeSession.mockReset().mockResolvedValue(undefined);
        mockClearStoredSession.mockReset();
        // jsdom reports document.location assignments as unimplemented navigation.
        consoleError = jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        consoleError.mockRestore();
        window.history.replaceState(null, "", "/");
    });

    it("clears the browser asset store and records the error", async () => {
        storeSignInAttempt();
        window.history.replaceState(null, "", `/?code=abc&state=${STATE}`);
        let rejectExchange!: (error: Error) => void;
        mockGetTokenFromCodeRedirect.mockReturnValue(
            new Promise((_resolve, reject) => {
                rejectExchange = reject;
            })
        );

        renderAuth();

        await waitFor(() => expect(mockGetTokenFromCodeRedirect).toHaveBeenCalledTimes(1));
        const clearsBefore = mockClearLocalStore.mock.calls.length;

        rejectExchange(new Error("bad code"));

        await waitFor(() => expect(mockClearLocalStore).toHaveBeenCalledTimes(clearsBefore + 1));
        await waitFor(() =>
            expect(localStorage.getItem("auth_error")).toBe("Failed to get access token")
        );
    });

    it("signs out of Amplify before clearing localStorage and the asset store", async () => {
        storeSignInAttempt();
        window.history.replaceState(null, "", `/?code=abc&state=${STATE}`);
        let rejectExchange!: (error: Error) => void;
        mockGetTokenFromCodeRedirect.mockReturnValue(
            new Promise((_resolve, reject) => {
                rejectExchange = reject;
            })
        );

        renderAuth();

        await waitFor(() => expect(mockGetTokenFromCodeRedirect).toHaveBeenCalledTimes(1));
        const order: string[] = [];
        let finishRevoke!: () => void;
        mockRevokeSession.mockImplementation(() => {
            order.push("revokeSession:start");
            return new Promise<void>((resolve) => {
                finishRevoke = () => {
                    order.push("revokeSession:done");
                    resolve();
                };
            });
        });
        mockClearStoredSession.mockImplementation(() => order.push("clearStoredSession"));
        mockClearLocalStore.mockImplementation(async () => {
            order.push("clearLocalStore");
        });

        rejectExchange(new Error("bad code"));

        await waitFor(() => expect(order).toEqual(["revokeSession:start"]));
        finishRevoke();
        await waitFor(() =>
            expect(order).toEqual([
                "revokeSession:start",
                "revokeSession:done",
                "clearStoredSession",
                "clearLocalStore",
            ])
        );
        expect(localStorage.getItem("auth_error")).toBe("Failed to get access token");
    });
});

describe("Auth external OAuth state", () => {
    let consoleError: jest.SpyInstance;
    let goTo: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        appCache.setItem("config", OAUTH2_CONFIG);
        mockGetAmplifyConfig.mockResolvedValue({ ...OAUTH2_CONFIG });
        mockEnsureValidSession.mockResolvedValue(false);
        mockClearLocalStore.mockResolvedValue(undefined);
        mockRevokeSession.mockReset().mockResolvedValue(undefined);
        mockClearStoredSession.mockReset();
        goTo = jest.spyOn(oauthNavigation, "goTo").mockImplementation(() => undefined);
        // jsdom reports document.location assignments as unimplemented navigation.
        consoleError = jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        goTo.mockRestore();
        consoleError.mockRestore();
        window.history.replaceState(null, "", "/");
    });

    it("sends an opaque state, not the page URL, and stores the deep link separately", async () => {
        window.history.replaceState(null, "", DEEP_LINK);
        mockGetAuthorizeUri.mockResolvedValue("https://idp.test/authorize?x=1");

        renderAuth();
        fireEvent.click(await screen.findByText("Log in with SSO"));

        await waitFor(() => expect(mockGetAuthorizeUri).toHaveBeenCalledTimes(1));
        const { state } = mockGetAuthorizeUri.mock.calls[0][0];
        expect(state).toMatch(/^[0-9a-f]{64}$/);
        expect(state).not.toMatch(/[#/:]/);
        expect(state).not.toBe(`http://localhost${DEEP_LINK}`);
        expect(localStorage.getItem("auth_state")).toBe(state);
        expect(localStorage.getItem("auth_return_to")).toBe(`http://localhost${DEEP_LINK}`);
    });

    it("returns to the deep link when the IdP sends state back without encoding it", async () => {
        window.history.replaceState(null, "", DEEP_LINK);
        mockGetAuthorizeUri.mockResolvedValue("https://idp.test/authorize?x=1");
        const signIn = renderAuth();
        fireEvent.click(await screen.findByText("Log in with SSO"));
        await waitFor(() => expect(mockGetAuthorizeUri).toHaveBeenCalledTimes(1));
        const sentState = mockGetAuthorizeUri.mock.calls[0][0].state;
        signIn.unmount();

        // The callback as an IdP that does not percent-encode builds it. A `#` in state would
        // start the fragment here and cut the value the browser reads back.
        window.history.replaceState(null, "", `/?code=abc&state=${sentState}`);
        expect(new URL(window.location.href).searchParams.get("state")).toBe(
            localStorage.getItem("auth_state")
        );
        mockGetTokenFromCodeRedirect.mockResolvedValue(TOKEN);

        renderAuth();

        await waitFor(() => expect(goTo).toHaveBeenCalledWith(`http://localhost${DEEP_LINK}`));
        expect(mockGetTokenFromCodeRedirect.mock.calls[0][1].state).toBe(sentState);
        expect(storedSignInValues()).toEqual([null, null, null]);
    });

    it("refuses a callback whose state does not match the stored one", async () => {
        storeSignInAttempt();
        window.history.replaceState(null, "", `/?code=abc&state=${"f".repeat(64)}`);
        mockGetTokenFromCodeRedirect.mockResolvedValue(TOKEN);

        renderAuth();

        await waitFor(() =>
            expect(localStorage.getItem("auth_error")).toBe("Failed to get access token")
        );
        expect(mockGetTokenFromCodeRedirect).not.toHaveBeenCalled();
        expect(goTo).not.toHaveBeenCalled();
        expect(storedSignInValues()).toEqual([null, null, null]);
    });

    it("goes to the app root instead of a stored return page on another origin", async () => {
        storeSignInAttempt(`https://other.test${DEEP_LINK}`);
        window.history.replaceState(null, "", `/?code=abc&state=${STATE}`);
        mockGetTokenFromCodeRedirect.mockResolvedValue(TOKEN);

        renderAuth();

        await waitFor(() => expect(goTo).toHaveBeenCalledTimes(1));
        expect(goTo).toHaveBeenCalledWith("http://localhost/");
        expect(storedSignInValues()).toEqual([null, null, null]);
    });

    it("removes the stored values when the code exchange fails", async () => {
        storeSignInAttempt();
        window.history.replaceState(null, "", `/?code=abc&state=${STATE}`);
        mockGetTokenFromCodeRedirect.mockRejectedValue(new Error("bad code"));

        renderAuth();

        await waitFor(() =>
            expect(localStorage.getItem("auth_error")).toBe("Failed to get access token")
        );
        expect(goTo).not.toHaveBeenCalled();
        expect(storedSignInValues()).toEqual([null, null, null]);
    });
});

/**
 * With `externalOAuthIdpUserIdClaim` set, the deep link and the one-time sign-in values work as
 * without it: a token carrying the claim lands on the deep link, and a token lacking it signs out
 * with the sign-in attempt's values removed and the deep link not followed.
 */
describe("Auth external OAuth deep link with a configured user id claim", () => {
    const OAUTH2_CONFIG_EMAIL = { ...OAUTH2_CONFIG, externalOAuthIdpUserIdClaim: "email" };
    const tokenWith = (claims: Record<string, unknown>) => ({
        ...TOKEN,
        accessToken: `header.${btoa(JSON.stringify(claims))}.signature`,
    });
    let consoleError: jest.SpyInstance;
    let goTo: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        appCache.setItem("config", OAUTH2_CONFIG_EMAIL);
        mockGetAmplifyConfig.mockResolvedValue({ ...OAUTH2_CONFIG_EMAIL });
        mockEnsureValidSession.mockResolvedValue(false);
        mockClearLocalStore.mockResolvedValue(undefined);
        mockRevokeSession.mockReset().mockResolvedValue(undefined);
        mockClearStoredSession.mockReset();
        goTo = jest.spyOn(oauthNavigation, "goTo").mockImplementation(() => undefined);
        // jsdom reports document.location assignments as unimplemented navigation.
        consoleError = jest.spyOn(console, "error").mockImplementation(() => undefined);
    });

    afterEach(() => {
        goTo.mockRestore();
        consoleError.mockRestore();
        window.history.replaceState(null, "", "/");
    });

    it("lands on the deep link after a sign-in whose token carries the claim", async () => {
        window.history.replaceState(null, "", DEEP_LINK);
        mockGetAuthorizeUri.mockResolvedValue("https://idp.test/authorize?x=1");
        const signIn = renderAuth();
        fireEvent.click(await screen.findByText("Log in with SSO"));
        await waitFor(() => expect(mockGetAuthorizeUri).toHaveBeenCalledTimes(1));
        const sentState = mockGetAuthorizeUri.mock.calls[0][0].state;
        signIn.unmount();

        window.history.replaceState(null, "", `/?code=abc&state=${sentState}`);
        mockGetTokenFromCodeRedirect.mockResolvedValue(
            tokenWith({ sub: "user-1", email: "first.last@example.com" })
        );

        renderAuth();

        await waitFor(() => expect(goTo).toHaveBeenCalledWith(`http://localhost${DEEP_LINK}`));
        expect(JSON.parse(localStorage.getItem("user") ?? "{}").username).toBe(
            "first.last@example.com"
        );
        expect(localStorage.getItem(MISSING_USER_ID_CLAIM_KEY)).toBeNull();
        expect(storedSignInValues()).toEqual([null, null, null]);
    });

    it("removes the sign-in attempt's values when the token lacks the claim", async () => {
        storeSignInAttempt();
        window.history.replaceState(null, "", `/?code=abc&state=${STATE}`);
        mockGetTokenFromCodeRedirect.mockResolvedValue(tokenWith({ sub: "user-1" }));

        renderAuth();

        await waitFor(() => expect(localStorage.getItem(MISSING_USER_ID_CLAIM_KEY)).toBe("email"));
        // The full sign-out wipe is stubbed here, so these were removed by the attempt's own
        // clean-up, not as a side effect of clearing localStorage.
        expect(storedSignInValues()).toEqual([null, null, null]);
        expect(localStorage.getItem("oauth2_token")).toBeNull();
        expect(goTo).not.toHaveBeenCalled();
        expect(mockRevokeSession).toHaveBeenCalledTimes(1);
        expect(await screen.findByText("Sign-in failed")).toBeInTheDocument();
    });
});

describe("Auth browser asset store on page load", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        appCache.setItem("config", OAUTH2_CONFIG);
        mockGetAmplifyConfig.mockResolvedValue({ ...OAUTH2_CONFIG });
        mockEnsureValidSession.mockResolvedValue(false);
        mockClearLocalStore.mockResolvedValue(undefined);
        mockRevokeSession.mockReset().mockResolvedValue(undefined);
        mockClearStoredSession.mockReset();
    });

    it("clears the store once for a signed-out visitor", async () => {
        renderAuth();

        expect(await screen.findByText("Log in with SSO")).toBeInTheDocument();
        expect(mockClearLocalStore).toHaveBeenCalledTimes(1);
        expect(screen.queryByTestId("app-children")).not.toBeInTheDocument();
    });
});

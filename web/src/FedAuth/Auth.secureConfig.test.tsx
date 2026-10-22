/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * secure-config carries the deployment's feature switches. A single failed request used to
 * leave the app on its no-features configuration for the whole session: the failure handler
 * cleared its "fetched" ref, but nothing in the effect's dependency list ever changed again,
 * so there was no next run to retry on. Feature-gated pages then read as "not deployed".
 *
 * The effect is keyed on isLoggedIn rather than on which flow produced the session, so it is
 * shared by both auth modes. Both are driven here: Cognito (window.DISABLE_COGNITO false,
 * session from getCurrentUser) and external OAuth2 (DISABLE_COGNITO true, session from the
 * stored token). Auth renders its login screen for one commit before the session resolves, so
 * a fixture necessarily passes through the mode's login branch on the way to the signed-in
 * tree -- which is why the Amplify UI mock below has to be complete.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
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

jest.mock("@badgateway/oauth2-client", () => ({
    OAuth2Client: class {},
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

jest.mock("../utils/sessionManager", () => ({
    ensureValidSession: jest.fn().mockResolvedValue(true),
    scheduleExpiryTimer: jest.fn().mockResolvedValue(undefined),
    clearExpiryTimer: jest.fn(),
    registerFocusRevalidation: jest.fn(() => () => {}),
    clearLocalStore: jest.fn().mockResolvedValue(undefined),
    SESSION_EXPIRED_KEY: "session_expired",
    SESSION_RETURN_TO_KEY: "session_return_to",
}));

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

/** A cached config is enough to reach ampInit synchronously on the first render. */
const COGNITO_CONFIG = {
    api: "https://api.test/api/",
    region: "us-west-2",
    cognitoUserPoolId: "pool-1",
    cognitoAppClientId: "client-1",
    cognitoIdentityPoolId: "",
    stackName: "vams-test",
};

/** An empty cognitoUserPoolId is what selects external OAuth2 mode (Auth.tsx:466-475). */
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
};

const SECURE_CONFIG = {
    featuresEnabled: ["LOCATIONSERVICES"],
    locationServiceApiUrl: "https://maps.test/style",
};

/** Seed the caches so Auth reaches the signed-in tree in the given mode. */
function seedMode(config: Record<string, any>) {
    localStorage.clear();
    appCache.setItem("config", config);
    localStorage.setItem("user", JSON.stringify({ username: "u1" }));
    if (!config.cognitoUserPoolId) {
        // An accessToken present means the code-exchange effect early-returns instead of
        // treating this as a login redirect in flight.
        localStorage.setItem(
            "oauth2_token",
            JSON.stringify({
                accessToken: "h.e.s",
                refreshToken: "r1",
                expiresAt: Date.now() + 3_600_000,
            })
        );
    }
    mockGetCurrentUser.mockResolvedValue({ username: "u1" });
    // Same values back, so the amplify-config refresh is a no-op and does not itself change
    // the config reference during the test.
    mockGetAmplifyConfig.mockResolvedValue({ ...config });
}

const renderAuth = () =>
    render(
        <Auth>
            <div data-testid="app-children" />
        </Auth>
    );

describe("Auth secure-config fetch", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        seedMode(COGNITO_CONFIG);
    });

    it("positive control: one successful fetch lands the feature switches and is not repeated", async () => {
        mockGetSecureConfig.mockResolvedValue(SECURE_CONFIG);

        renderAuth();

        // Reaching the signed-in tree at all is the control for the two assertions below:
        // a fixture stuck on the login screen never runs the secure-config effect.
        expect(await screen.findByTestId("app-children")).toBeInTheDocument();
        await waitFor(() =>
            expect(appCache.getItem("config").featuresEnabled).toContain("LOCATIONSERVICES")
        );
        expect(mockGetSecureConfig).toHaveBeenCalledTimes(1);
        expect(appCache.getItem("config").locationServiceApiUrl).toBe("https://maps.test/style");
    });

    it("retries after a transient failure and applies the feature switches", async () => {
        mockGetSecureConfig
            .mockRejectedValueOnce(new Error("503 Service Unavailable"))
            .mockResolvedValue(SECURE_CONFIG);

        renderAuth();

        await waitFor(() => expect(mockGetSecureConfig).toHaveBeenCalledTimes(2), {
            timeout: 15000,
        });
        await waitFor(() =>
            expect(appCache.getItem("config").featuresEnabled).toContain("LOCATIONSERVICES")
        );
    });

    it("retries after a transient failure in external OAuth2 mode too", async () => {
        // The other auth mode reaches the same effect by a different route to isLoggedIn:
        // the stored OAuth2 token rather than getCurrentUser.
        seedMode(OAUTH2_CONFIG);
        mockGetSecureConfig
            .mockRejectedValueOnce(new Error("503 Service Unavailable"))
            .mockResolvedValue(SECURE_CONFIG);

        renderAuth();

        await waitFor(() => expect(mockGetSecureConfig).toHaveBeenCalledTimes(2), {
            timeout: 15000,
        });
        expect(await screen.findByTestId("app-children")).toBeInTheDocument();
        expect((window as any).DISABLE_COGNITO).toBe(true); // the OAuth2 branch really was taken
        await waitFor(() =>
            expect(appCache.getItem("config").featuresEnabled).toContain("LOCATIONSERVICES")
        );
    });

    it("stops after a bounded number of attempts instead of retrying forever", async () => {
        mockGetSecureConfig.mockRejectedValue(new Error("503 Service Unavailable"));

        renderAuth();

        await waitFor(() => expect(mockGetSecureConfig).toHaveBeenCalledTimes(3), {
            timeout: 20000,
        });
        // Hold past another backoff window: no fourth attempt.
        await new Promise((resolve) => setTimeout(resolve, 2500));
        expect(mockGetSecureConfig).toHaveBeenCalledTimes(3);
    }, 30000);
});

/**
 * Pages and the viewer registry read the feature switches once, when they mount, so the app
 * shell waits for secure-config when no switches are cached (a fresh sign-in), and renders at
 * once when a reload finds them cached. The wait shows the deployment banner above the loading
 * screen and lasts at most 10 seconds, so a request that never answers does not strand the user.
 */
describe("Auth holds the app until the feature switches are known", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("does not render the app while secure-config is pending with no cached switches", async () => {
        seedMode(COGNITO_CONFIG);
        let resolveSecureConfig!: (value: any) => void;
        mockGetSecureConfig.mockReturnValue(
            new Promise((resolve) => {
                resolveSecureConfig = resolve;
            })
        );

        renderAuth();

        await waitFor(() => expect(mockGetSecureConfig).toHaveBeenCalledTimes(1));
        expect(screen.queryByTestId("app-children")).not.toBeInTheDocument();

        resolveSecureConfig(SECURE_CONFIG);

        expect(await screen.findByTestId("app-children")).toBeInTheDocument();
        expect(appCache.getItem("config").featuresEnabled).toContain("LOCATIONSERVICES");
    });

    it("renders the app after the last failed attempt", async () => {
        seedMode(COGNITO_CONFIG);
        mockGetSecureConfig.mockRejectedValue(new Error("503 Service Unavailable"));

        renderAuth();

        await waitFor(() => expect(mockGetSecureConfig).toHaveBeenCalledTimes(2), {
            timeout: 15000,
        });
        expect(screen.queryByTestId("app-children")).not.toBeInTheDocument();
        await waitFor(() => expect(mockGetSecureConfig).toHaveBeenCalledTimes(3), {
            timeout: 15000,
        });
        expect(await screen.findByTestId("app-children")).toBeInTheDocument();
    }, 30000);

    it("renders at once when the switches are already cached", async () => {
        seedMode({ ...COGNITO_CONFIG, featuresEnabled: ["LOCATIONSERVICES"] });
        mockGetSecureConfig.mockReturnValue(new Promise(() => {}));

        renderAuth();

        expect(await screen.findByTestId("app-children")).toBeInTheDocument();
        expect(mockGetSecureConfig).toHaveBeenCalledTimes(1);
    });

    it("shows the deployment banner above the loading screen while it holds", async () => {
        seedMode(COGNITO_CONFIG);
        mockGetSecureConfig.mockReturnValue(new Promise(() => {}));

        renderAuth();

        await waitFor(() => expect(mockGetSecureConfig).toHaveBeenCalledTimes(1));
        const banner = screen.getByTestId("global-header");
        const loading = screen.getByTestId("loading-screen");
        expect(
            banner.compareDocumentPosition(loading) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
        expect(screen.queryByTestId("app-children")).not.toBeInTheDocument();
    });

    it("renders the app after the hold's 10-second bound when secure-config never answers", async () => {
        jest.useFakeTimers();
        try {
            seedMode(COGNITO_CONFIG);
            mockGetSecureConfig.mockReturnValue(new Promise(() => {}));

            renderAuth();

            await waitFor(() => expect(mockGetSecureConfig).toHaveBeenCalledTimes(1));
            act(() => {
                jest.advanceTimersByTime(9000);
            });
            expect(screen.queryByTestId("app-children")).not.toBeInTheDocument();

            act(() => {
                jest.advanceTimersByTime(1000);
            });
            expect(screen.getByTestId("app-children")).toBeInTheDocument();
            expect(mockGetSecureConfig).toHaveBeenCalledTimes(1);
        } finally {
            jest.useRealTimers();
        }
    });
});

describe("Auth federated Cognito detection", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockGetSecureConfig.mockResolvedValue(SECURE_CONFIG);
    });

    it.each([
        ["an IdP configuration object", { customCognitoAuthDomain: "auth.test" }, true],
        ["absent", undefined, false],
        ["null", null, false],
    ])(
        "treats cognitoFederatedConfig that is %s (%p) as federated: %p",
        async (_label, federated, expected) => {
            seedMode({ ...COGNITO_CONFIG, cognitoFederatedConfig: federated });

            renderAuth();

            await waitFor(() => expect((window as any).COGNITO_FEDERATED).toBe(expected));
        }
    );
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    clearOAuthSignInState,
    generateOAuthState,
    getReturnedOAuthState,
    getSameOriginReturnUrl,
} from "./oauthSignInState";

describe("generateOAuthState", () => {
    it("returns 256 random bits as hex, different on each call", () => {
        const first = generateOAuthState();
        expect(first).toMatch(/^[0-9a-f]{64}$/);
        expect(generateOAuthState()).not.toBe(first);
    });
});

describe("getReturnedOAuthState", () => {
    it("reads state from the query string", () => {
        expect(getReturnedOAuthState("https://app.test/?code=c&state=abc")).toBe("abc");
    });

    it("reads state from the fragment when the query carries no code or error", () => {
        expect(getReturnedOAuthState("https://app.test/#code=c&state=abc")).toBe("abc");
    });

    it("returns null when there is no state", () => {
        expect(getReturnedOAuthState("https://app.test/#/databases")).toBeNull();
    });
});

describe("getSameOriginReturnUrl", () => {
    const ROOT = "http://localhost/";

    it.each([
        [
            "a page on this origin",
            "http://localhost/#/databases/db1",
            "http://localhost/#/databases/db1",
        ],
        ["a path on this origin", "/#/assets", "http://localhost/#/assets"],
    ])("follows %s", (_label, returnTo, expected) => {
        expect(getSameOriginReturnUrl(returnTo, ROOT)).toBe(expected);
    });

    it.each([
        ["another origin", "https://other.test/#/databases"],
        ["a protocol-relative URL", "//other.test/#/databases"],
        ["another scheme", "javascript:alert(1)"],
        ["the same host on another port", "http://localhost:8080/"],
        ["nothing stored", null],
        ["an empty value", ""],
    ])("goes to the fallback for %s", (_label, returnTo) => {
        expect(getSameOriginReturnUrl(returnTo, ROOT)).toBe(ROOT);
    });
});

describe("clearOAuthSignInState", () => {
    it("removes the state, the return page and the code verifier, and nothing else", () => {
        localStorage.setItem("auth_state", "s");
        localStorage.setItem("auth_return_to", "r");
        localStorage.setItem("code_verifier", "v");
        localStorage.setItem("theme", "dark");

        clearOAuthSignInState();

        expect(localStorage.getItem("auth_state")).toBeNull();
        expect(localStorage.getItem("auth_return_to")).toBeNull();
        expect(localStorage.getItem("code_verifier")).toBeNull();
        expect(localStorage.getItem("theme")).toBe("dark");
        localStorage.clear();
    });
});

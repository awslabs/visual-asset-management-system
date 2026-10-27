/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * One external OAuth sign-in attempt keeps three values in localStorage between the authorize
 * redirect and the callback. `state` is an opaque random value (RFC 6749 section 10.12, RFC 9700),
 * not the page URL: an IdP that returns `state` without percent-encoding would otherwise put the
 * page's `#/` route into the callback URL's fragment, where the browser cuts it off. The page to
 * return to is stored on its own and only followed when it is on this origin.
 */

/** The opaque `state` sent on the authorize request; the callback must return it exactly. */
export const OAUTH_STATE_KEY = "auth_state";
/** The page, `#/` route included, that started the sign-in. */
export const OAUTH_RETURN_TO_KEY = "auth_return_to";
/** The PKCE code verifier for the attempt. */
export const OAUTH_CODE_VERIFIER_KEY = "code_verifier";

/** 256 random bits as lowercase hex: unguessable, and unchanged by an IdP that does not encode it. */
export function generateOAuthState(): string {
    const bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * The `state` in a callback URL, read where `@badgateway/oauth2-client` reads the response: the
 * query string, or the fragment when the query carries neither `code` nor `error`.
 */
export function getReturnedOAuthState(href: string): string | null {
    const url = new URL(href);
    let params = url.searchParams;
    if (!params.has("code") && !params.has("error") && url.hash.length > 0) {
        params = new URLSearchParams(url.hash.slice(1));
    }
    return params.get("state");
}

/**
 * The stored return page when it is on this origin, otherwise `fallback` (the app root), so a
 * tampered value cannot turn sign-in into an open redirect.
 */
export function getSameOriginReturnUrl(returnTo: string | null, fallback: string): string {
    if (returnTo) {
        try {
            const url = new URL(returnTo, window.location.origin);
            if (url.origin === window.location.origin) {
                return url.href;
            }
        } catch {
            // Not a URL: use the fallback.
        }
    }
    return fallback;
}

/** Remove the attempt's values; each is used once, whether the callback succeeds or fails. */
export function clearOAuthSignInState(): void {
    localStorage.removeItem(OAUTH_STATE_KEY);
    localStorage.removeItem(OAUTH_RETURN_TO_KEY);
    localStorage.removeItem(OAUTH_CODE_VERIFIER_KEY);
}

/** Navigation seam: isolates the browser redirect after the code exchange so tests can spy it. */
export const oauthNavigation = {
    goTo(url: string): void {
        window.location.href = url;
    },
};

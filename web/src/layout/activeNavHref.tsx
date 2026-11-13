/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Lets a page choose which side-navigation link is highlighted when its route's static `active`
 * href (routes.tsx) cannot say. The API keys page is one route with two views: the admin view is
 * the Admin - Auth "API Keys (all users)" link, while the user view is opened from the profile
 * menu and has no side link to highlight.
 *
 * The page passes a href derived from its own resolved state (for the API keys page, the mode
 * resolved against the API's allowed-routes listing), never from the raw URL alone. This only
 * changes the highlight; it grants nothing, and the API still checks every call.
 */

import React, { createContext, useContext, useEffect } from "react";

/** The Admin - Auth link to the tenant-wide API keys view. */
export const API_KEYS_ADMIN_NAV_HREF = "#/auth/api-keys/?mode=admin";
/** The profile menu link to the user's own API keys. */
export const API_KEYS_USER_NAV_HREF = "#/auth/api-keys/?mode=user";

/**
 * Sets the highlighted side-navigation href. `null` means "no override: use the route's active
 * href"; an empty string highlights nothing.
 */
export type SetActiveNavHref = (href: string | null) => void;

const ActiveNavHrefContext = createContext<SetActiveNavHref>(() => undefined);

export const ActiveNavHrefProvider: React.FC<{
    setActiveNavHref: SetActiveNavHref;
    children?: React.ReactNode;
}> = ({ setActiveNavHref, children }) => (
    <ActiveNavHrefContext.Provider value={setActiveNavHref}>
        {children}
    </ActiveNavHrefContext.Provider>
);

/**
 * Highlight `href` in the side navigation while the calling page is mounted. Pass `null` to keep
 * the route's own active href. The override is removed when the page unmounts, so it never
 * leaks to the next route.
 */
export function useActiveNavHref(href: string | null): void {
    const setActiveNavHref = useContext(ActiveNavHrefContext);
    useEffect(() => {
        setActiveNavHref(href);
    }, [href, setActiveNavHref]);
    useEffect(() => () => setActiveNavHref(null), [setActiveNavHref]);
}

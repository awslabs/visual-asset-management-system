/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useMemo, useState } from "react";
import type { ButtonDropdownProps } from "@cloudscape-design/components/button-dropdown";
import { checkWebRoutesAllowed } from "../services/webRoutesCheck";
import { useFeaturesEnabled } from "../context/RuntimeConfigContext";
import {
    allowedEntriesFor,
    NavEntry,
    normaliseHref,
    webRouteChecksFor,
} from "./navigationRegistry";
import { ALLOWED_API_ROUTES_CACHED_EVENT } from "../FedAuth/allowedApiRoutesCache";

/** The menu item id of Sign out, which App.tsx handles in the menu's onItemClick. */
export const SIGN_OUT_ITEM_ID = "signout";

type CheckState = "loading" | "allowed" | "failed";

/** The id prefix of a page item; App.tsx navigates the hash router for the current page's item. */
export const PAGE_ITEM_ID_PREFIX = "page:";

/** The `?query` of a hash href, as parameters ("#/auth/api-keys/?mode=user" gives mode=user). */
function hashQuery(href: string): URLSearchParams {
    const query = href.split("?")[1];
    return new URLSearchParams(query ? query.split("#")[0] : "");
}

/**
 * Whether `href` names the page at `currentHash`: the route paths match (`normaliseHref`, so a
 * trailing slash does not matter) and every query parameter the href sets has the same value in
 * the current hash, so "#/auth/api-keys/?mode=user" is the user view of the API keys page and not
 * its admin view. The API keys page keeps `?mode=` on the view it shows.
 */
export function isCurrentPage(href: string, currentHash: string): boolean {
    if (href === "" || normaliseHref(href) !== normaliseHref(currentHash || "#/")) return false;
    const current = hashQuery(currentHash);
    return Array.from(hashQuery(href).entries()).every(
        ([key, value]) => current.get(key) === value
    );
}

/**
 * One registry link as a menu-dropdown item. The page the user is on stays a link and is marked as
 * the current page: Cloudscape's public menu items have no current-page state (no `aria-current`),
 * so it carries a "Current page" label tag and says so in its accessible name. A toggle item would
 * be announced as a checked checkbox, which this is not.
 */
function linkItem(entry: NavEntry, currentHash: string | null): ButtonDropdownProps.Item {
    const href = entry.href ?? "";
    const id = `${PAGE_ITEM_ID_PREFIX}${href}`;
    const text = entry.text ?? "";
    if (currentHash !== null && isCurrentPage(href, currentHash)) {
        return {
            id,
            text,
            href: entry.href,
            labelTag: "Current page",
            ariaLabel: `${text}, current page`,
        };
    }
    return { id, text, href: entry.href };
}

/** Allowed profile entries as menu items: a section is a group headed by its text. */
export function toMenuItems(
    entries: NavEntry[],
    currentHash: string | null = null
): ButtonDropdownProps.ItemOrGroup[] {
    return entries.map((entry) =>
        entry.type === "section"
            ? {
                  id: `group:${entry.text}`,
                  text: entry.text ?? "",
                  items: (entry.items ?? []).map((item) => linkItem(item, currentHash)),
              }
            : linkItem(entry, currentHash)
    );
}

/**
 * The top-bar profile menu's items: the profile pages the API allowed (registry order: core,
 * add-on, custom), then Sign out, always last.
 *
 * The pages go through the same `web`-constraint check as the side navigation
 * (`checkWebRoutesAllowed`, batched and cached), so this adds no `auth/routes` call. The menu fails
 * closed: page links appear only once the check has succeeded, and Sign out always works. The top bar
 * lives outside the HashRouter and has no Retry control, so a failed check is retried on the next
 * `hashchange`; every `hashchange`, and every new allowed-routes listing the auth flow caches,
 * also re-renders, so a link gated on that listing (`requiresApiRoute`) appears once it is cached. The check runs again when the
 * deployment's feature switches change, so an entry restricted to a switch
 * (`featuresEnabledRestriction`) appears once the secure config carrying it lands.
 */
export function useProfileMenuItems(): ButtonDropdownProps.ItemOrGroup[] {
    const [state, setState] = useState<CheckState>("loading");
    const [allowedPaths, setAllowedPaths] = useState<string[]>([]);
    const [attempt, setAttempt] = useState(0);
    const [, setHashTick] = useState(0);
    const featuresKey = JSON.stringify(useFeaturesEnabled());
    const enabledFeatures: string[] = useMemo(() => JSON.parse(featuresKey), [featuresKey]);

    useEffect(() => {
        let cancelled = false;
        checkWebRoutesAllowed(webRouteChecksFor(["profile"], enabledFeatures))
            .then((allowed) => {
                if (cancelled) return;
                setAllowedPaths(allowed.map((route) => route.route__path));
                setState("allowed");
            })
            .catch((error) => {
                if (cancelled) return;
                console.error(error);
                setState("failed");
            });
        return () => {
            cancelled = true;
        };
    }, [attempt, enabledFeatures]);

    useEffect(() => {
        const onHashChange = () => {
            if (state === "failed") {
                setState("loading");
                setAttempt((value) => value + 1);
            } else {
                setHashTick((value) => value + 1);
            }
        };
        window.addEventListener("hashchange", onHashChange);
        return () => window.removeEventListener("hashchange", onHashChange);
    }, [state]);

    // A link gated on the API allowed-routes listing appears once the auth flow caches it.
    useEffect(() => {
        const onListingCached = () => setHashTick((value) => value + 1);
        window.addEventListener(ALLOWED_API_ROUTES_CACHED_EVENT, onListingCached);
        return () => window.removeEventListener(ALLOWED_API_ROUTES_CACHED_EVENT, onListingCached);
    }, []);

    const pageItems =
        state === "allowed"
            ? toMenuItems(
                  allowedEntriesFor("profile", allowedPaths, enabledFeatures),
                  window.location.hash || "#/"
              )
            : [];
    return [...pageItems, { id: SIGN_OUT_ITEM_ID, text: "Sign out" }];
}

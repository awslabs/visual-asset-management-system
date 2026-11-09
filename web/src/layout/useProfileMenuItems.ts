/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import type { ButtonDropdownProps } from "@cloudscape-design/components/button-dropdown";
import { checkWebRoutesAllowed } from "../services/webRoutesCheck";
import { allowedEntriesFor, NavEntry, webRouteChecksFor } from "./navigationRegistry";

/** The menu item id of Sign out, which App.tsx handles in the menu's onItemClick. */
export const SIGN_OUT_ITEM_ID = "signout";

type CheckState = "loading" | "allowed" | "failed";

/** One registry link as a menu-dropdown link item; its href navigates the hash router. */
function linkItem(entry: NavEntry): ButtonDropdownProps.Item {
    return { id: `page:${entry.href}`, text: entry.text ?? "", href: entry.href };
}

/** Allowed profile entries as menu items: a section is a group headed by its text. */
export function toMenuItems(entries: NavEntry[]): ButtonDropdownProps.ItemOrGroup[] {
    return entries.map((entry) =>
        entry.type === "section"
            ? {
                  id: `group:${entry.text}`,
                  text: entry.text ?? "",
                  items: (entry.items ?? []).map(linkItem),
              }
            : linkItem(entry)
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
 * `hashchange`; every `hashchange` also re-renders, so a link gated on the API allowed-routes
 * listing (`requiresApiRoute`) appears once that listing is cached.
 */
export function useProfileMenuItems(): ButtonDropdownProps.ItemOrGroup[] {
    const [state, setState] = useState<CheckState>("loading");
    const [allowedPaths, setAllowedPaths] = useState<string[]>([]);
    const [attempt, setAttempt] = useState(0);
    const [, setHashTick] = useState(0);

    useEffect(() => {
        let cancelled = false;
        checkWebRoutesAllowed(webRouteChecksFor("profile"))
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
    }, [attempt]);

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

    const pageItems =
        state === "allowed" ? toMenuItems(allowedEntriesFor("profile", allowedPaths)) : [];
    return [...pageItems, { id: SIGN_OUT_ITEM_ID, text: "Sign out" }];
}

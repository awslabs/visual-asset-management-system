/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The navigation registry is the one place every page link is declared. Each top-level entry has
 * a placement (side by default, or profile), each placement keeps the core, add-on, custom order,
 * and the registry-only fields never reach Cloudscape.
 */

import { appCache } from "../services/appCache";
import {
    ALLOWED_API_ROUTES_CACHE_KEY,
    ALLOWED_API_ROUTES_CACHE_TTL_MILLIS,
} from "../common/constants/authRoutes";
import {
    allNavEntries,
    allowedEntriesFor,
    entriesFor,
    normaliseHref,
    webRouteChecksFor,
} from "./navigationRegistry";

jest.mock("../addons/addonNavigation", () => ({
    addonNavMainSections: [
        {
            type: "section",
            text: "Add-on main",
            items: [{ type: "link", text: "Add-on main page", href: "#/addon-main/" }],
        },
        {
            type: "link",
            text: "Add-on profile page",
            href: "#/addon-profile/",
            placement: "profile",
        },
        {
            type: "section",
            text: "Add-on switched",
            featuresEnabledRestriction: ["PROBE_SWITCH"],
            items: [
                { type: "link", text: "Add-on switched page", href: "#/addon-switched/" },
                {
                    type: "link",
                    text: "Add-on two-switch page",
                    href: "#/addon-switched/both/",
                    featuresEnabledRestriction: ["PROBE_SWITCH", "PROBE_OTHER"],
                },
            ],
        },
    ],
    addonNavAdminSections: [
        {
            type: "section",
            text: "Add-on side",
            items: [{ type: "link", text: "Add-on side page", href: "#/addon-side/" }],
        },
        {
            type: "expandable-link-group",
            text: "Add-on unsupported group",
            href: "#/addon-group/",
            placement: "profile",
            items: [{ type: "link", text: "Grouped", href: "#/addon-group/inner/" }],
        },
        { type: "divider", placement: "profile" },
    ],
}));

jest.mock("../custom/customNavigation", () => ({
    customNavSections: [
        {
            type: "section",
            text: "Custom profile",
            placement: "profile",
            items: [
                { type: "link", text: "Custom profile page", href: "#/custom/profile/" },
                { type: "divider" },
            ],
        },
        {
            type: "section",
            text: "Custom side",
            items: [{ type: "link", text: "Custom side page", href: "#/custom/side/" }],
        },
    ],
}));

function cacheApiRoutes(...routes: { path: string; methods: string[] }[]) {
    appCache.setItemWithExpiry(
        ALLOWED_API_ROUTES_CACHE_KEY,
        { userId: "u1", routes },
        ALLOWED_API_ROUTES_CACHE_TTL_MILLIS
    );
}

function texts(entries: any[]): string[] {
    return entries.map((entry) => entry.text ?? entry.type);
}

function hrefsIn(entries: any[]): string[] {
    return entries.flatMap((entry) => [
        ...(entry.href ? [entry.href] : []),
        ...hrefsIn(entry.items ?? []),
    ]);
}

let warn: jest.SpyInstance;

beforeEach(() => {
    localStorage.clear();
    warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
    warn.mockRestore();
});

describe("placement", () => {
    it("defaults an entry with no placement to the side navigation", () => {
        const side = texts(entriesFor("side"));
        expect(side).toEqual(
            expect.arrayContaining(["Home", "Manage", "Add-on side", "Custom side"])
        );
        expect(texts(entriesFor("profile"))).not.toContain("Manage");
    });

    it("keeps core, then add-on, then custom within each placement", () => {
        const side = texts(entriesFor("side"));
        expect(side.indexOf("Admin - Auth")).toBeLessThan(side.indexOf("Add-on side"));
        expect(side.indexOf("Add-on side")).toBeLessThan(side.indexOf("Custom side"));

        const profile = texts(entriesFor("profile"));
        expect(profile).toEqual(["Your pages", "Add-on profile page", "Custom profile"]);
    });

    it("places the add-on main-menu sections after Orchestrate & Automate, ahead of the divider", () => {
        const side = texts(entriesFor("side"));
        expect(side.indexOf("Add-on main")).toBe(side.indexOf("Orchestrate & Automate") + 1);
        expect(side.indexOf("divider")).toBe(side.indexOf("Add-on main") + 1);
        expect(side.indexOf("Admin - Data")).toBeGreaterThan(side.indexOf("divider"));
    });

    it("places the add-on admin sections after the core admin sections, before the fork's", () => {
        const side = texts(entriesFor("side"));
        expect(side.indexOf("Add-on side")).toBe(side.indexOf("Admin - Auth") + 1);
        expect(side.indexOf("Custom side")).toBe(side.indexOf("Add-on side") + 1);
    });

    it("lists every entry once across the two placements", () => {
        const all = allNavEntries().length;
        // Two profile entries in the add-on mock are unsupported and skipped, and one side section
        // is restricted to a feature switch that is off.
        expect(entriesFor("side").length + entriesFor("profile").length).toBe(all - 3);
    });

    it("moves API Key Management out of the side navigation and drops the User section", () => {
        expect(texts(entriesFor("side"))).not.toContain("User");
        const profileHrefs = hrefsIn(entriesFor("profile"));
        expect(profileHrefs).toContain("#/auth/api-keys/?mode=user");
        expect(hrefsIn(entriesFor("side"))).not.toContain("#/auth/api-keys/?mode=user");
    });

    it("strips placement and requiresApiRoute before entries reach Cloudscape", () => {
        cacheApiRoutes({ path: "/auth/api-keys", methods: ["GET"] });
        const all = [...entriesFor("side"), ...entriesFor("profile")];
        const serialised = JSON.stringify(all);
        expect(serialised).not.toContain("placement");
        expect(serialised).not.toContain("requiresApiRoute");
        // Positive control: the gated link is present, so its field was stripped, not dropped.
        expect(hrefsIn(all)).toContain("#/auth/api-keys/?mode=admin");
    });

    it("skips unsupported profile shapes with a warning", () => {
        const profile = entriesFor("profile");
        expect(texts(profile)).not.toContain("Add-on unsupported group");
        expect(texts(profile)).not.toContain("divider");
        // A divider inside a profile section is skipped too; its link stays.
        const custom = profile.find((entry) => entry.text === "Custom profile");
        expect(texts(custom?.items ?? [])).toEqual(["Custom profile page"]);
        // Each skipped entry is reported once per page load; a fresh module copy reports again.
        jest.isolateModules(() => {
            // eslint-disable-next-line @typescript-eslint/no-var-requires
            require("./navigationRegistry").entriesFor("profile");
        });
        const warnings = warn.mock.calls.map((call) => String(call[0]));
        expect(warnings.some((w) => w.includes("Add-on unsupported group"))).toBe(true);
        expect(warnings.some((w) => w.includes("divider"))).toBe(true);
    });
});

describe("User Management and the authentication mode", () => {
    const userManagementHref = "#/auth/cognitousers/";
    const originalDisableCognito = window.DISABLE_COGNITO;

    afterEach(() => {
        window.DISABLE_COGNITO = originalDisableCognito;
    });

    function adminAuthHrefs(): string[] {
        const adminAuth = entriesFor("side").find((entry) => entry.text === "Admin - Auth");
        expect(adminAuth).toBeDefined();
        return hrefsIn([adminAuth]);
    }

    it("shows User Management under Admin - Auth in Cognito mode", () => {
        window.DISABLE_COGNITO = false;
        expect(adminAuthHrefs()).toContain(userManagementHref);
    });

    it("hides User Management in external OAuth mode (window.DISABLE_COGNITO)", () => {
        window.DISABLE_COGNITO = true;
        const hrefs = adminAuthHrefs();
        expect(hrefs).not.toContain(userManagementHref);
        // Positive control: the rest of Admin - Auth is still listed.
        expect(hrefs).toContain("#/auth/roles/");
        // And it does not reappear in the profile menu.
        expect(hrefsIn(entriesFor("profile"))).not.toContain(userManagementHref);
    });
});

describe("requiresApiRoute", () => {
    const adminHref = "#/auth/api-keys/?mode=admin";

    it("hides the admin API keys link while the API listing is unknown", () => {
        expect(hrefsIn(entriesFor("side"))).not.toContain(adminHref);
    });

    it("hides the admin API keys link when the listing does not allow GET /auth/api-keys", () => {
        cacheApiRoutes(
            { path: "/auth/user/api-keys", methods: ["GET"] },
            { path: "/auth/api-keys", methods: ["POST"] }
        );
        expect(hrefsIn(entriesFor("side"))).not.toContain(adminHref);
    });

    it("hides it when the cached listing has expired", () => {
        appCache.setItemWithExpiry(
            ALLOWED_API_ROUTES_CACHE_KEY,
            { userId: "u1", routes: [{ path: "/auth/api-keys", methods: ["GET"] }] },
            -1
        );
        expect(hrefsIn(entriesFor("side"))).not.toContain(adminHref);
    });

    it("shows it under Admin - Auth when the listing allows GET /auth/api-keys", () => {
        cacheApiRoutes({ path: "/auth/api-keys", methods: ["GET"] });
        const adminAuth = entriesFor("side").find((entry) => entry.text === "Admin - Auth");
        expect(hrefsIn([adminAuth])).toContain(adminHref);
    });

    it("still needs the web route: the API listing alone does not show the link", () => {
        cacheApiRoutes({ path: "/auth/api-keys", methods: ["GET"] });
        expect(hrefsIn(allowedEntriesFor("side", ["/databases"]))).not.toContain(adminHref);
        expect(hrefsIn(allowedEntriesFor("side", ["/auth/api-keys"]))).toContain(adminHref);
    });
});

describe("route checks", () => {
    it("drops a ?query and a trailing slash from an href", () => {
        expect(normaliseHref("#/auth/api-keys/?mode=user")).toBe("/auth/api-keys");
        expect(normaliseHref("#/databases/")).toBe("/databases");
        expect(normaliseHref("#/")).toBe("/");
    });

    it("checks each web route once, whatever its API requirement", () => {
        const checks = webRouteChecksFor(["side", "profile"]).map((route) => route.route__path);
        expect(checks.filter((path) => path === "/auth/api-keys")).toHaveLength(1);
        expect(checks).toEqual(
            expect.arrayContaining(["/addon-profile", "/custom/profile", "/custom/side"])
        );
        expect(checks.every((path) => !path.includes("?"))).toBe(true);
    });
});

describe("featuresEnabledRestriction", () => {
    it("drops an entry whose feature switch is off, and does not check its route", () => {
        expect(texts(entriesFor("side"))).not.toContain("Add-on switched");
        const checks = webRouteChecksFor(["side"]).map((route) => route.route__path);
        expect(checks).not.toContain("/addon-switched");
        expect(checks).toContain("/addon-main");
    });

    it("keeps an entry only while every switch it lists is on, and strips the field", () => {
        const side = entriesFor("side", ["PROBE_SWITCH"]);
        const switched = side.find((entry) => entry.text === "Add-on switched");
        expect(hrefsIn([switched])).toEqual(["#/addon-switched/"]);
        expect(JSON.stringify(side)).not.toContain("featuresEnabledRestriction");
        expect(webRouteChecksFor(["side"], ["PROBE_SWITCH"]).map((r) => r.route__path)).toEqual(
            expect.arrayContaining(["/addon-switched"])
        );

        const both = entriesFor("side", ["PROBE_SWITCH", "PROBE_OTHER"]);
        expect(hrefsIn(both)).toContain("#/addon-switched/both/");
    });
});

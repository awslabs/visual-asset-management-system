/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    PERMISSIONS_FAILED_REASON,
    PERMISSIONS_LOADING_REASON,
    routeDisabledReason,
} from "./routeDisabledReason";

describe("routeDisabledReason", () => {
    it("disables with the loading text while the check runs, whatever it will say", () => {
        expect(routeDisabledReason(true, false, true, "denied")).toBe(PERMISSIONS_LOADING_REASON);
        expect(routeDisabledReason(true, false, false, "denied")).toBe(PERMISSIONS_LOADING_REASON);
        expect(routeDisabledReason(true, true, false, "denied")).toBe(PERMISSIONS_LOADING_REASON);
    });

    it("says the check failed, not that the role is refused, when the check failed", () => {
        expect(routeDisabledReason(false, true, false, "denied")).toBe(PERMISSIONS_FAILED_REASON);
        expect(PERMISSIONS_FAILED_REASON).not.toMatch(/Your role does not allow/);
    });

    it("gives the denied reason once the route is not allowed", () => {
        expect(routeDisabledReason(false, false, false, "denied")).toBe("denied");
    });

    it("gives no reason, so the action is enabled, once the route is allowed", () => {
        expect(routeDisabledReason(false, false, true, "denied")).toBeUndefined();
    });
});

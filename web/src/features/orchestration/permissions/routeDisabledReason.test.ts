/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { PERMISSIONS_LOADING_REASON, routeDisabledReason } from "./routeDisabledReason";

describe("routeDisabledReason", () => {
    it("disables with the loading text while the check runs, whatever it will say", () => {
        expect(routeDisabledReason(true, true, "denied")).toBe(PERMISSIONS_LOADING_REASON);
        expect(routeDisabledReason(true, false, "denied")).toBe(PERMISSIONS_LOADING_REASON);
    });

    it("gives the denied reason once the route is not allowed", () => {
        expect(routeDisabledReason(false, false, "denied")).toBe("denied");
    });

    it("gives no reason, so the action is enabled, once the route is allowed", () => {
        expect(routeDisabledReason(false, true, "denied")).toBeUndefined();
    });
});

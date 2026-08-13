/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The route table is also the source of the web-route permission list (`POST /auth/routes`) and of the
 * constraint editor's path options, so every entry must address a page that can actually render at that
 * path — a path whose component needs a parameter the path does not declare renders an error page.
 */

import { matchRoutes } from "react-router-dom";
import { routeTable } from "./routes";

const pageFor = (path: string) => routeTable.find((route) => route.path === path)?.Page;

describe("routeTable", () => {
    it("resolves /workflows/create to a page that needs no route parameters", () => {
        // Workflows are database-scoped: the builder reads :databaseId from the path and renders
        // "Missing Database ID" without one, so an unscoped create path must not point at it.
        const matched = matchRoutes([{ path: "/workflows/create" }], "/workflows/create");
        expect(matched?.[0].params).toEqual({});
        expect(pageFor("/workflows/create")).toBe(pageFor("/workflows"));
        expect(pageFor("/workflows/create")).not.toBe(
            pageFor("/databases/:databaseId/workflows/create")
        );
    });

    it("declares :databaseId on every path the workflow builder serves", () => {
        const builder = pageFor("/databases/:databaseId/workflows/create");
        for (const route of routeTable.filter((r) => r.Page === builder)) {
            expect(route.path).toContain(":databaseId");
        }
    });
});

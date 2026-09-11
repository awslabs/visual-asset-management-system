/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The route table is also the source of the web-route permission list (`POST /auth/routes`) and of the
 * constraint editor's path options, so every entry must address a page that can actually render at that
 * path — a path whose component needs a parameter the path does not declare renders an error page.
 *
 * The rendering tests below cover the two behaviours AppRoutes owns beyond the table itself: the
 * fail-open list used when the permission check is unavailable, and the per-page error boundary that
 * keeps a render error inside the layout instead of blanking the whole app.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { matchRoutes, MemoryRouter } from "react-router-dom";
import { routeTable, AppRoutes } from "./routes";
import { checkWebRoutesAllowed, WebRouteCheck } from "./services/webRoutesCheck";

jest.mock("./services/webRoutesCheck", () => ({ checkWebRoutesAllowed: jest.fn() }));

// The layout is Cloudscape's; what matters here is which slot each thing lands in, so the
// navigation slot can be asserted to survive a page that throws.
jest.mock("@cloudscape-design/components/app-layout", () => ({
    __esModule: true,
    default: ({ content, navigation }: any) => (
        <div>
            <div data-testid="layout-navigation">{navigation}</div>
            <div data-testid="layout-content">{content}</div>
        </div>
    ),
}));

jest.mock("./layout/Navigation", () => ({
    Navigation: () => <div data-testid="side-navigation" />,
}));

let mockLandingThrows = false;
jest.mock("./pages/LandingPage", () => ({
    __esModule: true,
    default: () => {
        if (mockLandingThrows) {
            throw new Error("landing page render exploded");
        }
        return <div data-testid="landing-page" />;
    },
}));

jest.mock("./pages/PipelinesPage2", () => ({
    __esModule: true,
    default: () => <div data-testid="pipelines-page" />,
}));

const mockCheckWebRoutesAllowed = checkWebRoutesAllowed as jest.MockedFunction<
    typeof checkWebRoutesAllowed
>;

const pageFor = (path: string) => routeTable.find((route) => route.path === path)?.Page;

/** Names of the parameters a path declares, e.g. "/a/:b/c" -> ["b"]. */
const paramNames = (path: string) =>
    path
        .split("/")
        .filter((segment) => segment.startsWith(":"))
        .map((segment) => segment.slice(1));

/** A concrete URL for a path template, with a recognisable value per declared parameter. */
const concreteUrlFor = (path: string) =>
    path
        .split("/")
        .map((segment) => {
            if (segment.startsWith(":")) return `val-${segment.slice(1)}`;
            if (segment === "*") return "some/nested/file.glb";
            return segment;
        })
        .join("/");

/** Entries serving `page` from a path that does not declare every one of `params`. */
function pathsMissingParams(
    table: { path: string; Page: React.FC }[],
    page: React.FC | undefined,
    params: string[]
): string[] {
    return table
        .filter((route) => route.Page === page)
        .filter((route) => params.some((name) => !route.path.includes(`:${name}`)))
        .map((route) => route.path);
}

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

    it.each(routeTable.filter((route) => route.path !== "*").map((route) => route.path))(
        "matches %s back to its own page with every declared parameter bound",
        (path) => {
            const matched = matchRoutes(routeTable as any, concreteUrlFor(path));
            expect(matched).not.toBeNull();
            const leaf = matched![matched!.length - 1];
            expect((leaf.route as any).Page).toBe(pageFor(path));
            for (const name of paramNames(path)) {
                expect(leaf.params[name]).toBe(`val-${name}`);
            }
        }
    );

    it.each([
        ["/databases/db1/pipelines/create", { databaseId: "db1" }],
        ["/databases/db1/pipelines/p1/templates/create", { databaseId: "db1", pipelineId: "p1" }],
        ["/databases/db1/workflows/create", { databaseId: "db1" }],
    ])("ranks the static create segment in %s above the id parameter", (url, expected) => {
        // Reordering the table so an :id route out-ranks its sibling "create" route would load
        // the edit page with id === "create" — the suite must not stay green for that.
        const matched = matchRoutes(routeTable as any, url as string);
        const leaf = matched![matched!.length - 1];
        expect(leaf.params).toEqual(expected);
    });

    it("routes /executions/:executionId to the execution detail page, not the board", () => {
        const matched = matchRoutes(routeTable as any, "/executions/exec-1");
        const leaf = matched![matched!.length - 1];
        expect((leaf.route as any).Page).toBe(pageFor("/executions/:executionId"));
        expect((leaf.route as any).Page).not.toBe(pageFor("/executions"));
        expect(leaf.params.executionId).toBe("exec-1");
    });

    // Each orchestration route shell renders a "Missing ..." panel when one of these is absent,
    // so no path may serve that shell without declaring them. Keyed by a sample path rather
    // than by component, because the components are lazy.
    it.each([
        ["/databases/:databaseId/pipelines/create", ["databaseId"]],
        ["/databases/:databaseId/pipelines/:pipelineId/templates", ["databaseId", "pipelineId"]],
        [
            "/databases/:databaseId/pipelines/:pipelineId/templates/create",
            ["databaseId", "pipelineId"],
        ],
        ["/databases/:databaseId/workflows/create", ["databaseId"]],
        ["/databases/:databaseId/workflows/:workflowId/triggers", ["databaseId", "workflowId"]],
        ["/executions/:executionId", ["executionId"]],
    ])("only serves the page behind %s from paths declaring %p", (samplePath, params) => {
        expect(
            pathsMissingParams(routeTable, pageFor(samplePath as string), params as string[])
        ).toEqual([]);
    });

    it("positive control: the parameter guard flags a path that omits a required parameter", () => {
        // Without this, an assertion of "no offending paths" would also pass if the filter
        // resolved nothing at all.
        const page = pageFor("/executions/:executionId");
        const badTable = [{ path: "/executions", Page: page as React.FC, active: "/" }];
        expect(pathsMissingParams(badTable, page, ["executionId"])).toEqual(["/executions"]);
    });
});

describe("AppRoutes", () => {
    /** Resolve the permission check by allowing only the given route paths. */
    function allowRoutes(...allowedPaths: string[]) {
        mockCheckWebRoutesAllowed.mockImplementation(async (routes: WebRouteCheck[]) =>
            routes.filter((route) => allowedPaths.includes(route.route__path))
        );
    }

    function renderAt(pathname: string) {
        return render(
            <MemoryRouter initialEntries={[pathname]}>
                <AppRoutes navigationOpen={true} setNavigationOpen={jest.fn()} user={undefined} />
            </MemoryRouter>
        );
    }

    beforeEach(() => {
        jest.clearAllMocks();
        mockLandingThrows = false;
    });

    it("falls open to the landing page only when the permission check fails", async () => {
        mockCheckWebRoutesAllowed.mockRejectedValue(new Error("routes check unavailable"));

        renderAt("/pipelines");

        // "/" and "*" are added back so the app is not a blank screen; the gated route is not.
        await waitFor(() => expect(screen.getByTestId("landing-page")).toBeInTheDocument());
        expect(screen.queryByTestId("pipelines-page")).not.toBeInTheDocument();
    });

    it("positive control: renders the gated page when the permission check allows it", async () => {
        // Proves the assertion above is about the fail-open list rather than about a harness
        // that never renders a gated page at all.
        allowRoutes("/pipelines");

        renderAt("/pipelines");

        await waitFor(() => expect(screen.getByTestId("pipelines-page")).toBeInTheDocument());
        expect(screen.queryByTestId("landing-page")).not.toBeInTheDocument();
    });

    it("contains a page render error inside the layout instead of blanking the app", async () => {
        const consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
        try {
            mockLandingThrows = true;
            allowRoutes("/");

            renderAt("/");

            await waitFor(() =>
                expect(screen.getByText("Something went wrong on this page")).toBeInTheDocument()
            );
            // The shell survives: the navigation slot is still populated, and there is a way out.
            expect(screen.getByTestId("side-navigation")).toBeInTheDocument();
            expect(screen.getByText("Go to Home")).toBeInTheDocument();
        } finally {
            consoleError.mockRestore();
        }
    });

    it("positive control: no error panel when the page renders normally", async () => {
        allowRoutes("/");

        renderAt("/");

        await waitFor(() => expect(screen.getByTestId("landing-page")).toBeInTheDocument());
        expect(screen.queryByText("Something went wrong on this page")).not.toBeInTheDocument();
    });
});

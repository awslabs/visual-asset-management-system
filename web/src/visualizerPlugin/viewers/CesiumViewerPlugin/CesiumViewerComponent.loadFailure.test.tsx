/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * How the Cesium viewer reports tilesets and tiles that fail to load. A tileset that fails beside
 * others that loaded, and failed tiles of a loaded tileset, are a warning over the scene; only a load
 * in which every tileset failed replaces the scene with the error view. A new load clears the
 * warnings of the last one. The warning sits in one notice stack at the top of the scene with the
 * initialization error and the file version notice, clear of the host's viewer controls along the
 * bottom; the viewer's own panels start below the stack, and pointer input between the notices
 * reaches the scene. A dismissed warning stays closed until the next load. The tilesets load whether
 * or not a Cesium Ion token is configured.
 *
 * `window.Cesium` is a stub: every member the viewer touches resolves to another stub, except the
 * tileset loader, which answers per tileset key.
 */
import React from "react";
import { render, screen, waitFor, act, fireEvent, within } from "@testing-library/react";
import CesiumViewerComponent from "./CesiumViewerComponent";

jest.mock("./dependencies", () => ({
    CesiumDependencyManager: { loadCesium: jest.fn(() => Promise.resolve()), cleanup: jest.fn() },
}));
jest.mock("./components/CesiumSceneGraph", () => ({ __esModule: true, default: () => null }));
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: () => ({ api: "https://api.example/api/" }) },
}));
jest.mock("../../../utils/authTokenUtils", () => ({
    getDualValidAccessToken: jest.fn(() => Promise.resolve("t")),
}));

/** A value standing in for any Cesium object: members, calls and constructions yield more stubs. */
function stub(): any {
    const values: Record<PropertyKey, unknown> = {};
    // A function target, so the stub can also be called and constructed.
    return new Proxy(
        function () {
            return undefined;
        } as any,
        {
            get(_target, prop) {
                if (prop in values) return values[prop];
                if (prop === "then") return undefined;
                if (prop === Symbol.toPrimitive) return () => 0;
                if (typeof prop === "symbol") return undefined;
                if (prop === "valueOf") return () => 0;
                if (prop === "toString") return () => "";
                values[prop] = stub();
                return values[prop];
            },
            set(_target, prop, value) {
                values[prop] = value;
                return true;
            },
            apply: () => stub(),
            construct: () => stub(),
        }
    );
}

/** A tileset whose tileFailed event the test raises. */
function tileset() {
    const listeners: ((event: any) => void)[] = [];
    const t = stub();
    t.boundingSphere = undefined;
    t.tileFailed = {
        addEventListener: (listener: (event: any) => void) => listeners.push(listener),
    };
    t.raiseTileFailed = (event: any) => listeners.forEach((listener) => listener(event));
    return t;
}

const loaded: Record<string, any> = {};
let answers: Record<string, () => Promise<any>> = {};

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    jest.spyOn(console, "warn").mockImplementation(() => undefined);
    jest.spyOn(console, "error").mockImplementation(() => undefined);
    const cesium = stub();
    cesium.Resource = function (this: any, options: any) {
        this.url = options.url;
    };
    cesium.Cesium3DTileset = {
        fromUrl: (resource: { url: string }) => {
            const key = Object.keys(answers).find((k) => resource.url.endsWith(`/${k}`));
            if (!key) return Promise.reject(new Error(`unexpected tileset ${resource.url}`));
            return answers[key]();
        },
    };
    (window as any).Cesium = cesium;
    answers = {};
    Object.keys(loaded).forEach((key) => delete loaded[key]);
});

afterEach(() => {
    jest.restoreAllMocks();
    delete (window as any).Cesium;
});

const loads = (key: string) => () => {
    loaded[key] = tileset();
    return Promise.resolve(loaded[key]);
};
const fails = (statusCode: number, response?: string) => () =>
    Promise.reject({ statusCode, response });

const props = { assetId: "asset1", databaseId: "db1" } as any;
const warning = () => screen.queryByTestId("cesium-load-warning");
/** The viewer's scene panel: the fixed-position ancestor of its Hide panel button. */
const scenePanel = () => {
    let node: HTMLElement | null = screen.getByTitle("Hide panel");
    while (node && node.style.position !== "fixed") node = node.parentElement;
    return node;
};

describe("Cesium viewer load failures", () => {
    it("warns over the scene when one of several tilesets fails", async () => {
        answers = { "a.json": loads("a.json"), "b.json": fails(404) };
        render(<CesiumViewerComponent {...props} multiFileKeys={["a.json", "b.json"]} />);
        await waitFor(() => expect(warning()).not.toBeNull());
        expect(warning()).toHaveTextContent("Incomplete 3D Tileset");
        expect(warning()).toHaveTextContent(
            '1 of 2 tilesets failed to load. Unable to load the tileset "b.json": it was not found.'
        );
        expect(screen.queryByText("Error Loading 3D Tileset")).not.toBeInTheDocument();
    });

    it("stacks the warning under the file version notice at the top of the scene", async () => {
        answers = { "a.json": loads("a.json"), "b.json": fails(404) };
        render(
            <CesiumViewerComponent {...props} versionId="v1" multiFileKeys={["a.json", "b.json"]} />
        );
        await waitFor(() => expect(warning()).not.toBeNull());
        const notices = screen.getByTestId("cesium-notices");
        expect(notices).toHaveStyle({ position: "absolute", top: "0px" });
        expect(within(notices).getByText(/Specific file versions cannot be viewed/)).toBeVisible();
        expect(notices).toContainElement(warning());
        expect(warning()?.style.bottom).toBe("");

        fireEvent.click(screen.getByTitle("Dismiss warning"));
        expect(warning()).toBeNull();
        expect(screen.getByText(/Specific file versions cannot be viewed/)).toBeInTheDocument();
    });

    it("shows the error view when every tileset fails", async () => {
        answers = { "a.json": fails(404), "b.json": fails(429) };
        render(<CesiumViewerComponent {...props} multiFileKeys={["a.json", "b.json"]} />);
        expect(await screen.findByText("Error Loading 3D Tileset")).toBeInTheDocument();
        expect(
            screen.getByText(
                '2 of 2 tilesets failed to load. Unable to load the tileset "a.json": it was not found.'
            )
        ).toBeInTheDocument();
    });

    it("names a blocked tileset in the error view", async () => {
        answers = {
            "city.json": fails(
                403,
                JSON.stringify({
                    message: "Request blocked by the deployment's AWS WAF web ACL",
                    blockedBy: "waf",
                    requestId: "r-1",
                })
            ),
        };
        render(<CesiumViewerComponent {...props} assetKey="city.json" />);
        expect(
            await screen.findByText(
                'Unable to load the tileset "city.json": Request blocked by the deployment\'s AWS WAF web ACL (request ID r-1).'
            )
        ).toBeInTheDocument();
    });

    it("counts failed tiles of a loaded tileset and clears the count on the next load", async () => {
        answers = { "city.json": loads("city.json"), "town.json": loads("town.json") };
        const { rerender } = render(<CesiumViewerComponent {...props} assetKey="city.json" />);
        await waitFor(() => expect(loaded["city.json"]).toBeDefined());
        act(() => {
            loaded["city.json"].raiseTileFailed({
                message: "Request has failed. Status Code: 404",
            });
            loaded["city.json"].raiseTileFailed({
                message: "Request has failed. Status Code: 429",
            });
        });
        await waitFor(() =>
            expect(warning()).toHaveTextContent(
                "2 tile requests failed, so the model may be incomplete. Last error: Request has failed. Status Code: 429"
            )
        );
        expect(screen.queryByText("Error Loading 3D Tileset")).not.toBeInTheDocument();

        rerender(<CesiumViewerComponent {...props} assetKey="town.json" />);
        await waitFor(() => expect(loaded["town.json"]).toBeDefined());
        await waitFor(() => expect(warning()).toBeNull());
    });

    it("stacks the initialization error, the warning and the file version notice", async () => {
        (window as any).Cesium.createWorldTerrainAsync = () =>
            Promise.reject(new Error("no terrain"));
        answers = { "a.json": loads("a.json"), "b.json": fails(404) };
        render(
            <CesiumViewerComponent
                {...props}
                versionId="v1"
                customParameters={{ cesiumIonToken: "t" }}
                multiFileKeys={["a.json", "b.json"]}
            />
        );
        await waitFor(() => expect(warning()).not.toBeNull());
        const notices = screen.getByTestId("cesium-notices");
        await waitFor(() =>
            expect(within(notices).getByText(/Terrain loading failed: no terrain/)).toBeVisible()
        );
        expect(notices).toContainElement(warning());
        expect(within(notices).getByText(/Specific file versions cannot be viewed/)).toBeVisible();
        const initErrorNotice = screen.getByTitle("Dismiss error").parentElement;
        const versionNotice = within(notices).getByText(
            /Specific file versions cannot be viewed/
        ).parentElement;
        expect(Array.from(notices.children)).toEqual([initErrorNotice, versionNotice, warning()]);
        // The stack spans the scene's width; only the notices themselves take pointer input.
        expect(notices).toHaveStyle({ pointerEvents: "none" });
        for (const notice of notices.children) {
            expect(notice).toHaveStyle({ pointerEvents: "auto" });
        }
    });

    it("starts the scene panel below the notices", async () => {
        jest.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (
            this: HTMLElement
        ) {
            return this.dataset.testid === "cesium-notices" ? 90 : 0;
        });
        answers = { "a.json": loads("a.json"), "b.json": fails(404) };
        render(<CesiumViewerComponent {...props} multiFileKeys={["a.json", "b.json"]} />);
        await waitFor(() => expect(warning()).not.toBeNull());
        await waitFor(() => expect(scenePanel()).toHaveStyle({ top: "90px" }));

        fireEvent.click(screen.getByTitle("Dismiss warning"));
        await waitFor(() => expect(scenePanel()).toHaveStyle({ top: "20px" }));
    });

    it("keeps a dismissed warning closed for the rest of the load", async () => {
        answers = { "city.json": loads("city.json"), "town.json": loads("town.json") };
        const { rerender } = render(<CesiumViewerComponent {...props} assetKey="city.json" />);
        await screen.findByText("1 tileset loaded");
        const failTile = (key: string) =>
            act(() =>
                loaded[key].raiseTileFailed({ message: "Request has failed. Status Code: 404" })
            );
        failTile("city.json");
        await waitFor(() => expect(warning()).not.toBeNull());
        fireEvent.click(screen.getByTitle("Dismiss warning"));
        failTile("city.json");
        await act(() => new Promise((resolve) => setTimeout(resolve, 700)));
        expect(warning()).toBeNull();

        rerender(<CesiumViewerComponent {...props} assetKey="town.json" />);
        await waitFor(() => expect(loaded["town.json"]).toBeDefined());
        await screen.findByText("1 tileset loaded");
        failTile("town.json");
        await waitFor(() => expect(warning()).toHaveTextContent("1 tile request failed"));
    });

    it("keeps a dismissed warning closed when a later tileset of the load fails", async () => {
        let failB: ((reason: unknown) => void) | undefined;
        answers = {
            "a.json": loads("a.json"),
            "b.json": () =>
                new Promise((_resolve, reject) => {
                    failB = reject;
                }),
        };
        render(<CesiumViewerComponent {...props} multiFileKeys={["a.json", "b.json"]} />);
        // The second tileset is requested once the first is in the scene with its listener.
        await waitFor(() => expect(failB).toBeDefined());
        act(() => loaded["a.json"].raiseTileFailed({ message: "Request has failed." }));
        await waitFor(() => expect(warning()).not.toBeNull());
        fireEvent.click(screen.getByTitle("Dismiss warning"));

        act(() => failB!({ statusCode: 404 }));
        await screen.findByText("1 tileset loaded");
        expect(warning()).toBeNull();
    });

    it("loads the tilesets when a Cesium Ion token is configured", async () => {
        answers = { "city.json": loads("city.json") };
        render(
            <CesiumViewerComponent
                {...props}
                assetKey="city.json"
                customParameters={{ cesiumIonToken: "t" }}
            />
        );
        expect(await screen.findByText("1 tileset loaded")).toBeInTheDocument();
        expect(loaded["city.json"]).toBeDefined();
    });
});

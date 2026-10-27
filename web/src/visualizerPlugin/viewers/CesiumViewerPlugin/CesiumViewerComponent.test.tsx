/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Cesium viewer lifecycle and controls. `window.Cesium` is a stub: every member the viewer touches
 * resolves to another stub, except the members a test replaces.
 */
import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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

let widgetConstructor: jest.Mock;
let widgetDestroy: jest.Mock;
let resources: any[];

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    jest.spyOn(console, "warn").mockImplementation(() => undefined);
    jest.spyOn(console, "error").mockImplementation(() => undefined);
    const cesium = stub();
    widgetDestroy = jest.fn();
    widgetConstructor = jest.fn(() => {
        const widget = stub();
        widget.destroy = widgetDestroy;
        return widget;
    });
    cesium.CesiumWidget = widgetConstructor;
    resources = [];
    cesium.Resource = function (this: any, options: any) {
        Object.assign(this, options);
        resources.push(this);
    };
    cesium.Cesium3DTileset = { fromUrl: () => new Promise(() => undefined) };
    (window as any).Cesium = cesium;
});

afterEach(() => {
    jest.restoreAllMocks();
    delete (window as any).Cesium;
});

const props = { assetId: "asset1", databaseId: "db1", assetKey: "city.json" } as any;

describe("Cesium viewer lifecycle", () => {
    it("keeps the same widget when the viewer mode changes", async () => {
        const { rerender } = render(<CesiumViewerComponent {...props} viewerMode="wide" />);
        await waitFor(() => expect(widgetConstructor).toHaveBeenCalledTimes(1));

        rerender(<CesiumViewerComponent {...props} viewerMode="fullscreen" />);
        rerender(<CesiumViewerComponent {...props} viewerMode="wide" />);
        // Let any re-run of the init effect complete before counting.
        await screen.findByTitle("Hide panel");
        await new Promise((resolve) => setTimeout(resolve, 50));

        expect(widgetConstructor).toHaveBeenCalledTimes(1);
        expect(widgetDestroy).not.toHaveBeenCalled();
    });
});

describe("Cesium tileset authorization", () => {
    const { getDualValidAccessToken } = jest.requireMock("../../../utils/authTokenUtils");

    it("retries a denied tile request once with fresh headers on the request and the tileset", async () => {
        render(<CesiumViewerComponent {...props} viewerMode="wide" />);
        await waitFor(() => expect(resources).toHaveLength(1));
        const root = resources[0];
        expect(root.headers.Authorization).toBe("Bearer t");
        expect(root.retryAttempts).toBe(1);

        getDualValidAccessToken.mockResolvedValueOnce("t2");
        const retried = { headers: { Authorization: "Bearer t" } };
        await expect(root.retryCallback(retried, { statusCode: 403 })).resolves.toBe(true);
        expect(retried.headers.Authorization).toBe("Bearer t2");
        expect(root.headers.Authorization).toBe("Bearer t2");
    });

    it("retries a 401 and leaves other failures alone", async () => {
        render(<CesiumViewerComponent {...props} viewerMode="wide" />);
        await waitFor(() => expect(resources).toHaveLength(1));
        const root = resources[0];

        getDualValidAccessToken.mockResolvedValueOnce("t3");
        await expect(root.retryCallback({ headers: {} }, { statusCode: 401 })).resolves.toBe(true);
        expect(root.headers.Authorization).toBe("Bearer t3");

        const untouched = { headers: { Authorization: "Bearer t3" } };
        await expect(root.retryCallback(untouched, { statusCode: 404 })).resolves.toBe(false);
        await expect(root.retryCallback(untouched, { statusCode: 500 })).resolves.toBe(false);
        expect(untouched.headers.Authorization).toBe("Bearer t3");
    });
});

describe("Cesium background swatches", () => {
    it("name each swatch and mark only the selected one pressed", async () => {
        render(<CesiumViewerComponent {...props} viewerMode="wide" />);
        await screen.findByTitle("Hide panel");

        const names = ["Black", "White", "Light Blue"];
        const swatches = names.map((name) => screen.getByRole("button", { name }));
        swatches.forEach((swatch) => {
            expect(swatch).toHaveAttribute("type", "button");
            expect(swatch).toHaveAttribute("aria-pressed", "false");
            expect((swatch.style as any).forcedColorAdjust).toBe("none");
        });

        fireEvent.click(swatches[1]);
        expect(swatches.map((swatch) => swatch.getAttribute("aria-pressed"))).toEqual([
            "false",
            "true",
            "false",
        ]);
        expect(swatches[1].querySelector('[aria-hidden="true"]')).toHaveTextContent("✓");
    });
});

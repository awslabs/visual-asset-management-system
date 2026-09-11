/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
// Registers DOM matchers (toBeInTheDocument, toHaveClass, ...) on expect
import "@testing-library/jest-dom";

// jsdom lacks ResizeObserver, which Radix primitives (Tooltip/Popover via react-use-size) touch on
// mount. Provide a no-op polyfill so components using those primitives render under jest.
if (typeof (globalThis as any).ResizeObserver === "undefined") {
    (globalThis as any).ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    };
}

// jsdom does not expose TextEncoder/TextDecoder, which react-router reads at module scope.
// Without these, any suite importing react-router-dom fails on import.
if (typeof (globalThis as any).TextEncoder === "undefined") {
    const { TextEncoder, TextDecoder } = require("util");
    (globalThis as any).TextEncoder = TextEncoder;
    (globalThis as any).TextDecoder = TextDecoder;
}

jest.mock("maplibre-gl/dist/maplibre-gl", () => ({
    GeolocateControl: jest.fn(),
    Map: jest.fn(() => ({
        addControl: jest.fn(),
        on: jest.fn(),
        remove: jest.fn(),
    })),
    NavigationControl: jest.fn(),
}));
jest.mock("@aws-amplify/ui-react", () => ({}));

export default undefined;

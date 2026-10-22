/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Map thumbnails share a fixed number of live maps (each holds a WebGL context): a thumbnail
 * mounts its map only while it is near the viewport and a slot is free, and frees the slot when it
 * leaves view or unmounts.
 */
import React from "react";
import { render, screen, act } from "@testing-library/react";
import MapThumbnail, { MAX_LIVE_MAP_THUMBNAILS } from "./MapThumbnail";

let mockLiveMaps = 0;
let mockPeakLiveMaps = 0;

jest.mock("react-map-gl/maplibre", () => {
    const ReactActual = jest.requireActual("react");
    const Map = ReactActual.forwardRef((props: any, _ref: any) => {
        ReactActual.useEffect(() => {
            mockLiveMaps += 1;
            mockPeakLiveMaps = Math.max(mockPeakLiveMaps, mockLiveMaps);
            return () => {
                mockLiveMaps -= 1;
            };
        }, []);
        return <div data-testid="live-map">{props.children}</div>;
    });
    return {
        __esModule: true,
        default: Map,
        Marker: ({ children }: any) => <div>{children}</div>,
        NavigationControl: () => null,
        Source: ({ children }: any) => <div>{children}</div>,
        Layer: () => null,
    };
});
jest.mock("../../../common/utils/maplibreWorker", () => ({}));
jest.mock("./MapPreviewModal", () => ({ __esModule: true, default: () => null }));

type ObserverEntry = { callback: IntersectionObserverCallback; element?: Element };
let observers: ObserverEntry[] = [];

class MockIntersectionObserver {
    private entry: ObserverEntry;
    constructor(callback: IntersectionObserverCallback) {
        this.entry = { callback };
        observers.push(this.entry);
    }
    observe(element: Element) {
        this.entry.element = element;
        // Every thumbnail starts in view.
        this.entry.callback(
            [{ isIntersecting: true, target: element } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver
        );
    }
    disconnect() {
        observers = observers.filter((o) => o !== this.entry);
    }
    unobserve() {}
    takeRecords() {
        return [];
    }
}

const located = (i: number) => ({
    geo_MD_location: { type: "Point", coordinates: [10 + i / 100, 50] },
});

const Rows = ({ count }: { count: number }) => (
    <>
        {Array.from({ length: count }, (_, i) => (
            <MapThumbnail key={i} assetData={located(i)} mapStyleUrl="style.json" />
        ))}
    </>
);

beforeEach(() => {
    mockLiveMaps = 0;
    mockPeakLiveMaps = 0;
    observers = [];
    (global as any).IntersectionObserver = MockIntersectionObserver;
});

afterEach(() => {
    delete (global as any).IntersectionObserver;
});

describe("MapThumbnail live map budget", () => {
    it("mounts at most the slot budget of maps for 50 located rows in view", () => {
        const { unmount } = render(<Rows count={50} />);
        expect(screen.getAllByTestId("live-map")).toHaveLength(MAX_LIVE_MAP_THUMBNAILS);
        expect(screen.getAllByTestId("map-thumbnail-placeholder")).toHaveLength(
            50 - MAX_LIVE_MAP_THUMBNAILS
        );
        expect(mockPeakLiveMaps).toBeLessThanOrEqual(MAX_LIVE_MAP_THUMBNAILS);
        unmount();
        expect(mockLiveMaps).toBe(0);
    });

    it("hands a freed slot to a waiting thumbnail when one leaves view", () => {
        const { unmount } = render(<Rows count={MAX_LIVE_MAP_THUMBNAILS + 1} />);
        expect(screen.getAllByTestId("map-thumbnail-placeholder")).toHaveLength(1);

        const first = observers[0];
        act(() => {
            first.callback(
                [{ isIntersecting: false, target: first.element } as IntersectionObserverEntry],
                {} as IntersectionObserver
            );
        });

        // The scrolled-away thumbnail is now the placeholder, and the waiting one has its map.
        expect(screen.getAllByTestId("live-map")).toHaveLength(MAX_LIVE_MAP_THUMBNAILS);
        expect(screen.getAllByTestId("map-thumbnail-placeholder")).toHaveLength(1);
        unmount();
        expect(mockLiveMaps).toBe(0);
    });

    it("mounts directly under the same budget without IntersectionObserver", () => {
        delete (global as any).IntersectionObserver;
        const { unmount } = render(<Rows count={20} />);
        expect(screen.getAllByTestId("live-map")).toHaveLength(MAX_LIVE_MAP_THUMBNAILS);
        unmount();
        expect(mockLiveMaps).toBe(0);
    });

    it("takes no slot for a row without location data", () => {
        const { unmount } = render(
            <>
                {Array.from({ length: 10 }, (_, i) => (
                    <MapThumbnail key={i} assetData={{}} mapStyleUrl="style.json" />
                ))}
                <MapThumbnail assetData={located(1)} mapStyleUrl="style.json" />
            </>
        );
        expect(screen.getAllByTestId("live-map")).toHaveLength(1);
        unmount();
    });
});

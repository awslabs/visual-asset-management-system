/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render } from "@testing-library/react";
import SearchRasterLayers from "./SearchRasterLayers";
import { shownRasterKey } from "./SearchRasterLayers.keys";

const mockLayers: { value: any } = { value: null };
jest.mock("../../common/cogRaster/useRasterLayers", () => ({
    useRasterLayers: () => mockLayers.value,
}));
jest.mock("../../common/cogRaster/RasterLayerPanel", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../../common/cogRaster/RasterLayerManager", () => ({
    firstLayerIdWithPrefix: () => undefined,
}));

const FILE = { databaseId: "db", assetId: "a1", key: "a1/maps/site.tif" };
const entry = (id: string, key: string, failed: string | null = null) => ({
    id,
    sourceKey: key,
    file: { databaseId: "db", assetId: "a1", key: `${key}.cog.tif` },
    failed,
});

beforeEach(() => {
    mockLayers.value = {
        entries: [],
        notices: [],
        busy: 0,
        addFile: jest.fn().mockResolvedValue(true),
        remove: jest.fn(),
        setOpacity: jest.fn(),
        move: jest.fn(),
        zoomTo: jest.fn(),
        dismissNotice: jest.fn(),
    };
});

describe("SearchRasterLayers hide (#1599)", () => {
    it("removes the layer showing the file, a converted COG included, and nothing else", () => {
        mockLayers.value.entries = [
            entry("cog-id", FILE.key),
            entry("other-id", "a1/maps/other.tif"),
        ];
        const map: any = {};
        const { rerender } = render(
            <SearchRasterLayers map={map} requests={[{ seq: 1, action: "show", file: FILE }]} />
        );
        expect(mockLayers.value.addFile).toHaveBeenCalledTimes(1);
        rerender(
            <SearchRasterLayers
                map={map}
                requests={[
                    { seq: 1, action: "show", file: FILE },
                    { seq: 2, action: "hide", file: FILE },
                ]}
            />
        );
        expect(mockLayers.value.remove).toHaveBeenCalledTimes(1);
        expect(mockLayers.value.remove).toHaveBeenCalledWith("cog-id");
        // A request is handled once.
        expect(mockLayers.value.addFile).toHaveBeenCalledTimes(1);
    });

    it("reports the shown files, leaving out a failed layer", () => {
        mockLayers.value.entries = [
            entry("a", FILE.key),
            entry("b", "a1/maps/broken.tif", "could not be drawn"),
        ];
        const onShownChange = jest.fn();
        render(<SearchRasterLayers map={{} as any} requests={[]} onShownChange={onShownChange} />);
        const shown: Set<string> = onShownChange.mock.calls[onShownChange.mock.calls.length - 1][0];
        expect([...shown]).toEqual([shownRasterKey(FILE)]);
    });
});

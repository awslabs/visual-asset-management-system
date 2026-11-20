/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { REDUCED_ASSET_COLUMNS, REDUCED_FILE_COLUMNS } from "./ModernSearchContainer";

jest.mock("../../services/appCache", () => ({ appCache: { getItem: jest.fn() } }));
jest.mock("../../services/APIService", () => ({}));
// The modules below use import.meta.glob or ESM-only libraries (unparseable under Jest) and are not
// under test here.
jest.mock("../../visualizerPlugin", () => ({
    initializePluginRegistry: jest.fn().mockResolvedValue(undefined),
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({
    PluginRegistry: { getInstance: () => ({}) },
    getFileExtension: () => "",
    getFileExtensions: () => [],
}));
jest.mock("./SearchPageMapView", () => ({ __esModule: true, default: () => null }));
jest.mock("./SearchLayout/GeoFilterMapSelector", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("./SearchResults/MapThumbnail", () => ({ __esModule: true, default: () => null }));
jest.mock("../filemanager/modals/FileViewerModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../filemanager/modals/AssetPreviewModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../modals/AssetDeleteModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/AssetUnarchiveModal", () => ({ __esModule: true, default: () => null }));

describe("the columns of a search without OpenSearch", () => {
    it("lists the file path directly after relevance for files", () => {
        expect(REDUCED_FILE_COLUMNS.slice(0, 2)).toEqual(["relevance", "str_key"]);
    });

    it("lists the asset name directly after relevance for assets", () => {
        expect(REDUCED_ASSET_COLUMNS.slice(0, 2)).toEqual(["relevance", "str_assetname"]);
    });
});

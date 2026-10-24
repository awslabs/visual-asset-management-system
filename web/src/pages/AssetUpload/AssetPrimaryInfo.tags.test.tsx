/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The create-asset form's tag/tag-type fetch.
 *
 * Two defects are pinned here, both about WHEN the fetch runs rather than what it returns:
 *
 *  - it was keyed on `[]` (tag types) and `[fileUploadTableItems]` (tags) while reading the database
 *    the user picks in this same step, so the scoping never took effect: the request always went out
 *    with `databaseId` undefined and the unscoped reply announced required tag types the correctly
 *    scoped picker could never offer, leaving step 1 permanently invalid.
 *  - the options lived in a module-level array mutated from the `.then`, with no ordering guard, so a
 *    slow earlier reply could overwrite a newer one and repopulate the picker with another database's
 *    tags.
 *
 * The heavy siblings are mocked so this file never loads `react-map-gl` (ESM-only; the same reason
 * `components/metadata/FileMetadata.test.tsx` mocks `metadataV2`).
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { AssetPrimaryInfo, AssetDetailContext } from "./AssetUpload";

jest.mock("../../services/APIService", () => ({
    fetchTagTypesForAsset: jest.fn(),
    fetchTagsForAsset: jest.fn(),
    fetchAllDatabases: jest.fn().mockResolvedValue([]),
}));
jest.mock("../../components/metadataV2", () => ({ MetadataContainer: () => null }));
jest.mock("../../components/asset/tabs/AssetLinksTab", () => ({ AssetLinksTab: () => null }));
jest.mock("./AssetUploadWorkflow", () => ({ __esModule: true, default: () => null }));
jest.mock("./onSubmit", () => ({
    __esModule: true,
    default: () => () => undefined,
    onUploadRetry: jest.fn(),
}));
jest.mock("localforage", () => ({
    setItem: jest.fn().mockResolvedValue(undefined),
    getItem: jest.fn().mockResolvedValue(null),
}));

const { fetchTagTypesForAsset, fetchTagsForAsset } = jest.requireMock("../../services/APIService");

/** One required tag type plus its tag, scoped to `databaseId`. */
const requiredType = (tagTypeName: string, databaseId: string) => ({
    tagTypeName,
    description: "",
    required: "True",
    tags: [`${tagTypeName}-tag`],
    databaseId,
});

const tagRecord = (tagName: string, tagTypeName: string, databaseId: string) => ({
    tagName,
    tagTypeName,
    databaseId,
});

/** Mounts the step against a databaseId we control, standing in for the DatabaseSelector. */
const Harness = ({ databaseId }: { databaseId?: string }) => (
    <AssetDetailContext.Provider
        value={{
            assetDetailState: { isMultiFile: false, isDistributable: true, databaseId } as any,
            assetDetailDispatch: jest.fn(),
        }}
    >
        <AssetPrimaryInfo setValid={() => undefined} showErrors={false} />
    </AssetDetailContext.Provider>
);

describe("create-asset form tag fetch scoping", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        fetchTagTypesForAsset.mockResolvedValue([]);
        fetchTagsForAsset.mockResolvedValue([]);
    });

    it("does not request tags or tag types before a database is chosen", () => {
        // The pre-fix effect fired once at mount with databaseId undefined, which is the unscoped
        // query whose reply demanded out-of-scope tags.
        render(<Harness />);
        expect(fetchTagTypesForAsset).not.toHaveBeenCalled();
        expect(fetchTagsForAsset).not.toHaveBeenCalled();
    });

    it("requests both lists scoped to the database once one is chosen", async () => {
        // Positive control for the test above: the mocks DO get called, so "not called" there is
        // about the missing database and not about a mock that was never wired up.
        const { rerender } = render(<Harness />);
        rerender(<Harness databaseId="db-b" />);

        await waitFor(() => expect(fetchTagTypesForAsset).toHaveBeenCalledTimes(1));
        expect(fetchTagTypesForAsset).toHaveBeenCalledWith({ databaseId: "db-b" });
        expect(fetchTagsForAsset).toHaveBeenCalledWith({ databaseId: "db-b" });
    });

    it("re-requests when the user switches database", async () => {
        const { rerender } = render(<Harness databaseId="db-a" />);
        await waitFor(() => expect(fetchTagTypesForAsset).toHaveBeenCalledTimes(1));

        rerender(<Harness databaseId="db-b" />);
        await waitFor(() => expect(fetchTagTypesForAsset).toHaveBeenCalledTimes(2));
        expect(fetchTagTypesForAsset).toHaveBeenLastCalledWith({ databaseId: "db-b" });
    });

    it("announces only the chosen database's required tag types", async () => {
        fetchTagTypesForAsset.mockResolvedValue([requiredType("Classification", "db-b")]);
        fetchTagsForAsset.mockResolvedValue([
            tagRecord("Classification-tag", "Classification", "db-b"),
        ]);

        const { rerender } = render(<Harness />);
        rerender(<Harness databaseId="db-b" />);

        await waitFor(() =>
            expect(screen.getByText(/require at least one selection/)).toBeInTheDocument()
        );
        expect(screen.getByText(/Classification/)).toBeInTheDocument();
    });

    it("clears the announced constraint when the database is cleared", async () => {
        fetchTagTypesForAsset.mockResolvedValue([requiredType("Classification", "db-b")]);
        fetchTagsForAsset.mockResolvedValue([
            tagRecord("Classification-tag", "Classification", "db-b"),
        ]);

        const { rerender } = render(<Harness databaseId="db-b" />);
        await waitFor(() =>
            expect(screen.getByText(/require at least one selection/)).toBeInTheDocument()
        );

        rerender(<Harness />);
        await waitFor(() =>
            expect(screen.queryByText(/require at least one selection/)).not.toBeInTheDocument()
        );
    });

    it("ignores a late reply from the database the user has already left", async () => {
        // The failure this guards: the first (wider) query resolves AFTER the second, so without a
        // sequence check its groups replace db-b's and the picker offers db-a's tags. Resolution
        // order is inverted here on purpose.
        let resolveFirstTypes: (v: any) => void = () => undefined;
        let resolveFirstTags: (v: any) => void = () => undefined;

        fetchTagTypesForAsset.mockImplementationOnce(
            () => new Promise((res) => (resolveFirstTypes = res))
        );
        fetchTagsForAsset.mockImplementationOnce(
            () => new Promise((res) => (resolveFirstTags = res))
        );

        const { rerender } = render(<Harness databaseId="db-a" />);
        await waitFor(() => expect(fetchTagTypesForAsset).toHaveBeenCalledTimes(1));

        fetchTagTypesForAsset.mockResolvedValue([requiredType("BravoOnly", "db-b")]);
        fetchTagsForAsset.mockResolvedValue([tagRecord("BravoOnly-tag", "BravoOnly", "db-b")]);
        rerender(<Harness databaseId="db-b" />);

        await waitFor(() => expect(screen.getByText(/BravoOnly/)).toBeInTheDocument());

        // Now let db-a's reply land. It must be discarded.
        resolveFirstTypes([requiredType("AlphaOnly", "db-a")]);
        resolveFirstTags([tagRecord("AlphaOnly-tag", "AlphaOnly", "db-a")]);

        await waitFor(() => expect(screen.getByText(/BravoOnly/)).toBeInTheDocument());
        expect(screen.queryByText(/AlphaOnly/)).not.toBeInTheDocument();
    });

    it("surfaces a load failure instead of leaving the picker silently empty", async () => {
        fetchTagTypesForAsset.mockResolvedValue("User is not authorized");
        fetchTagsForAsset.mockResolvedValue([]);

        render(<Harness databaseId="db-b" />);

        await waitFor(() => expect(screen.getByText("User is not authorized")).toBeInTheDocument());
    });
});

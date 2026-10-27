/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * JSX drops the line break between a text line and an expression on the next line, so a sentence
 * split that way renders glued words ("assetto", "mainasset"). These modal sentences keep their
 * spaces.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { CreateAssetLinkModal } from "./tabs/components/modals/CreateAssetLinkModal";
import { DeleteAssetLinkModal } from "./tabs/components/modals/DeleteAssetLinkModal";
import { CreateAssetVersionModal } from "./versions/components/CreateAssetVersionModal";
import { RevertVersionModal } from "./versions/components/RevertVersionModal";
import Synonyms from "../../synonyms";

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useParams: () => ({ databaseId: "db1", assetId: "a1" }),
}));
jest.mock("../../services/APIService", () => ({
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
    createAssetLink: jest.fn(),
    deleteAssetLink: jest.fn(),
    fetchAssetS3Files: jest.fn(() => new Promise(() => undefined)),
}));
jest.mock("../../services/AssetVersionService", () => ({
    revertAssetVersion: jest.fn(),
    fetchAssetVersions: jest.fn(() => new Promise(() => undefined)),
}));
jest.mock("../common/StatusMessage", () => ({
    useStatusMessage: () => ({ showMessage: jest.fn() }),
}));
jest.mock("../searchSmall/AssetSearchTable", () => ({ AssetSearchTable: () => null }));
jest.mock("./versions/components/AvailableFilesContainer", () => ({
    AvailableFilesContainer: () => null,
}));
jest.mock("./versions/components/SelectedFilesContainer", () => ({
    SelectedFilesContainer: () => null,
}));
jest.mock("./versions/components/FileVersionsContainer", () => ({
    FileVersionsContainer: () => null,
}));
jest.mock("./versions/components/BaseVersionSelectionContainer", () => ({
    BaseVersionSelectionContainer: () => null,
}));

/** The rendered text of the whole document, with runs of whitespace collapsed. */
const pageText = () => (document.body.textContent || "").replace(/\s+/g, " ");

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("modal sentences split across JSX lines", () => {
    it("keeps the spaces in the sub-child link notice", () => {
        render(
            <CreateAssetLinkModal
                visible={true}
                onDismiss={jest.fn()}
                relationshipType="child"
                currentAssetId="a1"
                currentDatabaseId="db1"
                onSuccess={jest.fn()}
                isSubChildMode={true}
                parentAssetData={{ assetId: "p1", databaseId: "db1", assetName: "Parent" }}
            />
        );
        expect(pageText()).toContain(`child ${Synonyms.asset} to "Parent"`);
        expect(pageText()).toContain(`main ${Synonyms.asset} being viewed`);
    });

    it("keeps the space before the hierarchy note in the delete-link dialog", () => {
        render(
            <DeleteAssetLinkModal
                visible={true}
                onDismiss={jest.fn()}
                assetLinkId="al-1"
                assetName="Bracket"
                relationshipType="related"
                onSuccess={jest.fn()}
            />
        );
        expect(pageText()).toContain("sub-relationships that may exist");
    });

    it("keeps the space in the version metadata note", () => {
        render(
            <CreateAssetVersionModal visible={true} onDismiss={jest.fn()} onSuccess={jest.fn()} />
        );
        expect(pageText()).toContain("automatically snapshot the current metadata");
    });

    it("keeps the spaces in the revert metadata notes", () => {
        render(
            <RevertVersionModal
                visible={true}
                onDismiss={jest.fn()}
                version={{ Version: "2" } as any}
                onSuccess={jest.fn()}
            />
        );
        expect(pageText()).toContain(`to the ${Synonyms.asset} and files`);
        fireEvent.click(screen.getByRole("checkbox"));
        expect(pageText()).toContain(`current metadata and attributes for the ${Synonyms.asset}`);
    });
});

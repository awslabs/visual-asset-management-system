/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Create Asset wizard's Select Files step on the shared upload control (#1462, #1471): the
 * preview shows each file's final path, the single-folder override changes it, duplicates block
 * the step, and loose referencing formats get the Add folder hint.
 */
import React, { useReducer } from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { AssetFileInfo, AssetDetailContext, assetDetailReducer } from "./AssetUpload";

jest.mock("../../services/APIService", () => ({
    fetchTagTypesForAsset: jest.fn(() => Promise.resolve([])),
    fetchTagsForAsset: jest.fn(() => Promise.resolve([])),
    fetchDatabase: jest.fn(),
}));
jest.mock("../../components/metadataV2", () => ({ MetadataContainer: () => null }));
jest.mock("../../components/selectors/DatabaseSelector", () => ({
    __esModule: true,
    default: () => null,
}));
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

const makeFile = (name: string, rel?: string) => {
    const file = new File(["x"], name);
    if (rel) Object.defineProperty(file, "webkitRelativePath", { value: rel });
    return file;
};

function Harness({
    setValid,
    onItems,
    restriction,
}: {
    setValid: jest.Mock;
    onItems: jest.Mock;
    restriction?: string;
}) {
    const [state, dispatch] = useReducer(assetDetailReducer, {
        isMultiFile: false,
        isDistributable: true,
        restrictFileUploadsToExtensions: restriction,
    });
    return (
        <AssetDetailContext.Provider
            value={{ assetDetailState: state, assetDetailDispatch: dispatch }}
        >
            <AssetFileInfo
                setFileUploadTableItems={onItems}
                setValid={setValid}
                showErrors={false}
            />
        </AssetDetailContext.Provider>
    );
}

const mount = (restriction?: string) => {
    const setValid = jest.fn();
    const onItems = jest.fn();
    render(<Harness setValid={setValid} onItems={onItems} restriction={restriction} />);
    return { setValid, onItems };
};

const addFolder = (files: File[]) =>
    fireEvent.change(screen.getByTestId("upload-folder-input"), { target: { files } });
const addFiles = (files: File[]) =>
    fireEvent.change(screen.getByTestId("upload-files-input"), { target: { files } });

const lastPaths = (onItems: jest.Mock) =>
    onItems.mock.calls[onItems.mock.calls.length - 1][0].map((i: any) => i.relativePath).sort();

const pack = () => [
    makeFile("scene.usda", "pack/scene.usda"),
    makeFile("pallet.usda", "pack/props/pallet.usda"),
];

describe("Create Asset, Select Files step", () => {
    it("a single picked folder becomes the asset's contents, and the override keeps its name", () => {
        const { onItems } = mount();
        addFolder(pack());
        expect(lastPaths(onItems)).toEqual(["/props/pallet.usda", "/scene.usda"]);
        expect(screen.getByText("/scene.usda")).toBeTruthy();

        fireEvent.click(screen.getByLabelText(/^Upload folder$/));
        expect(lastPaths(onItems)).toEqual(["/pack/props/pallet.usda", "/pack/scene.usda"]);
        expect(screen.getByText("/pack/scene.usda")).toBeTruthy();
    });

    it("adding a loose file after a single folder keeps the folder's name and says why", () => {
        const { onItems } = mount();
        addFolder(pack());
        addFiles([makeFile("notes.txt")]);
        expect(lastPaths(onItems)).toEqual([
            "/notes.txt",
            "/pack/props/pallet.usda",
            "/pack/scene.usda",
        ]);
        expect(screen.getByText(/Each folder keeps its name/)).toBeTruthy();
        expect(screen.queryByText("How to add this folder")).toBeNull();
    });

    it("a duplicate path is an error that blocks the step, and removing a row clears it", async () => {
        const { setValid } = mount();
        addFiles([makeFile("a.txt")]);
        addFiles([makeFile("a.txt")]);
        expect(screen.getAllByText("Duplicate path")).toHaveLength(2);
        expect(screen.getByText(/2 files can.t be uploaded \(duplicate path\)/)).toBeTruthy();
        await waitFor(() => expect(setValid).toHaveBeenLastCalledWith(false));

        fireEvent.click(screen.getAllByRole("button", { name: "Remove /a.txt" })[0]);
        expect(screen.queryByText("Duplicate path")).toBeNull();
        await waitFor(() => expect(setValid).toHaveBeenLastCalledWith(true));
    });

    it("suggests Add folder for a loose .gltf, but not for the same file inside a folder", () => {
        mount();
        addFolder([makeFile("model.gltf", "pack/model.gltf")]);
        expect(screen.queryByText(/can\s+reference other files/)).toBeNull();

        addFiles([makeFile("loose.gltf")]);
        const hint = screen.getByText(/can\s+reference other files/);
        expect(within(hint).queryByText(/model\.gltf/)).toBeNull();
        expect(hint.textContent).toContain("loose.gltf");
    });

    it("D3: a file the database's extension restriction refuses is an error row that blocks the step", async () => {
        const { setValid } = mount(".usda,.png");
        addFolder([
            makeFile("scene.usda", "pack/scene.usda"),
            makeFile("model.gltf", "pack/model.gltf"),
            makeFile("model.gltf.previewFile.png", "pack/model.gltf.previewFile.png"),
            makeFile("thumb.previewFile.bmp", "pack/thumb.previewFile.bmp"),
        ]);
        expect(screen.getByText("Extension .gltf not allowed")).toBeTruthy();
        expect(screen.getByText(/^Preview files must be \.png/)).toBeTruthy();
        expect(screen.getAllByText("Ready")).toHaveLength(2);
        expect(
            screen.getByText(
                /2 files can.t be uploaded \(extension not allowed, preview file format not allowed\)/
            )
        ).toBeTruthy();
        expect(screen.getByText(/2 with errors/)).toBeTruthy();
        // One banner names the errors; the separate Invalid Files Selected alert is gone.
        expect(screen.queryByText("Invalid Files Selected")).toBeNull();
        await waitFor(() => expect(setValid).toHaveBeenLastCalledWith(false));

        fireEvent.click(screen.getByRole("button", { name: "Show errors only" }));
        expect(screen.getByText("/model.gltf")).toBeTruthy();
        expect(screen.queryByText("/scene.usda")).toBeNull();
    });
});

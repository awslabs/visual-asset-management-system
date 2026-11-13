/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Add Files page on the shared upload control (#1462, #1471): the destination is preselected
 * from the file-manager folder (or ?folder=), every previewed path includes it, folders keep their
 * name under it, and files at existing paths are marked from the list-files API (advisory only).
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import ModifyAssetsUploadsPage from "./ModifyAssetsUploads";

jest.mock("../../services/APIService", () => ({
    fetchAsset: jest.fn(() => Promise.resolve({ assetId: "a1", assetName: "Asset One" })),
    fetchDatabase: jest.fn(() => Promise.resolve({ databaseId: "db1" })),
    fetchAssetS3FilesPage: jest.fn(),
}));
jest.mock("./AssetUploadWorkflow", () => ({ __esModule: true, default: () => null }));

const { fetchAssetS3FilesPage } = jest.requireMock("../../services/APIService");

const makeFile = (name: string, rel?: string) => {
    const file = new File(["x"], name);
    if (rel) Object.defineProperty(file, "webkitRelativePath", { value: rel });
    return file;
};

const mount = (opts: { folder?: string; query?: string } = {}) => {
    const path = `/databases/db1/assets/a1/uploads${opts.query ?? ""}`;
    const entry = opts.folder
        ? {
              pathname: path.split("?")[0],
              search: opts.query ?? "",
              state: { fileTree: { relativePath: opts.folder } },
          }
        : path;
    render(
        <MemoryRouter initialEntries={[entry as any]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId/uploads"
                    element={<ModifyAssetsUploadsPage />}
                />
            </Routes>
        </MemoryRouter>
    );
};

const destinationInput = () => screen.getByLabelText("Destination folder") as HTMLInputElement;
const addFolder = (files: File[]) =>
    fireEvent.change(screen.getByTestId("upload-folder-input"), { target: { files } });
const addFiles = (files: File[]) =>
    fireEvent.change(screen.getByTestId("upload-files-input"), { target: { files } });

beforeEach(() => {
    jest.clearAllMocks();
    fetchAssetS3FilesPage.mockResolvedValue({ success: true, items: [], nextToken: null });
});

describe("Add Files page", () => {
    it("preselects the file-manager folder as the destination and includes it in every path", async () => {
        mount({ folder: "/scene/" });
        expect(destinationInput().value).toBe("/scene/");

        addFolder([makeFile("a.png", "textures/a.png")]);
        addFiles([makeFile("b.txt")]);
        expect(screen.getByText("/scene/textures/a.png")).toBeTruthy();
        expect(screen.getByText("/scene/b.txt")).toBeTruthy();

        fireEvent.change(destinationInput(), { target: { value: "/other/" } });
        await waitFor(() => expect(screen.getByText("/other/textures/a.png")).toBeTruthy());
        expect(screen.getByText("/other/b.txt")).toBeTruthy();
        expect(screen.queryByText("/scene/b.txt")).toBeNull();
    });

    it("reads the destination from ?folder= when there is no navigation state", () => {
        mount({ query: "?folder=%2Fscene%2Fprops%2F" });
        expect(destinationInput().value).toBe("/scene/props/");
    });

    it("follows a navigation to the same page with another ?folder=", async () => {
        const Nav = () => {
            const navigate = useNavigate();
            return (
                <button
                    onClick={() =>
                        navigate("/databases/db1/assets/a1/uploads?folder=%2Fother%2Fplace%2F")
                    }
                >
                    go elsewhere
                </button>
            );
        };
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets/a1/uploads?folder=%2Fscene%2F"]}>
                <Nav />
                <Routes>
                    <Route
                        path="/databases/:databaseId/assets/:assetId/uploads"
                        element={<ModifyAssetsUploadsPage />}
                    />
                </Routes>
            </MemoryRouter>
        );
        expect(destinationInput().value).toBe("/scene/");
        addFiles([makeFile("b.txt")]);
        expect(screen.getByText("/scene/b.txt")).toBeTruthy();

        fireEvent.click(screen.getByText("go elsewhere"));
        await waitFor(() => expect(destinationInput().value).toBe("/other/place/"));
        expect(screen.getByText("/other/place/b.txt")).toBeTruthy();
    });

    it("refuses an invalid destination and disables the upload", () => {
        mount();
        addFiles([makeFile("b.txt")]);
        fireEvent.change(destinationInput(), { target: { value: "../x" } });
        expect(
            screen.getAllByText(/cannot contain "\." or "\.\." segments/).length
        ).toBeGreaterThan(0);
        expect(
            (screen.getByRole("button", { name: "Finalize and Upload" }) as HTMLButtonElement)
                .disabled
        ).toBe(true);
    });

    it("marks files at existing and archived paths from the list-files API", async () => {
        fetchAssetS3FilesPage.mockResolvedValue({
            success: true,
            nextToken: null,
            items: [
                { relativePath: "/textures/a.png", isArchived: false, isFolder: false },
                { relativePath: "/b.txt", isArchived: true, isFolder: false },
            ],
        });
        mount({ folder: "/scene/" });
        addFolder([makeFile("a.png", "textures/a.png")]);
        addFiles([makeFile("b.txt"), makeFile("c.txt")]);

        await waitFor(() => expect(screen.getByText("Exists: uploads a new version")).toBeTruthy());
        expect(screen.getByText("Archived: uploading restores it with a new version")).toBeTruthy();
        expect(fetchAssetS3FilesPage).toHaveBeenCalledWith(
            expect.objectContaining({
                databaseId: "db1",
                assetId: "a1",
                prefix: "scene/",
                includeArchived: true,
            })
        );
        expect(
            (screen.getByRole("button", { name: "Finalize and Upload" }) as HTMLButtonElement)
                .disabled
        ).toBe(false);
    });

    it("a failed existing-path check shows an info alert, marks nothing and keeps upload enabled", async () => {
        fetchAssetS3FilesPage.mockResolvedValue({ success: false, items: [], error: "denied" });
        mount({ folder: "/scene/" });
        addFiles([makeFile("b.txt")]);

        await waitFor(() =>
            expect(screen.getByText(/Could not check all existing files/)).toBeTruthy()
        );
        expect(screen.queryByText("Exists: uploads a new version")).toBeNull();
        expect(
            (screen.getByRole("button", { name: "Finalize and Upload" }) as HTMLButtonElement)
                .disabled
        ).toBe(false);
    });

    it("a duplicate path disables Finalize and Upload and states why", () => {
        mount({ folder: "/scene/" });
        addFiles([makeFile("b.txt")]);
        addFiles([makeFile("b.txt")]);
        expect(
            screen.getAllByText(/2 files can.t be uploaded \(duplicate path\)/).length
        ).toBeGreaterThan(0);
        expect(
            (screen.getByRole("button", { name: "Finalize and Upload" }) as HTMLButtonElement)
                .disabled
        ).toBe(true);
    });

    it("collapses the preview-file guidance by default and opens it for preview files", async () => {
        mount();
        const toggle = screen.getByRole("button", { name: "About preview files" });
        expect(toggle.getAttribute("aria-expanded")).toBe("false");

        addFiles([makeFile("model.gltf.previewFile.png")]);
        await waitFor(() => expect(toggle.getAttribute("aria-expanded")).toBe("true"));
        expect(screen.getByText(/will be treated as preview files/)).toBeTruthy();
    });

    it("keeps the guidance in the second column for a non-root destination", () => {
        mount({ folder: "/scene/" });
        expect(screen.queryByTestId("preview-file")).toBeNull();
        expect(screen.getByRole("button", { name: "About preview files" })).toBeTruthy();
    });

    it("shows the database's upload restrictions as a compact notice", async () => {
        const { fetchDatabase } = jest.requireMock("../../services/APIService");
        fetchDatabase.mockResolvedValueOnce({
            databaseId: "db1",
            restrictFileUploadsToExtensions: ".usda,.png",
        });
        mount();
        await waitFor(() => expect(screen.getByText("File upload restrictions")).toBeTruthy());
        expect(screen.getByText(".usda,.png")).toBeTruthy();
    });

    it("names the asset, database and destination under the page header, with Cancel beside Finalize and Upload", async () => {
        mount({ folder: "/scene/" });
        expect(screen.getByRole("heading", { level: 1, name: "Modify Asset Files" })).toBeTruthy();
        // The breadcrumb links the asset too; the key-value row adds the second link.
        await waitFor(() =>
            expect(screen.getAllByRole("link", { name: "Asset One" })).toHaveLength(2)
        );
        expect(screen.getAllByText("db1").length).toBeGreaterThanOrEqual(2);
        expect(screen.getByText("Destination")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
        const upload = screen.getByRole("button", { name: "Finalize and Upload" });
        expect((upload as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText(/Please select at least one asset file to upload/i)).toBeTruthy();
    });

    it("D3: a refused extension is an error row and the reason shows next to the disabled Finalize", async () => {
        const { fetchDatabase } = jest.requireMock("../../services/APIService");
        fetchDatabase.mockResolvedValue({
            databaseId: "db1",
            restrictFileUploadsToExtensions: ".txt,.png",
        });
        mount({ folder: "/scene/" });
        await waitFor(() => expect(screen.getByText("File upload restrictions")).toBeTruthy());
        addFiles([makeFile("model.gltf"), makeFile("notes.txt")]);

        expect(screen.getByText("Extension .gltf not allowed")).toBeTruthy();
        expect(screen.getByText(/1 with errors/)).toBeTruthy();
        expect(screen.queryByText("Invalid Files Selected")).toBeNull();
        const reasons = screen.getAllByText(/1 file can.t be uploaded \(extension not allowed\)/);
        // The preview banner and the inline reason beside Finalize and Upload.
        expect(reasons).toHaveLength(2);
        expect(
            (screen.getByRole("button", { name: "Finalize and Upload" }) as HTMLButtonElement)
                .disabled
        ).toBe(true);

        fireEvent.click(screen.getByRole("button", { name: "Show errors only" }));
        expect(screen.getByText("/scene/model.gltf")).toBeTruthy();
        expect(screen.queryByText("/scene/notes.txt")).toBeNull();
        fetchDatabase.mockResolvedValue({ databaseId: "db1" });
    });

    it("the single-folder override drops the folder name under the destination", () => {
        mount({ folder: "/scene/" });
        addFolder([makeFile("a.png", "textures/a.png")]);
        expect(screen.getByText("/scene/textures/a.png")).toBeTruthy();
        fireEvent.click(screen.getByLabelText(/^Upload folder contents$/));
        expect(screen.getByText("/scene/a.png")).toBeTruthy();
    });
});

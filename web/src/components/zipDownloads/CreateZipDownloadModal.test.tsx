/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import CreateZipDownloadModal from "./CreateZipDownloadModal";
import { ZipSelectionResult } from "./zipDownloads";

const mockCreate = jest.fn();
const mockVersions = jest.fn();
jest.mock("../../services/APIService", () => ({
    createZipDownload: (...args: any[]) => mockCreate(...args),
}));
jest.mock("../../services/AssetVersionService", () => ({
    fetchAllAssetVersions: (...args: any[]) => mockVersions(...args),
}));

const selection: ZipSelectionResult = {
    entries: [
        { type: "file", relativePath: "/a.glb" },
        { type: "folder", relativePath: "/textures/" },
    ],
    files: 1,
    folders: 1,
    wholeAsset: false,
    refused: [],
};

function renderModal(over: Partial<React.ComponentProps<typeof CreateZipDownloadModal>> = {}) {
    const onCreated = jest.fn();
    const onDismiss = jest.fn();
    render(
        <CreateZipDownloadModal
            visible
            onDismiss={onDismiss}
            databaseId="db1"
            assetId="a1"
            assetName="Boom Box"
            selection={selection}
            onCreated={onCreated}
            {...over}
        />
    );
    return { onCreated, onDismiss };
}

const radio = (label: RegExp) => screen.getByRole("radio", { name: label });

describe("CreateZipDownloadModal", () => {
    beforeEach(() => {
        mockCreate.mockReset();
        mockVersions.mockReset();
        mockVersions.mockResolvedValue([
            true,
            {
                versions: [
                    { Version: "2", Comment: "second", isCurrent: true },
                    { Version: "1", Comment: "first" },
                    { Version: "0", isArchived: true },
                ],
            },
        ]);
    });

    it("gives the dialog's close button an accessible name (D14)", () => {
        renderModal();
        const dialog = screen.getByRole("dialog");
        expect(screen.getByRole("button", { name: "Close modal" })).toBeInTheDocument();
        // The axe button-name rule: no button in the dialog may have an empty name.
        expect(within(dialog).queryAllByRole("button", { name: "" })).toHaveLength(0);
    });

    it("summarizes the selection and lists the selected paths", () => {
        renderModal();
        expect(screen.getByText("1 file and 1 folder")).toBeInTheDocument();
        expect(screen.getByText("/a.glb")).toBeInTheDocument();
        expect(screen.getByText("/textures/")).toBeInTheDocument();
        expect(
            screen.getByText(
                /Download Queue tab and in My Download Queue\. You can download it for 7 days/
            )
        ).toBeInTheDocument();
    });

    it("creates a zip of the current files with the default name and no version pin", async () => {
        mockCreate.mockResolvedValue([true, { requestId: "r1", archiveFileName: "x.zip" }]);
        const { onCreated } = renderModal();
        const name = (screen.getByLabelText("Archive name") as HTMLInputElement).value;
        expect(name).toMatch(/^Boom Box-\d{8}-\d{6}\.zip$/);

        await userEvent.click(screen.getByRole("button", { name: "Create zip download" }));
        await waitFor(() => expect(onCreated).toHaveBeenCalled());
        const { databaseId, assetId, body } = mockCreate.mock.calls[0][0];
        expect(databaseId).toBe("db1");
        expect(assetId).toBe("a1");
        expect(body).toEqual({
            selection: selection.entries,
            archiveFormat: "zip",
            compression: "store",
            archiveFileName: name,
        });
        expect(body).not.toHaveProperty("assetVersionId");
    });

    it("sends tar.gz with stored compression, swaps the extension and hides the zip compression", async () => {
        mockCreate.mockResolvedValue([true, { requestId: "r1" }]);
        renderModal();
        await userEvent.click(radio(/Compress \(deflate\)/));
        await userEvent.click(radio(/Compressed TAR/));
        expect(screen.queryByRole("radio", { name: /Compress \(deflate\)/ })).toBeNull();
        expect((screen.getByLabelText("Archive name") as HTMLInputElement).value).toMatch(
            /\.tar\.gz$/
        );
        await userEvent.click(screen.getByRole("button", { name: "Create zip download" }));
        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        expect(mockCreate.mock.calls[0][0].body).toMatchObject({
            archiveFormat: "tar.gz",
            compression: "store",
        });
    });

    it("sends deflate when the zip compression option is chosen", async () => {
        mockCreate.mockResolvedValue([true, { requestId: "r1" }]);
        renderModal();
        await userEvent.click(radio(/Compress \(deflate\)/));
        await userEvent.click(screen.getByRole("button", { name: "Create zip download" }));
        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        expect(mockCreate.mock.calls[0][0].body.compression).toBe("deflate");
    });

    it("pins an asset version chosen in the picker and offers only live versions", async () => {
        mockCreate.mockResolvedValue([true, { requestId: "r1" }]);
        renderModal();
        await userEvent.click(radio(/version/i));
        const submit = screen.getByRole("button", { name: "Create zip download" });
        expect(submit).toBeDisabled();

        const found = createWrapper(document.body).findSelect();
        expect(found).toBeTruthy();
        const select = found as NonNullable<typeof found>;
        await waitFor(() => expect(mockVersions).toHaveBeenCalled());
        select.openDropdown();
        const options = select.findDropdown().findOptions();
        expect(options.map((o) => o.getElement().textContent)).toEqual(
            expect.arrayContaining([expect.stringContaining("Version 2")])
        );
        expect(options).toHaveLength(2);
        select.selectOptionByValue("1");

        await userEvent.click(submit);
        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        expect(mockCreate.mock.calls[0][0].body.assetVersionId).toBe("1");
    });

    it("preselects the asset version being browsed", async () => {
        mockCreate.mockResolvedValue([true, { requestId: "r1" }]);
        renderModal({ initialAssetVersionId: "2" });
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "Create zip download" })).toBeEnabled()
        );
        await userEvent.click(screen.getByRole("button", { name: "Create zip download" }));
        await waitFor(() => expect(mockCreate).toHaveBeenCalled());
        expect(mockCreate.mock.calls[0][0].body.assetVersionId).toBe("2");
    });

    it("keeps the dialog open and shows the API's refusal", async () => {
        mockCreate.mockResolvedValue([
            false,
            "You already have the maximum number of active zip downloads",
            429,
        ]);
        const { onCreated } = renderModal();
        await userEvent.click(screen.getByRole("button", { name: "Create zip download" }));
        expect(
            await screen.findByText("You already have the maximum number of active zip downloads")
        ).toBeInTheDocument();
        expect(onCreated).not.toHaveBeenCalled();
    });

    it("refuses an archive name with a slash", async () => {
        renderModal();
        const input = screen.getByLabelText("Archive name");
        await userEvent.clear(input);
        await userEvent.type(input, "a/b.zip");
        expect(screen.getByText("The name cannot contain slashes.")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Create zip download" })).toBeDisabled();
    });

    it("refuses a selection with archived or deleted items and names them (#1125 M2)", async () => {
        mockCreate.mockClear();
        renderModal({ selection: { ...selection, refused: ["/old.glb", "/gone/"] } });
        expect(screen.getByText("This selection can't be downloaded as a zip")).toBeInTheDocument();
        const list = screen.getByRole("list", { name: "Archived or deleted items" });
        expect(list).toHaveTextContent("/old.glb");
        expect(list).toHaveTextContent("/gone/");
        const create = screen.getByRole("button", { name: "Create zip download" });
        expect(create).toBeDisabled();
        await userEvent.click(create);
        expect(mockCreate).not.toHaveBeenCalled();
        // Never the old "left out" wording: nothing is skipped silently.
        expect(screen.queryByText(/left out of the archive/)).not.toBeInTheDocument();
    });

    it("lists at most ten refused items and counts the rest", () => {
        const refused = Array.from({ length: 12 }, (_, i) => `/f${i}.glb`);
        renderModal({ selection: { ...selection, refused } });
        expect(screen.getByText("and 2 more")).toBeInTheDocument();
    });

    it("refuses more selection entries than the API accepts with a plain message (#1125 L6)", () => {
        const entries = Array.from({ length: 1001 }, (_, i) => ({
            type: "file" as const,
            relativePath: `/f${i}.glb`,
        }));
        renderModal({ selection: { ...selection, entries, files: 1001, folders: 0 } });
        expect(
            screen.getByText(/can hold up to 1,000 selected files and folders/)
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Create zip download" })).toBeDisabled();
    });

    it("accepts exactly 1,000 selection entries", () => {
        const entries = Array.from({ length: 1000 }, (_, i) => ({
            type: "file" as const,
            relativePath: `/f${i}.glb`,
        }));
        renderModal({ selection: { ...selection, entries, files: 1000, folders: 0 } });
        expect(screen.queryByText(/can hold up to/)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Create zip download" })).toBeEnabled();
    });
});

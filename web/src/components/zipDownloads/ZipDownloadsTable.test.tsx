/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { MemoryRouter } from "react-router-dom";
import ZipDownloadsTable from "./ZipDownloadsTable";
import { ZIP_POLL_INTERVAL_MS, ZipDownloadJob } from "./zipDownloads";

const mockAssetList = jest.fn();
const mockGlobalList = jest.fn();
const mockAllUsersList = jest.fn();
const mockUrl = jest.fn();
const mockDelete = jest.fn();
jest.mock("../../services/APIService", () => ({
    fetchAssetZipDownloads: (...a: any[]) => mockAssetList(...a),
    fetchZipDownloads: (...a: any[]) => mockGlobalList(...a),
    fetchAssetZipDownloadsAllUsers: (...a: any[]) => mockAllUsersList(...a),
    getZipDownloadUrl: (...a: any[]) => mockUrl(...a),
    deleteZipDownload: (...a: any[]) => mockDelete(...a),
}));

// The Tier-1 gate is the API's own decision; each test says which routes it allows.
let mockAllowed: Record<string, boolean> = {};
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        failed: false,
        can: (method: string, path: string) => !!mockAllowed[`${method} ${path}`],
    }),
}));

const ALL = {
    "GET /database/{databaseId}/assets/{assetId}/download/zip": true,
    "GET /download/zip": true,
    "POST /database/{databaseId}/assets/{assetId}/download/zip/{requestId}/url": true,
    "DELETE /database/{databaseId}/assets/{assetId}/download/zip/{requestId}": true,
};

const job = (requestId: string, status: ZipDownloadJob["status"], over = {}): ZipDownloadJob => ({
    requestId,
    databaseId: "db1",
    assetId: "a1",
    assetName: "Boom Box",
    status,
    archiveFormat: "zip",
    compression: "store",
    archiveFileName: `archive-${requestId}.zip`,
    selectionSummary: { files: 2, folders: 0, wholeAsset: false },
    selectionPreview: [
        { type: "file", relativePath: "/a.glb" },
        { type: "file", relativePath: "/b.glb" },
    ],
    progress: { filesDone: 1, filesTotal: 2, bytesDone: 10, bytesTotal: 40 },
    createdAt: "2026-11-06T00:00:00Z",
    expiresAt: "2099-01-01T00:00:00Z",
    ...over,
});

const page = (items: ZipDownloadJob[], NextToken: string | null = null) => [
    true,
    { Items: items, NextToken },
];

function renderTable(props: Partial<React.ComponentProps<typeof ZipDownloadsTable>> = {}) {
    return render(
        <MemoryRouter>
            <ZipDownloadsTable databaseId="db1" assetId="a1" {...props} />
        </MemoryRouter>
    );
}

const rowFor = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

describe("ZipDownloadsTable", () => {
    beforeEach(() => {
        mockAllowed = { ...ALL };
        mockAssetList.mockReset();
        mockGlobalList.mockReset();
        mockAllUsersList.mockReset();
        mockUrl.mockReset();
        mockDelete.mockReset();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it("lists the asset's jobs with their status, progress and actions", async () => {
        mockAssetList.mockResolvedValue(
            page([job("2", "RUNNING"), job("1", "SUCCEEDED", { archiveSizeBytes: 2048 })])
        );
        renderTable();
        expect(await screen.findByText("archive-2.zip")).toBeInTheDocument();
        expect(mockAssetList).toHaveBeenCalledWith(
            expect.objectContaining({ databaseId: "db1", assetId: "a1" })
        );
        const running = rowFor("archive-2.zip");
        expect(within(running).getByText("Preparing")).toBeInTheDocument();
        expect(within(running).getByText("1 of 2 files")).toBeInTheDocument();
        expect(within(running).getByRole("button", { name: "Cancel archive-2.zip" })).toBeEnabled();
        expect(within(running).queryByRole("button", { name: /^Download/ })).toBeNull();

        const done = rowFor("archive-1.zip");
        expect(within(done).getByText("Ready")).toBeInTheDocument();
        expect(within(done).getByText("2.00 KB")).toBeInTheDocument();
        expect(within(done).getByRole("button", { name: "Download archive-1.zip" })).toBeEnabled();
        expect(within(done).getByRole("button", { name: "Delete archive-1.zip" })).toBeEnabled();
    });

    it.each([
        ["the asset tab", {}],
        ["My Download Queue", { databaseId: undefined, assetId: undefined }],
    ])(
        "keeps Ready and the actions on one line in %s without widening the table (#1125 D28, D30)",
        async (_name, props: Partial<React.ComponentProps<typeof ZipDownloadsTable>>) => {
            mockAssetList.mockResolvedValue(
                page([job("1", "SUCCEEDED", { archiveFormat: "zip", compression: "deflate" })])
            );
            mockGlobalList.mockResolvedValue(
                page([job("1", "SUCCEEDED", { archiveFormat: "zip", compression: "deflate" })])
            );
            const { container } = renderTable(props);
            const done = await screen
                .findByText("archive-1.zip")
                .then(() => rowFor("archive-1.zip"));
            const table = createWrapper(container).findTable()!;
            // No column has a minimum width (D28's 150 / 240 px pushed the table past its container).
            for (const header of table.findColumnHeaders()) {
                expect([
                    header.getElement().textContent,
                    header.getElement().style.minWidth,
                ]).toEqual([header.getElement().textContent, ""]);
            }
            // The status label keeps Cloudscape's one-line form (ellipsis container, no wrap).
            const ready = within(done).getByText("Ready");
            expect(ready.closest("[class*='overflow-ellipsis']")).not.toBeNull();
            // The actions are icon buttons (nothing to wrap), each named by its ariaLabel.
            for (const name of ["Download archive-1.zip", "Delete archive-1.zip"]) {
                const button = within(done).getByRole("button", { name });
                expect(button.className).toMatch(/variant-icon/);
                expect(button.textContent).toBe("");
                expect(button.getAttribute("title")).toBe(name);
            }
            // Names and request IDs break anywhere instead of widening their columns.
            expect(within(done).getByText("archive-1.zip").style.overflowWrap).toBe("anywhere");
            expect(within(done).getByText(/^Request ID: /).style.overflowWrap).toBe("anywhere");
            // The compression is a second, smaller line under the format.
            expect(within(done).getByText("compressed")).toBeInTheDocument();
        }
    );

    it("shows the empty state", async () => {
        mockAssetList.mockResolvedValue(page([]));
        renderTable();
        expect(await screen.findByText("No download jobs")).toBeInTheDocument();
    });

    it("shows a load error with Retry", async () => {
        mockAssetList.mockResolvedValueOnce([false, "boom", 500]);
        mockAssetList.mockResolvedValueOnce(page([job("1", "SUCCEEDED")]));
        renderTable();
        expect(await screen.findByText("boom")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(await screen.findByText("archive-1.zip")).toBeInTheDocument();
    });

    it("says the feature is off when the API answers with the disabled message", async () => {
        mockAssetList.mockResolvedValue([
            false,
            "Zip downloads are not enabled for this deployment",
            404,
        ]);
        renderTable();
        expect(
            await screen.findByText("Zip downloads are not enabled for this deployment.")
        ).toBeInTheDocument();
    });

    it("loads nothing for a role that may not list download jobs", () => {
        mockAllowed = {};
        renderTable();
        expect(
            screen.getByText("Your role does not allow listing download jobs.")
        ).toBeInTheDocument();
        expect(mockAssetList).not.toHaveBeenCalled();
    });

    it("greys Download, Cancel and Delete with a reason when the role may not call them", async () => {
        mockAllowed = { "GET /database/{databaseId}/assets/{assetId}/download/zip": true };
        mockAssetList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        renderTable();
        await screen.findByText("archive-1.zip");
        const row = rowFor("archive-1.zip");
        expect(within(row).getByRole("button", { name: "Download archive-1.zip" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        expect(within(row).getByRole("button", { name: "Delete archive-1.zip" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("offers no Download on a finished job the API reports as not downloadable (D37)", async () => {
        // The API says a finished job of a quarantined asset has no download; the role's route grants
        // alone would have shown the button. Its Cancel / Delete stays with the caller.
        mockAssetList.mockResolvedValue(
            page([
                job("1", "SUCCEEDED", { canDownload: false, canRemove: true }),
                job("2", "SUCCEEDED", { canDownload: true, canRemove: true }),
            ])
        );
        renderTable();
        await screen.findByText("archive-1.zip");
        const blocked = rowFor("archive-1.zip");
        expect(within(blocked).queryByRole("button", { name: /^Download/ })).toBeNull();
        expect(within(blocked).getByRole("button", { name: "Delete archive-1.zip" })).toBeEnabled();
        const open = rowFor("archive-2.zip");
        expect(within(open).getByRole("button", { name: "Download archive-2.zip" })).toBeEnabled();
    });

    it("offers no Download on the cross-asset page for a job the API reports as blocked (D37)", async () => {
        mockGlobalList.mockResolvedValue(
            page([job("1", "SUCCEEDED", { canDownload: false, canRemove: true })])
        );
        renderTable({ databaseId: undefined, assetId: undefined });
        await screen.findByText("archive-1.zip");
        expect(
            within(rowFor("archive-1.zip")).queryByRole("button", { name: /^Download/ })
        ).toBeNull();
    });

    it("greys Delete when the API reports the row as not removable (D37)", async () => {
        mockAssetList.mockResolvedValue(
            page([job("1", "SUCCEEDED", { canDownload: true, canRemove: false })])
        );
        renderTable();
        await screen.findByText("archive-1.zip");
        expect(
            within(rowFor("archive-1.zip")).getByRole("button", { name: "Delete archive-1.zip" })
        ).toHaveAttribute("aria-disabled", "true");
    });

    it("does not load while its tab is inactive", () => {
        mockAssetList.mockResolvedValue(page([]));
        renderTable({ isActive: false });
        expect(mockAssetList).not.toHaveBeenCalled();
    });

    it("polls while a job is not finished and stops once every job is", async () => {
        jest.useFakeTimers();
        mockAssetList
            .mockResolvedValueOnce(page([job("1", "RUNNING")]))
            .mockResolvedValueOnce(page([job("1", "SUCCEEDED")]))
            .mockResolvedValue(page([job("1", "SUCCEEDED")]));
        renderTable();
        await act(async () => {
            await Promise.resolve();
        });
        expect(mockAssetList).toHaveBeenCalledTimes(1);

        await act(async () => {
            jest.advanceTimersByTime(ZIP_POLL_INTERVAL_MS);
        });
        await waitFor(() => expect(mockAssetList).toHaveBeenCalledTimes(2));
        await act(async () => {
            await Promise.resolve();
        });

        await act(async () => {
            jest.advanceTimersByTime(ZIP_POLL_INTERVAL_MS * 3);
        });
        expect(mockAssetList).toHaveBeenCalledTimes(2);
    });

    it("does not poll while the browser tab is hidden", async () => {
        jest.useFakeTimers();
        const visibility = jest.spyOn(document, "visibilityState", "get");
        visibility.mockReturnValue("hidden");
        mockAssetList.mockResolvedValue(page([job("1", "QUEUED")]));
        renderTable();
        await act(async () => {
            await Promise.resolve();
        });
        await act(async () => {
            jest.advanceTimersByTime(ZIP_POLL_INTERVAL_MS * 3);
        });
        expect(mockAssetList).toHaveBeenCalledTimes(1);
        visibility.mockRestore();
    });

    it("downloads through the URL route and an anchor click, without showing the URL", async () => {
        mockAssetList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        mockUrl.mockResolvedValue([
            true,
            { url: "https://example.invalid/signed", expiresIn: 900, archiveFileName: "n.zip" },
        ]);
        // jsdom does not navigate; the click is recorded instead.
        const click = jest
            .spyOn(HTMLAnchorElement.prototype, "click")
            .mockImplementation(() => undefined);
        renderTable();
        await screen.findByText("archive-1.zip");
        await userEvent.click(screen.getByRole("button", { name: "Download archive-1.zip" }));
        await waitFor(() => expect(click).toHaveBeenCalled());
        expect(mockUrl).toHaveBeenCalledWith({ databaseId: "db1", assetId: "a1", requestId: "1" });
        const anchor = click.mock.instances[0] as unknown as HTMLAnchorElement;
        expect(anchor.href).toBe("https://example.invalid/signed");
        expect(anchor.download).toBe("n.zip");
        expect(screen.queryByText(/example\.invalid/)).toBeNull();
        click.mockRestore();
    });

    it("reports a download link failure", async () => {
        mockAssetList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        mockUrl.mockResolvedValue([false, "The zip download has expired", 410]);
        renderTable();
        await screen.findByText("archive-1.zip");
        await userEvent.click(screen.getByRole("button", { name: "Download archive-1.zip" }));
        expect(
            await screen.findByText(
                /Download of archive-1.zip failed: The zip download has expired/
            )
        ).toBeInTheDocument();
    });

    it("deletes a finished job only after the confirm", async () => {
        mockAssetList.mockResolvedValue(page([job("1", "FAILED", { errorMessage: "bad file" })]));
        mockDelete.mockResolvedValue([
            true,
            { message: "Zip download deleted", action: "deleted" },
        ]);
        renderTable();
        await screen.findByText("bad file");
        await userEvent.click(screen.getByRole("button", { name: "Delete archive-1.zip" }));
        const dialog = await screen.findByRole("dialog");
        expect(within(dialog).getByText(/cannot be recovered/)).toBeInTheDocument();
        expect(mockDelete).not.toHaveBeenCalled();
        mockAssetList.mockResolvedValue(page([]));
        await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
        await waitFor(() =>
            expect(mockDelete).toHaveBeenCalledWith({
                databaseId: "db1",
                assetId: "a1",
                requestId: "1",
            })
        );
        expect(await screen.findByText("archive-1.zip was deleted.")).toBeInTheDocument();
    });

    it("cancels a running job after the confirm, and keeps it when the user backs out", async () => {
        mockAssetList.mockResolvedValue(page([job("1", "RUNNING")]));
        mockDelete.mockResolvedValue([
            true,
            { message: "Zip download cancelled", action: "cancelled" },
        ]);
        renderTable();
        await screen.findByText("archive-1.zip");
        await userEvent.click(screen.getByRole("button", { name: "Cancel archive-1.zip" }));
        let dialog = await screen.findByRole("dialog");
        await userEvent.click(within(dialog).getByRole("button", { name: "Keep it" }));
        expect(mockDelete).not.toHaveBeenCalled();

        await userEvent.click(screen.getByRole("button", { name: "Cancel archive-1.zip" }));
        dialog = await screen.findByRole("dialog");
        await userEvent.click(within(dialog).getByRole("button", { name: "Cancel download" }));
        expect(await screen.findByText("archive-1.zip was cancelled.")).toBeInTheDocument();
        // #1125 D23: the cancel carries the cancel intent, so the API never deletes the job.
        expect(mockDelete).toHaveBeenCalledWith({
            databaseId: "db1",
            assetId: "a1",
            requestId: "1",
            intent: "cancel",
        });
    });

    it("does not cancel a job that finished while the cancel dialog was open (D23)", async () => {
        jest.useFakeTimers();
        const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
        mockAssetList
            .mockResolvedValueOnce(page([job("1", "RUNNING")]))
            .mockResolvedValue(page([job("1", "SUCCEEDED", { archiveSizeBytes: 2048 })]));
        renderTable();
        await screen.findByText("archive-1.zip");
        await user.click(screen.getByRole("button", { name: "Cancel archive-1.zip" }));
        const dialog = await screen.findByRole("dialog");
        expect(within(dialog).getByText(/Stop preparing archive-1.zip\?/)).toBeInTheDocument();
        expect(within(dialog).getByRole("status")).toBeEmptyDOMElement();

        // The poll behind the dialog finds the job finished.
        await act(async () => {
            jest.advanceTimersByTime(ZIP_POLL_INTERVAL_MS);
        });
        expect(
            await within(dialog).findByText(
                "archive-1.zip finished (Ready) before it was cancelled. Nothing was cancelled or deleted."
            )
        ).toBeInTheDocument();
        // Announced through the dialog's polite live region.
        expect(within(dialog).getByRole("status")).toHaveTextContent(
            "This archive job has finished"
        );
        expect(within(dialog).queryByText(/Stop preparing/)).toBeNull();
        const confirmButton = within(dialog).getByRole("button", { name: "Cancel download" });
        expect(confirmButton).toBeDisabled();
        await user.click(confirmButton);
        expect(mockDelete).not.toHaveBeenCalled();

        await user.click(within(dialog).getByRole("button", { name: "Close" }));
        expect(mockDelete).not.toHaveBeenCalled();
        expect(within(rowFor("archive-1.zip")).getByText("Ready")).toBeInTheDocument();
    });

    it("tells the user when the job finished before the cancel (D22)", async () => {
        mockAssetList.mockResolvedValue(page([job("1", "RUNNING")]));
        mockDelete.mockResolvedValue([
            true,
            {
                message: "The zip download finished before it could be cancelled",
                action: "ended",
                status: "SUCCEEDED",
            },
        ]);
        renderTable();
        await screen.findByText("archive-1.zip");
        await userEvent.click(screen.getByRole("button", { name: "Cancel archive-1.zip" }));
        const dialog = await screen.findByRole("dialog");
        await userEvent.click(within(dialog).getByRole("button", { name: "Cancel download" }));
        expect(
            await screen.findByText("archive-1.zip finished before it could be cancelled.")
        ).toBeInTheDocument();
        expect(screen.queryByText("archive-1.zip was cancelled.")).not.toBeInTheDocument();
    });

    it.each<[string, ZipDownloadJob["status"], string]>([
        ["cancel", "RUNNING", "Cancel archive-1.zip"],
        ["delete", "SUCCEEDED", "Delete archive-1.zip"],
    ])(
        "gives the %s confirm's close button an accessible name (D14)",
        async (_kind, status, opener) => {
            mockAssetList.mockResolvedValue(page([job("1", status)]));
            renderTable();
            await screen.findByText("archive-1.zip");
            await userEvent.click(screen.getByRole("button", { name: opener }));
            const dialog = await screen.findByRole("dialog");
            expect(within(dialog).getByRole("button", { name: "Close modal" })).toBeInTheDocument();
            // The axe button-name rule: no button in the dialog may have an empty name.
            expect(within(dialog).queryAllByRole("button", { name: "" })).toHaveLength(0);
        }
    );

    it("names the table Download jobs and shows each job's request ID", async () => {
        mockAssetList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        renderTable();
        await screen.findByText("archive-1.zip");
        expect(
            screen.getByRole("heading", { level: 2, name: /Download jobs/ })
        ).toBeInTheDocument();
        expect(screen.queryByText(/zip downloads/i)).toBeNull();
        expect(within(rowFor("archive-1.zip")).getByText("Request ID: 1")).toBeInTheDocument();
    });

    it("places the refresh button next to the search, not in the header", async () => {
        mockAssetList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        renderTable();
        await screen.findByText("archive-1.zip");
        const search = screen.getByRole("searchbox", { name: "Search download jobs" });
        const refresh = screen.getByRole("button", { name: "Refresh download queue" });
        // Both sit in the table's filter slot (left side of the filter row).
        const filterSlot = createWrapper(document.body).findTable()!.findFilterSlot()!.getElement();
        expect(filterSlot).toContainElement(search);
        expect(filterSlot).toContainElement(refresh);
        const heading = screen.getByRole("heading", { level: 2, name: /Download jobs/ });
        expect(heading.closest("header") ?? heading.parentElement).not.toContainElement(refresh);

        mockAssetList.mockResolvedValue(page([job("2", "QUEUED"), job("1", "SUCCEEDED")]));
        await userEvent.click(refresh);
        expect(await screen.findByText("archive-2.zip")).toBeInTheDocument();
        expect(mockAssetList).toHaveBeenCalledTimes(2);
    });

    it("searches the loaded jobs by asset ID, request ID and archive name", async () => {
        mockGlobalList.mockResolvedValue(
            page([
                job("req-b", "SUCCEEDED", { assetId: "asset-two", assetName: "Two" }),
                job("req-a", "SUCCEEDED", { assetId: "asset-one", assetName: "One" }),
            ])
        );
        render(
            <MemoryRouter>
                <ZipDownloadsTable />
            </MemoryRouter>
        );
        await screen.findByText("archive-req-a.zip");
        const search = screen.getByRole("searchbox", { name: "Search download jobs" });

        await userEvent.type(search, "ASSET-TWO");
        expect(screen.getByText("archive-req-b.zip")).toBeInTheDocument();
        expect(screen.queryByText("archive-req-a.zip")).toBeNull();
        expect(screen.getAllByText("1 match").length).toBeGreaterThan(0);
        expect(screen.getByRole("heading", { level: 2, name: /\(1\/2\)/ })).toBeInTheDocument();

        await userEvent.clear(search);
        await userEvent.type(search, "req-a");
        expect(screen.getByText("archive-req-a.zip")).toBeInTheDocument();
        expect(screen.queryByText("archive-req-b.zip")).toBeNull();

        await userEvent.clear(search);
        await userEvent.type(search, "nothing-like-this");
        expect(screen.getByText("No matching download jobs")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Clear search" }));
        expect(screen.getByText("archive-req-a.zip")).toBeInTheDocument();
        expect(screen.getByText("archive-req-b.zip")).toBeInTheDocument();
    });

    it("says the search covers the loaded jobs while more pages remain", async () => {
        mockGlobalList.mockResolvedValue(page([job("1", "SUCCEEDED")], "tok"));
        render(
            <MemoryRouter>
                <ZipDownloadsTable />
            </MemoryRouter>
        );
        await screen.findByText("archive-1.zip");
        expect(screen.getByText(/The search covers the jobs loaded so far/)).toBeInTheDocument();
    });

    it("lists across assets with a link to each asset when not scoped", async () => {
        mockGlobalList.mockResolvedValue(page([job("1", "SUCCEEDED")], "tok"));
        mockGlobalList.mockResolvedValueOnce(page([job("2", "SUCCEEDED")], "tok"));
        mockGlobalList.mockResolvedValueOnce(page([job("1", "SUCCEEDED")], null));
        render(
            <MemoryRouter>
                <ZipDownloadsTable headerVariant="h1" />
            </MemoryRouter>
        );
        expect(await screen.findByRole("link", { name: "Boom Box" })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/a1"
        );
        expect(mockAssetList).not.toHaveBeenCalled();
        await userEvent.click(screen.getByRole("button", { name: "Load more" }));
        expect(await screen.findByText("archive-1.zip")).toBeInTheDocument();
        expect(mockGlobalList).toHaveBeenLastCalledWith(
            expect.objectContaining({ startingToken: "tok" })
        );
        expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    });
});

describe("ZipDownloadsTable — Show all users (asset tab)", () => {
    const ALL_USERS_ROUTE = "GET /database/{databaseId}/assets/{assetId}/download/zip-all-users";

    beforeEach(() => {
        mockAllowed = { ...ALL };
        mockAssetList.mockReset();
        mockAllUsersList.mockReset();
        mockUrl.mockReset();
        mockDelete.mockReset();
    });

    it("shows only the caller's jobs and no filter when the API does not allow the route", async () => {
        mockAssetList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        renderTable();
        await screen.findByText("archive-1.zip");
        expect(screen.queryByText("Show all users")).toBeNull();
        expect(mockAllUsersList).not.toHaveBeenCalled();
        // Nothing is rendered in the right-aligned tools group either.
        expect(createWrapper(document.body).findTable()!.findPagination()).toBeNull();
    });

    it("places the toggle right-aligned in the filter row, away from search and refresh", async () => {
        mockAllowed = { ...ALL, [ALL_USERS_ROUTE]: true };
        mockAssetList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        renderTable();
        await screen.findByText("archive-1.zip");
        const table = createWrapper(document.body).findTable()!;
        // The tools slot sits inside Cloudscape's right-aligned group of the filter row.
        const toolsSlot = table.findPagination()!.getElement();
        expect(toolsSlot.parentElement!.className).toMatch(/tools-align-right/);
        const control = createWrapper(toolsSlot).findSegmentedControl()!;
        expect(control).not.toBeNull();
        // Default: My jobs.
        expect(control.findSelectedSegment()!.getElement()).toHaveTextContent("My jobs");
        // Search and refresh stay on the left, in the filter slot; the toggle is not there.
        const filterSlot = table.findFilterSlot()!.getElement();
        expect(filterSlot).not.toContainElement(control.getElement());
        expect(filterSlot).toContainElement(
            screen.getByRole("searchbox", { name: "Search download jobs" })
        );
        expect(filterSlot).toContainElement(
            screen.getByRole("button", { name: "Refresh download queue" })
        );
        expect(mockAllUsersList).not.toHaveBeenCalled();
    });

    it("defaults to the caller's jobs, and lists every user's jobs on Show all users", async () => {
        mockAllowed = { ...ALL, [ALL_USERS_ROUTE]: true };
        mockAssetList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        mockAllUsersList.mockResolvedValue(
            page([
                job("2", "SUCCEEDED", {
                    requestedBy: "bob",
                    isOwn: false,
                    canDownload: true,
                    canRemove: false,
                    selectionPreview: [],
                }),
                job("3", "SUCCEEDED", {
                    requestedBy: "carol",
                    isOwn: false,
                    canDownload: false,
                    canRemove: false,
                    selectionPreview: [],
                }),
                job("1", "SUCCEEDED", {
                    requestedBy: "alice",
                    isOwn: true,
                    canDownload: true,
                    canRemove: true,
                }),
            ])
        );
        renderTable();
        await screen.findByText("archive-1.zip");
        // Default: the caller's own list; the all-users route is not called.
        expect(mockAllUsersList).not.toHaveBeenCalled();
        expect(screen.queryByText("Requested by")).toBeNull();

        await userEvent.click(screen.getByText("Show all users"));
        expect(await screen.findByText("archive-2.zip")).toBeInTheDocument();
        expect(mockAllUsersList).toHaveBeenCalledWith(
            expect.objectContaining({ databaseId: "db1", assetId: "a1" })
        );
        expect(screen.getByText("Requested by")).toBeInTheDocument();
        expect(within(rowFor("archive-2.zip")).getByText("bob")).toBeInTheDocument();
        expect(within(rowFor("archive-1.zip")).getByText("alice (you)")).toBeInTheDocument();

        // Download follows the API's row flag; another user's job has no Cancel or Delete.
        const bob = rowFor("archive-2.zip");
        expect(within(bob).getByRole("button", { name: "Download archive-2.zip" })).toBeEnabled();
        expect(within(bob).queryByRole("button", { name: /Delete|Cancel/ })).toBeNull();
        const carol = rowFor("archive-3.zip");
        expect(within(carol).queryByRole("button", { name: /Download/ })).toBeNull();
        const mine = rowFor("archive-1.zip");
        expect(within(mine).getByRole("button", { name: "Delete archive-1.zip" })).toBeEnabled();
    });

    it("downloads another user's archive through the URL route when its row allows it", async () => {
        mockAllowed = { ...ALL, [ALL_USERS_ROUTE]: true };
        mockAssetList.mockResolvedValue(page([]));
        mockAllUsersList.mockResolvedValue(
            page([job("2", "SUCCEEDED", { requestedBy: "bob", isOwn: false, canDownload: true })])
        );
        mockUrl.mockResolvedValue([
            true,
            { url: "https://example.invalid/signed", expiresIn: 900, archiveFileName: "n.zip" },
        ]);
        const click = jest
            .spyOn(HTMLAnchorElement.prototype, "click")
            .mockImplementation(() => undefined);
        renderTable();
        await screen.findByText("No download jobs");
        await userEvent.click(screen.getByText("Show all users"));
        await userEvent.click(
            await screen.findByRole("button", { name: "Download archive-2.zip" })
        );
        await waitFor(() => expect(click).toHaveBeenCalled());
        expect(mockUrl).toHaveBeenCalledWith({ databaseId: "db1", assetId: "a1", requestId: "2" });
        click.mockRestore();
    });

    it("never offers the filter on the global page", async () => {
        mockAllowed = { ...ALL, [ALL_USERS_ROUTE]: true };
        mockGlobalList.mockResolvedValue(page([job("1", "SUCCEEDED")]));
        render(
            <MemoryRouter>
                <ZipDownloadsTable />
            </MemoryRouter>
        );
        await screen.findByText("archive-1.zip");
        expect(screen.queryByText("Show all users")).toBeNull();
    });
});

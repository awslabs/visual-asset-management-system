/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Audit Log Search page against a stubbed API: the log groups come only from the server, a
 * link restores filters without running the search, results page and open as text, hostile record
 * content is never interpreted, and each refusal has its own message.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuditLogSearch, { WARNING_TOAST_BACKSTOP_MS } from "./AuditLogSearch";
import { ToastProvider } from "../../features/orchestration/components/ToastProvider";
import { appCache } from "../../services/appCache";

// The real toast provider, with the options of each warning recorded.
const warningOptions: { duration?: number }[] = [];
jest.mock("../../features/orchestration/components/ToastProvider", () => {
    const actual = jest.requireActual("../../features/orchestration/components/ToastProvider");
    return {
        ...actual,
        useToast: () => {
            const toast = actual.useToast();
            return {
                ...toast,
                warning: (title: string, options?: { duration?: number }) => {
                    if (options) warningOptions.push(options);
                    return toast.warning(title, options);
                },
            };
        },
    };
});

const mockGroups = jest.fn();
const mockStart = jest.fn();
const mockGet = jest.fn();
const mockStop = jest.fn();
jest.mock("../../services/APIService", () => ({
    fetchAuditLogGroups: (...a: any[]) => mockGroups(...a),
    startAuditQuery: (...a: any[]) => mockStart(...a),
    getAuditQuery: (...a: any[]) => mockGet(...a),
    stopAuditQuery: (...a: any[]) => mockStop(...a),
    exportAuditQuery: jest.fn(),
}));
jest.mock("../../hooks/useAssetPageAccess", () => ({
    useAssetPageAccess: () => ({ canOpenAsset: true, canOpenDatabaseAssets: true }),
}));

const GROUPS = {
    logGroups: [{ key: "auditActions", label: "Actions", prefix: "ACTIONS" }],
    actions: [
        { name: "assetUpdate", kind: "write", entityType: "asset", logGroup: "auditActions" },
    ],
    entityTypes: ["asset"],
    outcomes: ["success", "failed", "denied"],
    limits: { maxQueryWindowDays: 31, maxContainsTerms: 3, maxContainsTermLength: 64 },
};

const HOSTILE = "<img src=x onerror=alert(1)>\u202egnp.exe";

const row = (over: object = {}) => ({
    timestamp: "2026-11-17 10:00:00.000",
    groupKey: "auditActions",
    groupLabel: "Actions",
    action: "assetUpdate",
    user: "alice@example.com",
    outcome: "success",
    databaseId: "db1",
    assetId: "asset1",
    entity: { type: "asset", id: "asset1" },
    request: { requestId: "11111111-2222-3333", method: "PUT", route: "/database/db1" },
    parseStatus: "parsed",
    recordId: "r1",
    requestParameters: { note: HOSTILE },
    ...over,
});

const complete = (rows: object[], extra: object = {}) => [
    true,
    { status: "Complete", Items: rows, statistics: {}, ...extra },
];

beforeEach(() => {
    jest.clearAllMocks();
    // The deployment has Audit Log Search (the AUDITLOGSEARCH switch in /secure-config).
    appCache.setItem("config", { featuresEnabled: ["AUDITLOGSEARCH"] });
    window.location.hash = "#/auth/audit-log-search/";
    mockGroups.mockResolvedValue([true, GROUPS]);
    mockStart.mockResolvedValue([true, { queryId: "q-1", status: "Scheduled" }]);
    mockStop.mockResolvedValue([true, {}]);
});

const openPage = async () => {
    render(
        <ToastProvider>
            <AuditLogSearch />
        </ToastProvider>
    );
    await screen.findByRole("button", { name: "Search" });
};

describe("Audit Log Search page when the deployment switched it off", () => {
    it.each([[undefined], [[]], [["ZIPDOWNLOADS"]]])(
        "shows the not-enabled state and makes no API call (features: %p)",
        (features) => {
            appCache.setItem("config", { featuresEnabled: features });
            render(
                <ToastProvider>
                    <AuditLogSearch />
                </ToastProvider>
            );
            expect(
                screen.getByText("Audit Log Search is not enabled on this deployment")
            ).toBeTruthy();
            expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
            expect(mockGroups).not.toHaveBeenCalled();
            expect(mockStart).not.toHaveBeenCalled();
        }
    );
});

describe("Audit Log Search page", () => {
    it("shows the no-access panel for a 403 on the log groups and offers no search", async () => {
        mockGroups.mockResolvedValue([false, "Forbidden", 403]);
        render(
            <ToastProvider>
                <AuditLogSearch />
            </ToastProvider>
        );
        expect(await screen.findByText("You do not have access to audit logs.")).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
    });

    it("says so when no group is allowed, without naming any", async () => {
        mockGroups.mockResolvedValue([true, { logGroups: [], limits: {} }]);
        render(
            <ToastProvider>
                <AuditLogSearch />
            </ToastProvider>
        );
        expect(await screen.findByText("No audit logs available to you")).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
    });

    it("restores filters from a link and does not run the search", async () => {
        window.location.hash = "#/auth/audit-log-search/?user=alice&outcome=denied";
        await openPage();
        expect(mockStart).not.toHaveBeenCalled();
        expect(screen.getByText("No results yet")).toBeTruthy();
        expect(document.body.textContent).toContain("alice");
    });

    it("runs a search, long-polls to the end and shows records as plain text", async () => {
        mockGet
            .mockResolvedValueOnce([
                true,
                {
                    status: "Running",
                    Items: [],
                    statistics: { recordsMatched: 1, recordsScanned: 9 },
                },
            ])
            .mockResolvedValue(complete([row({ reason: HOSTILE })]));
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));

        await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1));
        const body = mockStart.mock.calls[0][0];
        // No group chosen: the server searches every group this caller may.
        expect(body).not.toHaveProperty("logGroups");
        expect(body).not.toHaveProperty("query");

        const table = await screen.findByRole("table");
        await waitFor(() => expect(within(table).getByText(/assetUpdate/)).toBeTruthy(), {
            timeout: 4000,
        });
        // Hostile content is text: no element came out of it, and the bidi mark is visible.
        expect(document.querySelector("img")).toBeNull();
        expect(table.textContent).toContain("\\u202e");
        expect(mockGet.mock.calls[0][0]).toMatchObject({ queryId: "q-1", wait: 20 });
    });

    it("shows the scanned count while running and never a matched count", async () => {
        mockGet.mockResolvedValue([
            true,
            { status: "Running", Items: [], statistics: { recordsMatched: 77, recordsScanned: 9 } },
        ]);
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        const progress = await screen.findByTestId("audit-progress");
        await waitFor(() => expect(progress.textContent).toContain("9 records scanned so far"));
        expect(progress.textContent).not.toMatch(/matched|77/);
    });

    it("stops without reading a page: a stopped search shows no records", async () => {
        mockGet.mockResolvedValue([true, { status: "Running", Items: [], statistics: {} }]);
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        const stop = await screen.findByRole("button", { name: "Stop" });
        const readsBefore = mockGet.mock.calls.length;
        await act(async () => {
            await userEvent.click(stop);
        });
        await waitFor(() => expect(mockStop).toHaveBeenCalledWith("q-1"));
        const stopped = await screen.findByTestId("audit-stopped");
        expect(stopped.textContent).toContain("Search stopped");
        // The explanation is in the info popover, not an inline banner.
        expect(screen.queryByText(/A stopped search shows no records/)).toBeNull();
        await userEvent.click(
            within(stopped).getByRole("button", { name: "About the stopped search" })
        );
        expect(await screen.findByText(/A stopped search shows no records/)).toBeTruthy();
        expect(mockGet.mock.calls.length).toBeLessThanOrEqual(readsBefore + 1);
    });

    it("sends the stop once the handle arrives when Stop is pressed while the search starts", async () => {
        let answerStart: (value: unknown) => void = () => undefined;
        mockStart.mockImplementation(
            () =>
                new Promise((resolve) => {
                    answerStart = resolve;
                })
        );
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await act(async () => {
            await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
        });
        expect(await screen.findByText("Search stopped")).toBeTruthy();
        expect(mockStop).not.toHaveBeenCalled();
        await act(async () => {
            answerStart([true, { queryId: "q-late", status: "Running" }]);
        });
        await waitFor(() => expect(mockStop).toHaveBeenCalledWith("q-late"));
        expect(mockGet).not.toHaveBeenCalled();
        expect(screen.getByText("Search stopped")).toBeTruthy();
    });

    it("stops a search that was replaced while its start was in flight", async () => {
        const answers: ((value: unknown) => void)[] = [];
        mockStart.mockImplementation(
            () =>
                new Promise((resolve) => {
                    answers.push(resolve);
                })
        );
        mockGet.mockResolvedValue(complete([row()]));
        const { unmount } = render(
            <ToastProvider>
                <AuditLogSearch />
            </ToastProvider>
        );
        await screen.findByRole("button", { name: "Search" });
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        unmount();
        await act(async () => {
            answers[0]([true, { queryId: "q-left", status: "Running" }]);
        });
        await waitFor(() => expect(mockStop).toHaveBeenCalledWith("q-left"));
    });

    it("explains that the Authentication group is empty while it is among the searched groups", async () => {
        window.location.hash = "#/auth/audit-log-search/?group=auditAuthentication";
        mockGroups.mockResolvedValue([
            true,
            {
                ...GROUPS,
                logGroups: [
                    ...GROUPS.logGroups,
                    {
                        key: "auditAuthentication",
                        label: "Authentication",
                        prefix: "AUTHENTICATION",
                    },
                ],
            },
        ]);
        await openPage();
        const note = screen.getByTestId("audit-authentication-note");
        // The second info icon next to Filters carries a visible name, so it is told apart.
        expect(within(note).getByText("Authentication group")).toBeTruthy();
        expect(screen.queryByText(/does not record sign-ins itself/)).toBeNull();
        await userEvent.click(
            within(note).getByRole("button", { name: "About the Authentication log group" })
        );
        expect(await screen.findByText(/does not record sign-ins itself/)).toBeTruthy();
    });

    it("shows no Authentication note for other groups", async () => {
        await openPage();
        expect(screen.queryByTestId("audit-authentication-note")).toBeNull();
    });

    it("drops the results when a link with other filters is opened while they are showing", async () => {
        mockGet.mockResolvedValue(complete([row()]));
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await screen.findByRole("table", {}, { timeout: 4000 });
        await waitFor(() => expect(document.body.textContent).toContain("assetUpdate"));
        await act(async () => {
            window.location.hash = "#/auth/audit-log-search/?user=mallory";
            window.dispatchEvent(new HashChangeEvent("hashchange"));
        });
        expect(await screen.findByText("No results yet")).toBeTruthy();
        expect(document.body.textContent).not.toContain("assetUpdate");
        expect(mockStart).toHaveBeenCalledTimes(1); // a link restores filters and never runs a search
    });

    it("opens a record in the detail panel without interpreting its content", async () => {
        mockGet.mockResolvedValue(complete([row()]));
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        const radio = await screen.findByRole(
            "radio",
            { name: /Show details of the/ },
            { timeout: 4000 }
        );
        await userEvent.click(radio);
        expect(await screen.findByText("Record details")).toBeTruthy();
        expect(document.querySelector("img")).toBeNull();
        expect(screen.getByTestId("audit-value-tree").textContent).toContain("<img src=x");
        expect(screen.getByRole("button", { name: "Show records of this request" })).toBeTruthy();
    });

    it("explains an unreadable record and shows only its masked preview", async () => {
        mockGet.mockResolvedValue(
            complete([
                row({
                    parseStatus: "unparsed",
                    preview: "[ACTIONS] <b>x</b>",
                    requestParameters: null,
                }),
            ])
        );
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await userEvent.click(
            await screen.findByRole("radio", { name: /Show details of the/ }, { timeout: 4000 })
        );
        expect(await screen.findByText("This record could not be read")).toBeTruthy();
        expect(screen.getByTestId("audit-preview").textContent).toBe("[ACTIONS] <b>x</b>");
    });

    it("shows the notices the server's statistics and warnings call for", async () => {
        mockGet.mockResolvedValue(
            complete([row()], {
                statistics: { candidateLimitReached: true },
                warnings: ["recheckTimeBudget"],
            })
        );
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        expect(
            await screen.findByText(/reached its record limit/, {}, { timeout: 4000 })
        ).toBeTruthy();
        expect(screen.getByText(/took longer than the time allowed/)).toBeTruthy();
        // Warnings are toasts: dismissible, and shown once.
        expect(screen.getAllByText(/reached its record limit/)).toHaveLength(1);
        await userEvent.click(screen.getAllByRole("button", { name: /Dismiss warning/ })[0]);
        await waitFor(() => expect(screen.queryByText(/reached its record limit/)).toBeNull());
    });

    it("keeps information out of banners: notes of the results sit in an info popover", async () => {
        mockGet.mockResolvedValue(complete([row()], { warnings: ["widerScan"] }));
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        const info = await screen.findByTestId("audit-results-info", {}, { timeout: 4000 });
        expect(screen.queryByText(/could not be used to narrow the scan/)).toBeNull();
        await userEvent.click(within(info).getByRole("button", { name: "About these results" }));
        expect(await screen.findByText(/could not be used to narrow the scan/)).toBeTruthy();
    });

    it("opens the filter hints from the keyboard", async () => {
        await openPage();
        const info = screen.getByTestId("audit-filters-info");
        const button = within(info).getByRole("button", { name: "About the filters" });
        button.focus();
        await userEvent.keyboard("{Enter}");
        expect(await screen.findByText(/Contains searches the identifiers/)).toBeTruthy();
    });

    it("raises a failed search once as a toast, however often the same error repeats", async () => {
        mockStart.mockResolvedValue([false, "busy", 429]);
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        expect(await screen.findByText("The service is busy")).toBeTruthy();
        expect(screen.getAllByText(/The service is busy with other searches/)).toHaveLength(1);
    });

    it.each([
        [429, "The service is busy with other searches. Wait a moment and search again."],
        [403, "You do not have access to audit logs."],
        [400, "Too many searches running"],
    ])("shows a refused start (%s) with its own message", async (status, message) => {
        mockStart.mockResolvedValue([false, "Too many searches running", status]);
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        expect(await screen.findByText(message)).toBeTruthy();
    });

    it("tells the user an expired handle needs a new search", async () => {
        mockGet.mockResolvedValue([false, "Query not found", 404]);
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        expect(
            await screen.findByText(
                "These results expired. Run the search again.",
                {},
                { timeout: 4000 }
            )
        ).toBeTruthy();
    });

    it("shows an empty result as its own state", async () => {
        mockGet.mockResolvedValue(complete([]));
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        expect(await screen.findByText("No records", {}, { timeout: 4000 })).toBeTruthy();
    });

    it("stops a running search", async () => {
        mockGet.mockImplementation(
            () =>
                new Promise((resolve) =>
                    setTimeout(() => resolve([true, { status: "Running", Items: [] }]), 50)
                )
        );
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        const stop = await screen.findByRole("button", { name: "Stop" });
        await act(async () => {
            await userEvent.click(stop);
        });
        await waitFor(() => expect(mockStop).toHaveBeenCalledWith("q-1"));
        expect(await screen.findByText("Search stopped")).toBeTruthy();
    });

    it("refuses a search the server limits would refuse, before any request", async () => {
        window.location.hash = "#/auth/audit-log-search/?contains=ab";
        await openPage();
        expect(screen.getByText(/A contains term has 3 to 64 characters/)).toBeTruthy();
        expect((screen.getByRole("button", { name: "Search" }) as HTMLButtonElement).disabled).toBe(
            true
        );
        expect(mockStart).not.toHaveBeenCalled();
    });
});

const LIMIT_WARNING = /reached its record limit/;
const limited = () =>
    complete([row()], {
        statistics: { candidateLimitReached: true },
        warnings: ["recheckTimeBudget"],
    });
const searchLimited = async () => {
    mockGet.mockResolvedValue(limited());
    await openPage();
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText(LIMIT_WARNING, {}, { timeout: 4000 });
};

describe("Audit Log Search page: warning toasts do not outlive their results (D14)", () => {
    it("gives the warnings a long finite duration as a backstop", async () => {
        expect(WARNING_TOAST_BACKSTOP_MS).toBe(600_000);
        warningOptions.length = 0;
        await searchLimited();
        expect(warningOptions.length).toBeGreaterThan(0);
        for (const options of warningOptions) {
            expect(options.duration).toBe(WARNING_TOAST_BACKSTOP_MS);
        }
    });

    it("dismisses them when a new search starts", async () => {
        await searchLimited();
        expect(screen.getByText(/took longer than the time allowed/)).toBeTruthy();
        mockGet.mockResolvedValue(complete([row()]));
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(screen.queryByText(LIMIT_WARNING)).toBeNull());
        expect(screen.queryByText(/took longer than the time allowed/)).toBeNull();
    });

    it("dismisses them when the results are cleared by a link with other filters", async () => {
        await searchLimited();
        await act(async () => {
            window.location.hash = "#/auth/audit-log-search/?user=mallory";
            window.dispatchEvent(new HashChangeEvent("hashchange"));
        });
        await waitFor(() => expect(screen.queryByText(LIMIT_WARNING)).toBeNull());
        expect(screen.queryByText(/took longer than the time allowed/)).toBeNull();
    });

    it("dismisses them when the page is left, while the toast stack lives on", async () => {
        mockGet.mockResolvedValue(limited());
        const view = render(
            <ToastProvider>
                <AuditLogSearch />
            </ToastProvider>
        );
        await userEvent.click(await screen.findByRole("button", { name: "Search" }));
        await screen.findByText(LIMIT_WARNING, {}, { timeout: 4000 });
        view.rerender(
            <ToastProvider>
                <div>Another page</div>
            </ToastProvider>
        );
        // The provider is the same instance across the rerender, so the toast would stay if the
        // page did not take it down.
        await waitFor(() => expect(screen.queryByText(LIMIT_WARNING)).toBeNull());
        expect(screen.getByText("Another page")).toBeTruthy();
    });

    it("does not dismiss a toast the page did not raise", async () => {
        await searchLimited();
        expect(screen.getAllByText(LIMIT_WARNING)).toHaveLength(1);
    });
});

describe("Audit Log Search page: layout and wording fixes of the UX recheck", () => {
    it("says the search was stopped in the empty table, not that no record matches", async () => {
        mockGet.mockResolvedValue([true, { status: "Running", Items: [], statistics: {} }]);
        await openPage();
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        const stop = await screen.findByRole("button", { name: "Stop" });
        await act(async () => {
            await userEvent.click(stop);
        });
        expect(await screen.findByText("The search was stopped")).toBeTruthy();
        expect(screen.queryByText(/No records match this search/)).toBeNull();
        expect(screen.queryByText("No records")).toBeNull();
    });

    it("keeps the status in a live region that exists before its text", async () => {
        mockGet.mockResolvedValue([true, { status: "Running", Items: [], statistics: {} }]);
        await openPage();
        const live = screen.getByTestId("audit-status-live");
        expect(live.getAttribute("aria-live")).toBe("polite");
        expect(live.textContent).toBe("");
        await userEvent.click(screen.getByRole("button", { name: "Search" }));
        await waitFor(() => expect(live.textContent).toContain("Waiting for the first results"));
        expect(screen.getByTestId("audit-status-live")).toBe(live);
    });

    it("puts the status on its own line under the controls so starting a search cannot reflow them", async () => {
        await openPage();
        const slot = screen.getByTestId("audit-status-slot");
        const controls = screen.getByTestId("audit-search-controls");
        expect(controls.contains(slot)).toBe(true);
        expect(screen.getByTestId("audit-search-controls-row").contains(slot)).toBe(false);
        // Zero intrinsic width, full width of the controls: the text can never widen them.
        expect(slot.style.width).toBe("0px");
        expect(slot.style.minWidth).toBe("100%");
        expect(slot.style.overflow).toBe("hidden");
    });

    it("aligns the controls to the top of the filter row, not to the bottom of its column", async () => {
        await openPage();
        const layout = screen.getByTestId("audit-filter-layout");
        expect(layout.style.alignItems).toBe("flex-start");
        expect(screen.getByTestId("audit-search-controls-row").style.alignItems).toBe("flex-start");
    });

    it("tells the user in the search popover that records appear at the end and Stop is available", async () => {
        await openPage();
        await userEvent.click(
            within(screen.getByTestId("audit-search-info")).getByRole("button", {
                name: "About running a search",
            })
        );
        expect(await screen.findByText(/Records appear when the search finishes/)).toBeTruthy();
        expect(screen.getByText(/Choose Stop to end it early/)).toBeTruthy();
    });

    it("shows a Log group token from a link by its label, not its key", async () => {
        window.location.hash = "#/auth/audit-log-search/?group=auditActions";
        await openPage();
        expect(screen.queryByText(/auditActions/)).toBeNull();
        expect(document.body.textContent).toContain("Actions");
    });

    it("keeps field problems inline with Search disabled, and raises no toast for them", async () => {
        window.location.hash = "#/auth/audit-log-search/?contains=ab";
        await openPage();
        expect(screen.getByText(/A contains term has 3 to 64 characters/)).toBeTruthy();
        expect(document.querySelector('[aria-label="Notifications"]')).toBeNull();
        expect((screen.getByRole("button", { name: "Search" }) as HTMLButtonElement).disabled).toBe(
            true
        );
    });
});

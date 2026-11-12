/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every sortable column of the compliance tables sorts, both ways (D183): a date by its value, a
 * state by the label it shows, text ignoring case with the numbers in it by value. On a listing
 * the server pages, the header sorts the page shown and says so while more than one page exists;
 * the schemas page, whose listing is the whole list, sorts and pages it in the browser (D186).
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import Synonyms from "../synonyms";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import ComplianceAuditLog from "./ComplianceAuditLog";
import DatabaseCompliancePage from "./DatabaseCompliance";
import ComplianceSchemas from "./ComplianceSchemas";
import { ComplianceTab } from "../components/asset/tabs/ComplianceTab";
import EvaluationHistoryModal from "../components/compliance/EvaluationHistoryModal";
import { CascadeNodeList, cascadeNodeEntries } from "../components/compliance/cascadeNodes";
import { BROWSER_PAGE_SIZE, PAGE_SORTING_NOTE } from "../components/list/tableSorting";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useNavigate: () => jest.fn(),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
    fetchAuditLog: jest.fn(),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
    fetchComplianceSchemaListing: jest.fn(),
    fetchComplianceState: jest.fn(),
    fetchEvaluationHistory: jest.fn(),
}));

jest.mock("../services/APIService", () => ({
    fetchAllDatabases: jest.fn(() => Promise.resolve([{ databaseId: "db1" }])),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../hooks/useAssetPageAccess", () => ({
    useAssetPageAccess: () => ({ canOpenAsset: false, canOpenDatabaseAssets: false }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

// Three timestamps whose display order (10/1, 2/1, 9/15 as text) differs from their value order.
const OCT = "2026-10-01T00:00:00Z";
const FEB = "2026-02-01T00:00:00Z";
const SEP = "2026-09-15T00:00:00Z";

/** The table whose header row holds `header`. */
const tableWith = (header: string) => {
    const table = createWrapper()
        .findAllTables()
        .find((candidate) =>
            candidate
                .findColumnHeaders()
                .some((cell) => cell.getElement().textContent?.trim() === header)
        );
    if (!table) throw new Error(`no table with the column ${header}`);
    return table;
};

/** The header cell of the column `header`. */
const headerCell = (header: string): HTMLElement => {
    const row = tableWith(header).getElement().querySelector("thead tr");
    const cell = Array.from(row?.children || []).find(
        (th: Element) => th.textContent?.trim() === header
    );
    if (!cell) throw new Error(`no header cell ${header}`);
    return cell as HTMLElement;
};

const sortBy = (header: string) => {
    const area = headerCell(header).querySelector<HTMLElement>("[role=button]");
    if (!area) throw new Error(`the column ${header} offers no sorting`);
    fireEvent.click(area);
};

/** The order of the rows, each named by the one of `keys` its text contains. */
const rowOrder = (header: string, keys: string[]): string[] =>
    tableWith(header)
        .findRows()
        .map(
            (row) => keys.find((key) => (row.getElement().textContent || "").includes(key)) || "?"
        );

/** Sorts by the column ascending, then descending, and checks the order each time. */
const expectSortsBothWays = (header: string, keys: string[], ascending: string[]) => {
    sortBy(header);
    expect(rowOrder(header, keys)).toEqual(ascending);
    sortBy(header);
    expect(rowOrder(header, keys)).toEqual([...ascending].reverse());
};

const NAMES = ["Alpha", "bravo", "Charlie"];

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    window.history.replaceState(null, "", "/#/compliance");
});

afterEach(() => jest.restoreAllMocks());

describe("Quarantine", () => {
    const rows = [
        {
            databaseId: "db2",
            assetId: "a1",
            assetName: "bravo",
            schemaName: "s-10",
            complianceState: "quarantined",
            quarantinedAt: OCT,
        },
        {
            databaseId: "db10",
            assetId: "a2",
            assetName: "Alpha",
            schemaName: "s-2",
            complianceState: "quarantined",
            quarantinedAt: FEB,
        },
        {
            databaseId: "db1",
            assetId: "a3",
            assetName: "Charlie",
            schemaName: "s-1",
            complianceState: "quarantined",
            quarantinedAt: SEP,
        },
    ];

    it.each([
        [Synonyms.Asset, ["Alpha", "bravo", "Charlie"]],
        [Synonyms.Database, ["Charlie", "bravo", "Alpha"]],
        ["Bound schema", ["Charlie", "Alpha", "bravo"]],
        ["Quarantined At", ["Alpha", "Charlie", "bravo"]],
    ])("sorts by %s both ways", async (header, ascending) => {
        service().fetchQuarantinedAssets.mockResolvedValue([true, { quarantinedAssets: rows }]);
        render(<ComplianceQuarantine />);
        await screen.findByText("Charlie");
        expectSortsBothWays(header, NAMES, ascending);
    });

    it("says the sorting covers the page shown only while more than one page exists", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: rows, nextToken: "page-2" },
        ]);
        render(<ComplianceQuarantine />);
        expect(await screen.findByText(PAGE_SORTING_NOTE)).toBeInTheDocument();
    });

    it("says nothing about the page when the listing is one page", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([true, { quarantinedAssets: rows }]);
        render(<ComplianceQuarantine />);
        await screen.findByText("Charlie");
        expect(screen.queryByText(PAGE_SORTING_NOTE)).not.toBeInTheDocument();
    });
});

describe("Cascades", () => {
    const rows = [
        {
            cascadeId: "c1-cascade",
            state: "pending_approval",
            triggeredByDatabaseId: "db1",
            triggeredByAssetId: "a1",
            assetName: "bravo",
            createdAt: OCT,
            approvalTimeoutAt: "2026-10-03T00:00:00Z",
        },
        {
            cascadeId: "c2-cascade",
            state: "completed",
            triggeredByDatabaseId: "db1",
            triggeredByAssetId: "a2",
            assetName: "Alpha",
            createdAt: FEB,
        },
        {
            cascadeId: "c3-cascade",
            state: "aborted",
            triggeredByDatabaseId: "db1",
            triggeredByAssetId: "a3",
            assetName: "Charlie",
            createdAt: SEP,
            approvalTimeoutAt: "2026-09-16T00:00:00Z",
        },
    ];

    it.each([
        // The status labels: Aborted, Completed, Pending Approval.
        ["Cascade", ["Charlie", "Alpha", "bravo"]],
        [`Trigger ${Synonyms.Asset}`, ["Alpha", "bravo", "Charlie"]],
        ["Created", ["Alpha", "Charlie", "bravo"]],
        // A cascade without an approval timeout sorts after the others.
        ["Approval timeout", ["Charlie", "bravo", "Alpha"]],
    ])("sorts by %s both ways", async (header, ascending) => {
        service().fetchCascades.mockResolvedValue([true, { cascades: rows }]);
        render(<ComplianceCascades />);
        await screen.findByText("Charlie");
        expectSortsBothWays(header, NAMES, ascending);
    });

    it("says the sorting covers the page shown while more pages exist", async () => {
        service().fetchCascades.mockResolvedValue([true, { cascades: rows, nextToken: "p2" }]);
        render(<ComplianceCascades />);
        expect(await screen.findByText(PAGE_SORTING_NOTE)).toBeInTheDocument();
    });
});

describe("Audit log", () => {
    const rows = [
        {
            entryId: "e1",
            timestamp: OCT,
            eventType: "exception_granted",
            databaseId: "db2",
            assetId: "a1",
            assetName: "bravo",
            actor: "zed@example.com",
        },
        {
            entryId: "e2",
            timestamp: FEB,
            eventType: "compliance_check",
            databaseId: "db10",
            assetId: "a2",
            assetName: "Alpha",
            actor: "amy@example.com",
        },
        {
            entryId: "e3",
            timestamp: SEP,
            eventType: "schema_bound_to_database",
            databaseId: "db1",
            assetId: "a3",
            assetName: "Charlie",
            actor: "max@example.com",
        },
    ];

    it.each([
        ["Timestamp", ["Alpha", "Charlie", "bravo"]],
        ["Event Type", ["Alpha", "bravo", "Charlie"]],
        [Synonyms.Database, ["Charlie", "bravo", "Alpha"]],
        [Synonyms.Asset, ["Alpha", "bravo", "Charlie"]],
        ["Actor", ["Alpha", "Charlie", "bravo"]],
    ])("sorts by %s both ways", async (header, ascending) => {
        service().fetchAuditLog.mockResolvedValue([true, { entries: rows }]);
        render(<ComplianceAuditLog />);
        await screen.findByText("Charlie");
        expectSortsBothWays(header, NAMES, ascending);
    });

    it("says the sorting covers the page shown while more pages exist", async () => {
        service().fetchAuditLog.mockResolvedValue([true, { entries: rows, nextToken: "p2" }]);
        render(<ComplianceAuditLog />);
        expect(await screen.findByText(PAGE_SORTING_NOTE)).toBeInTheDocument();
    });
});

describe(`${Synonyms.Database} compliance overview`, () => {
    const overview = (nextToken?: string) => ({
        databaseId: "db1",
        totalAssets: 3,
        summary: { compliant: 1, non_compliant: 1, quarantined: 1 },
        nextToken,
        assets: [
            {
                assetId: "a1",
                assetName: "bravo",
                complianceState: "quarantined",
                schemaName: "s-10",
                updatedAt: OCT,
            },
            {
                assetId: "a2",
                assetName: "Alpha",
                complianceState: "compliant",
                schemaName: "s-2",
                updatedAt: FEB,
            },
            {
                assetId: "a3",
                assetName: "Charlie",
                complianceState: "non_compliant",
                schemaName: "s-1",
                updatedAt: SEP,
            },
        ],
    });

    beforeEach(() => {
        service().getDatabaseBindings.mockResolvedValue([
            true,
            { databaseId: "db1", databaseSchema: "s-1", assetOverrides: [] },
        ]);
        service().fetchComplianceSchemas.mockResolvedValue([true, []]);
    });

    it.each([
        [Synonyms.Asset, ["Alpha", "bravo", "Charlie"]],
        // The state labels: Compliant, Non-Compliant, Quarantined.
        ["State", ["Alpha", "Charlie", "bravo"]],
        ["Bound schema", ["Charlie", "Alpha", "bravo"]],
        ["Last Updated", ["Alpha", "Charlie", "bravo"]],
    ])("sorts by %s both ways", async (header, ascending) => {
        service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview()]);
        render(<DatabaseCompliancePage />);
        await screen.findByText("Charlie");
        expectSortsBothWays(header, NAMES, ascending);
    });

    it("says the column sorting covers the page shown and Sort by the whole database", async () => {
        service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview("p2")]);
        render(<DatabaseCompliancePage />);
        expect(
            await screen.findByText(
                new RegExp(
                    `${PAGE_SORTING_NOTE} Sort by orders every ${Synonyms.asset} of the ${Synonyms.database}\\.`
                )
            )
        ).toBeInTheDocument();
    });

    // D191: the order the reader just chose for the whole database is the one the rows follow.
    it("drops a column-header sort when a Sort by order is chosen", async () => {
        window.history.replaceState(null, "", "/#/databases/db1/compliance");
        // The server answers in asset ID order (bravo, Alpha, Charlie), or by name when asked.
        service().fetchDatabaseComplianceOverview.mockImplementation(
            async (_databaseId: string, _paging: any, filters: any) => {
                const page = overview();
                if (filters?.sort === "name") {
                    page.assets = [page.assets[1], page.assets[0], page.assets[2]];
                }
                return [true, page];
            }
        );
        render(<DatabaseCompliancePage />);
        await screen.findByText("Charlie");
        expect(rowOrder(Synonyms.Asset, NAMES)).toEqual(["bravo", "Alpha", "Charlie"]);

        // A header sort first: descending by name on the page shown.
        sortBy(Synonyms.Asset);
        sortBy(Synonyms.Asset);
        expect(rowOrder(Synonyms.Asset, NAMES)).toEqual(["Charlie", "bravo", "Alpha"]);
        expect(headerCell(Synonyms.Asset).getAttribute("aria-sort")).toBe("descending");

        await userEvent.click(
            screen.getByRole("button", { name: new RegExp(`Sort ${Synonyms.assets}`) })
        );
        await userEvent.click(await screen.findByText(`Sort by ${Synonyms.asset} name`));

        await waitFor(() =>
            expect(
                service().fetchDatabaseComplianceOverview.mock.calls.some(
                    (call: any[]) => call[2]?.sort === "name"
                )
            ).toBe(true)
        );
        // The rows follow the server's name order, and no header shows a sort.
        await waitFor(() =>
            expect(rowOrder(Synonyms.Asset, NAMES)).toEqual(["Alpha", "bravo", "Charlie"])
        );
        expect(headerCell(Synonyms.Asset).getAttribute("aria-sort")).not.toBe("descending");
        expect(headerCell(Synonyms.Asset).getAttribute("aria-sort")).not.toBe("ascending");

        // A header chosen after Sort by still sorts the page shown.
        sortBy(Synonyms.Asset);
        sortBy(Synonyms.Asset);
        expect(rowOrder(Synonyms.Asset, NAMES)).toEqual(["Charlie", "bravo", "Alpha"]);
    });
});

describe("Compliance schemas", () => {
    const listing = (schemas: any[]) => [true, { schemas }];
    const three = [
        {
            schemaName: "bravo",
            databaseId: "db2",
            description: "Zulu checks",
            schemaFormat: "vams-rules-v1",
            schemaBody: { schemaFormat: "vams-rules-v1", rules: {} },
            version: 10,
        },
        {
            schemaName: "Alpha",
            description: "alpha checks",
            schemaFormat: "legacy",
            schemaBody: { type: "object" },
            version: 2,
        },
        {
            schemaName: "Charlie",
            databaseId: "db10",
            description: "Mike checks",
            schemaFormat: "vams-rules-v1",
            schemaBody: { schemaFormat: "vams-rules-v1", rules: {} },
            version: 1,
        },
    ];

    it.each([
        ["Schema Name", ["Alpha", "bravo", "Charlie"]],
        // db2, db10, then GLOBAL.
        ["Available to", ["bravo", "Charlie", "Alpha"]],
        ["Description", ["Alpha", "Charlie", "bravo"]],
        ["Version", ["Charlie", "Alpha", "bravo"]],
    ])("sorts by %s both ways", async (header, ascending) => {
        service().fetchComplianceSchemaListing.mockResolvedValue(listing(three));
        render(<ComplianceSchemas />);
        await screen.findByText("Charlie");
        expectSortsBothWays(header, NAMES, ascending);
    });

    it("sorts by Format both ways", async () => {
        service().fetchComplianceSchemaListing.mockResolvedValue(listing(three));
        render(<ComplianceSchemas />);
        await screen.findByText("Charlie");
        sortBy("Format");
        expect(rowOrder("Format", NAMES)[0]).toBe("Alpha");
        sortBy("Format");
        expect(rowOrder("Format", NAMES)[2]).toBe("Alpha");
    });

    const many = Array.from({ length: BROWSER_PAGE_SIZE + 5 }, (_, index) => ({
        schemaName: `schema-${String(index + 1).padStart(2, "0")}`,
        description: index % 2 === 0 ? "even row" : "odd row",
        schemaFormat: "vams-rules-v1",
        schemaBody: { schemaFormat: "vams-rules-v1", rules: {} },
        version: 1,
        ...(index >= BROWSER_PAGE_SIZE ? { databaseId: "db-late" } : {}),
    }));
    const shownNames = () =>
        tableWith("Schema Name")
            .findRows()
            .map((row) => /schema-\d+/.exec(row.getElement().textContent || "")?.[0]);

    it("pages the whole list in the browser", async () => {
        service().fetchComplianceSchemaListing.mockResolvedValue(listing(many));
        render(<ComplianceSchemas />);
        await screen.findByText("schema-01");
        expect(shownNames()).toHaveLength(BROWSER_PAGE_SIZE);
        expect(screen.queryByText("schema-20")).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Next page of compliance schemas" }));
        expect(await screen.findByText("schema-20")).toBeInTheDocument();
        expect(shownNames()).toEqual([
            "schema-16",
            "schema-17",
            "schema-18",
            "schema-19",
            "schema-20",
        ]);
    });

    it("sorts the whole list before paging it", async () => {
        service().fetchComplianceSchemaListing.mockResolvedValue(listing(many));
        render(<ComplianceSchemas />);
        await screen.findByText("schema-01");
        sortBy("Schema Name");
        sortBy("Schema Name");
        // Descending: the last schema, on page 2 before, leads page 1.
        expect(shownNames()[0]).toBe("schema-20");
        expect(shownNames()).toHaveLength(BROWSER_PAGE_SIZE);
    });

    it("filters the whole list, then pages it, and returns to the first page", async () => {
        service().fetchComplianceSchemaListing.mockResolvedValue(listing(many));
        render(<ComplianceSchemas />);
        await screen.findByText("schema-01");
        fireEvent.click(screen.getByRole("button", { name: "Next page of compliance schemas" }));
        await screen.findByText("schema-20");

        // The availability filter keeps the five schemas that were all on page 2.
        const availability = createWrapper(
            document.querySelector<HTMLElement>("[data-list-toolbar-group='filters']")!
        ).findSelect()!;
        availability.openDropdown();
        availability.selectOptionByValue("db-late");
        await waitFor(() =>
            expect(shownNames()).toEqual([
                "schema-16",
                "schema-17",
                "schema-18",
                "schema-19",
                "schema-20",
            ])
        );
        expect(createWrapper().findPagination()!.findCurrentPage().getElement().textContent).toBe(
            "1"
        );

        // The text filter matches across both pages of the unfiltered list.
        availability.openDropdown();
        availability.selectOptionByValue("*");
        createWrapper().findTextFilter()!.findInput().setInputValue("odd row");
        await waitFor(() => expect(shownNames()).toHaveLength(10));
        expect(shownNames()).toContain("schema-20");
        expect(createWrapper().findTextFilter()!.findResultsCount().getElement().textContent).toBe(
            "10 matches"
        );
    });
});

describe("the asset Compliance tab's history", () => {
    const evaluations = (nextToken?: string) => [
        true,
        {
            nextToken,
            evaluations: [
                {
                    evaluationId: "ev1",
                    databaseId: "db1",
                    assetId: "asset-1",
                    schemaName: "Bravo-std",
                    result: "quarantined",
                    evaluatedAt: OCT,
                },
                {
                    evaluationId: "ev2",
                    databaseId: "db1",
                    assetId: "asset-1",
                    schemaName: "alpha-std",
                    result: "compliant",
                    evaluatedAt: FEB,
                },
                {
                    evaluationId: "ev3",
                    databaseId: "db1",
                    assetId: "asset-1",
                    schemaName: "Charlie-std",
                    result: "non_compliant",
                    evaluatedAt: SEP,
                },
            ],
        },
    ];
    const KEYS = ["alpha-std", "Bravo-std", "Charlie-std"];

    beforeEach(() => {
        service().fetchComplianceState.mockResolvedValue([
            true,
            { databaseId: "db1", assetId: "asset-1", complianceState: "compliant" },
        ]);
    });

    const renderTab = () =>
        render(
            <MemoryRouter>
                <ComplianceTab databaseId="db1" assetId="asset-1" isActive />
            </MemoryRouter>
        );

    it.each([
        ["Date", ["alpha-std", "Charlie-std", "Bravo-std"]],
        ["Schema", ["alpha-std", "Bravo-std", "Charlie-std"]],
        // The verdict labels: Compliant, Non-Compliant, Quarantined.
        ["Result", ["alpha-std", "Charlie-std", "Bravo-std"]],
    ])("sorts by %s both ways", async (header, ascending) => {
        service().fetchEvaluationHistory.mockResolvedValue(evaluations());
        renderTab();
        await screen.findByText("Charlie-std");
        expectSortsBothWays(header, KEYS, ascending);
    });

    it("says the sorting covers the page shown while more pages exist", async () => {
        service().fetchEvaluationHistory.mockResolvedValue(evaluations("p2"));
        renderTab();
        expect(await screen.findByText(PAGE_SORTING_NOTE)).toBeInTheDocument();
    });
});

describe("the evaluation history dialog", () => {
    const history = (nextToken?: string) => [
        true,
        {
            nextToken,
            evaluations: [
                {
                    evaluationId: "ev1",
                    schemaName: "Bravo-std",
                    schemaVersion: 10,
                    result: "quarantined",
                    evaluatedAt: OCT,
                },
                {
                    evaluationId: "ev2",
                    schemaName: "alpha-std",
                    schemaVersion: 2,
                    result: "compliant",
                    evaluatedAt: FEB,
                },
                {
                    evaluationId: "ev3",
                    schemaName: "Charlie-std",
                    result: "non_compliant",
                    evaluatedAt: SEP,
                },
            ],
        },
    ];
    const KEYS = ["alpha-std", "Bravo-std", "Charlie-std"];
    const open = () =>
        render(
            <EvaluationHistoryModal
                visible
                databaseId="db1"
                assetId="asset-1"
                assetLabel="asset-1"
                onDismiss={jest.fn()}
            />
        );

    it.each([
        ["Date", ["alpha-std", "Charlie-std", "Bravo-std"]],
        ["Schema", ["alpha-std", "Bravo-std", "Charlie-std"]],
        ["Result", ["alpha-std", "Charlie-std", "Bravo-std"]],
    ])("sorts by %s both ways", async (header, ascending) => {
        service().fetchEvaluationHistory.mockResolvedValue(history());
        open();
        await screen.findByText(/Charlie-std/);
        expectSortsBothWays(header, KEYS, ascending);
    });

    it("says the sorting covers the page shown while more pages exist", async () => {
        service().fetchEvaluationHistory.mockResolvedValue(history("p2"));
        open();
        expect(await screen.findByText(PAGE_SORTING_NOTE)).toBeInTheDocument();
    });
});

describe("a cascade's node list", () => {
    const record = (count: number) => {
        const nodes: Record<string, string> = {};
        const nodeNames: Record<string, string> = {};
        for (let index = 1; index <= count; index++) {
            const key = `db1:node-${String(index).padStart(2, "0")}`;
            nodes[key] = index % 2 === 0 ? "compliant" : "skipped_archived";
            nodeNames[key] = `Node ${String(index).padStart(2, "0")}`;
        }
        return {
            cascadeId: "c1",
            state: "completed",
            nodes,
            nodeNames,
            executionOrder: Object.keys(nodes).reverse(),
        } as any;
    };
    const shownNodes = () =>
        tableWith(Synonyms.Asset)
            .findRows()
            .map((row) => /Node \d+/.exec(row.getElement().textContent || "")?.[0]);

    it("lists the nodes in evaluation order, then sorts by name both ways", () => {
        render(<CascadeNodeList entries={cascadeNodeEntries(record(3)) || []} />);
        expect(shownNodes()).toEqual(["Node 03", "Node 02", "Node 01"]);
        sortBy(Synonyms.Asset);
        expect(shownNodes()).toEqual(["Node 01", "Node 02", "Node 03"]);
        sortBy(Synonyms.Asset);
        expect(shownNodes()).toEqual(["Node 03", "Node 02", "Node 01"]);
    });

    it("sorts by outcome label both ways", () => {
        render(<CascadeNodeList entries={cascadeNodeEntries(record(3)) || []} />);
        // "Evaluated: Compliant" before "Skipped: archived".
        sortBy("Outcome");
        expect(shownNodes()[0]).toBe("Node 02");
        sortBy("Outcome");
        expect(shownNodes()[2]).toBe("Node 02");
    });

    it("pages a long cascade in the browser and sorts across its pages", () => {
        render(<CascadeNodeList entries={cascadeNodeEntries(record(20)) || []} />);
        expect(shownNodes()).toHaveLength(BROWSER_PAGE_SIZE);
        expect(shownNodes()[0]).toBe("Node 20");
        sortBy(Synonyms.Asset);
        expect(shownNodes()[0]).toBe("Node 01");
        fireEvent.click(screen.getByRole("button", { name: "Next page of cascade nodes" }));
        expect(shownNodes()).toEqual(["Node 16", "Node 17", "Node 18", "Node 19", "Node 20"]);
    });
});

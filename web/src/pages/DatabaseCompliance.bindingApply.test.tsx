/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A database binding change is applied to the asset records in the background (D161): the API
 * answers a bind, a change or a removal at once (202) with the apply, and reports its progress and
 * its end on the binding read. The database compliance page shows "Applying the binding" with the
 * progress while it runs, reads the binding again with backoff until it ends, offers no other
 * change and no Evaluate All meanwhile (the API answers 409 to them), then reports the counts and
 * re-reads the overview. A stalled change offers Resume to a caller who may make it; a failed one
 * says why. A request that answers at once never shows "binding failed" (a 504 used to).
 */
import React from "react";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import DatabaseCompliancePage from "./DatabaseCompliance";
import {
    BINDING_APPLY_POLL_DELAYS_MS,
    ComplianceBindingApply,
    bindingApplyOutcomeText,
    bindingApplyPollDelay,
    bindingApplyProgressText,
} from "../services/ComplianceService";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
    bindSchemaToDatabase: jest.fn(),
    unbindSchemaFromDatabase: jest.fn(),
    evaluateDatabaseCompliance: jest.fn(),
    // The page waits this long between the reads that follow a change; no wait in the tests.
    bindingApplyPollDelay: jest.fn(() => 0),
}));

let mockAllowed: string[] = [];
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        can: (method: string, path: string) => mockAllowed.includes(`${method} ${path}`),
    }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const VIEW = ["GET /compliance/state/{databaseId}"];
const BINDER = [
    ...VIEW,
    "GET /compliance/schemas",
    "PUT /compliance/bind/{databaseId}",
    "DELETE /compliance/bind/{databaseId}",
    "POST /compliance/evaluate/{databaseId}",
];

const overview = {
    databaseId: "db1",
    totalAssets: 1,
    summary: { compliant: 1 },
    assets: [
        {
            databaseId: "db1",
            assetId: "id-a",
            assetName: "asset-a",
            complianceState: "compliant",
            schemaName: "std",
        },
    ],
};

const apply = (overrides: Partial<ComplianceBindingApply> = {}): ComplianceBindingApply => ({
    applyId: "apply-1",
    operation: "change",
    status: "applying",
    schemaName: "strict",
    previousSchema: "std",
    assetCount: 2500,
    assetsProcessed: 1000,
    assetsPendingEvaluation: 998,
    assetsKeptQuarantined: 2,
    removedComplianceRecords: 0,
    evaluationsQueued: 0,
    startedAt: "2026-11-07T15:00:00+00:00",
    lastProgressAt: "2026-11-07T15:00:10+00:00",
    ...overrides,
});

const bindings = (databaseSchema: string | null, last: ComplianceBindingApply | null) => [
    true,
    {
        databaseId: "db1",
        databaseSchema,
        assetOverrides: [],
        assetOverrideCount: 0,
        applyStatus: last?.status ?? null,
        apply: last,
    },
];

const applied = apply({
    status: "applied",
    assetsProcessed: 2500,
    assetsPendingEvaluation: 2497,
    assetsKeptQuarantined: 3,
    finishedAt: "2026-11-07T15:01:00+00:00",
});

const mount = async () => {
    render(<DatabaseCompliancePage />);
    await screen.findByText("asset-a");
};

const button = (name: string) => screen.queryByRole("button", { name });

const choose = (value: string) => {
    const select = createWrapper()
        .findAllFormFields()
        .find((field) => field.findLabel()?.getElement().textContent === "Bound schema")
        ?.findControl()
        ?.findSelect();
    if (!select) throw new Error("no Bound schema select");
    act(() => {
        select.openDropdown();
    });
    act(() => {
        select.selectOptionByValue(value);
    });
};

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    service().bindingApplyPollDelay.mockImplementation(() => 0);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview]);
    service().fetchComplianceSchemas.mockResolvedValue([
        true,
        [
            { schemaName: "std", schemaBody: {} },
            { schemaName: "strict", schemaBody: {} },
        ],
    ]);
});

afterEach(() => jest.restoreAllMocks());

describe("a binding change applied in the background", () => {
    it("shows the progress, offers no other change, then reports the counts", async () => {
        mockAllowed = BINDER;
        const running = apply();
        // The page's first read, the read after the change, one read while it runs, its end.
        service()
            .getDatabaseBindings.mockResolvedValueOnce(bindings("std", null))
            .mockResolvedValueOnce(bindings("strict", running))
            .mockResolvedValueOnce(bindings("strict", apply({ assetsProcessed: 2000 })))
            .mockResolvedValue(bindings("strict", applied));
        service().bindSchemaToDatabase.mockResolvedValue([
            true,
            "Schema 'strict' bound to database 'db1'; the binding is being applied to 2500 assets",
            {
                changed: true,
                applyStatus: "applying",
                applyId: "apply-1",
                assetCount: 2500,
                apply: apply({ assetsProcessed: 0 }),
            },
        ]);
        await mount();
        await waitFor(() => expect(service().fetchComplianceSchemas).toHaveBeenCalled());
        choose("strict");
        fireEvent.click(screen.getByRole("button", { name: "Change" }));

        expect(await screen.findByText("Applying the binding")).toBeInTheDocument();
        expect(screen.getAllByText(/Applying the binding to 2,500 assets/).length).toBeGreaterThan(
            0
        );
        expect(screen.queryByText(/binding failed/)).not.toBeInTheDocument();

        // The end: the counts, the overview read again, the controls back.
        expect(
            await screen.findByText(
                "The binding was applied to 2,500 assets: 2497 pending evaluation. 3 quarantined assets stay quarantined until re-evaluated."
            )
        ).toBeInTheDocument();
        expect(screen.queryByText("Applying the binding")).not.toBeInTheDocument();
        expect(screen.queryByText(/binding failed/)).not.toBeInTheDocument();
        expect(service().getDatabaseBindings).toHaveBeenCalledTimes(4);
        // The reads back off: each wait is asked for the next attempt.
        expect(service().bindingApplyPollDelay.mock.calls.map((c: number[]) => c[0])).toEqual([
            0, 1,
        ]);
        expect(service().fetchDatabaseComplianceOverview.mock.calls.length).toBeGreaterThanOrEqual(
            3
        );
        expect(button("Remove")).not.toBeDisabled();
        expect(button("Evaluate All Assets")).not.toBeDisabled();
    });

    it("disables Bind, Change, Remove and Evaluate All while a change runs", async () => {
        mockAllowed = BINDER;
        // Another user's change is running when the page opens; it is followed until it ends.
        let resolveEnd: (value: unknown) => void = () => undefined;
        const end = new Promise((resolve) => (resolveEnd = resolve));
        service()
            .getDatabaseBindings.mockResolvedValueOnce(bindings("strict", apply()))
            .mockImplementation(() => end);
        await mount();
        expect(await screen.findByText("Applying the binding")).toBeInTheDocument();
        expect(button("Remove")).toBeDisabled();
        expect(button("Evaluate All Assets")).toBeDisabled();
        expect(button("Change")).toBeDisabled();
        const bar = createWrapper().findProgressBar();
        expect(bar?.findPercentageText()?.getElement().textContent).toBe("40%");
        expect(bar?.findAdditionalInfo()?.getElement().textContent).toBe("1,000 of 2,500 done");
        await act(async () => resolveEnd(bindings("strict", applied)));
        await waitFor(() => expect(button("Remove")).not.toBeDisabled());
    });

    it("a removal says it is removing the binding", async () => {
        mockAllowed = BINDER;
        const removing = apply({ operation: "unbind", schemaName: null, assetCount: 3 });
        service()
            .getDatabaseBindings.mockResolvedValueOnce(bindings("std", null))
            .mockResolvedValue(bindings(null, removing));
        service().unbindSchemaFromDatabase.mockResolvedValue([
            true,
            "removed",
            { changed: true, applyStatus: "applying", applyId: "apply-1", apply: removing },
        ]);
        await mount();
        fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
        fireEvent.click(screen.getByRole("button", { name: "Remove binding" }));
        expect(await screen.findByText("Removing the binding")).toBeInTheDocument();
        expect(
            screen.getByText(
                `Compliance schema std removed from this database. Removing the binding from 3 assets.`
            )
        ).toBeInTheDocument();
    });

    it("a stalled change offers Resume to a binder, which repeats it", async () => {
        mockAllowed = BINDER;
        const stalled = apply({
            status: "stalled",
            reason: "No progress for 15 minutes; repeat the same change to resume it",
        });
        service()
            .getDatabaseBindings.mockResolvedValueOnce(bindings("strict", stalled))
            .mockResolvedValue(bindings("strict", applied));
        service().bindSchemaToDatabase.mockResolvedValue([
            true,
            "resumes",
            { changed: true, resumed: true, applyStatus: "applying", apply: apply() },
        ]);
        await mount();
        expect(await screen.findByText("Stopped")).toBeInTheDocument();
        expect(
            screen.getByText(
                "The binding change stopped while it was being applied: No progress for 15 minutes; repeat the same change to resume it."
            )
        ).toBeInTheDocument();
        expect(button("Remove")).toBeDisabled();
        fireEvent.click(screen.getByRole("button", { name: "Resume" }));
        await waitFor(() =>
            expect(service().bindSchemaToDatabase).toHaveBeenCalledWith("db1", "strict")
        );
        expect(await screen.findByText(/The binding was applied to 2,500 assets/)).toBeInTheDocument();
    });

    it("a reader who may not bind sees a stalled change without Resume", async () => {
        mockAllowed = VIEW;
        service().getDatabaseBindings.mockResolvedValue(
            bindings("strict", apply({ status: "stalled", reason: "No progress" }))
        );
        await mount();
        expect(await screen.findByText("Stopped")).toBeInTheDocument();
        expect(button("Resume")).not.toBeInTheDocument();
    });

    it("a failed change says why and the controls are offered again", async () => {
        mockAllowed = BINDER;
        service().getDatabaseBindings.mockResolvedValue(
            bindings(
                "strict",
                apply({
                    status: "failed",
                    reason: "The database binding no longer matches the change being applied",
                })
            )
        );
        await mount();
        expect(await screen.findByText("Not finished")).toBeInTheDocument();
        expect(
            screen.getByText(
                "The binding change did not finish: The database binding no longer matches the change being applied."
            )
        ).toBeInTheDocument();
        expect(button("Remove")).not.toBeDisabled();
        expect(button("Resume")).not.toBeInTheDocument();
    });

    it("an applied change from before shows nothing more than the binding", async () => {
        mockAllowed = BINDER;
        service().getDatabaseBindings.mockResolvedValue(bindings("strict", applied));
        await mount();
        expect(await screen.findByText("Schema: strict")).toBeInTheDocument();
        expect(screen.queryByText("Applying the binding")).not.toBeInTheDocument();
        expect(screen.queryByText(/The binding was applied/)).not.toBeInTheDocument();
        expect(service().getDatabaseBindings).toHaveBeenCalledTimes(1);
    });
});

describe("binding change texts", () => {
    it("back off to 15 s", () => {
        const actual = jest.requireActual("../services/ComplianceService");
        expect([0, 1, 2, 3, 4, 5, 6, 20].map(actual.bindingApplyPollDelay)).toEqual([
            2000, 3000, 5000, 8000, 10000, 15000, 15000, 15000,
        ]);
        expect(BINDING_APPLY_POLL_DELAYS_MS[BINDING_APPLY_POLL_DELAYS_MS.length - 1]).toBe(15000);
        expect(bindingApplyPollDelay).toBeDefined();
    });

    it("say what each kind of change does and how it ended", () => {
        expect(bindingApplyProgressText(apply())).toBe("Applying the binding to 2,500 assets");
        expect(bindingApplyProgressText(apply({ operation: "unbind", assetCount: 1 }))).toBe(
            "Removing the binding from 1 asset"
        );
        expect(bindingApplyProgressText(apply({ operation: "evaluatePending" }))).toBe(
            "Queueing the evaluations of 2,500 assets"
        );
        expect(
            bindingApplyOutcomeText(
                apply({
                    operation: "unbind",
                    status: "applied",
                    assetsProcessed: 4,
                    removedComplianceRecords: 3,
                    assetsKeptQuarantined: 1,
                })
            )
        ).toBe(
            "The binding was removed from 4 assets: 3 compliance records removed. 1 quarantined asset stays quarantined until re-evaluated."
        );
        expect(
            bindingApplyOutcomeText(
                apply({ operation: "evaluatePending", status: "applied", evaluationsQueued: 7 })
            )
        ).toBe("The evaluations of 7 assets were queued.");
    });
});

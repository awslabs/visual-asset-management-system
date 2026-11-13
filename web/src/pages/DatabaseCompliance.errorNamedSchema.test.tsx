/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The database compliance page binds a schema whose name holds "error" and reports the success
 * the API answered (D188). The page runs over the real compliance service, with only the HTTP
 * client stubbed: the service decides success from the answer (a 2xx, or a thrown non-2xx), never
 * from the message text, which repeats the schema's name. A refusal the API answers with a 409
 * still reads as a refusal.
 */
import React from "react";
import { fireEvent, render, screen, waitFor, act } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import DatabaseCompliancePage from "./DatabaseCompliance";
import { apiClient, ApiError } from "../services/apiClient";
import Synonyms from "../synonyms";

jest.mock("../services/apiClient", () => ({
    ...jest.requireActual("../services/apiClient"),
    apiClient: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
}));

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../hooks/useAssetPageAccess", () => ({
    useAssetPageAccess: () => ({ canOpenAsset: false, canOpenDatabaseAssets: false }),
}));

const NAMED = "error-budget";
const get = apiClient.get as jest.Mock;
const put = apiClient.put as jest.Mock;

const overview = {
    databaseId: "db1",
    totalAssets: 1,
    summary: { pending_evaluation: 1 },
    assets: [
        {
            databaseId: "db1",
            assetId: "id-a",
            assetName: "asset-a",
            complianceState: "pending_evaluation",
        },
    ],
};

const apply = (status: "applying" | "applied") => ({
    applyId: "apply-1",
    operation: "bind",
    status,
    schemaName: NAMED,
    previousSchema: null,
    assetCount: 1,
    assetsProcessed: status === "applied" ? 1 : 0,
    assetsPendingEvaluation: status === "applied" ? 1 : 0,
    assetsKeptQuarantined: 0,
    removedComplianceRecords: 0,
    evaluationsQueued: 0,
    startedAt: "2026-11-12T15:00:00+00:00",
    lastProgressAt: "2026-11-12T15:00:01+00:00",
});

// The binding the database holds, as the API answers its read.
let bound: { databaseSchema: string | null; apply: any } = { databaseSchema: null, apply: null };

const routeGet = async (path: string) => {
    if (path === "compliance/state/db1") return overview;
    if (path === "compliance/bind/db1") {
        return {
            databaseId: "db1",
            databaseSchema: bound.databaseSchema,
            complianceAutoEval: true,
            assetOverrides: [],
            assetOverrideCount: 0,
            applyStatus: bound.apply?.status ?? null,
            apply: bound.apply,
        };
    }
    if (path === "compliance/schemas") {
        return { schemas: [{ schemaName: NAMED, schemaBody: { schemaFormat: "vams-rules-v1" } }] };
    }
    throw new ApiError("Not Found", 404);
};

const chooseSchema = () => {
    const select = createWrapper()
        .findAllFormFields()
        .find((field) => field.findLabel()?.getElement().textContent === "Bound schema")!
        .findControl()!
        .findSelect()!;
    act(() => select.openDropdown());
    act(() => select.selectOptionByValue(NAMED));
};

const bindAndConfirm = async () => {
    render(<DatabaseCompliancePage />);
    await screen.findByText("asset-a");
    await waitFor(() => expect(get).toHaveBeenCalledWith("compliance/schemas", expect.anything()));
    chooseSchema();
    fireEvent.click(screen.getByRole("button", { name: "Bind" }));
    fireEvent.click(screen.getByRole("button", { name: "Bind schema" }));
};

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    window.history.replaceState(null, "", "/#/databases/db1/compliance");
    bound = { databaseSchema: null, apply: null };
    get.mockImplementation(routeGet);
});

afterEach(() => jest.restoreAllMocks());

describe(`binding a schema named ${NAMED} on the ${Synonyms.database} compliance overview`, () => {
    it("reports the binding as made, not as failed", async () => {
        put.mockImplementation(async () => {
            // The binding is saved; the read that follows finds the change applied.
            bound = { databaseSchema: NAMED, apply: apply("applied") };
            return {
                message: `Schema '${NAMED}' bound to database 'db1'; the binding is being applied to 1 assets`,
                databaseId: "db1",
                changed: true,
                applyStatus: "applying",
                applyId: "apply-1",
                assetCount: 1,
                apply: apply("applying"),
            };
        });
        await bindAndConfirm();

        expect(await screen.findByText(`Schema: ${NAMED}`)).toBeInTheDocument();
        expect(
            await screen.findByText("The binding was applied to 1 asset: 1 pending evaluation.")
        ).toBeInTheDocument();
        expect(put).toHaveBeenCalledTimes(1);
        expect(put).toHaveBeenCalledWith("compliance/bind/db1", { body: { schemaName: NAMED } });
        expect(screen.queryByText(/binding failed/)).not.toBeInTheDocument();
        const alerts = createWrapper().findAllAlerts();
        expect(alerts.some((alert) => alert.getElement().textContent?.includes("failed"))).toBe(
            false
        );
    });

    it("still reports a change refused while another is applied (409) as a refusal", async () => {
        const busy =
            "The compliance schema binding of this database is still being applied; try again when it finishes";
        put.mockRejectedValue(new ApiError(busy, 409));
        await bindAndConfirm();

        expect(
            await screen.findByText(`The compliance schema binding failed: ${busy}`)
        ).toBeInTheDocument();
        expect(screen.getByText("No compliance schema bound")).toBeInTheDocument();
    });
});

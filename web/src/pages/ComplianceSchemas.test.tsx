/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Synonyms from "../synonyms";
import ComplianceSchemas, { LEGACY_SCHEMA_LABEL, sweepMessage } from "./ComplianceSchemas";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

// The page's write actions are gated on the caller's routes; these tests run as a caller who may
// call every route (ComplianceWriteActionGating.test.tsx covers the gating).
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchComplianceSchemas: jest.fn(),
    // The Schemas page reads the unfiltered listing through fetchComplianceSchemaListing; it
    // answers with what the test's fetchComplianceSchemas mock returns.
    fetchComplianceSchemaListing: jest.fn(async () => {
        const [ok, result] = await jest
            .requireMock("../services/ComplianceService")
            .fetchComplianceSchemas();
        return [ok, ok ? { schemas: result } : result];
    }),
    createComplianceSchema: jest.fn(),
    updateComplianceSchema: jest.fn(),
    sweepSchema: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const rulesSchema = {
    schemaName: "cad-quality",
    description: "CAD deliverable checks",
    schemaFormat: "vams-rules-v1",
    schemaBody: { schemaFormat: "vams-rules-v1", rules: {} },
    version: 2,
};

const legacySchema = {
    schemaName: "old-json-schema",
    description: "A JSON Schema body",
    schemaFormat: "legacy",
    schemaBody: { type: "object", properties: {} },
    version: 1,
};

const rowOf = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

describe("ComplianceSchemas format column", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("shows each schema's format and marks legacy rows", async () => {
        service().fetchComplianceSchemas.mockResolvedValue([true, [rulesSchema, legacySchema]]);

        render(<ComplianceSchemas />);
        await screen.findByText("old-json-schema");

        expect(screen.getByRole("columnheader", { name: "Format" })).toBeInTheDocument();
        expect(within(rowOf("cad-quality")).getByText("vams-rules-v1")).toBeInTheDocument();
        expect(within(rowOf("cad-quality")).queryByText(LEGACY_SCHEMA_LABEL)).toBeNull();
        expect(within(rowOf("old-json-schema")).getByText(LEGACY_SCHEMA_LABEL)).toBeInTheDocument();
    });

    it("derives the format from the body when the record carries none", async () => {
        service().fetchComplianceSchemas.mockResolvedValue([
            true,
            [
                { ...rulesSchema, schemaFormat: undefined },
                { ...legacySchema, schemaFormat: undefined },
            ],
        ]);

        render(<ComplianceSchemas />);
        await screen.findByText("old-json-schema");

        expect(within(rowOf("cad-quality")).getByText("vams-rules-v1")).toBeInTheDocument();
        expect(within(rowOf("old-json-schema")).getByText(LEGACY_SCHEMA_LABEL)).toBeInTheDocument();
    });

    it("trusts the record's format over the body", async () => {
        // A record the listing marks legacy is legacy even when its body carries the format field.
        service().fetchComplianceSchemas.mockResolvedValue([
            true,
            [{ ...legacySchema, schemaBody: { schemaFormat: "vams-rules-v1", rules: {} } }],
        ]);

        render(<ComplianceSchemas />);
        await screen.findByText("old-json-schema");

        expect(within(rowOf("old-json-schema")).getByText(LEGACY_SCHEMA_LABEL)).toBeInTheDocument();
    });
});

describe("ComplianceSchemas row actions", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("disables Edit and Sweep on a legacy row and keeps them on a rules row", async () => {
        service().fetchComplianceSchemas.mockResolvedValue([true, [rulesSchema, legacySchema]]);

        render(<ComplianceSchemas />);
        await screen.findByText("old-json-schema");

        const legacyRow = rowOf("old-json-schema");
        expect(within(legacyRow).getByRole("button", { name: "Edit" })).toBeDisabled();
        expect(within(legacyRow).getByRole("button", { name: "Sweep" })).toBeDisabled();

        const rulesRow = rowOf("cad-quality");
        expect(within(rulesRow).getByRole("button", { name: "Edit" })).toBeEnabled();
        expect(within(rulesRow).getByRole("button", { name: "Sweep" })).toBeEnabled();

        await userEvent.click(within(legacyRow).getByRole("button", { name: "Sweep" }));
        expect(service().sweepSchema).not.toHaveBeenCalled();
    });

    it("dismissing a flash message removes only that message", async () => {
        service().fetchComplianceSchemas.mockResolvedValue([true, [rulesSchema]]);
        service()
            .sweepSchema.mockResolvedValueOnce([true, { message: "started" }])
            .mockResolvedValueOnce([false, "Sweep failed for that schema"]);

        render(<ComplianceSchemas />);
        await screen.findByText("cad-quality");

        // Two flashes raised back to back: one success, one error.
        const sweep = () =>
            userEvent.click(within(rowOf("cad-quality")).getByRole("button", { name: "Sweep" }));
        await sweep();
        const successText = `Sweep of schema "cad-quality": 0 ${Synonyms.assets} evaluated.`;
        expect(await screen.findByText(successText)).toBeInTheDocument();
        await sweep();
        expect(await screen.findByText("Sweep failed for that schema")).toBeInTheDocument();

        const successFlash = screen.getByText(successText).closest("li") as HTMLElement;
        await userEvent.click(
            within(successFlash).getByRole("button", { name: "Dismiss message" })
        );

        await waitFor(() => {
            expect(screen.queryByText(successText)).not.toBeInTheDocument();
        });
        expect(screen.getByText("Sweep failed for that schema")).toBeInTheDocument();
    });

    it("continues a sweep that left assets with the token it returned", async () => {
        service().fetchComplianceSchemas.mockResolvedValue([true, [rulesSchema]]);
        service()
            .sweepSchema.mockResolvedValueOnce([
                true,
                { assetsTriggered: [], assetsRemaining: 313, NextToken: "tok-2" },
            ])
            .mockResolvedValueOnce([true, { assetsTriggered: [], assetsRemaining: 0 }])
            .mockResolvedValueOnce([true, { assetsTriggered: [], assetsRemaining: 0 }]);

        render(<ComplianceSchemas />);
        await screen.findByText("cad-quality");
        const sweep = () =>
            userEvent.click(within(rowOf("cad-quality")).getByRole("button", { name: "Sweep" }));
        await sweep();
        expect(await screen.findByText(/313 not reached yet/)).toBeInTheDocument();
        await sweep();
        await sweep();
        await waitFor(() => expect(service().sweepSchema).toHaveBeenCalledTimes(3));
        // The second sweep continues the first; the finished sweep leaves no token for the third.
        expect(service().sweepSchema.mock.calls).toEqual([
            ["cad-quality", undefined],
            ["cad-quality", "tok-2"],
            ["cad-quality", undefined],
        ]);
    });
});

describe("ComplianceSchemas save errors", () => {
    const scrollIntoView = jest.fn();
    const originalScrollIntoView = Element.prototype.scrollIntoView;

    beforeEach(() => {
        jest.clearAllMocks();
        // jsdom does not implement scrolling.
        Element.prototype.scrollIntoView = scrollIntoView;
    });

    afterEach(() => {
        Element.prototype.scrollIntoView = originalScrollIntoView;
    });

    it("brings the API's refusal into view and announces it", async () => {
        const message =
            "Invalid schema: rules[0]: checks.0: requiredPaths is invalid. Too many open-ended quantifiers (*, +, ?, {n,}) to evaluate safely; simplify the expression.";
        service().fetchComplianceSchemas.mockResolvedValue([true, []]);
        service().createComplianceSchema.mockResolvedValue([false, message]);

        render(<ComplianceSchemas />);
        await userEvent.click(await screen.findByRole("button", { name: "Create Schema" }));
        const dialog = await screen.findByRole("dialog");
        await userEvent.type(
            within(dialog).getByPlaceholderText(/engineering-asset-standard/),
            "s1"
        );
        expect(scrollIntoView).not.toHaveBeenCalled();
        await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));

        const alert = await within(dialog).findByRole("alert");
        expect(alert).toHaveTextContent(message);
        expect(service().createComplianceSchema).toHaveBeenCalledTimes(1);
        expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
        expect(scrollIntoView.mock.instances[scrollIntoView.mock.instances.length - 1]).toBe(alert);
    });
});

describe("ComplianceSchemas sweep message", () => {
    it("reports the evaluated, skipped and remaining counts", () => {
        expect(
            sweepMessage("std", {
                assetsTriggered: [{}, {}, {}],
                skipped: 2,
                assetsRemaining: 40,
            })
        ).toBe(
            `Sweep of schema "std": 3 ${Synonyms.assets} evaluated; 2 skipped (you may not evaluate or read them); 40 not reached yet; run the sweep again.`
        );
        expect(sweepMessage("std", { assetsTriggered: [{}] })).toBe(
            `Sweep of schema "std": 1 ${Synonyms.asset} evaluated.`
        );
    });

    it("reports the archived assets apart from those the caller may not evaluate", () => {
        // `skipped` totals both reasons; each is named on its own.
        expect(
            sweepMessage("std", {
                assetsTriggered: [{}],
                skipped: 3,
                skippedNotAuthorized: 1,
                skippedArchived: 2,
            })
        ).toBe(
            `Sweep of schema "std": 1 ${Synonyms.asset} evaluated; 1 skipped (you may not evaluate or read them); 2 archived, not evaluated.`
        );
        expect(
            sweepMessage("std", {
                assetsTriggered: [{}],
                skipped: 2,
                skippedNotAuthorized: 0,
                skippedArchived: 2,
            })
        ).toBe(`Sweep of schema "std": 1 ${Synonyms.asset} evaluated; 2 archived, not evaluated.`);
    });

    it("says nothing of archived assets when none is counted", () => {
        // A caller who may not see the database's archive gets 0 for it, as when there is none.
        expect(
            sweepMessage("std", {
                assetsTriggered: [{}, {}],
                skipped: 0,
                skippedNotAuthorized: 0,
                skippedArchived: 0,
                assetsRemaining: 0,
            })
        ).toBe(`Sweep of schema "std": 2 ${Synonyms.assets} evaluated.`);
    });
});

describe("ComplianceSchemas rule-name errors", () => {
    beforeEach(() => jest.clearAllMocks());

    // Text is entered with one change event per field, not typed key by key, and each step waits
    // for the element it produces: under a full-suite run, key-by-key typing into the Visual
    // Builder's re-rendering form took longer than the default test timeout. The rule-name input is
    // looked up again after every render instead of reusing a node that may have been replaced.
    // The 20 s timeout is a ceiling for a loaded runner, not a delay.
    const ruleNameInputs = (dialog: HTMLElement) =>
        within(dialog).findAllByPlaceholderText("rule-name") as Promise<HTMLInputElement[]>;

    it("does not send Create while the builder shows a rule-name error", async () => {
        service().fetchComplianceSchemas.mockResolvedValue([true, []]);
        service().createComplianceSchema.mockResolvedValue([true, { schemaName: "s1" }]);
        render(<ComplianceSchemas />);
        await userEvent.click(await screen.findByRole("button", { name: "Create Schema" }));
        const dialog = await screen.findByRole("dialog");
        fireEvent.change(within(dialog).getByPlaceholderText(/engineering-asset-standard/), {
            target: { value: "s1" },
        });
        await userEvent.click(within(dialog).getByRole("tab", { name: "Visual Builder" }));

        fireEvent.change((await ruleNameInputs(dialog))[0], { target: { value: "" } });
        expect(await within(dialog).findByText("Rule name is required")).toBeInTheDocument();

        await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
        expect(
            await within(dialog).findByText(
                "Fix the rule names before saving: Rule name is required."
            )
        ).toBeInTheDocument();
        expect(service().createComplianceSchema).not.toHaveBeenCalled();

        fireEvent.change((await ruleNameInputs(dialog))[0], {
            target: { value: "renamed-rule" },
        });
        await waitFor(() =>
            expect(within(dialog).queryByText("Rule name is required")).not.toBeInTheDocument()
        );
        await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
        await waitFor(() => expect(service().createComplianceSchema).toHaveBeenCalledTimes(1));
        expect(
            Object.keys(service().createComplianceSchema.mock.calls[0][0].schemaBody.rules)
        ).toContain("renamed-rule");
    }, 20_000);
});

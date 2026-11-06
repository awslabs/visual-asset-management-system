/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A schema whose pipeline rules share a workflow saves; the API's answer warns that they run one
 * after another when the workflow restricts concurrency (D113), and the Schemas page shows each
 * warning as a warning notification beside the success message once the editor closes.
 */

import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceSchemas from "./ComplianceSchemas";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchComplianceSchemaListing: jest.fn(async () => [true, { schemas: [] }]),
    fetchComplianceSchemas: jest.fn(async () => [true, []]),
    createComplianceSchema: jest.fn(),
    updateComplianceSchema: jest.fn(),
    sweepSchema: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const WARNING =
    "Pipeline rules residual-bound, residual-tight run the same workflow. When the workflow restricts concurrency (perAsset, perInputFile or perInputFileVersion), they run one after another on an asset, so its evaluation takes as long as their runs together.";

const createWith = async (answer: any) => {
    service().createComplianceSchema.mockResolvedValue([true, answer]);
    render(<ComplianceSchemas />);
    await userEvent.click(await screen.findByRole("button", { name: "Create Schema" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByPlaceholderText(/engineering-asset-standard/), {
        target: { value: "s1" },
    });
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(service().createComplianceSchema).toHaveBeenCalledTimes(1));
};

describe("the Schemas page after a save that returned warnings", () => {
    beforeEach(() => jest.clearAllMocks());

    it("shows each warning beside the success message", async () => {
        await createWith({
            message: "Schema 's1' registered as v1",
            schemaName: "s1",
            warnings: [
                {
                    code: "pipelineRulesShareWorkflow",
                    message: WARNING,
                    workflowDatabaseId: "GLOBAL",
                    workflowId: "wf-1",
                    ruleNames: ["residual-bound", "residual-tight"],
                },
            ],
        });
        expect(await screen.findByText('Schema "s1" created')).toBeInTheDocument();
        expect(await screen.findByText(WARNING)).toBeInTheDocument();
    }, 20_000);

    it("shows the missing-parent warning the server returned, word for word", async () => {
        // D150: decided by the API from what the caller may read; the page only shows it.
        const missingParent =
            "The parent schema 'base-standard' was not found as a vams-rules-v1 schema you may read. If it does not exist or is not vams-rules-v1, this schema is evaluated with its own rules only.";
        await createWith({
            message: "Schema 's1' registered as v1",
            schemaName: "s1",
            warnings: [
                { code: "extendsSchemaNotFound", message: missingParent, extends: "base-standard" },
            ],
        });
        expect(await screen.findByText('Schema "s1" created')).toBeInTheDocument();
        expect(await screen.findByText(missingParent)).toBeInTheDocument();
    }, 20_000);

    it("shows the success message alone when the answer has no warning", async () => {
        await createWith({ message: "ok", schemaName: "s1", warnings: [] });
        expect(await screen.findByText('Schema "s1" created')).toBeInTheDocument();
        expect(screen.queryByText(/run the same workflow/)).not.toBeInTheDocument();
    }, 20_000);
});

describe("the Schemas page after a save refused for a pipeline rule", () => {
    beforeEach(() => jest.clearAllMocks());

    it("keeps the editor open and names the rule whose workflow the caller may not run", async () => {
        const refusal = "Not authorized to run the workflow of pipeline rule 'residual-bound'";
        service().createComplianceSchema.mockResolvedValue([false, refusal]);
        render(<ComplianceSchemas />);
        await userEvent.click(await screen.findByRole("button", { name: "Create Schema" }));
        const dialog = await screen.findByRole("dialog");
        fireEvent.change(within(dialog).getByPlaceholderText(/engineering-asset-standard/), {
            target: { value: "s1" },
        });
        await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
        expect(await within(dialog).findByText(refusal)).toBeInTheDocument();
        expect(screen.queryByText('Schema "s1" created')).not.toBeInTheDocument();
    }, 20_000);
});

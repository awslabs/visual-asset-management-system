/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A compliance schema's own scope is visible and chosen on purpose: the create form asks where the
 * schema is available (all databases, or one), the table shows it as a column with a filter, and the
 * pickers that bind a schema list only what the database may bind (GLOBAL and its own).
 */

import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import ComplianceSchemas from "./ComplianceSchemas";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../services/APIService", () => ({
    ...jest.requireActual("../services/APIService"),
    fetchAllDatabases: jest.fn(),
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
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");
const api = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/APIService");

const body = { schemaFormat: "vams-rules-v1", rules: {} };
const schemas = [
    {
        schemaName: "everywhere",
        databaseId: "GLOBAL",
        schemaBody: body,
        schemaFormat: "vams-rules-v1",
    },
    {
        schemaName: "only-db-a",
        databaseId: "db-a",
        schemaBody: body,
        schemaFormat: "vams-rules-v1",
    },
];

const rowOf = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

beforeEach(() => {
    jest.clearAllMocks();
    service().fetchComplianceSchemas.mockResolvedValue([true, schemas]);
    service().createComplianceSchema.mockResolvedValue([true, { schemaName: "s1" }]);
    api().fetchAllDatabases.mockResolvedValue([{ databaseId: "db-a" }, { databaseId: "db-b" }]);
});

it("shows where each schema is available", async () => {
    render(<ComplianceSchemas />);
    await screen.findByText("only-db-a");
    expect(screen.getByRole("columnheader", { name: /Available to/ })).toBeInTheDocument();
    expect(within(rowOf("everywhere")).getByText(/GLOBAL/)).toBeInTheDocument();
    expect(within(rowOf("only-db-a")).getByText("db-a")).toBeInTheDocument();
});

it("filters the table by availability", async () => {
    render(<ComplianceSchemas />);
    await screen.findByText("only-db-a");
    const filter = createWrapper().findSelect()!;
    act(() => filter.openDropdown());
    act(() => filter.selectOptionByValue("db-a"));
    await waitFor(() => expect(screen.queryByText("everywhere")).not.toBeInTheDocument());
    expect(screen.getByText("only-db-a")).toBeInTheDocument();
});

it("asks where a new schema is available and sends the choice", async () => {
    render(<ComplianceSchemas />);
    await userEvent.click(await screen.findByRole("button", { name: "Create Schema" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("All databases (GLOBAL)")).toBeChecked();
    await userEvent.type(within(dialog).getByPlaceholderText(/engineering-asset-standard/), "s1");
    await userEvent.click(within(dialog).getByLabelText(/One/));

    // Without a database chosen the schema is not sent.
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    expect(
        await within(dialog).findByText(/Choose the .* the schema is available to/)
    ).toBeInTheDocument();
    expect(service().createComplianceSchema).not.toHaveBeenCalled();

    // The database picker (the template picker is the first select of the dialog).
    const select = createWrapper(dialog)
        .findAllSelects()
        .find((candidate) => /Choose a/.test(candidate.getElement().textContent || ""))!;
    act(() => select.openDropdown());
    act(() => select.selectOptionByValue("db-b"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(service().createComplianceSchema).toHaveBeenCalledTimes(1));
    expect(service().createComplianceSchema.mock.calls[0][0].databaseId).toBe("db-b");
});

it("creates a GLOBAL schema by default", async () => {
    render(<ComplianceSchemas />);
    await userEvent.click(await screen.findByRole("button", { name: "Create Schema" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByPlaceholderText(/engineering-asset-standard/), "s1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(service().createComplianceSchema).toHaveBeenCalledTimes(1));
    expect(service().createComplianceSchema.mock.calls[0][0].databaseId).toBe("GLOBAL");
});

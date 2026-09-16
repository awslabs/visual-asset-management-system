/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import ComplianceSchemas, { LEGACY_SCHEMA_LABEL } from "./ComplianceSchemas";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchComplianceSchemas: jest.fn(),
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

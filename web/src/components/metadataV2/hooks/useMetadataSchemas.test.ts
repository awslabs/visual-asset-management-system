/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { renderHook, waitFor } from "@testing-library/react";
import { useMetadataSchemas, isSchemaEnabled } from "./useMetadataSchemas";

jest.mock("../../../services/MetadataSchemaService", () => ({
    fetchMetadataSchemas: jest.fn(),
}));

const { fetchMetadataSchemas } = jest.requireMock("../../../services/MetadataSchemaService");

const schema = (name: string, enabled: unknown, fieldKey: string, required = false) => ({
    metadataSchemaId: name,
    schemaName: name,
    enabled,
    fields: {
        fields: [
            {
                metadataFieldKeyName: fieldKey,
                metadataFieldValueType: "string",
                required,
            },
        ],
    },
});

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("useMetadataSchemas", () => {
    it("leaves a disabled schema's fields out of the aggregated result", async () => {
        fetchMetadataSchemas.mockImplementation(async (dbId: string) =>
            dbId === "db1"
                ? {
                      Items: [
                          schema("active", true, "title"),
                          schema("retired", false, "legacyCode", true),
                      ],
                  }
                : { Items: [] }
        );

        const { result } = renderHook(() => useMetadataSchemas("db1", "asset"));

        await waitFor(() => expect(result.current.schemas).not.toBeNull());
        const keys = result.current.schemas!.fields.map((f) => f.metadataFieldKeyName);
        expect(keys).toEqual(["title"]);
        expect(result.current.schemas!.schemaNames).toEqual(["active"]);
    });

    it("reads enabled the way the backend validation does", () => {
        expect(isSchemaEnabled(undefined)).toBe(true);
        expect(isSchemaEnabled(true)).toBe(true);
        expect(isSchemaEnabled(false)).toBe(false);
        expect(isSchemaEnabled("TRUE")).toBe(true);
        expect(isSchemaEnabled("false")).toBe(false);
        expect(isSchemaEnabled("yes")).toBe(false);
        expect(isSchemaEnabled(null)).toBe(false);
        expect(isSchemaEnabled(0)).toBe(false);
        expect(isSchemaEnabled(1)).toBe(true);
    });
});

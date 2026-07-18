/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DataTable from "./DataTable";

// Mock Monaco in case ConfigEditor is imported elsewhere in the test suite
jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

describe("DataTable", () => {
    it("paginates and displays only pageSize rows initially", async () => {
        const rows = Array.from({ length: 30 }, (_, i) => ({ id: i + 1, name: `Row ${i + 1}` }));
        const columns = [
            { header: "ID", accessorKey: "id" },
            { header: "Name", accessorKey: "name" },
        ];

        render(<DataTable columns={columns} rows={rows} pageSize={10} />);

        // Should see rows 1-10 initially
        expect(screen.getByText("Row 1")).toBeInTheDocument();
        expect(screen.getByText("Row 10")).toBeInTheDocument();
        expect(screen.queryByText("Row 11")).not.toBeInTheDocument();

        // Should have a next page control
        const nextButton = screen.getByRole("button", { name: /next/i });
        expect(nextButton).toBeInTheDocument();

        // Click next -> rows 11-20
        await userEvent.click(nextButton);
        expect(screen.getByText("Row 11")).toBeInTheDocument();
        expect(screen.getByText("Row 20")).toBeInTheDocument();
        expect(screen.queryByText("Row 10")).not.toBeInTheDocument();
    });
});

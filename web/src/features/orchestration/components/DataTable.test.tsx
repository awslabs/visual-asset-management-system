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

        render(<DataTable columns={columns} rows={rows} ariaLabel="Rows" pageSize={10} />);

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

    it("fires onRowClick with the clicked row's data", async () => {
        const rows = [
            { id: 1, name: "Alpha" },
            { id: 2, name: "Beta" },
        ];
        const columns = [
            { header: "ID", accessorKey: "id" },
            { header: "Name", accessorKey: "name" },
        ];
        const onRowClick = jest.fn();

        render(
            <DataTable columns={columns} rows={rows} ariaLabel="Rows" onRowClick={onRowClick} />
        );

        await userEvent.click(screen.getByText("Beta"));
        expect(onRowClick).toHaveBeenCalledTimes(1);
        expect(onRowClick).toHaveBeenCalledWith({ id: 2, name: "Beta" });
    });

    it("sorts from a focusable header button and reports the direction via aria-sort", async () => {
        const rows = [
            { id: 2, name: "Beta" },
            { id: 1, name: "Alpha" },
        ];
        const columns = [{ header: "Name", accessorKey: "name" }];

        render(<DataTable columns={columns} rows={rows} ariaLabel="Rows" filtering={false} />);

        const header = screen.getByRole("columnheader", { name: /Name/ });
        expect(header).toHaveAttribute("aria-sort", "none");

        await userEvent.click(screen.getByRole("button", { name: /Name/ }));
        expect(header).toHaveAttribute("aria-sort", "ascending");
        await userEvent.click(screen.getByRole("button", { name: /Name/ }));
        expect(header).toHaveAttribute("aria-sort", "descending");
    });

    it("activates a clickable row from the keyboard", async () => {
        const rows = [{ id: 1, name: "Alpha" }];
        const columns = [{ header: "Name", accessorKey: "name" }];
        const onRowClick = jest.fn();

        render(
            <DataTable
                columns={columns}
                rows={rows}
                ariaLabel="Rows"
                onRowClick={onRowClick}
                filtering={false}
                sorting={false}
            />
        );

        const row = screen.getByRole("row", { name: /Alpha/ });
        row.focus();
        await userEvent.keyboard("{Enter}");
        expect(onRowClick).toHaveBeenCalledWith({ id: 1, name: "Alpha" });
    });

    // A cell owning local state (like the row kebab menu's open flag): it must follow its row
    // across a reorder rather than staying with the position.
    const MarkCell: React.FC<{ name: string }> = ({ name }) => {
        const [marked, setMarked] = React.useState(false);
        return (
            <button onClick={() => setMarked(true)}>
                {marked ? `marked ${name}` : `mark ${name}`}
            </button>
        );
    };

    it("keeps per-row state bound to the same row when getRowId is supplied and rows reorder", async () => {
        const columns = [
            { header: "Name", accessorKey: "name" },
            {
                header: "State",
                id: "state",
                cell: ({ row }: any) => <MarkCell name={row.original.name} />,
            },
        ];

        const first = [
            { id: 1, name: "Alpha" },
            { id: 2, name: "Beta" },
        ];
        const getRowId = (row: any) => String(row.id);

        const { rerender } = render(
            <DataTable
                columns={columns}
                rows={first}
                ariaLabel="Rows"
                getRowId={getRowId}
                filtering={false}
            />
        );

        await userEvent.click(screen.getByRole("button", { name: "mark Alpha" }));
        expect(screen.getByRole("button", { name: "marked Alpha" })).toBeInTheDocument();

        // Reorder: Alpha moves to the second position.
        rerender(
            <DataTable
                columns={columns}
                rows={[first[1], first[0]]}
                ariaLabel="Rows"
                getRowId={getRowId}
                filtering={false}
            />
        );

        expect(screen.getByRole("button", { name: "marked Alpha" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "mark Beta" })).toBeInTheDocument();
    });

    it("names the table, so several on one page can be told apart", () => {
        render(
            <DataTable
                columns={[{ header: "Name", accessorKey: "name" }]}
                rows={[{ name: "Alpha" }]}
                ariaLabel="Output files"
                filtering={false}
            />
        );

        expect(screen.getByRole("table", { name: "Output files" })).toBeInTheDocument();
        // Control: the name is the supplied one, not any table at all.
        expect(screen.queryByRole("table", { name: "Input files" })).not.toBeInTheDocument();
    });

    it("offers a sort control only for a column that can actually sort", () => {
        render(
            <DataTable
                columns={[
                    { header: "Name", accessorKey: "name" },
                    // No accessor — a display column, which react-table cannot sort even though it
                    // still hands back a toggle handler for it.
                    { header: "Duration", id: "duration", cell: () => <span>1m</span> },
                ]}
                rows={[{ name: "Alpha" }]}
                ariaLabel="Rows"
                filtering={false}
            />
        );

        // Control: the sortable column DOES get a button, so the negative below is not vacuous.
        expect(screen.getByRole("button", { name: /Name/ })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Duration/ })).not.toBeInTheDocument();
        expect(screen.getByRole("columnheader", { name: /Duration/ })).not.toHaveAttribute(
            "aria-sort"
        );
    });

    it("qualifies an applied sort when the caller says the rows are only part of the set", async () => {
        render(
            <DataTable
                columns={[{ header: "Name", accessorKey: "name" }]}
                rows={[{ name: "Beta" }, { name: "Alpha" }]}
                ariaLabel="Rows"
                filtering={false}
                sortScopeNote="Sorted within the 2 rows loaded so far."
            />
        );

        // Control: with no sort applied there is nothing to qualify, so the note must be absent.
        expect(screen.queryByText(/Sorted within/)).not.toBeInTheDocument();

        await userEvent.click(screen.getByRole("button", { name: /Name/ }));
        expect(screen.getByText("Sorted within the 2 rows loaded so far.")).toBeInTheDocument();
    });

    it("describes what activating a clickable row does", () => {
        render(
            <DataTable
                columns={[{ header: "Name", accessorKey: "name" }]}
                rows={[{ name: "Alpha" }]}
                ariaLabel="Rows"
                onRowClick={jest.fn()}
                filtering={false}
                sorting={false}
            />
        );

        const row = screen.getByRole("row", { name: /Alpha/ });
        const describedBy = row.getAttribute("aria-describedby");
        expect(describedBy).toBeTruthy();
        expect(document.getElementById(describedBy as string)).toHaveTextContent(
            /Press Enter on a row/
        );
    });

    it("does not describe rows as activatable when they are not", () => {
        render(
            <DataTable
                columns={[{ header: "Name", accessorKey: "name" }]}
                rows={[{ name: "Alpha" }]}
                ariaLabel="Rows"
                filtering={false}
                sorting={false}
            />
        );

        expect(screen.getByRole("row", { name: /Alpha/ })).not.toHaveAttribute("aria-describedby");
        expect(screen.queryByText(/Press Enter on a row/)).not.toBeInTheDocument();
    });
});

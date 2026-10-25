/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The database editor's save and compliance schema binding are separate requests with separate
 * permissions: a role may bind a schema without being allowed to update the database. The editor
 * sends the save only when a database field changed and the binding only when the schema selection
 * changed, sends no binding after a refused save, reports a refused binding and stays open, and
 * closes only when every request it sent succeeded.
 */
import React from "react";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import CreateDatabase from "./CreateDatabase";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    createDatabase: jest.fn(),
    updateDatabase: jest.fn(),
    fetchBuckets: jest.fn(),
}));
jest.mock("../../services/ComplianceService", () => ({
    fetchComplianceSchemas: jest.fn(),
    getDatabaseBindings: jest.fn(),
    bindSchemaToDatabase: jest.fn(),
    unbindSchemaFromDatabase: jest.fn(),
}));
// An administrator's routes: the bucket list, the schema list and the database binding.
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const api = jest.requireMock("../../services/APIService");
const compliance = jest.requireMock("../../services/ComplianceService");

const database = {
    databaseId: "db-one",
    description: "first database",
    defaultBucketId: "b1",
    restrictMetadataOutsideSchemas: false,
    restrictFileUploadsToExtensions: "",
};

const formField = (label: string) => {
    const field = createWrapper()
        .findAllFormFields()
        .find((f) => f.findLabel()?.getElement().textContent === label);
    if (!field) throw new Error(`no ${label} field`);
    return field;
};

function present<T>(value: T | null | undefined, what: string): T {
    if (value === null || value === undefined) throw new Error(`no ${what}`);
    return value;
}

const descriptionTextarea = () =>
    present(
        formField(`${Synonyms.Database} Description`).findControl()?.findTextarea(),
        "description textarea"
    );

const editDescription = (value: string) => {
    const textarea = descriptionTextarea();
    act(() => {
        textarea.setTextareaValue(value);
    });
};

const descriptionValue = () =>
    (descriptionTextarea().findNativeTextarea().getElement() as HTMLTextAreaElement).value;

// Options of the schema select: 1 is "None", then the schemas in list order.
const chooseSchema = (index: number) => {
    const select = present(
        formField("Compliance Schema").findControl()?.findSelect(),
        "schema select"
    );
    act(() => {
        select.openDropdown();
    });
    act(() => {
        select.selectOption(index);
    });
};

const submit = (label: string) =>
    fireEvent.click(screen.getByRole("button", { name: `${label} ${Synonyms.Database}` }));

// Opens the editor on `database` with `databaseSchema` bound, once its reads have settled.
const openEditor = async (databaseSchema: string | null = null) => {
    compliance.getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db-one", databaseSchema },
    ]);
    const setOpen = jest.fn();
    const setReload = jest.fn();
    const view = render(
        <CreateDatabase open={true} setOpen={setOpen} setReload={setReload} initState={database} />
    );
    await waitFor(() => expect(compliance.getDatabaseBindings).toHaveBeenCalled());
    await waitFor(() => expect(compliance.fetchComplianceSchemas).toHaveBeenCalled());
    await waitFor(() => expect(api.fetchBuckets).toHaveBeenCalled());
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
    });
    return { setOpen, setReload, view };
};

const closedWith = (setOpen: jest.Mock) => setOpen.mock.calls.some(([open]) => open === false);

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    api.fetchBuckets.mockResolvedValue({
        Items: [{ bucketId: "b1", bucketName: "bucket-one", baseAssetsPrefix: "/" }],
    });
    compliance.fetchComplianceSchemas.mockResolvedValue([
        true,
        [
            { schemaName: "std", schemaBody: {} },
            { schemaName: "strict", schemaBody: {} },
        ],
    ]);
});

afterEach(() => jest.restoreAllMocks());

describe(`${Synonyms.Database} editor save and compliance schema binding`, () => {
    it("sends only the binding for a schema-only change", async () => {
        compliance.bindSchemaToDatabase.mockResolvedValue([true, "Schema bound"]);
        const { setOpen } = await openEditor();
        chooseSchema(2);
        submit("Update");
        await waitFor(() => expect(closedWith(setOpen)).toBe(true));
        expect(compliance.bindSchemaToDatabase).toHaveBeenCalledWith("db-one", "std");
        expect(api.updateDatabase).not.toHaveBeenCalled();
    });

    it("sends only the save for a field-only change", async () => {
        api.updateDatabase.mockResolvedValue([true, "Database updated"]);
        const { setOpen } = await openEditor("std");
        editDescription("revised database");
        submit("Update");
        await waitFor(() => expect(closedWith(setOpen)).toBe(true));
        expect(api.updateDatabase).toHaveBeenCalledWith({
            ...database,
            description: "revised database",
        });
        expect(compliance.bindSchemaToDatabase).not.toHaveBeenCalled();
        expect(compliance.unbindSchemaFromDatabase).not.toHaveBeenCalled();
    });

    it("sends no binding after a refused save and shows the save's error", async () => {
        api.updateDatabase.mockResolvedValue([false, "Not Authorized"]);
        const { setOpen } = await openEditor();
        editDescription("revised database");
        chooseSchema(2);
        submit("Update");
        expect(await screen.findByText("Not Authorized")).toBeInTheDocument();
        expect(compliance.bindSchemaToDatabase).not.toHaveBeenCalled();
        expect(closedWith(setOpen)).toBe(false);
    });

    it("reports a refused binding after a save and stays open", async () => {
        api.updateDatabase.mockResolvedValue([true, "Database updated"]);
        compliance.bindSchemaToDatabase.mockResolvedValue([false, "Not Authorized"]);
        const { setOpen, setReload } = await openEditor();
        editDescription("revised database");
        chooseSchema(2);
        submit("Update");
        expect(
            await screen.findByText(
                `${Synonyms.Database} saved, but the compliance schema binding failed: Not Authorized`
            )
        ).toBeInTheDocument();
        expect(closedWith(setOpen)).toBe(false);
        expect(setReload).toHaveBeenCalledWith(true);

        // A retry resends only the binding: the save already landed.
        compliance.bindSchemaToDatabase.mockResolvedValue([true, "Schema bound"]);
        submit("Update");
        await waitFor(() => expect(closedWith(setOpen)).toBe(true));
        expect(api.updateDatabase).toHaveBeenCalledTimes(1);
        expect(compliance.bindSchemaToDatabase).toHaveBeenCalledTimes(2);
    });

    it("reports a refused schema-only binding without claiming a save", async () => {
        compliance.bindSchemaToDatabase.mockResolvedValue([false, "Not Authorized"]);
        const { setOpen } = await openEditor();
        chooseSchema(2);
        submit("Update");
        expect(
            await screen.findByText("The compliance schema binding failed: Not Authorized")
        ).toBeInTheDocument();
        expect(api.updateDatabase).not.toHaveBeenCalled();
        expect(closedWith(setOpen)).toBe(false);
    });

    it("sends the save then the binding and closes when both succeed", async () => {
        api.updateDatabase.mockResolvedValue([true, "Database updated"]);
        compliance.bindSchemaToDatabase.mockResolvedValue([true, "Schema bound"]);
        const { setOpen } = await openEditor("std");
        editDescription("revised database");
        chooseSchema(3);
        submit("Update");
        await waitFor(() => expect(closedWith(setOpen)).toBe(true));
        expect(compliance.bindSchemaToDatabase).toHaveBeenCalledWith("db-one", "strict");
        expect(api.updateDatabase.mock.invocationCallOrder[0]).toBeLessThan(
            compliance.bindSchemaToDatabase.mock.invocationCallOrder[0]
        );
    });

    it("removes the binding when None is chosen", async () => {
        compliance.unbindSchemaFromDatabase.mockResolvedValue([true, "Schema unbound"]);
        const { setOpen } = await openEditor("std");
        chooseSchema(1);
        submit("Update");
        await waitFor(() => expect(closedWith(setOpen)).toBe(true));
        expect(compliance.unbindSchemaFromDatabase).toHaveBeenCalledWith("db-one");
        expect(api.updateDatabase).not.toHaveBeenCalled();
    });

    it("closes without sending anything when nothing changed", async () => {
        const { setOpen } = await openEditor("std");
        submit("Update");
        await waitFor(() => expect(closedWith(setOpen)).toBe(true));
        expect(api.updateDatabase).not.toHaveBeenCalled();
        expect(compliance.bindSchemaToDatabase).not.toHaveBeenCalled();
        expect(compliance.unbindSchemaFromDatabase).not.toHaveBeenCalled();
    });

    it("reopens on the database's values after Cancel", async () => {
        const { view } = await openEditor();
        editDescription("an edit that is cancelled");
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        view.rerender(
            <CreateDatabase
                open={true}
                setOpen={jest.fn()}
                setReload={jest.fn()}
                initState={database}
            />
        );
        expect(descriptionValue()).toBe("first database");
        expect(screen.getByRole("button", { name: `Update ${Synonyms.Database}` })).toBeEnabled();
    });

    it("retries only the binding after a create whose binding was refused", async () => {
        api.createDatabase.mockResolvedValue([true, "Database created"]);
        compliance.bindSchemaToDatabase.mockResolvedValue([false, "Not Authorized"]);
        const setOpen = jest.fn();
        render(<CreateDatabase open={true} setOpen={setOpen} setReload={jest.fn()} />);
        await waitFor(() => expect(compliance.fetchComplianceSchemas).toHaveBeenCalled());
        await waitFor(() => expect(api.fetchBuckets).toHaveBeenCalled());
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });
        fireEvent.change(screen.getByPlaceholderText(`${Synonyms.Database} Name`), {
            target: { value: "db-new" },
        });
        editDescription("a new database");
        chooseSchema(2);
        submit("Create");
        expect(
            await screen.findByText(
                `${Synonyms.Database} saved, but the compliance schema binding failed: Not Authorized`
            )
        ).toBeInTheDocument();

        compliance.bindSchemaToDatabase.mockResolvedValue([true, "Schema bound"]);
        submit("Create");
        await waitFor(() => expect(closedWith(setOpen)).toBe(true));
        expect(api.createDatabase).toHaveBeenCalledTimes(1);
        expect(api.updateDatabase).not.toHaveBeenCalled();
        expect(compliance.bindSchemaToDatabase).toHaveBeenCalledTimes(2);
        expect(compliance.bindSchemaToDatabase).toHaveBeenLastCalledWith("db-new", "std");
    });
});

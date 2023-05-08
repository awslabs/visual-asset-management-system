/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render } from "@testing-library/react";
import { act } from "react-dom/test-utils";

import ListPageNoDatabase from "./ListPageNoDatabase";
import ListDefinition from "../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../components/list/list-definitions/types/ColumnDefinition";

import createWrapper from "@cloudscape-design/components/test-utils/dom";

const listDef = new ListDefinition({
    pluralName: "constraints",
    pluralNameTitleCase: "Constraints",
    visibleColumns: ["name", "description"],
    filterColumns: [{ name: "name", placeholder: "Name" }],
    elementId: "name",
    deleteRoute: "auth/constraints/{constraintId}",
    columnDefinitions: [
        new ColumnDefinition({
            id: "name",
            header: "Name",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "name",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "description",
        }),
    ],
});

describe("ListPageNoDatabase", () => {
    it("renders without crashing", async () => {
        const promise = Promise.resolve([
            {
                name: "test",
                description: "test",
            },
        ]);

        const fetchAllElements = jest.fn(() => promise);

        const { container } = render(
            <ListPageNoDatabase
                singularName={"thing"}
                singularNameTitleCase={"Thing"}
                pluralName={"things"}
                pluralNameTitleCase={"Things"}
                listDefinition={listDef}
                fetchAllElements={fetchAllElements}
                fetchElements={fetchAllElements}
                onCreateCallback={jest.fn()}
            />
        );

        await act(async () => {
            await promise;
        });

        const wrapper = createWrapper(container!);
        expect(container).toBeTruthy();
        expect(fetchAllElements).toHaveBeenCalledTimes(1);

        expect(wrapper.findTable()).toBeTruthy();
        expect(wrapper.findTable()?.findRows()).toHaveLength(1);

        expect(wrapper.findButton("[data-testid=create-new-element-button]")).toBeTruthy();
    });

    // test('renders the correct text', () => {
    //     const { getByText } = render(<ListPageNoDatabase />);
    //     expect(getByText('No database selected')).toBeInTheDocument();
    // });

    // test('renders the correct text when a database is selected', () => {
    //     const { getByText } = render(<ListPageNoDatabase databaseId="test" />);
    //     expect(getByText('No tables in database test')).toBeInTheDocument();
    // });

    // test('renders the correct text when a table is selected', () => {
    //     const { getByText } = render(<ListPageNoDatabase databaseId="test" tableId="test" />);
    //     expect(getByText('No columns in table test')).toBeInTheDocument();
    // });

    // test('renders the correct text when a column is selected', () => {
    //     const { getByText } = render(<ListPageNoDatabase databaseId="test" tableId="test" columnId="test" />);
    // }
});

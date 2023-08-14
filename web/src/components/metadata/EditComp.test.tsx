/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { render } from "@testing-library/react";
import { SchemaContextData } from "../../pages/MetadataSchema";
import { EditComp } from "./EditComp";
import { Metadata, TableRow } from "./ControlledMetadata";
import createWrapper from "@cloudscape-design/components/test-utils/dom";

describe("EditComp", () => {
    it("renders string fields", () => {
        const item: TableRow = {
            idx: 0,
            name: "field1",
            value: "value1",
            type: "string",
            dependsOn: [],
            inlineValues: [],
        };
        const schema: SchemaContextData = {
            databaseId: "dbId",
            schemas: [],
        };

        const metadata: Metadata = {};

        const view = render(
            <EditComp
                item={item}
                schema={schema}
                controlledLists={undefined}
                controlData={undefined}
                setValue={function (row: string | undefined): void {
                    throw new Error("Function not implemented.");
                }}
                currentValue={""}
                metadata={metadata}
                setValid={(v) => {}}
            />
        );

        //TODO: Make assertions
        //expect(view).toMatchSnapshot();
    });

    it("renders textarea fields", () => {
        const item: TableRow = {
            idx: 0,
            name: "field1",
            value: "value1",
            type: "textarea",
            dependsOn: [],
            inlineValues: [],
        };
        const schema: SchemaContextData = {
            databaseId: "dbId",
            schemas: [],
        };

        const metadata: Metadata = {};

        const view = render(
            <EditComp
                item={item}
                schema={schema}
                controlledLists={undefined}
                controlData={undefined}
                setValue={function (row: string | undefined): void {
                    throw new Error("Function not implemented.");
                }}
                currentValue={""}
                metadata={metadata}
                setValid={(v) => {}}
            />
        );

        //TODO: Make assertions
        //expect(view).toMatchSnapshot();
    });

    it("renders inline controlled lists", () => {
        const item: TableRow = {
            idx: 0,
            name: "field1",
            value: "value1",
            type: "inline-controlled-list",
            dependsOn: [],
            inlineValues: ["opt1", "opt2"],
        };
        const schema: SchemaContextData = {
            databaseId: "dbId",
            schemas: [],
        };

        const metadata: Metadata = {};

        const view = render(
            <EditComp
                item={item}
                schema={schema}
                controlledLists={undefined}
                controlData={undefined}
                setValue={function (row: string | undefined): void {
                    throw new Error("Function not implemented.");
                }}
                currentValue={""}
                metadata={metadata}
                setValid={(v) => {}}
            />
        );

        const wrapper = createWrapper(view.container);
        expect(wrapper.findSelect()).toBeTruthy();
        expect(wrapper.findSelect()?.findDropdown()).toBeTruthy();
    });

    it("renders controlled lists", () => {
        const item: TableRow = {
            idx: 0,
            name: "field1",
            value: "value1",
            type: "controlled-list",
            dependsOn: [],
            inlineValues: [],
        };
        const schema: SchemaContextData = {
            databaseId: "dbId",
            schemas: [],
        };

        const controlledLists = {
            field1: {
                data: ["v1", "v2"],
            },
        };

        const metadata: Metadata = {};

        render(
            <EditComp
                item={item}
                schema={schema}
                controlledLists={controlledLists}
                controlData={undefined}
                setValue={function (row: string | undefined): void {
                    throw new Error("Function not implemented.");
                }}
                currentValue={""}
                metadata={metadata}
                setValid={(v) => {}}
            />
        );

        const wrapper = createWrapper();
        expect(wrapper.findSelect()).toBeTruthy();
        expect(wrapper.findSelect()?.findDropdown()).toBeTruthy();
    });

    it("renders controlled lists with deps", () => {
        const item: TableRow = {
            idx: 0,
            name: "field1",
            value: "value1",
            type: "controlled-list",
            dependsOn: ["field2"],
            inlineValues: [],
        };
        const schema: SchemaContextData = {
            databaseId: "dbId",
            schemas: [],
        };

        const controlledLists = {
            field1: {
                data: ["v1", "v2"],
            },
        };

        const metadata: Metadata = {};

        render(
            <EditComp
                item={item}
                schema={schema}
                controlledLists={controlledLists}
                controlData={undefined}
                setValue={function (row: string | undefined): void {
                    throw new Error("Function not implemented.");
                }}
                currentValue={""}
                metadata={metadata}
                setValid={(v) => {}}
            />
        );

        const wrapper = createWrapper();
        expect(wrapper.findSelect()).toBeTruthy();
        expect(wrapper.findSelect()?.findDropdown()).toBeTruthy();
    });

    it("renders a date input", () => {
        const item: TableRow = {
            idx: 0,
            name: "field1",
            value: "2023/01/01",
            type: "date",
            dependsOn: [],
            inlineValues: [],
        };
        const schema: SchemaContextData = {
            databaseId: "dbId",
            schemas: [],
        };

        const metadata: Metadata = {};

        const view = render(
            <EditComp
                item={item}
                schema={schema}
                controlledLists={undefined}
                controlData={undefined}
                setValue={function (row: string | undefined): void {
                    throw new Error("Function not implemented.");
                }}
                currentValue={""}
                metadata={metadata}
                setValid={(v) => {}}
            />
        );

        const wrapper = createWrapper(view.container);
        expect(wrapper.findDatePicker()).toBeTruthy();
    });
});

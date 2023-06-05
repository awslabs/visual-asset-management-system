/* eslint-disable testing-library/no-unnecessary-act */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render, act } from "@testing-library/react";
import ControlledMetadata from "./ControlledMetadata";
import path from "path";
import fs from "fs";

describe("ControlledMetadata", () => {
    it("should render", () => {
        expect(ControlledMetadata).toBeDefined();
    });

    it("should render with default props", async () => {
        const schemas = Promise.resolve({
            schemas: [
                {
                    id: "122",
                    databaseId: "adatabaseid",
                    field: "country_name",
                    dataType: "controlled-list",
                    dependsOn: [],
                    required: false,
                    sequenceNumber: 0,
                },
                {
                    databaseId: "adatabaseid",
                    field: "state_name",
                    dataType: "controlled-list",
                    dependsOn: ["country_name"],
                    id: "123",
                    required: false,
                    sequenceNumber: 0,
                },
            ],
        });
        const get = jest.fn(() => {
            return schemas;
        });
        const citiesMetadataTestCSV = fs.readFileSync(
            path.join(__dirname, "cities.metadata.test.csv"),
            "utf8"
        );
        const controlledListData = Promise.resolve(citiesMetadataTestCSV);
        const storageget = jest.fn(() => {
            return controlledListData;
        });

        let view;

        await act(async () => {
            view = await render(
                <ControlledMetadata
                    databaseId="123"
                    assetId="456"
                    apiget={get}
                    storageget={storageget}
                />
            );
            await schemas;
            await controlledListData;
        });
        expect(storageget).toHaveBeenCalledTimes(1);
        expect(get).toHaveBeenCalledTimes(1);
        expect(view).toMatchSnapshot();
    });

});

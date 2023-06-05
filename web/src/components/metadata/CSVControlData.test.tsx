/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Papa, { ParseRemoteConfig, ParseResult, ParseWorkerConfig } from "papaparse";
import { createPapaParseConfig, hashCode } from "./CSVControlData";
import { SchemaContextData } from "../../pages/MetadataSchema";

const fs = require("fs");
const path = require("path");

describe("CSVControlData", () => {
    test("hashCode", () => {
        expect(hashCode("a string to hash")).toBe(260647971);
        expect(hashCode("another string to hash")).toBe(-1152987839);
    });

    test("handleCSVControlData", () => {
        // schema has the fields country_name, state_name
        const schema: SchemaContextData = {
            databaseId: "adatabaseid",
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
        };
        let data = null;
        const setControlledLists = jest.fn((x) => {
            data = x;
        });
        const setRawControlData = jest.fn();
        const config: ParseRemoteConfig = createPapaParseConfig(
            schema,
            setControlledLists,
            setRawControlData
        );
        const config2: ParseWorkerConfig = {
            worker: true,
            complete: (results: ParseResult<any>, ) => {
                config.complete(results, "");
            }
        };

        // read contents of cities.metadata.test.csv into a string
        const citiesMetadataTestCSV = fs.readFileSync(
            path.join(__dirname, "cities.metadata.test.csv"),
            "utf8"
        );

        Papa.parse(citiesMetadataTestCSV, config2 );

        expect(setControlledLists).toHaveBeenCalledTimes(1);
        expect(setRawControlData).toHaveBeenCalledTimes(1);
        expect(data).toMatchSnapshot(data);
    });
});

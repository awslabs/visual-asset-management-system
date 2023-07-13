/* eslint-disable testing-library/no-unnecessary-act */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render, act } from "@testing-library/react";
import ControlledMetadata from "./ControlledMetadata";
import path from "path";
import fs from "fs";
import { MetadataApi } from "../single/Metadata";

describe("ControlledMetadata", () => {
    let schemas: Promise<any>;
    let get: (apiName: string, path: string, init: any) => Promise<any>;
    let citiesMetadataTestCSV;
    let controlledListData: Promise<string>;
    let storageget: ((key: string) => Promise<any>) | undefined;
    let view: any;

    beforeEach(() => {
        jest.clearAllMocks();
        schemas = Promise.resolve({
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
        get = jest.fn((api, path, init) => {
            if (path.startsWith("metadataschema")) return schemas;
            if (path.startsWith("metadata/"))
                return Promise.resolve<MetadataApi>({
                    version: "1",
                    metadata: {
                        country_name: "United States",
                        state_name: "California",
                    },
                });
            return Promise.resolve<MetadataApi | null>(null);
        });
        citiesMetadataTestCSV = fs.readFileSync(
            path.join(__dirname, "cities.metadata.test.csv"),
            "utf8",
        );
        controlledListData = Promise.resolve(citiesMetadataTestCSV);
        storageget = jest.fn(() => {
            return controlledListData;
        });
    });

    it("should render", () => {
        expect(ControlledMetadata).toBeDefined();
    });

    it("should call for metadata when initial state is not provided", async () => {
        await act(async () => {
            view = await render(
                <ControlledMetadata
                    databaseId="123"
                    assetId="456"
                    apiget={get}
                    storageget={storageget}
                />,
            );
            await schemas;
            await controlledListData;
        });
        expect(storageget).toHaveBeenCalledTimes(1);
        expect(get).toHaveBeenCalledWith("api", "metadataschema/123", {});
        expect(get).toHaveBeenCalledWith("api", "metadata/123/456", {});
        expect(view).toMatchSnapshot();
    });

    it("should not call metadata when the initial state is provided", async () => {
        await act(async () => {
            view = await render(
                <ControlledMetadata
                    databaseId="123"
                    assetId="456"
                    apiget={get}
                    storageget={storageget}
                    initialState={{
                        country_name: "United States",
                        state_name: "California",
                    }}
                />,
            );
            await schemas;
            await controlledListData;
        });
        expect(storageget).toHaveBeenCalledTimes(1);
        expect(get).toHaveBeenCalledWith("api", "metadataschema/123", {});
        expect(get).toHaveBeenCalledTimes(1);

        expect(view).toMatchSnapshot();
    });
});

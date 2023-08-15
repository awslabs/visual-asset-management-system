/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { API } from "aws-amplify";
import { Button, Header, Table, Input, Box, SpaceBetween } from "@cloudscape-design/components";

export class MetadataApi {
    version!: string;
    metadata!: Metadata;
}
export class Metadata {
    [key: string]: string;
}

class TableRow {
    idx!: number;
    name: string | null | undefined;
    description: string | null | undefined;
    type: string | null | undefined;
}

export const put = async (
    databaseId: string,
    assetId: string,
    record: Metadata,
    prefix?: string
) => {
    if (Object.keys(record).length < 1) {
        return;
    }
    if (prefix) {
        return API.put("api", `metadata/${databaseId}/${assetId}?prefix=${prefix}`, {
            body: {
                version: "1",
                metadata: record,
            },
        });
    } else {
        return API.put("api", `metadata/${databaseId}/${assetId}`, {
            body: {
                version: "1",
                metadata: record,
            },
        });
    }
};

const get = async (databaseId: string, assetId: string, prefix?: string): Promise<object> => {
    if (prefix) {
        return API.get("api", `metadata/${databaseId}/${assetId}?prefix=${prefix}`, {});
    } else {
        return API.get("api", `metadata/${databaseId}/${assetId}`, {});
    }
};

class MetadataInputs {
    assetId!: string;
    databaseId!: string;
    initialState?: Metadata;
    prefix?: string;
    store?: (
        databaseId: string,
        assetId: string,
        record: Metadata,
        prefix?: string
    ) => Promise<any>;
}

const MetadataTable = ({ assetId, databaseId, store, prefix, initialState }: MetadataInputs) => {
    const _store = store !== undefined ? store : put;
    const tableRowToMeta = (rows: TableRow[]): Metadata => {
        const result: Metadata = {};
        rows.forEach((row) => {
            if (row && row.name && row.description) {
                result[row.name] = row.description;
            }
        });
        return result;
    };

    const metaToTableRow = (meta: Metadata) =>
        Object.keys(meta)
            .filter((key) => key !== "databaseId" && key !== "assetId")
            .map((key, idx): TableRow => {
                return {
                    idx: idx,
                    name: key,
                    description: meta[key],
                    type: "string",
                };
            });

    const [items, setItems] = useState<TableRow[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!loading) {
            return;
        }

        if (initialState !== undefined) {
            setLoading(false);
            setItems(metaToTableRow(initialState));
            return;
        }

        get(databaseId, assetId, prefix)
            .catch((x) => {
                // if 404 , then set an initial status to empty
                if (x.response.status === 404) {
                    setLoading(false);
                }
            })
            .then((result) => {
                setLoading(false);
                const meta = result as MetadataApi;
                if (meta && meta.metadata) {
                    setItems(metaToTableRow(meta.metadata));
                }
            });
    }, [loading, items, initialState, databaseId, assetId]);

    const addItems = () => {
        const next = [...items];
        next.unshift({
            idx: -1,
            name: null,
            description: null,
            type: "string",
        });
        for (let i = 0; i < next.length; i++) {
            next[i].idx = i;
        }
        setItems(next);
    };

    const AddButton = () => {
        return (
            <Button
                onClick={addItems}
                disabled={items.filter((x) => x.name === null || x.description === null).length > 0}
                variant="primary"
            >
                Add Field
            </Button>
        );
    };

    const HeaderControls = () => {
        return (
            <div
                style={{
                    width: "calc(100% - 40px)",
                    textAlign: "right",
                    position: "absolute",
                }}
            >
                <AddButton />
            </div>
        );
    };

    const validationFunction = (field1: string, reqs: string[]) => {
        const field = field1 as keyof TableRow;
        return (item: TableRow, value: "name" | "description" | null | undefined) => {
            if (value === undefined || value === null) {
                return;
            }
            if (reqs.indexOf("no-underscore-prefix") > -1) {
                if (value.indexOf("_") === 0) {
                    return "Field name must not start with underscore";
                }
            }
            if (reqs.indexOf("non-empty") > -1) {
                if (value.length < 1) {
                    return "Field must not be empty";
                }
            }
            if (reqs.indexOf("unique") > -1) {
                if (
                    items.filter((item) => item[field] === value).length > 0 &&
                    item[field] !== value
                ) {
                    return (
                        "Field name must be unique, " +
                        value +
                        " is already in use by another field"
                    );
                }
            }
        };
    };

    return (
        <div style={{ width: "100%", marginTop: "8px" }}>
            <Table
                header={
                    <>
                        <HeaderControls />
                        <Header
                            counter={
                                items.length +
                                (items.length > 1 || items.length === 0 ? " fields" : " field")
                            }
                        >
                            Metadata
                        </Header>
                    </>
                }
                loading={loading}
                loadingText="Loading..."
                submitEdit={async (blah, column, newValue) => {
                    const item: TableRow = blah as TableRow;
                    const next: TableRow[] = [...items];
                    switch (column.id) {
                        case "description":
                            next[item.idx].description = newValue as string;
                            break;
                        case "name":
                            next[item.idx].name = newValue as string;
                            break;
                    }
                    setItems(next);

                    await _store(databaseId, assetId, tableRowToMeta(next), prefix);
                }}
                items={items}
                empty={
                    <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                        <SpaceBetween size="m">
                            <b>No fields</b>
                            <AddButton />
                        </SpaceBetween>
                    </Box>
                }
                columnDefinitions={[
                    {
                        id: "name",
                        header: "Variable name",
                        cell: (item) => {
                            return item.name;
                        },
                        editConfig: {
                            ariaLabel: "Name",
                            editIconAriaLabel: "editable",
                            errorIconAriaLabel: "Name Error",
                            validation: validationFunction("name", [
                                "non-empty",
                                "unique",
                                "no-underscore-prefix",
                            ]),
                            editingCell: (item, { currentValue, setValue }) => {
                                return (
                                    <Input
                                        autoFocus={true}
                                        placeholder="Name"
                                        value={currentValue ?? item.name}
                                        onChange={(event) => {
                                            setValue(event.detail.value);
                                        }}
                                    />
                                );
                            },
                        },
                    },
                    {
                        id: "description",
                        header: "Description",
                        cell: (e) => e.description,
                        editConfig: {
                            ariaLabel: "Description",
                            editIconAriaLabel: "editable",
                            errorIconAriaLabel: "Description Error",
                            validation: validationFunction("description", ["non-empty"]),
                            editingCell: (item, { currentValue, setValue }) => {
                                return (
                                    <Input
                                        autoFocus={true}
                                        value={currentValue ?? item.description}
                                        placeholder="Description"
                                        onChange={(event) => {
                                            const next = [...items];
                                            next[item.idx]["description"] = event.detail.value;
                                            setValue(event.detail.value);
                                            setItems(next);
                                        }}
                                    />
                                );
                            },
                        },
                    },
                ]}
            />
        </div>
    );
};

export default MetadataTable;

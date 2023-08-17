/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useEffect, useState } from "react";
import { SchemaContextData } from "../../pages/MetadataSchema";
import { API, Storage } from "aws-amplify";
import { Container, Grid, Header } from "@cloudscape-design/components";
import { EditComp } from "./EditComp";
import { HandleControlData, handleCSVControlData as originHandler } from "./CSVControlData";
import MetadataTable, { MetadataApi, put } from "../single/Metadata";
import { isAxiosError } from "../../common/typeUtils";

export interface Metadata {
    [k: string]: string;
}

interface ControlledMetadataProps {
    assetId: string;
    databaseId: string;
    prefix?: string;
    initialState?: Metadata;
    store?: (
        databaseId: string,
        assetId: string,
        record: Metadata,
        prefix?: string
    ) => Promise<any>;
    apiget?: (apiName: string, path: string, init: any) => Promise<any>;
    storageget?: (key: string) => Promise<any>;
    handleCSVControlData?: HandleControlData;
    showErrors?: boolean;
    setValid?: (v: boolean) => void;
}

export interface TableRow {
    idx: number;
    name: string;
    value: string;
    inlineValues: string[];
    type: string;
    dependsOn: string[];
}

export default function ControlledMetadata({
    databaseId,
    assetId,
    prefix,
    initialState,
    apiget = API.get.bind(API),
    storageget = Storage.get.bind(Storage),
    handleCSVControlData = originHandler,
    store = put,
    showErrors,
    setValid,
}: ControlledMetadataProps) {
    const [schema, setSchema] = useState<SchemaContextData | null>(null);
    const [controlledLists, setControlledLists] = useState<any | null>(null);
    const [rawControlData, setRawControlData] = useState<any>([]);
    const [metadata, setMetadata] = useState<Metadata | null>();
    const [items, setItems] = useState<TableRow[]>([]);
    const [requiredRows, setRequiredRows] = useState<string[]>([]);
    const [validRows, setValidRows] = useState<string[]>([]);

    const metaToTableRow = (meta: Metadata, schema: SchemaContextData) => {
        schema.schemas.sort((a, b) => {
            if (a.sequenceNumber === undefined) {
                return 1;
            }
            if (b.sequenceNumber === undefined) {
                return -1;
            }
            return a.sequenceNumber - b.sequenceNumber;
        });

        const controlled = schema.schemas
            .map((x) => x.field)
            .map((key, idx): TableRow => {
                return {
                    idx: idx,
                    name: key,
                    value: meta[key],
                    dependsOn: schema.schemas.find((x) => x.field === key)?.dependsOn || [],
                    inlineValues:
                        schema.schemas
                            .find((x) => x.field === key)
                            ?.inlineControlledListValues?.split(",")
                            .map((x) => x.trim()) || [],
                    type: schema.schemas.find((x) => x.field === key)?.dataType || "string",
                };
            });

        return controlled;
    };

    useEffect(() => {
        setRequiredRows(schema?.schemas.filter((s) => s.required).map((s) => s.field) || []);
    }, [schema]);

    useEffect(() => {
        if (setValid) {
            const isValid =
                requiredRows.length > 0
                    ? requiredRows
                          ?.map((row) => validRows.includes(row))
                          .reduce((acc, curr) => acc && curr)
                    : true;
            setValid(!!isValid);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [requiredRows, validRows]);

    const tableRowToMeta = (rows: TableRow[]): Metadata => {
        const result: Metadata = {};
        rows.forEach((row) => {
            if (row && row.name && row.value) {
                result[row.name] = row.value;
            }
        });
        return result;
    };

    // given the databaseId, retrieve the metadata schema for the database

    useEffect(() => {
        if (schema !== null) {
            return;
        }

        const getMetadata = async () => {
            if (initialState === undefined) {
                let path = `metadata/${databaseId}/${assetId}`;
                if (prefix) {
                    path += `?prefix=${prefix}`;
                }

                let start: Metadata = {};

                try {
                    const { metadata }: MetadataApi = await apiget("api", path, {});
                    start = metadata;
                } catch (e) {
                    if (
                        isAxiosError(e) &&
                        e.response.status === 404 &&
                        e.response.data === "Item Not Found"
                    ) {
                        console.warn("No metadata found.");
                    } else {
                        throw e;
                    }
                }

                try {
                    const data: SchemaContextData = await apiget(
                        "api",
                        `metadataschema/${databaseId}`,
                        {}
                    );

                    setSchema(data);

                    if (data.schemas.length > 0) {
                        const meta = data.schemas.reduce((acc, x) => {
                            acc[x.field] = start[x.field] || "";
                            return acc;
                        }, start);
                        setMetadata(meta);
                        setItems(metaToTableRow(meta, data));
                    }
                } catch (error) {
                    setSchema({ databaseId, schemas: [] });
                    console.error(error);
                }
            } else {
                const data: SchemaContextData = await apiget(
                    "api",
                    `metadataschema/${databaseId}`,
                    {}
                );
                setSchema(data);
                if (data.schemas.length > 0) {
                    const start: Metadata = initialState || {};
                    const meta = data.schemas.reduce((acc, x) => {
                        acc[x.field] = start[x.field] || "";
                        return acc;
                    }, start);
                    setMetadata(meta);
                    setItems(metaToTableRow(meta, data));
                }
            }
        };
        getMetadata();

        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [databaseId, assetId, initialState]);

    useEffect(() => {
        if (schema === null || schema.schemas.length === 0) return;
        if (controlledLists !== null) return;
        storageget(`metadataschema/${databaseId}/controlledlist.csv`).then((url: string) => {
            handleCSVControlData(url, schema, setControlledLists, setRawControlData);
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [databaseId, schema]);

    if (schema && schema.schemas.length === 0) {
        return (
            <MetadataTable
                assetId={assetId || ""}
                databaseId={databaseId || ""}
                initialState={initialState}
                store={store}
                prefix={prefix}
                data-testid="metadata-table"
            />
        );
    }
    if (!metadata || !controlledLists || !schema) {
        return <p>Loading...</p>;
    }

    return (
        <React.Fragment>
            <Container header={<Header variant="h2">Metadata</Header>}>
                {items.map((row) => {
                    return (
                        <Grid gridDefinition={[{ colspan: 3 }, { colspan: 6 }]} key={row.idx}>
                            <div>{row.name}</div>
                            <div>
                                <EditComp
                                    controlledLists={controlledLists}
                                    metadata={metadata}
                                    item={row}
                                    currentValue={row.value}
                                    schema={schema}
                                    controlData={rawControlData}
                                    setValue={(value) => {
                                        if (value !== undefined) {
                                            const next: TableRow[] = [...items];
                                            next[row.idx].value = value;

                                            const resetDeps = (name: string) => {
                                                for (const item of next) {
                                                    if (item.dependsOn.includes(name)) {
                                                        item.value = "";
                                                        resetDeps(item.name);
                                                    }
                                                }
                                            };

                                            resetDeps(row.name);

                                            setItems(next);
                                            setMetadata(tableRowToMeta(next));
                                            if (store)
                                                store(
                                                    databaseId,
                                                    assetId,
                                                    tableRowToMeta(next),
                                                    prefix
                                                );
                                        } else {
                                            console.warn("undefined value", row);
                                        }
                                    }}
                                    setValid={(valid: boolean) => {
                                        if (valid) {
                                            if (!validRows.includes(row.name)) {
                                                setValidRows((prev) => [...prev, row.name]);
                                            }
                                        } else {
                                            setValidRows((prev) =>
                                                prev.filter((r) => r !== row.name)
                                            );
                                        }
                                    }}
                                    showErrors={showErrors}
                                />
                            </div>
                        </Grid>
                    );
                })}
            </Container>
        </React.Fragment>
    );
}

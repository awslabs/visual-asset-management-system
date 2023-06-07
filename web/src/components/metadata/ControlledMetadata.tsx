import { useEffect, useState } from "react";
import { SchemaContextData } from "../../pages/MetadataSchema";
import { API, Storage } from "aws-amplify";
import { Container, Grid, Header } from "@cloudscape-design/components";
import React from "react";
import { EditComp } from "./EditComp";
import { HandleControlData, handleCSVControlData as originHandler } from "./CSVControlData";
import MetadataTable, { MetadataApi, put } from "../single/Metadata";

export interface Metadata {
    [k: string]: string;
}

interface ControlledMetadataProps {
    assetId: string;
    databaseId: string;
    initialState?: Metadata;
    store?: (databaseId: string, assetId: string, record: Metadata) => Promise<any>;
    apiget?: (apiName: string, path: string, init: any) => Promise<any>;
    storageget?: (key: string) => Promise<any>;
    handleCSVControlData?: HandleControlData;
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
    initialState,
    apiget = API.get.bind(API),
    storageget = Storage.get.bind(Storage),
    handleCSVControlData = originHandler,
    store = put,
}: ControlledMetadataProps) {
    const [schema, setSchema] = useState<SchemaContextData | null>(null);
    const [controlledLists, setControlledLists] = useState<any | null>(null);
    const [rawControlData, setRawControlData] = useState<any>([]);
    const [metadata, setMetadata] = useState<Metadata | null>();
    const [items, setItems] = useState<TableRow[]>([]);

    // console.log("schema", schema);
    // console.log("items", items);
    // console.log("controlledLists", controlledLists);

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

        return schema.schemas
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
    };

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

        if (initialState === undefined) {
            apiget("api", `metadata/${databaseId}/${assetId}`, {}).then(
                ({ metadata: start }: MetadataApi) => {
                    apiget("api", `metadataschema/${databaseId}`, {}).then(
                        (data: SchemaContextData) => {
                            setSchema(data);
                            if (data.schemas.length > 0) {
                                const meta = data.schemas.reduce((acc, x) => {
                                    acc[x.field] = start[x.field] || "";
                                    return acc;
                                }, start);
                                console.log("metadata in init", meta);
                                setMetadata(meta);
                                setItems(metaToTableRow(meta, data));
                            }
                        }
                    );
                }
            );
        } else {
            apiget("api", `metadataschema/${databaseId}`, {}).then((data: SchemaContextData) => {
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
            });
        }
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
                data-testid="metadata-table"
            />
        );
    }
    if (!metadata || !controlledLists || !schema) {
        return <p>Loading...</p>;
    }

    return (
        <React.Fragment>
            <Container
                header={
                    <Header variant="h2" description="Metadata">
                        Metadata
                    </Header>
                }
            >
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
                                            console.log("items", items);
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
                                                store(databaseId, assetId, tableRowToMeta(next));
                                        } else {
                                            console.log("undefined value", row);
                                        }
                                    }}
                                />
                            </div>
                        </Grid>
                    );
                })}
            </Container>
        </React.Fragment>
    );
}

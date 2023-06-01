import { useEffect, useState } from "react";
import { SchemaContextData } from "../../pages/MetadataSchema";
import { API, Storage } from "aws-amplify";
import Papa, { ParseRemoteConfig } from "papaparse";
import { Grid } from "@cloudscape-design/components";
import React from "react";
import { EditComp } from "./EditComp";

export interface Metadata {
    [k: string]: string;
}

interface ControlledMetadataProps {
    assetId: string;
    databaseId: string;
    initialState?: Metadata;
    store?: (databaseId: string, assetId: string, record: Metadata) => Promise<any>;
}

export interface TableRow {
    idx: number;
    name: string;
    value: string;
    type: string;
    dependsOn: string[];
}

export default function ControlledMetadata({ databaseId, assetId }: ControlledMetadataProps) {
    const [schema, setSchema] = useState<SchemaContextData | null>(null);
    const [controlledLists, setControlledLists] = useState<any | null>(null);
    const [rawControlData, setRawControlData] = useState<any>([]);
    const [metadata, setMetadata] = useState<Metadata | null>();
    const [items, setItems] = useState<TableRow[]>([]);

    console.log("schema", schema);
    console.log("items", items);
    console.log("controlledLists", controlledLists);

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
        API.get("api", `metadataschema/${databaseId}`, {}).then((data: SchemaContextData) => {
            setSchema(data);
            const start: Metadata = {};
            const meta = data.schemas.reduce((acc, x) => {
                acc[x.field] = "";
                return acc;
            }, start);
            setMetadata(meta);
            setItems(metaToTableRow(meta, data));
        });
    }, [setSchema, schema, databaseId]);

    useEffect(() => {
        if (schema === null) return;
        if (controlledLists !== null) return;

        const hashCode = (x: string) => {
            let hash = 0;
            for (let i = 0; i < x.length; i++) {
                hash = (hash << 5) - hash + x.charCodeAt(i);
                hash |= 0; // Convert to 32bit integer
            }
            return hash;
        };

        const config: ParseRemoteConfig = {
            download: true,
            header: true,
            complete: (results: any) => {
                const lists = schema.schemas
                    .filter((field) => field.dataType === "controlled-list")
                    .map((field) => {
                        const deps = field.dependsOn ? field.dependsOn : [];
                        const def: { [k: number]: any } = {};
                        return {
                            field: field.field,
                            columns: [field.field, ...deps],
                            data: def,
                        };
                    });

                for (const result of results.data) {
                    for (const list of lists) {
                        const item = list.columns.reduce((acc: any, column: string) => {
                            acc[column] = result[column];
                            return acc;
                        }, {});
                        const hash = hashCode(JSON.stringify(item));
                        list.data[hash] = item;
                    }
                }
                const listsSorted = lists.map((x) => {
                    return {
                        ...x,
                        data: Object.values(x.data).sort((a, b) => {
                            if (JSON.stringify(a) < JSON.stringify(b)) return -1;
                            if (JSON.stringify(a) > JSON.stringify(b)) return 1;
                            return 0;
                        }),
                    };
                });
                const byField: { [k: string]: any } = {};
                console.log("listsSorted", listsSorted);
                setControlledLists(
                    listsSorted.reduce((acc, x) => {
                        acc[x.field] = x;
                        return acc;
                    }, byField)
                );
                setRawControlData(results.data);
            },
        };
        Storage.get(`metadataschema/${databaseId}/controlledlist.csv`).then((url: string) => {
            // note: config has a callback with a side effect that calls setControlledLists
            Papa.parse(url, config);
        });
    }, [controlledLists, databaseId, schema]);

    if (!metadata || !controlledLists || !schema) {
        return <p>Loading...</p>;
    }

    return (
        <React.Fragment>
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
                                    } else {
                                        console.log("undefined value", row);
                                    }
                                }}
                            />
                        </div>
                    </Grid>
                );
            })}
        </React.Fragment>
    );
}

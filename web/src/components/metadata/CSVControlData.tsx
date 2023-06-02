import { SchemaContextData } from "../../pages/MetadataSchema";
import Papa, { ParseRemoteConfig } from "papaparse";
import React from "react";

export function createPapaParseConfig(
    schema: SchemaContextData,
    setControlledLists: React.Dispatch<any>,
    setRawControlData: React.Dispatch<any>
) {
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
    return config;
}

export function handleCSVControlData(
    url: string,
    schema: SchemaContextData,
    setControlledLists: React.Dispatch<any>,
    setRawControlData: React.Dispatch<any>
) {
    // note: config has a callback with a side effect that calls setControlledLists
    const config: ParseRemoteConfig = createPapaParseConfig(
        schema,
        setControlledLists,
        setRawControlData
    );
    Papa.parse(url, config);
}

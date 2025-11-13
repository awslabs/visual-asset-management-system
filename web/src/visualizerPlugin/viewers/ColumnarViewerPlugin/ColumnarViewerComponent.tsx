/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { readRemoteFile } from "react-papaparse";
import DataGrid from "react-data-grid";
import FCS from "fcs";
import arrayBufferToBuffer from "arraybuffer-to-buffer";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

interface Column {
    key: string;
    name: string;
}

interface Row {
    [key: string]: any;
}

const readFcsFile = (
    remoteFileUrl: string,
    setColumns: (columns: Column[]) => void,
    setRows: (rows: Row[]) => void
) => {
    const request = new XMLHttpRequest();
    request.open("GET", remoteFileUrl, true);
    request.responseType = "arraybuffer";
    request.onload = function () {
        const buffer = arrayBufferToBuffer(request.response);
        const fcs = new (FCS as any)({}, buffer);
        if (fcs.dataAsStrings && Array.isArray(fcs.dataAsStrings) && fcs.text) {
            const newRows: Row[] = [];
            const newColumns: Column[] = [];
            const columnCount = fcs.dataAsStrings[0]
                ?.replace("[", "")
                .replace("]", "")
                .split(",").length;

            for (let i = 1; i <= columnCount; i++) {
                const name = fcs.text["$P" + i + "N"] || " ";
                newColumns.push({
                    key: name,
                    name: name,
                });
            }
            for (let i = 0; i < columnCount; i++) {
                const newRow = fcs.dataAsStrings[i]
                    .replace("[", "")
                    .replace("]", "")
                    .split(",")
                    .reduce((acc: Row, cur: string, j: number) => {
                        acc[newColumns[j].key] = cur;
                        return acc;
                    }, {});
                newRows.push(newRow);
            }
            setColumns(newColumns);
            setRows(newRows);
        }
    };
    request.send();
};

const readCsvFile = (
    remoteFileUrl: string,
    setColumns: (columns: Column[]) => void,
    setRows: (rows: Row[]) => void
) => {
    readRemoteFile(remoteFileUrl, {
        download: true,
        complete: (results: any) => {
            const { data } = results;
            const newRows: Row[] = [];
            let newColumns: Column[] = [];
            for (let i = 0; i < data.length; i++) {
                if (i === 0) {
                    newColumns = data[i].map((column: string) => {
                        return {
                            key: column,
                            name: column,
                        };
                    });
                } else {
                    const newRow = data[i].reduce((acc: Row, cur: string, j: number) => {
                        acc[newColumns[j].key] = cur;
                        return acc;
                    }, {});
                    newRows.push(newRow);
                }
            }
            setColumns(newColumns);
            setRows(newRows);
        },
    });
};

const ColumnarViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
}) => {
    const [loaded, setLoaded] = useState(false);
    const [columns, setColumns] = useState<Column[]>([]);
    const [rows, setRows] = useState<Row[]>([]);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const loadAsset = async () => {
            if (!assetKey) return;

            try {
                setError(null);

                console.log("ColumnarViewerComponent loading file:", {
                    assetId,
                    databaseId,
                    key: assetKey,
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey,
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading data file:", response);
                        throw new Error("Failed to download data file");
                    } else {
                        if (assetKey.indexOf(".fcs") !== -1) {
                            try {
                                readFcsFile(response[1], setColumns, setRows);
                            } catch (error) {
                                console.error("Error reading FCS file:", error);
                                setError("Failed to read FCS file format");
                            }
                        } else {
                            try {
                                readCsvFile(response[1], setColumns, setRows);
                            } catch (error) {
                                console.error("Error reading CSV file:", error);
                                setError("Failed to read CSV file format");
                            }
                        }
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error loading columnar data:", error);
                setError(error instanceof Error ? error.message : "Failed to load data");
            }
        };

        if (!loaded && assetKey !== "") {
            loadAsset();
            setLoaded(true);
        }
    }, [loaded, assetKey, assetId, databaseId, versionId]);

    if (error) {
        return (
            <div
                style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                    fontSize: "16px",
                    color: "#d13212",
                }}
            >
                Error: {error}
            </div>
        );
    }

    if (!loaded || columns.length === 0) {
        return (
            <div
                style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                    fontSize: "16px",
                    color: "#666",
                }}
            >
                Loading data...
            </div>
        );
    }

    return (
        <div style={{ width: "100%", height: "100%", overflowX: "scroll" }}>
            <DataGrid columns={columns} rows={rows} />
        </div>
    );
};

export default ColumnarViewerComponent;

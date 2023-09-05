/* eslint-disable no-loop-func */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { getPresignedKey } from "../../common/auth/s3";
import { readRemoteFile } from "react-papaparse";
import DataGrid from "react-data-grid";
import FCS from "fcs";
import arrayBufferToBuffer from "arraybuffer-to-buffer";

//@todo refactor without side effects, abstract common parts with other visualizers to higher level
const readFcsFile = (remoteFileUrl, setColumns, setRows) => {
    const request = new XMLHttpRequest();
    request.open("GET", remoteFileUrl, true);
    request.responseType = "arraybuffer";
    request.onload = function () {
        const buffer = arrayBufferToBuffer(request.response);
        const fcs = new FCS({}, buffer);
        if (fcs.dataAsStrings && Array.isArray(fcs.dataAsStrings) && fcs.text) {
            const newRows = [];
            const newColumns = [];
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
                    .reduce((acc, cur, j) => {
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

//@todo refactor without side effects, abstract common parts with other visualizers to higher level
const readCsvFile = (remoteFileUrl, setColumns, setRows) => {
    readRemoteFile(remoteFileUrl, {
        complete: (results) => {
            const { data } = results;
            const newRows = [];
            let newColumns;
            for (let i = 0; i < data.length; i++) {
                if (i === 0) {
                    newColumns = data[i].map((column) => {
                        return {
                            key: column,
                            name: column,
                        };
                    });
                } else {
                    const newRow = data[i].reduce((acc, cur, j) => {
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

export default function ColumnarViewer(props) {
    const { assetId, databaseId, assetKey } = props;
    const [loaded, setLoaded] = useState(false);
    const [columns, setColumns] = useState([]);
    const [rows, setRows] = useState([]);

    useEffect(() => {
        const loadAsset = async () => {
            await getPresignedKey(assetId, databaseId, assetKey).then((remoteFileUrl) => {
                if (assetKey.indexOf(".fcs") !== -1) {
                    try {
                        readFcsFile(remoteFileUrl, setColumns, setRows);
                    } catch (error) {
                        console.log(error);
                    }
                } else {
                    try {
                        readCsvFile(remoteFileUrl, setColumns, setRows);
                    } catch (error) {
                        console.log(error);
                    }
                }
            });
        };
        if (!loaded && assetKey !== "") {
            loadAsset();
            setLoaded(true);
        }
    }, [loaded, assetKey]);

    return (
        <div style={{ width: "100%", height: "100%", overflowX: "scroll" }}>
            <DataGrid columns={columns} rows={rows} />
        </div>
    );
}

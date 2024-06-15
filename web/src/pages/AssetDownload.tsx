import { useLocation } from "react-router";
//import { Storage } from "aws-amplify";
import { downloadAsset } from "../services/APIService";
import { FileTree } from "../components/filemanager/FileManager";
import { FileUploadTable, FileUploadTableItem } from "./AssetUpload/FileUploadTable";
import { useReducer, useState } from "react";
import { useNavigate, useParams } from "react-router";
import axios from "axios";

async function downloadAllFilesRecursively(
    assetId: string,
    databaseId: string,
    tree: FileTree,
    directoryHandle: any,
    dispatch: any
) {
    for (let subtree of tree.subTree) {
        if (subtree.subTree.length > 0) {
            const subFolderDirectoryHandle = await directoryHandle.getDirectoryHandle(
                subtree.name,
                { create: true }
            );
            await downloadAllFilesRecursively(
                assetId,
                databaseId,
                subtree,
                subFolderDirectoryHandle,
                dispatch
            );
        } else {
            const fileHandle = await directoryHandle.getFileHandle(subtree.name, { create: true });
            const writable = await fileHandle.createWritable();
            let errorUpload = false;

            try {
                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: subtree.keyPrefix,
                    version: "",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("API Error with downloading file");
                        dispatch({
                            type: "UPDATE_STATUS",
                            payload: { relativePath: subtree.relativePath, status: "Failed" },
                        });
                    } else {
                        //Download file
                        const responseFile = await axios({
                            url: response[1],
                            method: "GET",
                            responseType: "blob",
                            onDownloadProgress: (progressEvent) => {
                                dispatch({
                                    //console.log("Progress update");
                                    type: "UPDATE_PROGRESS",
                                    payload: {
                                        status: "In Progress",
                                        relativePath: subtree.relativePath,
                                        progress: progressEvent.loaded,
                                        loaded: progressEvent.loaded,
                                        total: progressEvent.total,
                                    },
                                });
                            },
                        });
                        await writable.write(responseFile.data);
                    }
                }
            } catch (error) {
                console.error(error);
                dispatch({
                    type: "UPDATE_STATUS",
                    payload: { relativePath: subtree.relativePath, status: "Failed" },
                });
                errorUpload = true;
            }

            await writable.close();

            //If we didn't error, we are complete!
            if (!errorUpload) {
                dispatch({
                    type: "UPDATE_STATUS",
                    payload: { relativePath: subtree.relativePath, status: "Completed" },
                });
            }
        }
    }
}

async function downloadFolder(assetId: string, databaseId: string, tree: FileTree, dispatch: any) {
    //@ts-ignore
    const directoryHandle = await window.showDirectoryPicker();
    await downloadAllFilesRecursively(assetId, databaseId, tree, directoryHandle, dispatch);
}

const convertFileTreeItemsToFileUploadTableItems = (fileTree: FileTree): FileUploadTableItem[] => {
    const allItems: FileUploadTableItem[] = [];
    for (let subtree of fileTree.subTree) {
        if (subtree.subTree.length > 0) {
            allItems.push(...convertFileTreeItemsToFileUploadTableItems(subtree));
        } else {
            allItems.push({
                name: subtree.name,
                index: 0,
                size: 0,
                relativePath: subtree.relativePath,
                status: "Queued",
                progress: 0,
                startedAt: Date.now(),
                loaded: 0,
                total: 0,
            });
        }
    }
    return allItems;
};

const updateIndices = (fileUploadTableItems: FileUploadTableItem[]): FileUploadTableItem[] => {
    let index = 0;
    for (let item of fileUploadTableItems) {
        item.index = index;
        index++;
    }
    return fileUploadTableItems;
};

function assetDownloadReducer(
    state: FileUploadTableItem[],
    action: { type: string; payload: any }
): FileUploadTableItem[] {
    switch (action.type) {
        case "UPDATE_INDICES":
            return updateIndices(state);
        case "UPDATE_PROGRESS":
            return state.map((item) => {
                if (item.relativePath === action.payload.relativePath) {
                    return {
                        ...item,
                        status: action.payload.status,
                        size: action.payload.total,
                        progress: Math.floor((action.payload.loaded / action.payload.total) * 100),
                        loaded: action.payload.loaded,
                        total: action.payload.total,
                    };
                } else {
                    return item;
                }
            });
        case "UPDATE_STATUS":
            return state.map((item) => {
                if (item.relativePath === action.payload.relativePath) {
                    return {
                        ...item,
                        status: action.payload.status,
                    };
                } else {
                    return item;
                }
            });
        default:
            return state;
    }
}
export default function AssetDownloadsPage() {
    const { state } = useLocation();
    const { databaseId, assetId } = useParams();
    const fileTree = state["fileTree"] as FileTree;
    const [resume, setResume] = useState(true);
    const fileUploadTableItems = convertFileTreeItemsToFileUploadTableItems(fileTree);
    const [fileUploadTableItemsState, dispatch] = useReducer(
        assetDownloadReducer,
        fileUploadTableItems
    );
    return (
        <div>
            <h1> Downloading {fileTree.relativePath} </h1>
            <FileUploadTable
                allItems={fileUploadTableItemsState}
                resume={resume}
                onRetry={() => {
                    downloadFolder(assetId!, databaseId!, fileTree, dispatch).then(() => {
                        setResume(false);
                    });
                }}
                mode={"Download"}
            />
        </div>
    );
}

import { useLocation } from "react-router";
import { Storage } from "aws-amplify";
import { FileTree } from "../components/filemanager/FileManager";
import { FileUploadTable, FileUploadTableItem } from "./AssetUpload/FileUploadTable";
import { useReducer, useState } from "react";

async function downloadAllFilesRecursively(tree: FileTree, directoryHandle: any, dispatch: any) {
    for (let subtree of tree.subTree) {
        if (subtree.subTree.length > 0) {
            const subFolderDirectoryHandle = await directoryHandle.getDirectoryHandle(
                subtree.name,
                { create: true }
            );
            await downloadAllFilesRecursively(subtree, subFolderDirectoryHandle, dispatch);
        } else {
            const fileHandle = await directoryHandle.getFileHandle(subtree.name, { create: true });
            const writable = await fileHandle.createWritable();
            const data = await Storage.get(subtree.keyPrefix, {
                download: true,
                progressCallback: (progress: any) => {
                    console.log("Progress callback ", progress);
                    dispatch({
                        type: "UPDATE_PROGRESS",
                        payload: {
                            status: "In Progress",
                            relativePath: subtree.relativePath,
                            progress: progress.loaded,
                            loaded: progress.loaded,
                            total: progress.total,
                        },
                    });
                },
            });
            await writable.write(data.Body);
            await writable.close();
            dispatch({
                type: "UPDATE_STATUS",
                payload: { relativePath: subtree.relativePath, status: "Completed" },
            });
        }
    }
}

async function downloadFolder(tree: FileTree, dispatch: any) {
    //@ts-ignore
    const directoryHandle = await window.showDirectoryPicker();
    await downloadAllFilesRecursively(tree, directoryHandle, dispatch);
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
                    downloadFolder(fileTree, dispatch).then(() => {
                        setResume(false);
                    });
                }}
                mode={"Download"}
            />
        </div>
    );
}

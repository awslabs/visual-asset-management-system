import { useEffect, useReducer, useState } from "react";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import { Button, SpaceBetween, Toggle } from "@cloudscape-design/components";
import FolderUpload from "../form/FolderUpload";
import { FileUploadTableItem } from "../../pages/AssetUpload/FileUploadTable";

export interface FileInfo {
    path: string;
    handle: any;
}

interface MultiFileSelectState {
    directories: {
        [key: string]: FileInfo[];
    };
    files: FileInfo[];
}

interface MultiFileSelectAction {
    type: string;
    payload: any;
}

function multiFileSelectReducer(state: MultiFileSelectState, action: MultiFileSelectAction) {
    switch (action.type) {
        case "ADD_DIRECTORY":
            return {
                ...state,
                directories: {
                    ...state.directories,
                    [action.payload.directory]: action.payload.files,
                },
            };
        case "REMOVE_DIRECTORY":
            return {
                ...state,
                directories: {
                    ...state.directories,
                    [action.payload.name]: undefined,
                },
            };
        case "ADD_FILES":
            return {
                ...state,
                files: [...state.files, ...action.payload.fileHandles],
            };
        case "REMOVE_FILE":
            return {
                ...state,
                files: state.files.filter((file) => file.handle !== action.payload.fileHandle),
            };
        default:
            return state;
    }
}
const getAllFiles = (multiFileSelect: MultiFileSelectState): FileInfo[] => {
    let fileInfo: FileInfo[] = [];
    for (const directory in multiFileSelect.directories) {
        if (multiFileSelect.directories[directory]) {
            fileInfo = [...fileInfo, ...multiFileSelect.directories[directory]];
        }
    }
    for (const file of multiFileSelect.files) {
        fileInfo.push(file);
    }
    return fileInfo;
};

export function MultiFileSelect({ onChange }: { onChange: (state: FileInfo[]) => void }) {
    const initialState: MultiFileSelectState = {
        directories: {},
        files: [],
    };
    const [isMultiFile, setIsMultiFile] = useState(false);
    const [state, dispatch] = useReducer(multiFileSelectReducer, initialState);
    useEffect(() => {
        onChange(getAllFiles(state));
    }, [state]);
    return (
        <Container>
            <FormField>
                <Toggle onChange={() => setIsMultiFile(!isMultiFile)} checked={isMultiFile}>
                    {isMultiFile ? "Folder Upload" : "File Upload"}
                </Toggle>
            </FormField>
            <SpaceBetween size={"xs"} direction={"vertical"}>
                <FolderUpload
                    label={""}
                    description={`Total Files to upload: ${
                        state.files.length +
                        Object.keys(state.directories).reduce((acc, directory) => {
                            if (state.directories[directory]) {
                                return acc + state.directories[directory].length;
                            } else {
                                return acc;
                            }
                        }, 0)
                    }`}
                    multiFile={isMultiFile}
                    onSelect={async (directoryHandle: any, fileHandles: any[]) => {
                        if (directoryHandle) {
                            dispatch({
                                type: "ADD_DIRECTORY",
                                payload: {
                                    directory: directoryHandle.name,
                                    files: fileHandles,
                                },
                            });
                        } else if (fileHandles) {
                            dispatch({
                                type: "ADD_FILES",
                                payload: {
                                    fileHandles: fileHandles,
                                },
                            });
                        }
                    }}
                ></FolderUpload>
                <SpaceBetween size={"xs"} direction={"vertical"}>
                    {state.directories &&
                        Object.keys(state.directories)
                            .filter((directory) => state.directories[directory])
                            .map((directory) => {
                                return (
                                    <Button
                                        variant={"primary"}
                                        key={directory}
                                        onClick={() =>
                                            dispatch({
                                                type: "REMOVE_DIRECTORY",
                                                payload: {
                                                    name: directory,
                                                },
                                            })
                                        }
                                    >
                                        Remove Directory {directory} -{" "}
                                        {state.directories[directory].length} Files
                                    </Button>
                                );
                            })}
                </SpaceBetween>
                <SpaceBetween size={"xs"} direction={"vertical"}>
                    {state.files &&
                        state.files.map((file) => {
                            return (
                                <Button
                                    variant={"primary"}
                                    key={file}
                                    onClick={() =>
                                        dispatch({
                                            type: "REMOVE_FILE",
                                            payload: {
                                                fileHandle: file.handle,
                                            },
                                        })
                                    }
                                >
                                    Remove File {file.path}
                                </Button>
                            );
                        })}
                </SpaceBetween>
            </SpaceBetween>
        </Container>
    );
}

/**
 * This is a component that allows the user to upload a folder.
 * Writing this in JavaScript instead of TypeScript because HTMLInputElement does not implement required attributes for directory selection
 * More on this :
 * https://stackoverflow.com/questions/63809230/reactjs-directory-selection-dialog-not-working
 * https://github.com/facebook/react/pull/3644
 *
 * Once selected, this component will show in total how many files are in the folder and return the path for each of the files
 *
 */
import { useState } from "react";
import Button from "@cloudscape-design/components/button";
import { Grid } from "@cloudscape-design/components";
import { showOpenFilePicker, showDirectoryPicker } from "file-system-access";

import FormField from "@cloudscape-design/components/form-field";

function FolderUpload(props) {
    const handleFileListChange = (directoryHandle, fileHandles) => {
        if (props.onSelect) {
            props.onSelect(directoryHandle, fileHandles);
        }
    };

    async function* getFilesRecursively(entry, path) {
        if (entry.kind === "file") {
            yield { path: path + "/" + entry.name, handle: entry };
        } else if (entry.kind === "directory") {
            const newPath = path ? path + "/" + entry.name : entry.name;
            for await (const handle of entry.values()) {
                yield* getFilesRecursively(handle, newPath);
            }
        }
    }
    const getFilesFromFileHandle = async (directoryHandle) => {
        const fileHandles = [];
        for await (const handle of getFilesRecursively(directoryHandle)) {
            fileHandles.push(handle);
        }
        return fileHandles;
    };
    const handleDirectorySelection = async () => {
        try {
            const directoryHandle = await showDirectoryPicker();
            const fileHandles = await getFilesFromFileHandle(directoryHandle, directoryHandle.name);
            console.log(fileHandles);
            return { directoryHandle, fileHandles };
        } catch (err) {
            // Check for user cancellation in multiple ways
            const isUserCancellation =
                err.name === "AbortError" ||
                err.message?.includes("aborted") ||
                err.message?.includes("cancelled") ||
                err.message?.includes("canceled") ||
                err.code === 20; // DOMException.ABORT_ERR

            if (!isUserCancellation) {
                console.error("Error selecting directory:", err);
            } else {
                console.log("User cancelled directory selection");
            }
            return { directoryHandle: null, fileHandles: [] };
        }
    };

    const handleFileSelection = async () => {
        try {
            const handles = await showOpenFilePicker({ multiple: true });
            const fileHandles = [];
            for (let i = 0; i < handles.length; i++) {
                fileHandles.push({ path: handles[i].name, handle: handles[i] });
            }
            return { handles, fileHandles };
        } catch (err) {
            // Check for user cancellation in multiple ways
            const isUserCancellation =
                err.name === "AbortError" ||
                err.message?.includes("aborted") ||
                err.message?.includes("cancelled") ||
                err.message?.includes("canceled") ||
                err.code === 20; // DOMException.ABORT_ERR

            if (!isUserCancellation) {
                console.error("Error selecting files:", err);
            } else {
                console.log("User cancelled file selection");
            }
            return { handles: null, fileHandles: [] };
        }
    };

    return (
        /**
         * For some reason the guide mentioned at the MDN docs doesn't work.
         * I found this from the stackoverflow answer mentioned at https://stackoverflow.com/a/5849341
         */
        <FormField
            label={props.label}
            description={props.description || ""}
            errorText={props.errorText}
        >
            <Grid gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}>
                {props.multiFile ? (
                    <Button
                        variant="normal"
                        iconName="upload"
                        onClick={(e) => {
                            handleDirectorySelection().then((fileSelectionResult) => {
                                console.log(fileSelectionResult);
                                const { directoryHandle, fileHandles } = fileSelectionResult;
                                if (directoryHandle) {
                                    handleFileListChange(directoryHandle, fileHandles);
                                }
                            });
                        }}
                    >
                        Choose Folder
                    </Button>
                ) : (
                    <>
                        <Button
                            variant="normal"
                            iconName="upload"
                            onClick={(e) => {
                                handleFileSelection().then((fileSelectionResult) => {
                                    console.log(fileSelectionResult);
                                    const { handles, fileHandles } = fileSelectionResult;
                                    if (handles && fileHandles.length > 0) {
                                        handleFileListChange(null, fileHandles);
                                    }
                                });
                            }}
                        >
                            Choose File
                        </Button>
                    </>
                )}
            </Grid>
        </FormField>
    );
}

export default FolderUpload;

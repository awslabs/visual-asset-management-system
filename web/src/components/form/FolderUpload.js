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
        const directoryHandle = await window.showDirectoryPicker();
        const fileHandles = await getFilesFromFileHandle(directoryHandle, directoryHandle.name);
        console.log(fileHandles);
        return { directoryHandle, fileHandles };
    };

    const handleFileSelection = async () => {
        const handles = await window.showOpenFilePicker({ multiple: true });
        const fileHandles = [];
        for (let i = 0; i < handles.length; i++) {
            fileHandles.push({ path: handles[i].name, handle: handles[i] });
        }
        return { handles, fileHandles };
    };

    return (
        /**
         * For some reason the guide mentioned at the MDN docs doesn't work.
         * I found this from the stackoverflow answer mentioned at https://stackoverflow.com/a/5849341
         */
        <FormField label={props.label} description={props.description} errorText={props.errorText}>
            <Grid gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}>
                {props.multiFile ? (
                    <Button
                        variant="normal"
                        iconName="upload"
                        onClick={(e) => {
                            handleDirectorySelection().then((fileSelectionResult) => {
                                console.log(fileSelectionResult);
                                const { directoryHandle, fileHandles } = fileSelectionResult;
                                handleFileListChange(directoryHandle, fileHandles);
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
                                    const { directoryHandle, fileHandles } = fileSelectionResult;
                                    handleFileListChange(directoryHandle, fileHandles);
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

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
import {useState, useRef, useEffect} from 'react'
import SpaceBetween from "@cloudscape-design/components/space-between";
import Button from "@cloudscape-design/components/button";
import { Grid } from "@cloudscape-design/components";

import FormField from "@cloudscape-design/components/form-field";


function FolderUpload(props) {

    const [files, setFileHandles] = useState([]);
    const [description, setDescription] = useState("");

    /**
     * Borrowed from : https://stackoverflow.com/a/42408230
     * @param {*} n : Number of Bytes to shorten
     * @returns : Readable Bytes count
     */
    function shortenBytes(n) {
        const k = n > 0 ? Math.floor((Math.log2(n) / 10)) : 0;
        const rank = (k > 0 ? 'KMGT'[k - 1] : '') + 'b';
        const count = Math.floor(n / Math.pow(1024, k));
        return count + rank;
    }

    const handleFileListChange = (fileHandles) => {
        console.log(fileHandles);
        setFileHandles(fileHandles)
        if (props.onSelect) {
            props.onSelect(fileHandles);
        }
    }

    async function* getFilesRecursively(entry, path) {
        if (entry.kind === "file") {
            yield { 'path': path + "/" + entry.name, 'handle': entry } ;
        } else if (entry.kind === "directory") {
            const newPath = path ? path + "/" + entry.name : entry.name;
            for await (const handle of entry.values()) {
                yield* getFilesRecursively(handle, newPath);
            }
        }
    }
    const getFilesFromFileHandle = async (directoryHandle) => {
        const fileHandles = [];
        // console.log("Handle is ")
        // console.log(directoryHandle)
        for await (const handle of getFilesRecursively(directoryHandle)) {
            fileHandles.push(handle)
        }
        return fileHandles
    }
    const handleFileSelection = async () => {
        const directoryHandle = await window.showDirectoryPicker();
        const fileHandles = await getFilesFromFileHandle(directoryHandle, directoryHandle.name)
        console.log(fileHandles)
        return fileHandles;
    };

    return (
        /**
         * For some reason the guide mentioned at the MDN docs doesn't work.
         * I found this from the stackoverflow answer mentioned at https://stackoverflow.com/a/5849341
         */
        <FormField label={props.label} description={description}>

                <Grid gridDefinition={[
                    {colspan: {default: 6}},
                    {colspan: {default: 6}},
                ]}>
                    <Button
                        variant="normal"
                        iconName="upload"
                        onClick={(e) => {
                            //directoryRef?.current?.click();
                            handleFileSelection()
                                .then((fileList) => {
                                    setDescription(`Total Files to Upload: ${fileList.length}`)
                                    handleFileListChange(fileList)})
                        }}
                    >
                        Choose Folder
                    </Button>
                </Grid>
        </FormField>
    );
}

export default FolderUpload
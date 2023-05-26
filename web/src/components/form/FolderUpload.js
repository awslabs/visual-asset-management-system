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

import FormField from "@cloudscape-design/components/form-field";

function FolderUpload(props) {
    const directoryRef = useRef();

    const [files, setFiles] = useState([]);
    const [totalSize, setTotalSize] = useState(0);
    const [description, setDescription] = useState("")

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


    useEffect(() => {
        if (directoryRef.current !== null) {
            directoryRef.current.setAttribute("directory", "");
            directoryRef.current.setAttribute("webkitdirectory", "");
            directoryRef.current.setAttribute("mozkitdirectory", "");
            directoryRef.current.setAttribute("multiple", "");
        }
        // 3. monitor change of your ref with useEffect
    }, [directoryRef]);

    useEffect(() => {
        setDescription(`File Count: ${files.length} Total Size: ${shortenBytes(totalSize)}`)
    }, [files, totalSize])

    const handleFileChange = (event) => {
        console.log(event.target.files);
        let tempTotalSize = 0
        for (let i = 0; i < event.target.files.length; i++) {
            tempTotalSize += event.target.files[i].size
        }
        setTotalSize(tempTotalSize)
        setFiles(event.target.files)
        if (props.onSelect) {
            props.onSelect(event.target.files);
        }
    }

    return (
        /**
         * For some reason the guide mentioned at the MDN docs doesn't work.
         * I found this from the stackoverflow answer mentioned at https://stackoverflow.com/a/5849341
         */
        <FormField label={props.label} description={description}>
            <input hidden type="file" name="fileList" ref={directoryRef} onChange={(e) => handleFileChange(e)}/>
            <SpaceBetween size="l">
                <Button
                    variant="normal"
                    iconName="upload"
                    onClick={(e) => {
                        directoryRef?.current?.click();
                    }}
                >
                    Choose Folder
                </Button>
            </SpaceBetween>
        </FormField>
    );
}

export default FolderUpload
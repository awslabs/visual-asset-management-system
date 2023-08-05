import { Container, Grid, Header, FormField, Toggle } from "@cloudscape-design/components";
import FolderUpload from "../../components/form/FolderUpload";
import { FileUpload } from "./components";
import { useAssetUploadState } from "./state";
import { previewFileFormats } from "../../common/constants/fileFormats";
import type { FileUploadTableItem } from "./types";

const getFilesFromFileHandles = async (fileHandles: any[]) => {
    const fileUploadTableItems: FileUploadTableItem[] = [];
    for (let i = 0; i < fileHandles.length; i++) {
        const file = (await fileHandles[i].handle.getFile()) as File;
        fileUploadTableItems.push({
            handle: fileHandles[i].handle,
            index: i,
            name: fileHandles[i].handle.name,
            size: file.size,
            relativePath: fileHandles[i].path,
            progress: 0,
            status: "Queued",
            loaded: 0,
            total: file.size,
        });
    }
    return fileUploadTableItems;
};

const previewFileFormatsStr = previewFileFormats.join(", ");

export const AssetFileInfo = ({
    setFileUploadTableItems,
}: {
    setFileUploadTableItems: (fileUploadTableItems: FileUploadTableItem[]) => void;
}) => {
    const [state, dispatch] = useAssetUploadState();

    return (
        <Container header={<Header variant="h2">Select Files to Upload</Header>}>
            <>
                <FormField>
                    <Toggle
                        onChange={({ detail }) => {
                            dispatch({
                                type: "UPDATE_ASSET_IS_MULTI_FILE",
                                payload: detail.checked,
                            });
                        }}
                        checked={state.isMultiFile}
                    >
                        Folder Upload?
                    </Toggle>
                </FormField>
                <Grid gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}>
                    <FolderUpload
                        label={state.isMultiFile ? "Choose Folder" : "Choose File"}
                        description={
                            state.Asset ? "Total Files to Upload " + state.Asset.length : ""
                        }
                        multiFile={state.isMultiFile}
                        errorText={(!state.Asset && "Asset is required") || undefined}
                        onSelect={async (directoryHandle: any, fileHandles: any[]) => {
                            const files = await getFilesFromFileHandles(fileHandles);
                            setFileUploadTableItems(files);
                            dispatch({
                                type: "UPDATE_ASSET_DIRECTORY_HANDLE",
                                payload: directoryHandle,
                            });
                            dispatch({ type: "UPDATE_ASSET_FILES", payload: files });
                            dispatch({
                                type: "UPDATE_ASSET_IS_MULTI_FILE",
                                payload: files.length > 1,
                            });
                        }}
                    ></FolderUpload>

                    <FileUpload
                        label="Preview (Optional)"
                        disabled={false}
                        setFile={(file) => {
                            dispatch({ type: "UPDATE_ASSET_PREVIEW", payload: file });
                        }}
                        fileFormats={previewFileFormatsStr}
                        file={state.Preview}
                        data-testid="preview-file"
                    />
                </Grid>
            </>
        </Container>
    );
};

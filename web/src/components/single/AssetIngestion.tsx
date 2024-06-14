import {
    FileUpload,
    FormField,
    Container,
    Button,
    Textarea,
    Header,
    Box,
    SpaceBetween,
    Grid,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { useState } from "react";
import { generateUUID } from "../../common/utils/utils";

export default function AssetIngestion() {
    const [file, setFile] = useState<File | null>(null);
    const [errorMessage, setErrorMessage] = useState("");
    const [jsonBody, setJsonBody] = useState("");
    const [uploading, setUploading] = useState(false);
    const [statusMessage, setStatusMessage] = useState("");
    let fileBody = {
        key: "",
        file_size: 0,
        assetId: "",
        databaseId: "",
    };

    const splitFile = async (file: File, numParts: number) => {
        setStatusMessage("Splitting file...");
        const fileParts = [];
        const fileSize = file.size;
        const partSize = Math.ceil(fileSize / numParts);

        for (let i = 0; i < numParts; i++) {
            const start = i * partSize;
            const end = Math.min(start + partSize, fileSize);

            const part = file.slice(start, end);
            fileParts.push(part);
        }
        setStatusMessage("Splitting file done.");
        return fileParts;
    };

    const uploadPart = async (url: string, partNumber: number, filePart: Blob) => {
        const file = new File([filePart], fileBody.key, { lastModified: Date.now() });
        console.log(file);

        setStatusMessage(`Uploading part ${partNumber}...`);
        console.log(file);
        try {
            const response = await Promise.race([
                fetch(url, {
                    method: "PUT",
                    body: file,
                    headers: {
                        "Content-Type": file.type,
                    },
                }),
                new Promise((_, reject) =>
                    setTimeout(() => reject(new Error("Request timeout")), 300000)
                ),
            ]);

            const castedResponse = response as Response;

            if (castedResponse.ok) {
                const etag = castedResponse.headers.get("ETag")?.replace(/"/g, "");
                setStatusMessage(`Part ${partNumber} uploaded.`);
                return etag;
            } else {
                setStatusMessage(`Part ${partNumber} upload failed.`);
                console.error(`Failed to upload part ${partNumber}`);
                return null;
            }
        } catch (error) {
            setStatusMessage(`Part ${partNumber} upload failed due to timeout.`);
            setUploading(false);
            console.error(`Request timeout for part ${partNumber}`);
            return null;
        }
    };

    const uploadFile = async (
        file: File,
        uploadId: string,
        numParts: number,
        partUploadUrls: any,
        assetIdForAsset: string
    ) => {
        const fileParts = await splitFile(file, numParts);
        const etags = [];

        for (let i = 0; i < numParts; i++) {
            const partNumber = i + 1;
            const url = partUploadUrls[i];
            const filePart = fileParts[i];
            const etag = await uploadPart(url, partNumber, filePart);

            if (etag) {
                etags.push(etag);
            } else {
                console.error("Aborting multipart upload due to part failure");
                return;
            }
        }

        setStatusMessage("Uploading asset...");
        const assetBody = {
            parts: etags.map((etag, index) => ({
                PartNumber: index + 1,
                ETag: etag,
            })),
            upload_id: uploadId,
            ...fileBody,
        };
        try {
            const response = await API.post("api", "/ingest-asset", {
                body: assetBody,
            });
            let msg: any = (
                <div>
                    <strong>Asset uploaded.</strong>
                    <br />
                    <strong>Database Id-</strong> {assetBody.databaseId}
                    <br />
                    <strong>Asset Id / File -</strong> {assetBody.key}
                    <br />
                    <strong>Message:</strong> {response.message}
                </div>
            );
            setStatusMessage(msg);

            console.log("Asset Upload Success", response);
        } catch (error) {
            console.error("Error uploading asset:", error);
            setUploading(false);
            setStatusMessage(`Error uploading asset.\nMessage: ${error}`);
        } finally {
            setUploading(false);
            setTimeout(() => {
                setStatusMessage("");
            }, 10000);
        }
    };

    const uploadObjectStart = async () => {
        setUploading(true);
        setStatusMessage("");
        let assetIdForAsset = `i${generateUUID()}`;
        try {
            if (!file) {
                setErrorMessage("Please choose a file.");
                setUploading(false);
                return;
            }
            const additionalBody = jsonBody ? JSON.parse(jsonBody) : {};
            fileBody = {
                key: assetIdForAsset + "/" + file.name,
                file_size: file.size,
                assetId: assetIdForAsset,
                ...additionalBody,
            };

            const response = await API.post("api", "ingest-asset", {
                body: fileBody,
            });

            const { message } = response;
            const { uploadId, numParts, partUploadUrls } = message;
            const partUrlsArray: string[] = [];

            for (let i = 0; i < numParts; i++) {
                partUrlsArray.push(partUploadUrls[i]);
            }

            uploadFile(file, uploadId, numParts, partUploadUrls, assetIdForAsset);

            return response;
        } catch (error) {
            console.error("Error uploading objects:", error);
        }
    };

    let defaultPlaceholder = `{
    "databaseId": "<database-name>",
    "assetName": "<name of asset>",
    "description": "<Enter Asset description here>"
}`;

    return (
        <Box padding={{ top: "m", horizontal: "l" }}>
            <Container header={<Header variant="h2">Asset Ingestion</Header>}>
                <SpaceBetween size="l">
                    <Grid gridDefinition={[{ colspan: 2 }, { colspan: 3 }]}>
                        <FormField label="Choose File">
                            <FileUpload
                                errorText={errorMessage}
                                onChange={({ detail }) => setFile(detail.value[0] || null)}
                                value={file ? [file] : []}
                                i18nStrings={{
                                    uploadButtonText: (e) => (e ? "Choose files" : "Choose file"),
                                    dropzoneText: (e) =>
                                        e ? "Drop files to upload" : "Drop file to upload",
                                    removeFileAriaLabel: (e) => `Remove file ${e + 1}`,
                                    limitShowFewer: "Show fewer files",
                                    limitShowMore: "Show more files",
                                    errorIconAriaLabel: "Error",
                                }}
                                showFileLastModified
                                showFileSize
                                showFileThumbnail
                            />
                        </FormField>
                        <FormField label="Upload Asset">
                            <Button
                                variant="primary"
                                onClick={() => {
                                    uploadObjectStart();
                                }}
                                loading={uploading}
                            >
                                {uploading ? "Uploading..." : "Upload Object"}
                            </Button>
                        </FormField>
                    </Grid>
                    <Grid gridDefinition={[{ colspan: 3 }]}>
                        <FormField label="JSON Body - Additional Data">
                            <Textarea
                                onChange={({ detail }) => setJsonBody(detail.value)}
                                value={jsonBody || defaultPlaceholder}
                                placeholder={defaultPlaceholder}
                                rows={10}
                            />
                            <div style={{ marginTop: "1em" }}>{statusMessage}</div>
                        </FormField>
                    </Grid>
                </SpaceBetween>
            </Container>
        </Box>
    );
}

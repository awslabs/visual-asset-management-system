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

// Interface for file upload part
interface UploadPart {
    PartNumber: number;
    ETag: string;
}

// Interface for file upload URL
interface PartUploadUrl {
    PartNumber: number;
    UploadUrl: string;
}

// Interface for file upload response
interface FileUploadResponse {
    relativeKey: string;
    uploadIdS3: string;
    numParts: number;
    partUploadUrls: PartUploadUrl[];
}

export default function AssetIngestion() {
    const [file, setFile] = useState<File | null>(null);
    const [errorMessage, setErrorMessage] = useState("");
    const [jsonBody, setJsonBody] = useState("");
    const [uploading, setUploading] = useState(false);
    const [statusMessage, setStatusMessage] = useState("");

    // State for tracking upload process
    const [uploadId, setUploadId] = useState("");
    const [fileResponses, setFileResponses] = useState<FileUploadResponse[]>([]);

    // Request body for API calls
    let requestBody = {
        databaseId: "",
        assetId: "",
        assetName: "",
        description: "",
        isDistributable: true,
        tags: [] as string[],
        files: [] as { relativeKey: string; file_size: number }[],
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

    const uploadPart = async (
        url: string,
        partNumber: number,
        filePart: Blob,
        fileName: string
    ) => {
        const file = new File([filePart], fileName, { lastModified: Date.now() });

        setStatusMessage(`Uploading part ${partNumber}...`);
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

    const uploadFileParts = async (
        file: File,
        fileResponse: FileUploadResponse
    ): Promise<UploadPart[] | null> => {
        const fileParts = await splitFile(file, fileResponse.numParts);
        const parts: UploadPart[] = [];

        for (let i = 0; i < fileResponse.numParts; i++) {
            const partUploadUrl = fileResponse.partUploadUrls.find((p) => p.PartNumber === i + 1);
            if (!partUploadUrl) {
                console.error(`Missing upload URL for part ${i + 1}`);
                return null;
            }

            const partNumber = partUploadUrl.PartNumber;
            const url = partUploadUrl.UploadUrl;
            const filePart = fileParts[i];
            const etag = await uploadPart(url, partNumber, filePart, file.name);

            if (etag) {
                parts.push({
                    PartNumber: partNumber,
                    ETag: etag,
                });
            } else {
                console.error("Aborting multipart upload due to part failure");
                return null;
            }
        }

        return parts;
    };

    const completeUpload = async (
        uploadId: string,
        assetId: string,
        databaseId: string,
        fileResponse: FileUploadResponse,
        parts: UploadPart[],
        jsonData: any
    ) => {
        setStatusMessage("Completing asset upload...");

        try {
            const completeBody = {
                databaseId,
                assetId,
                assetName: jsonData.assetName,
                description: jsonData.description,
                isDistributable:
                    jsonData.isDistributable !== undefined ? jsonData.isDistributable : true,
                tags: jsonData.tags || [],
                uploadId,
                files: [
                    {
                        relativeKey: fileResponse.relativeKey,
                        uploadIdS3: fileResponse.uploadIdS3,
                        parts,
                    },
                ],
            };

            const response = await API.post("api", "/ingest-asset", {
                body: completeBody,
            });

            let msg: any = (
                <div>
                    <strong>Asset uploaded successfully.</strong>
                    <br />
                    <strong>Database Id:</strong> {databaseId}
                    <br />
                    <strong>Asset Id:</strong> {assetId}
                    <br />
                    <strong>Asset Name:</strong> {jsonData.assetName}
                    <br />
                    <strong>Message:</strong> {response.message}
                </div>
            );
            setStatusMessage(msg);

            console.log("Asset Upload Success", response);
            return true;
        } catch (error) {
            console.error("Error completing asset upload:", error);
            setStatusMessage(`Error completing asset upload.\nMessage: ${error}`);
            return false;
        }
    };

    const initializeUpload = async () => {
        setUploading(true);
        setStatusMessage("Initializing upload...");

        try {
            if (!file) {
                setErrorMessage("Please choose a file.");
                setUploading(false);
                return;
            }

            // Parse JSON body for all fields
            let jsonData;
            try {
                jsonData = jsonBody ? JSON.parse(jsonBody) : {};
            } catch (error) {
                setErrorMessage("Invalid JSON format in the JSON body.");
                setUploading(false);
                return;
            }

            const assetId = `i${generateUUID()}`;
            const databaseId = jsonData.databaseId || "";

            if (!databaseId) {
                setErrorMessage("Please provide a databaseId in the JSON body.");
                setUploading(false);
                return;
            }

            if (!jsonData.assetName) {
                setErrorMessage("Please provide an assetName in the JSON body.");
                setUploading(false);
                return;
            }

            if (!jsonData.description) {
                setErrorMessage("Please provide a description in the JSON body.");
                setUploading(false);
                return;
            }

            // Prepare request body for initialization
            requestBody = {
                databaseId,
                assetId,
                assetName: jsonData.assetName,
                description: jsonData.description,
                isDistributable:
                    jsonData.isDistributable !== undefined ? jsonData.isDistributable : true,
                tags: jsonData.tags || [],
                files: [
                    {
                        relativeKey: `${assetId}/${file.name}`,
                        file_size: file.size,
                    },
                ],
            };

            // Call the API to initialize the upload
            const response = await API.post("api", "/ingest-asset", {
                body: requestBody,
            });

            // Store upload ID and file responses for the next stage
            setUploadId(response.uploadId);
            setFileResponses(response.files);

            setStatusMessage("Upload initialized. Starting file upload...");

            // Start uploading file parts
            if (response.files && response.files.length > 0) {
                const fileResponse = response.files[0];
                const parts = await uploadFileParts(file, fileResponse);

                if (parts) {
                    // Complete the upload with the parts information
                    await completeUpload(
                        response.uploadId,
                        assetId,
                        databaseId,
                        fileResponse,
                        parts,
                        jsonData
                    );
                } else {
                    setStatusMessage("Failed to upload file parts.");
                    setUploading(false);
                }
            } else {
                setStatusMessage("No file information received from server.");
                setUploading(false);
            }
        } catch (error) {
            console.error("Error initializing upload:", error);
            setStatusMessage(`Error initializing upload: ${error}`);
            setUploading(false);
        }
    };

    let defaultPlaceholder = `{
    "databaseId": "<database-name>",
    "assetName": "<name of asset>",
    "description": "<Enter Asset description here>",
    "isDistributable": true,
    "tags": ["optional", "tags", "to", "add"]
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
                                    initializeUpload();
                                }}
                                loading={uploading}
                                disabled={!file}
                            >
                                {uploading ? "Uploading..." : "Upload Asset"}
                            </Button>
                        </FormField>
                    </Grid>
                    <Grid gridDefinition={[{ colspan: 3 }]}>
                        <FormField label="JSON Body - Asset Data">
                            <Textarea
                                onChange={({ detail }) => setJsonBody(detail.value)}
                                value={jsonBody || defaultPlaceholder}
                                placeholder={defaultPlaceholder}
                                rows={10}
                            />
                        </FormField>
                    </Grid>
                    <div style={{ marginTop: "1em" }}>{statusMessage}</div>
                </SpaceBetween>
            </Container>
        </Box>
    );
}

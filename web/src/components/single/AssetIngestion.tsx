import {
    Alert,
    FileUpload,
    FormField,
    Container,
    Button,
    Textarea,
    Header,
    Box,
    SpaceBetween,
    Grid,
    StatusIndicator,
} from "@cloudscape-design/components";
import { ingestAsset } from "../../services/APIService";
import AssetUploadService from "../../services/AssetUploadService";
import { useState } from "react";
import { generateUUID } from "../../common/utils/utils";
import { usePageTitle } from "../../hooks/usePageTitle";
import Synonyms from "../../synonyms";

// A part upload that has not answered in this long is reported as failed.
const PART_UPLOAD_TIMEOUT_MS = 300000;

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

// One file of the ingest initialize response (`files[]`)
interface FileUploadResponse {
    relativeKey: string;
    uploadIdS3: string;
    numParts: number;
    partUploadUrls: PartUploadUrl[];
}

// One file of the ingest complete response (`fileResults[]`)
interface FileCompletionResult {
    relativeKey?: string;
    success?: boolean;
    error?: string;
}

interface UploadedAsset {
    databaseId: string;
    assetId: string;
    assetName: string;
    message: string;
    largeFileAsynchronousHandling: boolean;
}

// What the page reports below the form: the step under way, the failure, or the uploaded asset.
type UploadStatus =
    | { kind: "progress"; text: string }
    | { kind: "error"; text: string }
    | { kind: "success"; asset: UploadedAsset };

/** The text of a thrown value, for a failure the page reports. */
function describeError(error: unknown): string {
    if (error instanceof Error && error.message) return error.message;
    if (typeof error === "string" && error) return error;
    return "An unexpected error occurred.";
}

/** `promise`, or a rejection once `ms` milliseconds pass without it settling. */
function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("the request timed out")), ms);
        promise.then(
            (value) => {
                clearTimeout(timer);
                resolve(value);
            },
            (error) => {
                clearTimeout(timer);
                reject(error);
            }
        );
    });
}

/** The file split into `numParts` contiguous parts, the last one taking the remainder. */
function splitFile(file: File, numParts: number): Blob[] {
    const parts: Blob[] = [];
    const partSize = Math.ceil(file.size / numParts);
    for (let i = 0; i < numParts; i++) {
        const start = i * partSize;
        parts.push(file.slice(start, Math.min(start + partSize, file.size)));
    }
    return parts;
}

export default function AssetIngestion() {
    usePageTitle(`${Synonyms.Asset} Ingestion`);
    const [file, setFile] = useState<File | null>(null);
    const [errorMessage, setErrorMessage] = useState("");
    const [jsonBody, setJsonBody] = useState("");
    const [uploading, setUploading] = useState(false);
    const [status, setStatus] = useState<UploadStatus | null>(null);

    const showProgress = (text: string) => setStatus({ kind: "progress", text });

    /** Upload every part of `file` to its presigned URL, in order; the parts with their ETags. */
    const uploadFileParts = async (
        file: File,
        fileResponse: FileUploadResponse
    ): Promise<UploadPart[]> => {
        const numParts = fileResponse.numParts;
        const blobs = splitFile(file, numParts);
        const parts: UploadPart[] = [];

        for (let i = 0; i < numParts; i++) {
            const partNumber = i + 1;
            const partUploadUrl = (fileResponse.partUploadUrls || []).find(
                (p) => p.PartNumber === partNumber
            );
            if (!partUploadUrl) {
                throw new Error(
                    `The server returned no upload address for part ${partNumber} of ${numParts}.`
                );
            }

            showProgress(`Uploading part ${partNumber} of ${numParts}...`);
            try {
                const etag = await withTimeout(
                    AssetUploadService.uploadPart(partUploadUrl.UploadUrl, blobs[i]),
                    PART_UPLOAD_TIMEOUT_MS
                );
                parts.push({ PartNumber: partNumber, ETag: etag });
            } catch (error) {
                throw new Error(
                    `Part ${partNumber} of ${numParts} could not be uploaded: ${describeError(
                        error
                    )}`
                );
            }
        }

        return parts;
    };

    /** Initialize the ingest, upload the file's parts, then complete the ingest. */
    const ingestFile = async () => {
        if (!file) {
            setErrorMessage("Please choose a file.");
            return;
        }

        // Parse JSON body for all fields
        let jsonData;
        try {
            jsonData = jsonBody ? JSON.parse(jsonBody) : {};
        } catch (error) {
            setErrorMessage("Invalid JSON format in the JSON body.");
            return;
        }

        const databaseId = jsonData.databaseId || "";
        if (!databaseId) {
            setErrorMessage("Please provide a databaseId in the JSON body.");
            return;
        }
        if (!jsonData.assetName) {
            setErrorMessage("Please provide an assetName in the JSON body.");
            return;
        }
        if (!jsonData.description) {
            setErrorMessage("Please provide a description in the JSON body.");
            return;
        }
        setErrorMessage("");

        const assetId = `i${generateUUID()}`;
        const relativeKey = `${assetId}/${file.name}`;
        const assetFields = {
            databaseId,
            assetId,
            assetName: jsonData.assetName,
            description: jsonData.description,
            isDistributable:
                jsonData.isDistributable !== undefined ? jsonData.isDistributable : true,
            tags: jsonData.tags || [],
        };

        setUploading(true);
        try {
            // Stage 1: initialize. The response names the upload and each file's part URLs.
            showProgress("Initializing upload...");
            const [initSuccess, initData] = (await ingestAsset({
                ...assetFields,
                files: [{ relativeKey, file_size: file.size }],
            })) as [boolean, any];
            if (!initSuccess) {
                throw new Error(
                    `The upload could not be started: ${initData || "no reason given"}`
                );
            }
            const files: FileUploadResponse[] = Array.isArray(initData?.files)
                ? initData.files
                : [];
            const fileResponse = files.find((f) => f.relativeKey === relativeKey) || files[0];
            if (!initData?.uploadId || !fileResponse) {
                throw new Error("The server did not return the upload details for the file.");
            }

            // Stage 2: upload the parts to their presigned URLs.
            const parts = await uploadFileParts(file, fileResponse);

            // Stage 3: complete. The call can succeed while a file is rejected, so the per-file
            // verdict in `overallSuccess` and `fileResults` decides whether the file was stored.
            showProgress(`Completing ${Synonyms.asset} upload...`);
            const [completeSuccess, completeData] = (await ingestAsset({
                ...assetFields,
                uploadId: initData.uploadId,
                files: [
                    {
                        relativeKey: fileResponse.relativeKey,
                        uploadIdS3: fileResponse.uploadIdS3,
                        parts,
                    },
                ],
            })) as [boolean, any];
            if (!completeSuccess) {
                throw new Error(
                    `The upload could not be completed: ${completeData || "no reason given"}`
                );
            }

            const rejected: FileCompletionResult[] = Array.isArray(completeData?.fileResults)
                ? completeData.fileResults.filter((r: FileCompletionResult) => r?.success === false)
                : [];
            if (completeData?.overallSuccess === false || rejected.length > 0) {
                const detail = rejected.length
                    ? rejected
                          .map((r) => `${r.relativeKey || "file"}: ${r.error || "no reason given"}`)
                          .join("; ")
                    : "the server reported the upload as unsuccessful";
                throw new Error(`Upload was rejected and is not stored — ${detail}`);
            }

            setStatus({
                kind: "success",
                asset: {
                    databaseId,
                    assetId,
                    assetName: jsonData.assetName,
                    message: typeof completeData?.message === "string" ? completeData.message : "",
                    largeFileAsynchronousHandling:
                        completeData?.largeFileAsynchronousHandling === true,
                },
            });
        } catch (error) {
            console.log("Error ingesting asset:", error);
            setStatus({ kind: "error", text: describeError(error) });
        } finally {
            setUploading(false);
        }
    };

    const defaultPlaceholder = `{
    "databaseId": "<database-name>",
    "assetName": "<name of asset>",
    "description": "<Enter Asset description here>",
    "isDistributable": true,
    "tags": ["optional", "tags", "to", "add"]
}`;

    return (
        <Box padding={{ top: "m", horizontal: "l" }}>
            <Container header={<Header variant="h2">{`${Synonyms.Asset} Ingestion`}</Header>}>
                <SpaceBetween size="l">
                    <Grid
                        gridDefinition={[
                            { colspan: { default: 12, s: 6, l: 4 } },
                            { colspan: { default: 12, s: 6, l: 3 } },
                        ]}
                    >
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
                        <FormField label={`Upload ${Synonyms.Asset}`}>
                            <Button
                                variant="primary"
                                onClick={() => {
                                    ingestFile();
                                }}
                                loading={uploading}
                                disabled={!file}
                            >
                                {uploading ? "Uploading..." : `Upload ${Synonyms.Asset}`}
                            </Button>
                        </FormField>
                    </Grid>
                    <Grid gridDefinition={[{ colspan: { default: 12, m: 8, l: 6 } }]}>
                        <FormField label={`JSON Body - ${Synonyms.Asset} Data`}>
                            <Textarea
                                onChange={({ detail }) => setJsonBody(detail.value)}
                                value={jsonBody || defaultPlaceholder}
                                placeholder={defaultPlaceholder}
                                rows={10}
                            />
                        </FormField>
                    </Grid>
                    <div aria-live="polite">
                        {status?.kind === "progress" && (
                            <StatusIndicator type="in-progress">{status.text}</StatusIndicator>
                        )}
                        {status?.kind === "error" && (
                            <Alert type="error" header="Upload failed">
                                <span style={{ whiteSpace: "pre-line", overflowWrap: "anywhere" }}>
                                    {status.text}
                                </span>
                            </Alert>
                        )}
                        {status?.kind === "success" && (
                            <Alert
                                type="success"
                                header={`${Synonyms.Asset} uploaded successfully`}
                            >
                                <SpaceBetween size="xxs">
                                    <Box>
                                        <strong>Database Id:</strong> {status.asset.databaseId}
                                    </Box>
                                    <Box>
                                        <strong>{Synonyms.Asset} Id:</strong> {status.asset.assetId}
                                    </Box>
                                    <Box>
                                        <strong>{Synonyms.Asset} Name:</strong>{" "}
                                        {status.asset.assetName}
                                    </Box>
                                    {status.asset.message && (
                                        <Box>
                                            <strong>Message:</strong> {status.asset.message}
                                        </Box>
                                    )}
                                    {status.asset.largeFileAsynchronousHandling && (
                                        <Box>
                                            Large files are still being processed and appear in the{" "}
                                            {Synonyms.asset} shortly.
                                        </Box>
                                    )}
                                </SpaceBetween>
                            </Alert>
                        )}
                    </div>
                </SpaceBetween>
            </Container>
            <div style={{ paddingBottom: "20px" }} />
        </Box>
    );
}

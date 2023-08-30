import {
    S3Client,
    GetObjectCommand,
    ListMultipartUploadsCommand,
    ListPartsCommand,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { Upload } from "@aws-sdk/lib-storage";
import { API } from "aws-amplify";

interface GetCredentials {
    assetId: string;
    databaseId: string;
}

async function getCredentials(assetIdentifiers: GetCredentials): Promise<any> {
    console.log("asset identifiers", assetIdentifiers);
    return API.post("api", "auth/scopeds3access", { body: assetIdentifiers });
}

function createS3Client(
    accessKeyId: string,
    secretAccessKey: string,
    sessionToken: string,
    region: string
): S3Client {
    return new S3Client({
        credentials: {
            accessKeyId: accessKeyId,
            secretAccessKey: secretAccessKey,
            sessionToken,
        },
        region: region,
    });
}

interface UploadParams {
    s3: S3Client;
    Bucket: string;
    Key: string;
    Body: Blob | Buffer | ReadableStream;
    metadata: { [p: string]: string };
}

class ProgressCallbackArgs {
    loaded!: number;
    total!: number;
}

async function uploadToS3(
    params: UploadParams,
    progressCallback: (index: number, progress: ProgressCallbackArgs) => void,
    completeCallback: (index: number, event: any) => void,
    errorCallback: (index: number, event: any) => void
): Promise<void> {
    const uploader = new Upload({
        client: params.s3,
        params: {
            Bucket: params.Bucket,
            Key: params.Key,
            Body: params.Body,
            Metadata: params.metadata,
        },
    });

    uploader.on("httpUploadProgress", (progressEvent) => {
        if (
            progressEvent &&
            progressEvent.loaded &&
            progressEvent.total &&
            progressEvent.part &&
            progressCallback !== undefined
        ) {
            progressCallback(progressEvent.part, {
                loaded: progressEvent.loaded,
                total: progressEvent.total,
            });
        }
    });

    try {
        const result = await uploader.done();
        completeCallback(0, result);
    } catch (e) {
        errorCallback(0, e);
    }
}

async function getOngoingUploadId(
    s3: S3Client,
    Bucket: string,
    Key: string
): Promise<string | null> {
    const command = new ListMultipartUploadsCommand({ Bucket: Bucket });
    const response = await s3.send(command);

    const ongoingUpload = response.Uploads?.find((upload) => upload.Key === Key);
    return ongoingUpload?.UploadId || null;
}

async function getUploadedParts(
    s3: S3Client,
    Bucket: string,
    Key: string,
    uploadId: string
): Promise<number[]> {
    const command = new ListPartsCommand({
        Bucket: Bucket,
        Key: Key,
        UploadId: uploadId,
    });

    const response = await s3.send(command);
    if (response.Parts) {
        return response.Parts.map((part) => part.PartNumber || 0);
    }
    return [];
}

/*
async function uploadToS3(params: UploadParams, onProgress: (progress: number) => void): Promise<void> {
    // Identify if there's an ongoing upload for the given key
    const uploadId = await getOngoingUploadId(params.s3, params.Bucket, params.Key);
    let uploadedParts: number[] = [];

    if (uploadId) {
        // If there's an ongoing upload, get the already uploaded parts
        uploadedParts = await getUploadedParts(params.s3, params.Bucket, params.Key, uploadId);
    }

    const uploader = new Upload({
        client: params.s3,
        params: {
            Bucket: params.Bucket,
            Key: params.Key,
            Body: params.Body
        },
        leavePartsOnError: true, // If set to true, it won't clean up the parts if the multipart upload fails.
        partSize: 5 * 1024 * 1024,
        queueSize: 4
    });

    uploader.on('httpUploadProgress', (progressEvent) => {
        const progress = (progressEvent.loaded / progressEvent.total) * 100;
        onProgress(progress);
    });

    // Skip already uploaded parts
    if (uploadedParts.length) {
        uploader['partNumbersToUpload'] = uploader['partNumbersToUpload'].filter((partNumber: number) => !uploadedParts.includes(partNumber));
    }

    await uploader.done();
}
*/

async function getS3ClientForAsset(
    assetId: string,
    databaseId: string
): Promise<{ s3Client: S3Client; region: string; bucket: string }> {
    const creds = await getCredentials({ assetId, databaseId });
    return {
        s3Client: createS3Client(
            creds.Credentials.AccessKeyId,
            creds.Credentials.SecretAccessKey,
            creds.Credentials.SessionToken,
            creds.region
        ),
        region: creds.region,
        bucket: creds.bucket,
    };
}

async function getPresignedKey(assetId: string, databaseId: string, key: string): Promise<string> {
    const { s3Client, bucket, region } = await getS3ClientForAsset(assetId, databaseId);

    // presign the key
    const command = new GetObjectCommand({
        Bucket: bucket,
        Key: key,
    });

    const url = await getSignedUrl(s3Client, command, { expiresIn: 3600 });
    return url;
}

async function getUploadTaskPromise(
    index: number,
    key: string,
    f: File,
    metadata: { [p: string]: string },
    progressCallback: (index: number, progress: ProgressCallbackArgs) => void,
    completeCallback: (index: number, event: any) => void,
    errorCallback: (index: number, event: any) => void
) {
    return new Promise(async (resolve, reject) => {
        const { s3Client, bucket } = await getS3ClientForAsset(
            metadata.assetId,
            metadata.databaseId
        );

        uploadToS3(
            { s3: s3Client, Bucket: bucket, Key: key, Body: f, metadata },

            (part, progress) => {
                console.log("progress", progress);
                progressCallback(index, {
                    loaded: progress.loaded,
                    total: progress.total,
                });
            },
            (part, event) => {
                console.log("complete", part, event);
                completeCallback(index, null);
                resolve(true);
            },
            (part, event) => {
                console.log("error", part, event);
                errorCallback(index, event);
                resolve(true);
            }
        );
    });
}

export { createS3Client, uploadToS3, getUploadTaskPromise, getPresignedKey };

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { Upload } from "@aws-sdk/lib-storage";
import { API } from "aws-amplify";

interface GetCredentials {
    assetId: string;
    databaseId: string;
}

async function getCredentials(assetIdentifiers: GetCredentials): Promise<any> {
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
    const { s3Client, bucket } = await getS3ClientForAsset(assetId, databaseId);

    // presign the key
    const command = new GetObjectCommand({
        Bucket: bucket,
        Key: key,
    });

    const url = await getSignedUrl(s3Client, command, { expiresIn: 900 });
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
                progressCallback(index, {
                    loaded: progress.loaded,
                    total: progress.total,
                });
            },
            (part, event) => {
                completeCallback(index, null);
                resolve(true);
            },
            (part, event) => {
                errorCallback(index, event);
                resolve(true);
            }
        );
    });
}

export { createS3Client, uploadToS3, getUploadTaskPromise, getPresignedKey };

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/*

This was derived in large part from Amplify's upload functionality. This
version was created to leverage @aws-sdk/client-s3 and a different
credentials model to support the authorization models in VAMS.

- Instead of using the embedded client inside of amplify, we use the
  @aws-sdk/client-s3 library. Functions to call the S3Client are found in
  this class.

- The credential model is to pass in a function to the S3Upload constructor
  that can be called to get fresh credentials. So it can be used with any
  source of credentials and not just a Cognito user pool as is the case with Amplify.

https://github.com/aws-amplify/amplify-js/blob/main/packages/storage/src/providers/AWSS3Provider.ts

*/

import {
    Part,
    UploadPartCommandInput,
    CompletedPart,
    ListPartsCommand,
    S3Client,
    ListPartsCommandInput,
    ListPartsCommandOutput,
    CompleteMultipartUploadCommandInput,
    CompleteMultipartUploadCommand,
    CompleteMultipartUploadCommandOutput,
    ListObjectsV2Command,
    ListObjectsV2CommandInput,
    ListObjectsV2CommandOutput,
    UploadPartCommand,
    UploadPartCommandOutput,
    CreateMultipartUploadCommandInput,
    CreateMultipartUploadCommand,
} from "@aws-sdk/client-s3";

import * as events from "events";
import { ICredentials } from "./types";

export enum AWSS3UploadTaskState {
    INIT,
    IN_PROGRESS,
    PAUSED,
    CANCELLED,
    COMPLETED,
}

export enum TaskEvents {
    CANCEL = "cancel",
    UPLOAD_COMPLETE = "uploadComplete",
    UPLOAD_PROGRESS = "uploadPartProgress",
    ERROR = "error",
}

export interface InProgressRequest {
    uploadPartInput: UploadPartCommandInput;
    s3Request: Promise<any>;
    abortController: AbortController;
}

type UploadTaskProgressEvent = {
    loaded: number;
    total: number;
    index?: number;
};

export interface UploadTaskCompleteEvent {
    key?: string;
}

export interface FileMetadata {
    bucket: string;
    fileName: string;
    key: string;
    // Unix timestamp in ms
    lastTouched: number;
    uploadId: string;
}

type CredentialsProvider = () => Promise<ICredentials>;

type PutObjectInput = {
    Bucket: string;
    Key: string;
    Body: File;
    Metadata: { [key: string]: string };
};

interface S3UploadProps {
    credentialsProvider: CredentialsProvider;
    region: string;
    params: PutObjectInput;
    storage: Storage;
    emitter?: events.EventEmitter;
}

const DEFAULT_QUEUE_SIZE = 4;
const DEFAULT_PART_SIZE = 5 * 1024 * 1024;

const UPLOADS_STORAGE_KEY = "@@upload";
const logger = console;

class S3Upload {
    private readonly emitter: events.EventEmitter;
    private readonly file: Blob;
    private readonly queueSize = DEFAULT_QUEUE_SIZE;
    private readonly storage: Storage;
    private readonly fileId: string;
    private readonly params: PutObjectInput;

    private readonly credentialsProvider: CredentialsProvider;
    private readonly region: string;

    private state = AWSS3UploadTaskState.INIT;

    private partSize: number = DEFAULT_PART_SIZE;
    private inProgress: InProgressRequest[] = [];
    private completedParts: CompletedPart[] = [];
    private queued: UploadPartCommandInput[] = [];
    private bytesUploaded: number = 0;
    private totalBytes: number = 0;
    private uploadId?: string | null;

    constructor({ credentialsProvider, region, params, storage, emitter }: S3UploadProps) {
        this.credentialsProvider = credentialsProvider;
        this.params = params;
        this.file = params.Body;
        this.totalBytes = this.file.size;
        this.fileId = this._getFileId();
        this.storage = storage;
        this.region = region;
        this.emitter = emitter || new events.EventEmitter();
    }

    public resume(): void {
        if (this.state === AWSS3UploadTaskState.CANCELLED) {
            logger.warn("This task has already been cancelled");
        } else if (this.state === AWSS3UploadTaskState.COMPLETED) {
            logger.warn("This task has already been completed");
        } else if (this.state === AWSS3UploadTaskState.IN_PROGRESS) {
            logger.warn("Upload task already in progress");
            // first time running resume, find any cached parts on s3 or start a new multipart upload request before
            // starting the upload
        } else if (!this.uploadId) {
            logger.debug("resuming upload");
            this._initializeUploadTask();
        } else {
            logger.debug("starting upload");
            this._startUpload();
        }
    }

    private async _initializeUploadTask() {
        this.state = AWSS3UploadTaskState.IN_PROGRESS;
        this.partSize = calculatePartSize(this.totalBytes);
        try {
            if (await this._isCached()) {
                const { parts, uploadId } = await this._findCachedUploadParts();
                this.uploadId = uploadId;
                this.queued = this._createParts();
                this._initCachedUploadParts(parts);
                if (this._isDone()) {
                    this._completeUpload();
                } else {
                    this._startUpload();
                }
            } else {
                if (!this.uploadId) {
                    const uploadId = await this._initMultipartUpload();
                    this.uploadId = uploadId;
                    this.queued = this._createParts();
                    this._startUpload();
                }
            }
        } catch (err) {
            if (!isCancelError(err)) {
                logger.error("Error initializing the upload task", err);
                this._emitEvent(TaskEvents.ERROR, err);
            }
        }
    }

    private async _initMultipartUpload() {
        const res = await this._createMultipartUpload(this.params);
        this._cache({
            uploadId: res.UploadId!,
            lastTouched: Date.now(),
            bucket: this.params.Bucket,
            key: this.params.Key,
            fileName: this.file instanceof File ? this.file.name : "",
        });
        return res.UploadId;
    }

    private _startUpload() {
        this.state = AWSS3UploadTaskState.IN_PROGRESS;
        for (let i = 0; i < this.queueSize; i++) {
            this._startNextPart();
        }
    }

    private _startNextPart() {
        if (this.queued.length > 0 && this.state !== AWSS3UploadTaskState.PAUSED) {
            const abortController = new AbortController();
            const nextPart = this.queued.shift();
            this.inProgress.push({
                uploadPartInput: nextPart!,
                s3Request: this._makeUploadPartRequest(nextPart!, abortController.signal),
                abortController,
            });
        }
    }

    private async _makeUploadPartRequest(input: UploadPartCommandInput, abortSignal: AbortSignal) {
        try {
            const res = await this._uploadPart(abortSignal, input);
            await this._onPartUploadCompletion({
                eTag: res.ETag!,
                partNumber: input.PartNumber!,
                chunk: input.Body,
            });
        } catch (err) {
            if (this.state === AWSS3UploadTaskState.PAUSED) {
                logger.log("upload paused");
            } else if (this.state === AWSS3UploadTaskState.CANCELLED) {
                logger.log("upload aborted");
            } else {
                logger.error("error starting next part of upload: ", err);
            }
            // xhr transfer handlers' cancel will also throw an error, however we don't need to emit an event in that case as it's an
            // expected behavior
            if (
                !isCancelError(err) &&
                err instanceof Error &&
                err.message !== CANCELED_ERROR_MESSAGE
            ) {
                this._emitEvent(TaskEvents.ERROR, err);
                this.pause();
            }
        }
    }

    private async _onPartUploadCompletion({
        eTag,
        partNumber,
        chunk,
    }: {
        eTag: string;
        partNumber: number;
        chunk: UploadPartCommandInput["Body"];
    }) {
        this.completedParts.push({
            ETag: eTag,
            PartNumber: partNumber,
        });
        this.bytesUploaded += byteLength(chunk);
        this._emitEvent<UploadTaskProgressEvent>(TaskEvents.UPLOAD_PROGRESS, {
            loaded: this.bytesUploaded,
            total: this.totalBytes,
        });
        // Remove the completed item from the inProgress array
        this.inProgress = this.inProgress.filter(
            (job) => job.uploadPartInput.PartNumber !== partNumber
        );
        if (this.queued.length && this.state !== AWSS3UploadTaskState.PAUSED) this._startNextPart();
        if (this._isDone()) this._completeUpload();
    }

    private _initCachedUploadParts(cachedParts: Part[]) {
        this.bytesUploaded += cachedParts.reduce((acc, part) => acc + part.Size!, 0);
        // Find the set of part numbers that have already been uploaded
        const uploadedPartNumSet = new Set(cachedParts.map((part) => part.PartNumber));
        this.queued = this.queued.filter((part) => !uploadedPartNumSet.has(part.PartNumber));
        this.completedParts = cachedParts.map((part) => ({
            PartNumber: part.PartNumber,
            ETag: part.ETag,
        }));
        this._emitEvent<UploadTaskProgressEvent>(TaskEvents.UPLOAD_PROGRESS, {
            loaded: this.bytesUploaded,
            total: this.totalBytes,
        });
    }

    private async _completeUpload() {
        try {
            await this._completeMultipartUpload({
                Bucket: this.params.Bucket,
                Key: this.params.Key,
                UploadId: this.uploadId!,
                MultipartUpload: {
                    // Parts are not always completed in order, we need to manually sort them
                    Parts: [...this.completedParts].sort(comparePartNumber),
                },
            });
            await this._verifyFileSize();
            this._emitEvent<UploadTaskCompleteEvent>(TaskEvents.UPLOAD_COMPLETE, {
                key: this.params.Key,
            });
            this._removeFromCache();
            this.state = AWSS3UploadTaskState.COMPLETED;
        } catch (err) {
            logger.error("error completing upload", err);
            this._emitEvent(TaskEvents.ERROR, err);
        }
    }

    /**
     * Verify on S3 side that the file size matches the one on the client side.
     *
     * @async
     * @throws throws an error if the file size does not match between local copy of the file and the file on s3.
     */
    private async _verifyFileSize() {
        let valid = false;
        const args = {
            Prefix: this.params.Key,
            Bucket: this.params.Bucket,
        };
        try {
            const resp = await this._listSingleFile(args);
            if (resp.Contents && resp.Contents.length > 0) {
                const obj = resp.Contents[0];
                valid = Boolean(obj && obj.Size === this.file.size);
            }
        } catch (e) {
            logger.log("Could not get file on s3 for size matching: ", e, args);
            // Don't gate verification on auth or other errors
            // unrelated to file size verification
            return;
        }

        if (!valid) {
            throw new Error("File size does not match between local file and file on s3");
        }
    }

    private _isDone() {
        return (
            !this.queued.length && !this.inProgress.length && this.bytesUploaded === this.totalBytes
        );
    }

    private _createParts() {
        const size = this.file.size;
        const parts: UploadPartCommandInput[] = [];
        for (let bodyStart = 0; bodyStart < size; ) {
            const bodyEnd = Math.min(bodyStart + this.partSize, size);
            parts.push({
                Body: this.file.slice(bodyStart, bodyEnd),
                Key: this.params.Key,
                Bucket: this.params.Bucket,
                PartNumber: parts.length + 1,
                UploadId: this.uploadId!,
            });
            bodyStart += this.partSize;
        }
        return parts;
    }

    private async _findCachedUploadParts(): Promise<{
        parts: Part[];
        uploadId: string | null;
    }> {
        const uploadRequests = await this._listCachedUploadTasks();

        if (
            Object.keys(uploadRequests).length === 0 ||
            !Object.prototype.hasOwnProperty.call(uploadRequests, this.fileId)
        ) {
            return { parts: [], uploadId: null };
        }

        const cachedUploadFileData = uploadRequests[this.fileId];
        cachedUploadFileData.lastTouched = Date.now();
        this.storage.setItem(UPLOADS_STORAGE_KEY, JSON.stringify(uploadRequests));

        const { Parts = [] } = await this._listParts({
            Bucket: this.params.Bucket,
            Key: this.params.Key,
            UploadId: cachedUploadFileData.uploadId,
        });

        return {
            parts: Parts,
            uploadId: cachedUploadFileData.uploadId,
        };
    }

    private async _s3Client(): Promise<S3Client> {
        const credentials = await this.credentialsProvider();

        const s3 = new S3Client({
            credentials,
            region: this.region,
        });

        return s3;
    }

    private async _createMultipartUpload(params: CreateMultipartUploadCommandInput) {
        const s3 = await this._s3Client();
        return s3.send(new CreateMultipartUploadCommand(params));
    }

    private async _uploadPart(
        abortSignal: AbortSignal,
        params: UploadPartCommandInput
    ): Promise<UploadPartCommandOutput> {
        const s3 = await this._s3Client();
        return s3.send(new UploadPartCommand(params), {
            abortSignal,
        });
    }

    private async _listSingleFile(
        params: ListObjectsV2CommandInput
    ): Promise<ListObjectsV2CommandOutput> {
        const s3 = await this._s3Client();
        const cmd = new ListObjectsV2Command(params);
        return s3.send(cmd);
    }

    private async _completeMultipartUpload(
        params: CompleteMultipartUploadCommandInput
    ): Promise<CompleteMultipartUploadCommandOutput> {
        const s3 = await this._s3Client();

        return s3.send(new CompleteMultipartUploadCommand(params));
    }

    private async _listParts(input: ListPartsCommandInput): Promise<ListPartsCommandOutput> {
        const s3 = await this._s3Client();

        const cmd = new ListPartsCommand({
            ...input,
        });

        return s3.send(cmd);
    }

    private async _listCachedUploadTasks(): Promise<Record<string, FileMetadata>> {
        // await this.storageSync;
        const tasks = this.storage.getItem(UPLOADS_STORAGE_KEY) || "{}";
        return JSON.parse(tasks);
    }

    private async _cache(fileMetadata: FileMetadata): Promise<void> {
        const uploadRequests = await this._listCachedUploadTasks();
        uploadRequests[this.fileId] = fileMetadata;
        this.storage.setItem(UPLOADS_STORAGE_KEY, JSON.stringify(uploadRequests));
    }

    private async _isCached(): Promise<boolean> {
        return Object.prototype.hasOwnProperty.call(
            await this._listCachedUploadTasks(),
            this.fileId
        );
    }

    private async _removeFromCache(): Promise<void> {
        const uploadRequests = await this._listCachedUploadTasks();
        delete uploadRequests[this.fileId];
        this.storage.setItem(UPLOADS_STORAGE_KEY, JSON.stringify(uploadRequests));
    }

    /**
     * pause this particular upload task
     **/
    public pause(): void {
        if (this.state === AWSS3UploadTaskState.CANCELLED) {
            logger.warn("This task has already been cancelled");
        } else if (this.state === AWSS3UploadTaskState.COMPLETED) {
            logger.warn("This task has already been completed");
        } else if (this.state === AWSS3UploadTaskState.PAUSED) {
            logger.warn("This task is already paused");
        }
        this.state = AWSS3UploadTaskState.PAUSED;
        // Abort the part request immediately
        // Add the inProgress parts back to pending
        const removedInProgressReq = this.inProgress.splice(0, this.inProgress.length);
        removedInProgressReq.forEach((req) => {
            req.abortController.abort();
        });
        // Put all removed in progress parts back into the queue
        this.queued.unshift(...removedInProgressReq.map((req) => req.uploadPartInput));
    }

    private _getFileId(): string {
        // We should check if it's a File first because File is also instance of a Blob
        if (isFile(this.file)) {
            return [
                this.file.name,
                this.file.lastModified,
                this.file.size,
                this.file.type,
                this.params.Bucket,
                this.params.Key,
            ].join("-");
        } else {
            return [this.file.size, this.file.type, this.params.Bucket, this.params.Key].join("-");
        }
    }

    private _emitEvent<T = any>(event: string, payload: T) {
        this.emitter.emit(event, payload);
    }
}

export const isFile = (x: unknown): x is File => {
    return typeof x !== "undefined" && x instanceof File;
};

export const isBlob = (x: unknown): x is Blob => {
    return typeof x !== "undefined" && x instanceof Blob;
};

const isArrayBuffer = (x: unknown): x is ArrayBuffer => {
    return typeof x !== "undefined" && x instanceof ArrayBuffer;
};

function comparePartNumber(a: CompletedPart, b: CompletedPart) {
    return a.PartNumber! - b.PartNumber!;
}
export const MAX_PARTS_COUNT = 10000;
export const calculatePartSize = (totalSize: number): number => {
    let partSize = DEFAULT_PART_SIZE;
    let partsCount = Math.ceil(totalSize / partSize);
    while (partsCount > MAX_PARTS_COUNT) {
        partSize *= 2;
        partsCount = Math.ceil(totalSize / partSize);
    }
    return partSize;
};

export const byteLength = (x: unknown) => {
    if (typeof x === "string") {
        return x.length;
    } else if (isArrayBuffer(x)) {
        return x.byteLength;
    } else if (isBlob(x)) {
        return x.size;
    } else {
        throw new Error("Cannot determine byte length of " + x);
    }
};

export const isCancelError = (error: any): boolean => !!error?.["__CANCEL__"];

export const CANCELED_ERROR_MESSAGE = "canceled";

export default S3Upload;

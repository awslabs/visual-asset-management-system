/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import events from "events";
import S3Upload, { TaskEvents } from "./upload";
import AssetCredentials from "./credentials";

import { Cache } from "aws-amplify";
import { GetCredentials } from "./types";

class ProgressCallbackArgs {
    loaded!: number;
    total!: number;
}

export default async function getUploadTaskPromise(
    index: number,
    key: string,
    f: File,
    metadata: { [p: string]: string } & GetCredentials,
    progressCallback: (index: number, progress: ProgressCallbackArgs) => void,
    completeCallback: (index: number, event: any) => void,
    errorCallback: (index: number, event: any) => void
): Promise<void> {
    const creds = new AssetCredentials(metadata);

    const config = Cache.getItem("config");

    const em = new events.EventEmitter();
    const uploadTask = new S3Upload({
        credentialsProvider: creds.getCredentials.bind(creds),
        params: {
            Body: f,
            Bucket: config.bucket,
            Key: key,
            Metadata: metadata,
        },
        region: config.region,
        storage: localStorage,
        emitter: em,
    });

    em.on(TaskEvents.UPLOAD_PROGRESS, (progress) => {
        progressCallback(index, progress);
    });
    em.on(TaskEvents.UPLOAD_COMPLETE, () => {
        completeCallback(index, {});
    });
    em.on(TaskEvents.ERROR, (error) => {
        errorCallback(index, error);
    });

    uploadTask.resume();
}

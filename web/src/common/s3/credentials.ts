/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";
import { ICredentials, GetCredentials } from "./types";

class UnableToRetrieveCredentialsError extends Error {
    constructor(message: string) {
        super(message);
        this.name = "UnableToRetrieveCredentialsError";
    }
}

interface ScopedS3Response {
    Credentials: {
        AccessKeyId: string;
        SecretAccessKey: string;
        SessionToken: string;
        Expiration: string;
    };
    region: string;
    bucket: string;
    [key: string]: any;
}

class AssetCredentials {
    private credentials: ICredentials | null;
    private identifiers: GetCredentials;

    constructor(identifiers: GetCredentials) {
        this.credentials = null;
        this.identifiers = identifiers;
    }

    async getCredentials(): Promise<ICredentials> {
        if (
            this.credentials &&
            this.credentials.expiration &&
            this.credentials.expiration.getTime() > Date.now() + 60000
        ) {
            return this.credentials;
        }

        const resp: ScopedS3Response = await API.post("api", "auth/scopeds3access", {
            body: this.identifiers,
        });

        if (!resp.Credentials) {
            throw new UnableToRetrieveCredentialsError("No credentials returned from API");
        }
        this.credentials = {
            accessKeyId: resp.Credentials.AccessKeyId,
            secretAccessKey: resp.Credentials.SecretAccessKey,
            sessionToken: resp.Credentials.SessionToken,
            expiration: new Date(resp.Credentials.Expiration),
            authenticated: true,
            identityId: resp.AssumedRoleUser.AssumedRoleId,
        };

        return this.credentials;
    }
}

export default AssetCredentials;

/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

interface AxiosError extends Error {
    response: {
        config: unknown;
        data: string | Record<string, unknown>;
        headers: Record<string, string>;
        request: XMLHttpRequest;
        status: number;
        statusText: string;
    };
}

/**
 * Amplify uses Axios under the hood to power it's API client, but it doesn't
 * pass through Axios's error types, and attempting to type-narrow just based
 * on properties of the object makes Webpack complain. This type-assertion
 * gives us type-safety and satisfies Webpack.
 * @param e a possible `AxiosError`
 * @returns true if `e` is an `AxiosError`
 */
export function isAxiosError(e: unknown): e is AxiosError {
    return e instanceof Error && "response" in e;
}

/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

declare interface WebpackContext {
    (id: string): any;
    keys(): string[];
    resolve(id: string): string;
    id: string;
}

declare interface NodeRequire {
    context(
        directory: string,
        useSubdirectories?: boolean,
        regExp?: RegExp,
        mode?: "sync" | "eager" | "weak" | "lazy" | "lazy-once"
    ): WebpackContext;
}

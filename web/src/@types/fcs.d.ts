/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

declare module "fcs" {
    interface FCSData {
        dataAsStrings?: string[];
        text?: { [key: string]: string };
    }

    class FCS {
        dataAsStrings?: string[];
        text?: { [key: string]: string };

        constructor(options: any, buffer: Buffer);
    }

    export = FCS;
}

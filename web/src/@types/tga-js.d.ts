/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** `tga-js` ships no type declarations; only the surface the TGA/TIFF viewer uses is declared. */
declare module "tga-js" {
    interface TgaHeader {
        idLength: number;
        colorMapType: number;
        imageType: number;
        colorMapLength: number;
        colorMapDepth: number;
        width: number;
        height: number;
        pixelDepth: number;
        flags: number;
    }

    interface TgaImageData {
        width: number;
        height: number;
        data: Uint8ClampedArray;
    }

    export default class TGA {
        header: TgaHeader;
        /** Parses the file; throws on a header it cannot read. */
        load(data: Uint8Array): void;
        /**
         * Fills `target` with RGBA pixels. Without a target the library creates a canvas through
         * `document`, so callers always pass one.
         */
        getImageData(target: TgaImageData): TgaImageData;
    }
}

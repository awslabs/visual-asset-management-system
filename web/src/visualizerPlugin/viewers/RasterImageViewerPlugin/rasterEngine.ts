/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The decode steps of the TIFF/TGA viewer behind one small interface, so the same code runs inside
 * the decode worker (`rasterWorker.ts`) and, where a worker cannot be created, on the page
 * (`rasterSession.ts`). No React and no `Worker` here.
 */

import type { PakoModule, TgaConstructor, UtifModule } from "./dependencies";
import {
    DecodedRaster,
    RasterKind,
    RasterLimits,
    TiffDocument,
    decodeTga,
    decodeTiffPage,
    inspectTiff,
    orientationSwapsAxes,
    readTgaHeader,
} from "./rasterDecode";

export interface RasterLibs {
    utif: UtifModule;
    tga: TgaConstructor;
    pako: PakoModule;
}

/** One image, or one TIFF page, as it is shown (orientation applied to the size). */
export interface PageSummary {
    index: number;
    width: number;
    height: number;
}

export class RasterEngine {
    private kind: RasterKind = "tiff";
    private limits!: RasterLimits;
    private bytes: Uint8Array | null = null;
    private doc: TiffDocument | null = null;

    constructor(private readonly libs: RasterLibs) {}

    /** Reads the file's header or directories (no pixels) and lists what can be shown. */
    open(buffer: ArrayBuffer, kind: RasterKind, limits: RasterLimits): PageSummary[] {
        this.kind = kind;
        this.limits = limits;
        if (kind === "tga") {
            this.bytes = new Uint8Array(buffer);
            const header = readTgaHeader(this.bytes);
            return [{ index: 0, width: header.width, height: header.height }];
        }
        this.doc = inspectTiff(buffer);
        return this.doc.pages.map((page) => {
            const swap = orientationSwapsAxes(page.ifd.t274?.[0]);
            return {
                index: page.index,
                width: swap ? page.height : page.width,
                height: swap ? page.width : page.height,
            };
        });
    }

    decode(index: number): DecodedRaster {
        if (this.kind === "tga") {
            return decodeTga(this.bytes!, this.libs.tga, this.limits);
        }
        return decodeTiffPage(this.libs.utif, this.libs.pako, this.doc!, index, this.limits);
    }
}

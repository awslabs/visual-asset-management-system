/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAMS-authored data source for the robotics viewer build, copied into the Lichtblick clone by
 * web/customInstalls/robotics/roboticsViewerInstall.js. Built from upstream's public classes.
 *
 * Opens the formats the upstream remote-file source cannot stream, ROS 2 .db3 and PX4 .ulg/.ulog,
 * by downloading each presigned URL whole and handing the files to the upstream worker that
 * otherwise reads a local file. A ROS 2 split bag is several .db3 files opened together. The
 * selection's total size is limited (ds.maxBytes, 1 GiB by default) and checked against the
 * responses' Content-Length before any body is read.
 *
 * Deep link: ?ds=vams-download&ds.maxBytes=<bytes>&ds.url=<url>[&ds.url=<url>...]
 */

import type {
    DataSourceFactoryInitializeArgs,
    IDataSourceFactory,
} from "@lichtblick/suite-base/context/PlayerSelectionContext";
import {
    IterablePlayer,
    WorkerIterableSource,
} from "@lichtblick/suite-base/players/IterablePlayer";
import type {
    Initialization,
    IterableSourceInitializeArgs,
} from "@lichtblick/suite-base/players/IterablePlayer/IIterableSource";
import { WorkerSerializedIterableSource } from "@lichtblick/suite-base/players/IterablePlayer/WorkerSerializedIterableSource";
import type { Player } from "@lichtblick/suite-base/players/types";

import { withScrubbedErrors } from "./ScrubbedIterableSource";
import {
    downloadWholeFiles,
    extensionOf,
    fileNameFromUrl,
    parseMaxBytes,
    takeSignedUrls,
} from "./vamsSourceSafety";

type DownloadKind = "db3" | "ulog";

function initDb3Worker(): Worker {
    return new Worker(
        // foxglove-depcheck-used: babel-plugin-transform-import-meta
        new URL(
            "@lichtblick/suite-base/players/IterablePlayer/rosdb3/RosDb3IterableSourceWorker.worker",
            import.meta.url
        )
    );
}

function initUlogWorker(): Worker {
    return new Worker(
        // foxglove-depcheck-used: babel-plugin-transform-import-meta
        new URL(
            "@lichtblick/suite-base/players/IterablePlayer/ulog/UlogIterableSourceWorker.worker",
            import.meta.url
        )
    );
}

function kindOf(name: string): DownloadKind {
    const extension = extensionOf(name);
    if (extension === ".db3") {
        return "db3";
    }
    if (extension === ".ulg" || extension === ".ulog") {
        return "ulog";
    }
    throw new Error(`${name} is not a .db3, .ulg or .ulog file.`);
}

/** Downloads the selection once; a failed download may be retried. */
class WholeFileDownload {
    readonly #urls: string[];
    readonly #names: string[];
    readonly #maxBytes: number;
    #files?: Promise<File[]>;

    public constructor(urls: string[], names: string[], maxBytes: number) {
        this.#urls = urls;
        this.#names = names;
        this.#maxBytes = maxBytes;
    }

    public async files(): Promise<File[]> {
        this.#files ??= downloadWholeFiles(
            this.#urls,
            this.#names,
            this.#maxBytes,
            async (url, init) => await fetch(url, init)
        ).then(
            (downloaded) => downloaded.map((file) => new File(file.parts as BlobPart[], file.name)),
            (error: unknown) => {
                this.#files = undefined;
                throw error;
            }
        );
        return await this.#files;
    }
}

// The worker sources keep the initArgs object they are constructed with and read it when
// initialize() starts the worker, so the downloaded files are filled in just before that.

/** ROS 2 .db3 (one file, or the files of a split bag): the upstream serialized db3 worker. */
class DownloadedDb3Source extends WorkerSerializedIterableSource {
    readonly #download: WholeFileDownload;
    readonly #initArgs: IterableSourceInitializeArgs;

    public constructor(download: WholeFileDownload) {
        const initArgs: IterableSourceInitializeArgs = {};
        super({ initWorker: initDb3Worker, initArgs });
        this.#download = download;
        this.#initArgs = initArgs;
    }

    public override async initialize(): Promise<Initialization> {
        this.#initArgs.files = await this.#download.files();
        return await super.initialize();
    }
}

/** PX4 ULog (one file): the upstream deserialized ulog worker. */
class DownloadedUlogSource extends WorkerIterableSource {
    readonly #download: WholeFileDownload;
    readonly #initArgs: IterableSourceInitializeArgs;

    public constructor(download: WholeFileDownload) {
        const initArgs: IterableSourceInitializeArgs = {};
        super({ initWorker: initUlogWorker, initArgs });
        this.#download = download;
        this.#initArgs = initArgs;
    }

    public override async initialize(): Promise<Initialization> {
        this.#initArgs.file = (await this.#download.files())[0];
        return await super.initialize();
    }
}

export class VamsDownloadDataSourceFactory implements IDataSourceFactory {
    public id = "vams-download";
    public type: IDataSourceFactory["type"] = "connection";
    public displayName = "VAMS file";
    public iconName: IDataSourceFactory["iconName"] = "FileASPX";
    public supportedFileTypes = [".db3", ".ulg", ".ulog"];
    // Opened only by deep link from VAMS; not offered in the app's "Open connection" dialog.
    public hidden = true;

    public initialize(args: DataSourceFactoryInitializeArgs): Player | undefined {
        const maxBytes = parseMaxBytes(args.params?.maxBytes);
        const urls = takeSignedUrls(args.params);
        if (urls.length === 0) {
            return undefined;
        }
        const names = urls.map(fileNameFromUrl);
        const kind = kindOf(names[0] ?? "");
        for (const name of names) {
            if (kindOf(name) !== kind) {
                throw new Error("All selected files must be of the same type.");
            }
        }
        if (kind === "ulog" && urls.length > 1) {
            throw new Error("A PX4 ULog opens one file at a time.");
        }

        const download = new WholeFileDownload(urls, names, maxBytes);
        const name = names.join(", ");
        // No urlParams: see VamsRemoteDataSourceFactory.
        if (kind === "db3") {
            return new IterablePlayer({
                source: withScrubbedErrors(new DownloadedDb3Source(download), urls, names),
                name,
                metricsCollector: args.metricsCollector,
                sourceId: this.id,
                readAheadDuration: { sec: 120, nsec: 0 },
            });
        }
        return new IterablePlayer({
            source: withScrubbedErrors(new DownloadedUlogSource(download), urls, names),
            name,
            metricsCollector: args.metricsCollector,
            sourceId: this.id,
        });
    }
}

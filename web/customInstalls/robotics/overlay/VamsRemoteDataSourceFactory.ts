/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAMS-authored data source for the robotics viewer build, copied into the Lichtblick clone by
 * web/customInstalls/robotics/roboticsViewerInstall.js. Built from upstream's public classes.
 *
 * Opens .mcap (one or more files, merged on one timeline) and ROS 1 .bag (one file) from presigned
 * URLs, reading them with HTTP Range requests through the upstream remote-file workers, as the
 * upstream "Remote file" source does. Unlike that source it names the player by the files' names
 * (the upstream source names it by the URLs, which the app bar then shows), keeps the URLs out of
 * the address bar and of the saved recent sources, and scrubs them from error text.
 *
 * Deep link: ?ds=vams-remote&ds.url=<url>[&ds.url=<url>...]
 */

import type {
    DataSourceFactoryInitializeArgs,
    IDataSourceFactory,
} from "@lichtblick/suite-base/context/PlayerSelectionContext";
import { IterablePlayer } from "@lichtblick/suite-base/players/IterablePlayer";
import { WorkerSerializedIterableSource } from "@lichtblick/suite-base/players/IterablePlayer/WorkerSerializedIterableSource";
import { expandVideoSeekBackfill } from "@lichtblick/suite-base/players/IterablePlayer/videoSeekBackfill";
import type { Player } from "@lichtblick/suite-base/players/types";

import { withScrubbedErrors } from "./ScrubbedIterableSource";
import { extensionOf, fileNameFromUrl, takeSignedUrls } from "./vamsSourceSafety";

type RemoteExtension = ".bag" | ".mcap";

const initWorkers: Record<RemoteExtension, () => Worker> = {
    ".bag": () =>
        new Worker(
            // foxglove-depcheck-used: babel-plugin-transform-import-meta
            new URL(
                "@lichtblick/suite-base/players/IterablePlayer/BagIterableSourceWorker.worker",
                import.meta.url
            )
        ),
    ".mcap": () =>
        new Worker(
            // foxglove-depcheck-used: babel-plugin-transform-import-meta
            new URL(
                "@lichtblick/suite-base/players/IterablePlayer/Mcap/McapIterableSourceWorker.worker",
                import.meta.url
            )
        ),
};

function remoteExtension(name: string): RemoteExtension {
    const extension = extensionOf(name);
    if (extension === ".bag" || extension === ".mcap") {
        return extension;
    }
    throw new Error(`${name} is not an .mcap or .bag file.`);
}

export class VamsRemoteDataSourceFactory implements IDataSourceFactory {
    public id = "vams-remote";
    public type: IDataSourceFactory["type"] = "connection";
    public displayName = "VAMS file";
    public iconName: IDataSourceFactory["iconName"] = "FileASPX";
    public supportedFileTypes = [".mcap", ".bag"];
    // Opened only by deep link from VAMS; not offered in the app's "Open connection" dialog.
    public hidden = true;

    public initialize(args: DataSourceFactoryInitializeArgs): Player | undefined {
        const urls = takeSignedUrls(args.params);
        if (urls.length === 0) {
            return undefined;
        }
        const names = urls.map(fileNameFromUrl);
        const extension = remoteExtension(names[0] ?? "");
        for (const name of names) {
            if (remoteExtension(name) !== extension) {
                throw new Error("All selected files must be of the same type.");
            }
        }
        if (extension === ".bag" && urls.length > 1) {
            throw new Error("A ROS 1 bag opens one file at a time.");
        }

        const initArgs = urls.length === 1 ? { url: urls[0] } : { urls };
        const source = withScrubbedErrors(
            new WorkerSerializedIterableSource({ initWorker: initWorkers[extension], initArgs }),
            urls,
            names
        );

        // No urlParams: the app writes a player's urlParams back into the address bar, and with
        // none it removes the ds.* parameters (the signed URLs) once the player reports its state.
        return new IterablePlayer({
            source,
            name: names.join(", "),
            metricsCollector: args.metricsCollector,
            sourceId: this.id,
            readAheadDuration: { sec: 10, nsec: 0 },
            expandBackfill: expandVideoSeekBackfill,
        });
    }
}

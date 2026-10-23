/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAMS-authored entrypoint for the robotics viewer build, copied into the Lichtblick clone by
 * web/customInstalls/robotics/roboticsViewerInstall.js. VAMS opens this app only by deep link, so
 * it registers just the two VAMS data sources: VamsRemoteDataSourceFactory (.mcap and ROS 1 .bag,
 * read with HTTP Range requests) and VamsDownloadDataSourceFactory (ROS 2 .db3 and PX4 .ulg/.ulog,
 * downloaded whole). Live connections (WebSocket, rosbridge), local files and the sample dataset
 * are left out.
 *
 * VAMS hands the presigned URLs over in the URL fragment, which no request carries. The app reads
 * its deep link from the query string when its root first renders, so:
 *  1. the fragment is moved into the query inside the callback the upstream main() calls after it
 *     has awaited the app's fonts (a CSS font request sends the document URL as its Referer
 *     whatever the page's referrer policy) and before the root renders;
 *  2. once the root has rendered, ForgetDeepLink replaces the document URL with one that carries
 *     no signed URL. The app's address-bar sync later rewrites the URL with decodeURIComponent
 *     applied to the whole of it, which would split each signed URL's own query parameters
 *     (X-Amz-Signature, X-Amz-Credential, X-Amz-Security-Token) into the page's query, where they
 *     would stay for the life of the page.
 * `openIn=web` keeps the app from offering to hand the link to a desktop app.
 */

import { useLayoutEffect } from "react";

import { main } from "@lichtblick/suite-web";

import { VamsDownloadDataSourceFactory } from "./VamsDownloadDataSourceFactory";
import { VamsRemoteDataSourceFactory } from "./VamsRemoteDataSourceFactory";

/** The document URL without the deep link, set when the deep link is moved into the query. */
let urlWithoutDeepLink: string | undefined;

function moveFragmentDeepLinkToQuery(): void {
    const fragment = window.location.hash.slice(1);
    if (!new URLSearchParams(fragment).has("ds")) {
        return;
    }
    urlWithoutDeepLink = `${window.location.pathname}?openIn=web`;
    window.history.replaceState(null, "", `${window.location.pathname}?${fragment}&openIn=web`);
}

/** Mounted inside the app's providers: runs after the root has rendered and read its deep link. */
function ForgetDeepLink(props: React.PropsWithChildren): React.JSX.Element {
    useLayoutEffect(() => {
        if (urlWithoutDeepLink != undefined) {
            window.history.replaceState(null, "", urlWithoutDeepLink);
            urlWithoutDeepLink = undefined;
        }
    }, []);
    return <>{props.children}</>;
}

void main(async () => {
    moveFragmentDeepLinkToQuery();
    return {
        dataSources: [new VamsRemoteDataSourceFactory(), new VamsDownloadDataSourceFactory()],
        extraProviders: [<ForgetDeepLink key="vams-forget-deep-link" />],
    };
});

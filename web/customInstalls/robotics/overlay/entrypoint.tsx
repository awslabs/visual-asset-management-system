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
 * No extension can be installed or loaded. Upstream's WebRoot always adds two IndexedDB extension
 * loaders, and an installed extension runs as code in this document, which is same-origin with
 * VAMS. So the entrypoint renders upstream's SharedRoot itself, as WebRoot would but with an empty
 * extension loader list: a dropped .foxe file installs nothing (the app reports that no extension
 * loader exists), an extension stored by an earlier build is never read, and the extension
 * databases are never opened. Settings > Extensions says extensions are unavailable instead of
 * listing the marketplace. User Scripts are not affected: they run in a worker.
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

import Alert from "@mui/material/Alert";
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

/** Shown in Settings > Extensions in place of the extension list and the marketplace. */
function ExtensionsUnavailable(): React.JSX.Element {
    return (
        <Alert severity="info" data-testid="vams-extensions-unavailable">
            Extensions are not available in the VAMS Robotics Data Viewer.
        </Alert>
    );
}

void main(async () => {
    moveFragmentDeepLinkToQuery();
    // Both modules are already loaded by the time main() calls this; importing them here keeps
    // them out of the entry chunk, as upstream's lazily imported WebRoot does.
    const [suiteBase, appConfigurationModule] = await Promise.all([
        import("@lichtblick/suite-base"),
        import("@lichtblick/suite-web/src/services/LocalStorageAppConfiguration"),
    ]);
    const { AppContext, AppSetting, SharedRoot, StudioApp } = suiteBase;
    const LocalStorageAppConfiguration = appConfigurationModule.default;

    // What upstream's WebRoot renders, minus its extension loaders, its default data sources and
    // its `layout` / `workspace` query parameters (the VAMS deep link sets neither).
    return {
        rootElement: (
            <SharedRoot
                enableLaunchPreferenceScreen
                deepLinks={[window.location.href]}
                dataSources={[
                    new VamsRemoteDataSourceFactory(),
                    new VamsDownloadDataSourceFactory(),
                ]}
                appConfiguration={
                    new LocalStorageAppConfiguration({
                        defaults: { [AppSetting.SHOW_DEBUG_PANELS]: false },
                    })
                }
                appParameters={{}}
                extensionLoaders={[]}
                enableGlobalCss
                extraProviders={[
                    <AppContext.Provider
                        key="vams-app-context"
                        value={{
                            wrapPlayer: (player) => player,
                            extensionSettings: <ExtensionsUnavailable />,
                        }}
                    />,
                    <ForgetDeepLink key="vams-forget-deep-link" />,
                ]}
            >
                <StudioApp />
            </SharedRoot>
        ),
    };
});

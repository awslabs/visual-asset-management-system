/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as Config from "../../../config/config";

/** CSP directive sources the enabled add-ons contribute to the web distribution policy. */
export interface AddonCspSources {
    connectSrc: string[];
    frameSrc: string[];
    /** Sources merged into `script-src`. */
    scriptSrc: string[];
}

/**
 * CSP sources for the enabled add-ons, merged into the base policy by
 * `generateContentSecurityPolicy()` (`lib/helper/security.ts`).
 */
export function addonCspSources(config: Config.Config): AddonCspSources {
    const sources: AddonCspSources = { connectSrc: [], frameSrc: [], scriptSrc: [] };

    // When the Physna add-on is enabled the viewer plugin embeds Physna's
    // hosted viewer URL directly in an `<iframe src>`. Allow that origin
    // in `frame-src` so the iframe isn't blocked by CSP, and in
    // `connect-src` so any auxiliary fetches the VAMS frontend makes
    // against Physna also pass. We add only the origin portion (not the
    // full config URL, which may include a path) because CSP source
    // expressions match on scheme + host + port.
    if (
        config.app.addons?.usePhysnaSync?.enabled &&
        config.app.addons.usePhysnaSync.apiBaseEndpoint
    ) {
        try {
            const physnaUrl = new URL(config.app.addons.usePhysnaSync.apiBaseEndpoint);
            const origin = `${physnaUrl.protocol}//${physnaUrl.host}`;
            sources.connectSrc.push(origin);
            sources.frameSrc.push(origin);
        } catch {
            // Config validation in getConfig() already rejects invalid URLs,
            // so this is defensive — never raise during CSP generation.
        }

        // The add-on relaxes no script-src source. The viewer's `<iframe src>`
        // is Physna's own HTTPS origin, so that document loads under Physna's
        // CSP and its inline scripts are outside this policy's reach — the
        // frame-src and connect-src origins above are all it needs from here.
        // `'unsafe-inline'` would have no effect on it, and none on a VAMS page
        // either: a source list that carries a hash source never allows all
        // inline script, so browsers ignore the keyword wherever it appears.
    }

    // The Miris web SDK streams from *.miris.com over HTTPS and WebSocket. The
    // Miris API base URL is called only by the add-on's Lambdas, so it is not a
    // browser source. Two script sources serve SDK internals and can be removed
    // once an SDK release drops them: `blob:`, because the SDK imports its engine
    // glue from a blob: URL, and `'unsafe-eval'`, because the SDK's generated
    // bindings create call wrappers with `new Function`. The latter comes from
    // app.webUi.allowUnsafeEvalFeatures, which getConfig() requires with the add-on.
    if (config.app.addons?.useMirisStreaming?.enabled) {
        const mirisHosts = "*.miris.com";
        sources.connectSrc.push(`https://${mirisHosts}`, `wss://${mirisHosts}`);
        sources.scriptSrc.push("blob:");
    }

    return sources;
}

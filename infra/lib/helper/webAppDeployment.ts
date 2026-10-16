/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import { Construct } from "constructs";
import * as Config from "../../config/config";

// The /tmp budget of the CDK BucketDeployment Lambda that uploads the built web bundle. That function
// downloads the bundle archive into /tmp and expands it there, holding the archive and the extracted tree
// at once — roughly twice the size of web/dist, which is ~255 MB (about half of it viewer plugins) and so
// already near the 512 MiB Lambda default. 4 GiB carries several times the current bundle and needs
// raising only once web/dist grows past ~2 GB (10 GiB is the Lambda maximum); ephemeral storage is billed
// per GB-second of the function's own runtime, so one upload per deployment costs a fraction of a cent.
export const WEB_DEPLOYMENT_EPHEMERAL_STORAGE = cdk.Size.gibibytes(4);

// Content-Type for the bundle's `.mjs` files, which the other deployments cannot set.
//
// `BucketDeployment` applies `contentType` to EVERY object in that deployment, so the extension can only
// be handled by a deployment of its own. Left to the default, the uploader derives the type from the
// extension using Python's `mimetypes`, which has no `.mjs` entry and falls back to `text/plain`. Browsers
// enforce strict MIME checking on module scripts and the bucket also sends `X-Content-Type-Options:
// nosniff`, so such a file is refused with "Failed to load module script" however valid its contents.
//
// `text/javascript` is the type the HTML spec prescribes for JavaScript. The ALB path is affected exactly
// as the CloudFront one is: the ALB forwards to an S3 VPC endpoint, so the object's stored Content-Type is
// what reaches the browser either way.
export const ES_MODULE_CONTENT_TYPE = "text/javascript";

/** Prefix of the Vite build output whose file names carry a content hash (`build.assetsDir`). */
export const WEB_HASHED_ASSETS_PREFIX = "assets/";

/**
 * `Cache-Control` for content-hashed build output. A changed file gets a new name, so a stored copy can
 * never be stale and the browser need not revalidate it.
 */
export const WEB_HASHED_ASSET_CACHE_CONTROL: s3deployment.CacheControl[] = [
    s3deployment.CacheControl.setPublic(),
    s3deployment.CacheControl.maxAge(cdk.Duration.days(365)),
    s3deployment.CacheControl.immutable(),
];

/**
 * `Cache-Control` for files whose name stays the same between releases: `index.html`, the service
 * worker, icons and manifests, and the vendored viewer bundles under `viewers/`. Browsers revalidate on
 * every use (`max-age=0, must-revalidate`, answered with a 304 when unchanged), so a new release is picked
 * up on the next load. `s-maxage` lets CloudFront keep its edge copy for the cache policy's default hour;
 * each web deployment invalidates `/*`, so the edge does not serve a previous release either. The ALB path
 * has no shared cache and ignores it.
 */
export const WEB_REVALIDATE_CACHE_CONTROL: s3deployment.CacheControl[] = [
    s3deployment.CacheControl.setPublic(),
    s3deployment.CacheControl.maxAge(cdk.Duration.seconds(0)),
    s3deployment.CacheControl.mustRevalidate(),
    s3deployment.CacheControl.sMaxAge(cdk.Duration.hours(1)),
];

export interface WebAppBundleDeploymentProps {
    readonly webSiteBuildPath: string;
    readonly webAppBucket: s3.IBucket;
    /** Invalidated (`/*`) after the upload. Omitted on the ALB path, which has no edge cache. */
    readonly distribution?: cloudfront.IDistribution;
}

export interface WebAppBundleDeployments {
    readonly hashedAssets: s3deployment.BucketDeployment;
    readonly esModules: s3deployment.BucketDeployment;
    readonly main: s3deployment.BucketDeployment;
    readonly hashedAssetsPrune: s3deployment.BucketDeployment;
}

/**
 * Uploads the built web bundle to the web app bucket. A BucketDeployment applies one Content-Type and one
 * Cache-Control to every object it writes, so the bundle is split into disjoint key sets, run in order:
 *
 * 1. `DeployHashedAssets` — `assets/*` with {@link WEB_HASHED_ASSET_CACHE_CONTROL}, without pruning, so
 *    the previous release's chunks stay readable.
 * 2. `DeployEsModules` — `*.mjs` (the viewer bundles' stable module paths) with
 *    {@link ES_MODULE_CONTENT_TYPE}; never prunes. The web build emits no `.mjs` under `assets/`.
 * 3. `DeployWithInvalidation` — everything else, including `index.html`, with
 *    {@link WEB_REVALIDATE_CACHE_CONTROL}; prunes within its own key set, then invalidates the
 *    distribution when one is given.
 * 4. `PruneHashedAssets` — re-syncs `assets/*` with pruning, deleting the previous release's chunks once
 *    the new `index.html` is stored and the edge no longer serves the old one.
 *
 * Each deployment's include/exclude filters scope both its upload and its prune (`aws s3 sync --delete`
 * only deletes keys the filters match), so none removes another's files. The order means a page served at
 * any point during a deployment references only assets that are still stored.
 */
export function deployWebAppBundle(
    scope: Construct,
    props: WebAppBundleDeploymentProps
): WebAppBundleDeployments {
    const common = {
        destinationBucket: props.webAppBucket,
        memoryLimit: Config.LAMBDA_MEMORY_SIZE,
        ephemeralStorageSize: WEB_DEPLOYMENT_EPHEMERAL_STORAGE,
    };
    const hashedAssetsOnly = {
        exclude: ["*"],
        include: [`${WEB_HASHED_ASSETS_PREFIX}*`],
        cacheControl: WEB_HASHED_ASSET_CACHE_CONTROL,
    };

    const hashedAssets = new s3deployment.BucketDeployment(scope, "DeployHashedAssets", {
        ...common,
        ...hashedAssetsOnly,
        sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
        prune: false,
    });

    const esModules = new s3deployment.BucketDeployment(scope, "DeployEsModules", {
        ...common,
        sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
        exclude: ["*"],
        include: ["*.mjs"],
        contentType: ES_MODULE_CONTENT_TYPE,
        cacheControl: WEB_REVALIDATE_CACHE_CONTROL,
        prune: false,
    });

    const main = new s3deployment.BucketDeployment(scope, "DeployWithInvalidation", {
        ...common,
        sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
        exclude: [`${WEB_HASHED_ASSETS_PREFIX}*`, "*.mjs"],
        cacheControl: WEB_REVALIDATE_CACHE_CONTROL,
        // On redeploy this invalidates the CloudFront cache once every new object is in place.
        distribution: props.distribution,
        distributionPaths: props.distribution ? ["/*"] : undefined,
    });

    // The handler's archive extraction does not keep file times, so `aws s3 sync` uploads every `assets/*`
    // file again, byte-identical and with the same metadata, before it deletes the keys this build lacks.
    const hashedAssetsPrune = new s3deployment.BucketDeployment(scope, "PruneHashedAssets", {
        ...common,
        ...hashedAssetsOnly,
        sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
    });

    esModules.node.addDependency(hashedAssets);
    main.node.addDependency(esModules);
    hashedAssetsPrune.node.addDependency(main);

    return { hashedAssets, esModules, main, hashedAssetsPrune };
}

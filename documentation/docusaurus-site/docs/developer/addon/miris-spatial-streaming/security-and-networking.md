---
sidebar_label: Security and networking
---

# Miris Add-on Security and Networking

## Permissions

The status route needs Tier 1 `GET` on `/addon/miris/asset-status` and the viewer key route Tier 1 `POST` on `/addon/miris/viewer-key`. Both need Tier 2 `GET` on the asset. The Database Admin, Database User, Database Read-Only, and Global Read-Only permission templates grant both routes, and the seeded `basicReadOnly` role gains them when the add-on is enabled. A custom role, or one imported from an earlier copy of a template, needs constraints that allow both routes. See [Permissions Model](../../../concepts/permissions-model.md#add-on-routes).

The viewer key route returns a key only for an asset the core download and stream routes would serve. In `static` mode every key it returns is the same deployment key, so the check decides who receives it, not which Miris assets it can stream; see [Which assets can be streamed and uploaded](./how-it-works.md#which-assets-can-be-streamed-and-uploaded).

## Data sent to Miris

-   **Uploads.** The root USD file and the files it references, packaged as one `.usdz`, with its file name and size; the Miris asset name (the template's value, the VAMS asset name, or the file name); and the tags `vams`, `vams-asset-<assetId>` and `vams-database-<databaseId>`, plus any tags a user enters in the upload template.
-   **API calls with the Integration Key.** Reads of Miris assets, and, in `perView` mode, the create and delete of each viewer key. A per-view key is created with the name `vams:<databaseId>:<assetId>:<epoch milliseconds>`, scoped to the one Miris asset, and with its expiry.
-   **From the browser.** The viewer key and the Miris asset ID, to stream. The Miris web SDK also reports viewer interaction telemetry to Miris.

VAMS sends no other asset data, metadata or user identity to Miris.

## Credentials

The Integration Key stays server side, in a secret the operator creates (`apiKeySecretArn`). The functions and the upload container read it with `secretsmanager:GetSecretValue` on exactly that ARN, and never log it.

In `static` mode the viewer key (`viewerKey`) is a configuration value handled like the other credentials supplied in configuration (see [Third-Party Credentials Supplied in Configuration](../../../architecture/security.md#third-party-credentials-supplied-in-configuration)): VAMS creates an empty Secrets Manager secret, populates it at deployment time from a custom resource's code asset, and grants the status and viewer key functions `secretsmanager:GetSecretValue` on exactly that secret, so the key is in neither the CloudFormation template nor a Lambda environment, with the same deployment-package caveats. A viewer key authorizes streaming only and reaches the browser, so scope it in Miris with tags and an expiry, or use `perView` mode, which deploys no shared viewer key.

## Web origin and Content Security Policy

The Miris web SDK runs in the VAMS web application's own origin, with the same access to the page as VAMS code. The add-on requires `app.webUi.allowUnsafeEvalFeatures`, which adds `'unsafe-eval'` to `script-src` for the whole web application, not only the Miris viewer: the SDK's generated WebAssembly bindings compile code with `new Function`. Enabling the add-on also adds `blob:` to `script-src`, from which the SDK loads its engine, and `https://*.miris.com` and `wss://*.miris.com` to `connect-src` (see [Conditional CSP Sources](../../../architecture/security.md#conditional-csp-sources)).

## Network

The add-on's Lambda functions run outside the VPC and call the Miris API at `*.miris.com` over HTTPS. Configuration validation rejects the add-on with `app.useGlobalVpc.useForAllLambdas`, which would place them in the isolated subnets, where nothing reaches Miris. Browsers stream from Miris directly over HTTPS and WebSocket.

The upload pipeline needs `app.useGlobalVpc.enabled`. Its AWS Batch jobs run on AWS Fargate in the private subnets and reach Miris through the NAT gateway. Enabling it adds public and private subnets, a NAT gateway per Availability Zone, and the AWS Batch, Amazon ECR API and Amazon ECR Docker interface endpoints to a VPC that VAMS creates; an imported VPC needs `app.useGlobalVpc.optionalExternalPrivateSubnetIds` with a route to the internet. Input and output buckets must be in the deployment Region.

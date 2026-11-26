---
sidebar_label: Overview
---

# Miris Spatial Streaming Add-on

[Miris](https://miris.com/) is a partner ISV whose Spatial Streaming service delivers 3D geometry progressively: an asset loads at a lower fidelity first and sharpens to full quality within seconds. VAMS integrates with Miris through an optional [add-on](../../addons-overview.md) that streams USD files from Miris inside the VAMS file viewer, without downloading the full geometry to the browser.

-   **Streaming** (`app.addons.useMirisStreaming`) deploys the Miris viewer plugin for USD files, the two API routes it calls, the GLOBAL **Miris stream** file metadata schema, and the Content Security Policy sources the Miris web SDK needs. It holds the Miris Integration Key and API base URL that both parts use.
-   **The [Miris Upload pipeline](./upload-pipeline.md)** (`app.addons.useMirisStreaming.upload`) uploads a VAMS asset's root USD file to Miris and records the Miris asset as [stream metadata](./how-it-works.md#stream-metadata) on that file. It publishes the `MIRIS_UPLOAD` feature switch.

A USD asset is uploaded to Miris, on demand or, when the upload trigger is on, automatically on file upload; Miris prepares it for streaming; the Miris asset UUID is written to the metadata of the asset's root USD file; and the viewer streams the asset when a user opens that file. The stream metadata can also be set by hand to point a USD file at an asset that is already on Miris.

:::info
The add-on is off by default. It deploys only to the commercial `aws` partition and requires `app.webUi.allowUnsafeEvalFeatures` (see [Requirements and limitations](#requirements-and-limitations)).
:::

The add-on's pages cover the rest: [Enable the Miris Spatial Streaming Add-on](./enable.md) sets it up, [How the Miris Add-on Works](./how-it-works.md) describes the viewer, its viewer keys and the stream metadata, [Miris Upload Pipeline](./upload-pipeline.md) describes the upload pipeline, [Miris Add-on Resources](./resources.md) lists the AWS resources it adds and how to monitor them, [Miris Add-on Security and Networking](./security-and-networking.md) covers permissions, the data sent to Miris, the web origin and the network paths, and [Uninstall the Miris Add-on](./uninstall.md) removes what it leaves behind.

---

## Requirements and limitations

-   **Commercial partition only.** Configuration validation rejects the add-on in any partition other than `aws`. Every browser that streams must reach `*.miris.com` and `*.miris-content.com`, and the add-on's Lambda functions `*.miris.com`.
-   **`app.webUi.allowUnsafeEvalFeatures` must be `true`.** The Miris web SDK runs in the VAMS web application's own origin and compiles code at runtime. The setting adds `'unsafe-eval'` to `script-src` for the whole web application, not only the viewer. Review this with your security team (see [Security](../../../architecture/security.md)).
-   **Not with `app.useGlobalVpc.useForAllLambdas`.** Configuration validation rejects the add-on with that setting, which would place the add-on's Lambda functions in the VPC's isolated subnets, where they have no route to the Miris API. See [Network](./security-and-networking.md#network).
-   **Browser support.** A browser with WebGPU, for example Chrome or Edge 113 or later, or Safari 26 or later, over HTTPS, with WebSocket support.
-   **Processing time.** After upload, an asset can take a few hours to become streamable.

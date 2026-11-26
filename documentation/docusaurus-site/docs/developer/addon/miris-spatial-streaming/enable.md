---
sidebar_label: Enable
---

# Enable the Miris Spatial Streaming Add-on

## Configuration

Enable the add-on by setting `app.addons.useMirisStreaming.enabled` to `true`, with `app.webUi.allowUnsafeEvalFeatures` set to `true`, and the ARN of the secret that holds the Miris Integration Key. No viewer key is configured: the add-on mints one for each viewer open (see [Viewer keys](./how-it-works.md#viewer-keys)). The [Configuration Reference](../../../deployment/configuration-reference.md#miris-spatial-streaming-appaddonsusemirisstreaming) lists every field, default, and validation rule.

```json
{
    "app": {
        "webUi": {
            "allowUnsafeEvalFeatures": true
        },
        "addons": {
            "useMirisStreaming": {
                "enabled": true,
                "apiKeySecretArn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:miris-integration-key-AbCdEf"
            }
        }
    }
}
```

Enabling the add-on sets the `MIRIS_ADDON` feature switch; enabling `upload` also sets `MIRIS_UPLOAD`. The same setting decides whether the web application bundles the Miris web SDK, so a deployment without the add-on neither installs nor bundles it:

1. Run `npm install` (or `npm ci`) in `web/` with the add-on configuration above in the configuration file you deploy with. The install reads `infra/config/config.json`; when you deploy with `-c configFile=<name>`, set `VAMS_CONFIG_PATH` to the same value, which resolves as `configFile` does (a file name or relative path under `infra/config/`, or an absolute path). Its `postinstall` bundles the [web SDK](#web-sdk) only when that configuration enables `app.addons.useMirisStreaming`. To rebuild only the SDK bundle, run `node customInstalls/miris/mirisInstall.js` from `web/`.
2. Build the web application (`npm run build` in `web/`) and deploy it with the same configuration.

The viewer entry ships enabled, and the viewer is offered only when both `MIRIS_ADDON` and `ALLOWUNSAFEEVAL` are present.

## Integration Key

The Integration Key authenticates the add-on's Lambda functions and the upload pipeline to the Miris API. It carries the permissions of the person who created it and resolves to that person's default Miris workspace, which must be the workspace that holds the streamed assets.

-   Create the key for a Miris service account that is a member of only the workspace VAMS uses.
-   Store it in AWS Secrets Manager as a plain-string secret in the deployment's account. VAMS grants `secretsmanager:GetSecretValue` on exactly that ARN and no AWS KMS permission, so a secret encrypted with a customer managed key other than the VAMS key needs a key policy that lets the reading roles call `kms:Decrypt`.
-   The functions cache the key for up to an hour per execution environment, and read it again when Miris answers `401` or `403`. A refusal after that read is logged as `miris_auth_failed` and counted in the `MirisAuthFailures` metric (see [Monitoring](./resources.md#monitoring)).
-   A Miris integration key belongs to the person who created it; Miris has no key owned by an organization. When that person's account is removed or moves to another default workspace, status checks, viewer keys and uploads fail until a new key is stored.

The functions never follow a redirect from Miris, so a `mirisApiBaseUrl` that redirects makes every call fail. Miris calls are bounded to fit within the API Gateway integration timeout; when time runs out, the status route reports `unknown` and the viewer key route answers `504`.

## Web SDK

The viewer renders with the Miris web SDK (`@miris-inc/components`, `@miris-inc/three`, `@miris-inc/core`, and `three`), which is not a dependency of the web application. The custom install script `web/customInstalls/miris/mirisInstall.js`, run by the web application's `postinstall`, acts only when the deployment configuration enables the add-on (see [Configuration](#configuration)): it bundles the SDK into `web/public/viewers/miris/`, and the viewer imports `/viewers/miris/miris-sdk.js` from the same origin. The SDK packages and `three` are pinned in `web/customInstalls/miris/package.json` and are upgraded together.

The SDK renders only with WebGPU. Before it requests a viewer key, the viewer asks the browser for a WebGPU adapter, as the SDK does; a browser that grants none shows "Miris streaming needs WebGPU" and no key is requested. The viewer loads the SDK only after it has a viewer key. It waits 60 seconds for the SDK to load and 60 seconds for the first frame, counting the first-frame wait only while the browser tab is visible.

With the add-on enabled, the Content Security Policy gains `https://*.miris.com`, `wss://*.miris.com` and `https://*.miris-content.com` in `connect-src` and `blob:` in `script-src`. The SDK also needs `'unsafe-eval'`, which only `app.webUi.allowUnsafeEvalFeatures` adds. See [Security Architecture](../../../architecture/security.md#conditional-csp-sources).

# Miris Spatial Streaming SDK Bundle Installation

This directory builds the Miris web SDK for the Miris Spatial Streaming viewer (`miris-stream-viewer`). The SDK is not a dependency of the web application: `mirisInstall.js`, run by the `postinstall` chain in `web/package.json`, installs the packages pinned in `package.json`, bundles them with webpack into an ES module, and copies the bundle and the SDK's `.wasm` files to `web/public/viewers/miris/`, only when the deployment configuration enables the add-on.

## Enabling the viewer

The viewer entry ships enabled. `mirisAddonEnabled.js` reads the deployment configuration (the file named by `VAMS_CONFIG_PATH`, else `infra/config/config.json`), and the SDK is bundled only when it enables `app.addons.useMirisStreaming`.

1. Run `npm install` (or `npm ci`) in `web/` with that configuration in place. To rebuild only this bundle, run `node customInstalls/miris/mirisInstall.js` from `web/`.

The viewer is offered only when the deployment publishes `MIRIS_ADDON` and `ALLOWUNSAFEEVAL` (the add-on and `app.webUi.allowUnsafeEvalFeatures`). See `documentation/docusaurus-site/docs/developer/addon/miris-spatial-streaming/`.

## Version updates

1. Update the three `@miris-inc/*` versions together, and `three` within the SDK's peer range (`npm view @miris-inc/three@<version> peerDependencies`), in `package.json`.
2. Run `npm install` in this directory to update `package-lock.json`.
3. Run `node customInstalls/miris/mirisInstall.js` from `web/` with a configuration that enables the add-on, and test the viewer.
4. Update the versions in `NOTICE.md`.

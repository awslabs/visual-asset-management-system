# Miris Spatial Streaming SDK Bundle Installation

This directory builds the Miris web SDK for the Miris Spatial Streaming viewer (`miris-stream-viewer`). The SDK is not a dependency of the web application: `mirisInstall.js`, run by the `postinstall` chain in `web/package.json`, installs the packages pinned in `package.json`, bundles them with webpack into an ES module, and copies the bundle and the SDK's `.wasm` files to `web/public/viewers/miris/`, only when the viewer is enabled.

## Enabling the viewer

1. Set `"enabled": true` on the `miris-stream-viewer` entry in `web/src/visualizerPlugin/config/addonViewerConfig.json`.
2. Run `npm install` (or `npm ci`) in `web/`. To rebuild only this bundle, run `node customInstalls/miris/mirisInstall.js` from `web/`.

The viewer is offered only when the deployment also enables the add-on (`app.addons.useMirisStreaming`) and `app.webUi.allowUnsafeEvalFeatures`. See `documentation/docusaurus-site/docs/developer/miris-spatial-streaming.md`.

## Version updates

1. Update the three `@miris-inc/*` versions together, and `three` within the SDK's peer range (`npm view @miris-inc/three@<version> peerDependencies`), in `package.json`.
2. Run `npm install` in this directory to update `package-lock.json`.
3. Run `node customInstalls/miris/mirisInstall.js` from `web/` with the viewer enabled, and test the viewer.
4. Update the versions in `NOTICE.md`.

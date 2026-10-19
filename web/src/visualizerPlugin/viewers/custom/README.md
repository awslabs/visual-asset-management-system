# Custom viewer plugins

Fork extension point. Upstream VAMS ships only this README and `customManifest.ts` in this directory and does not modify them, so viewers added here do not conflict on upgrade.

A viewer a fork adds lives in `viewers/custom/<Name>Plugin/`, with its component in a file named `<Name>Component.tsx` (the registry's loader picks up only `*Component.tsx` files). It is registered in two fork-owned files instead of the core `manifest.ts` and `viewerConfig.json`:

1. `customManifest.ts` (this directory): `CUSTOM_VIEWER_COMPONENTS` maps the entry's `componentPath` to its path below `viewers/`, and `CUSTOM_DEPENDENCY_MANAGERS` does the same for a `dependencyManager`.
2. `../../config/customViewerConfig.json`: the viewer's entry, in the same shape as a `viewerConfig.json` entry.

The registry loads core, add-on and custom entries in that order. A custom entry whose `id` repeats a core or add-on viewer `id` is skipped with a console error rather than replacing that viewer, and a manifest key that repeats a core key is ignored. Ids prefixed `custom-` are recommended so a later core viewer does not collide with one added here.

An npm install or bundle step for a custom viewer goes in `web/customInstalls/custom/customInstall.js`, which `npm install` runs last. A third-party origin the viewer loads from goes in `infra/config/csp/cspAdditionalConfig.json`. The viewer contract (`ViewerPluginProps`) and the rest of the plugin rules are in `web/src/visualizerPlugin/CLAUDE.md`.

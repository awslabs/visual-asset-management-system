# Robotics Data Viewer (Lichtblick) install

`roboticsViewerInstall.js` builds the Robotics Data Viewer that `RoboticsDataViewerPlugin` (`web/src/visualizerPlugin/viewers/RoboticsDataViewerPlugin/`) frames. It runs in the `postinstall` chain of `web/package.json`, after the SuperSplat install.

The viewer is the [Lichtblick](https://github.com/lichtblick-suite/lichtblick) web app (MPL-2.0), built from source at tag `v1.29.1` (commit `95713edb309d77589b130286642ab149995a5f7b`) and served statically from `/viewers/robotics/`. It opens MCAP (`.mcap`), ROS 1 bag (`.bag`), ROS 2 bag (`.db3`) and PX4 ULog (`.ulg`, `.ulog`) files.

## What the script does

1. Removes the previous build (`web/public/viewers/robotics/`) and any clone left in `source/`.
2. Skips the rest when `robotics-data-viewer` is disabled in the viewer configuration (`checkViewerEnabled`), so `web/public/viewers/robotics/` stays absent.
3. Checks that `corepack` is available. Lichtblick builds with the Yarn version its `package.json` pins (`yarn@4.17.0`), through corepack. Node.js 20 to 24 bundle corepack; Node.js 25 and later do not, and the install stops with a message naming the remedies.
4. Clones the pinned tag with `--depth 1` into `source/` and stops unless `git rev-parse HEAD` is the pinned commit.
5. Runs `corepack yarn install --immutable` against the upstream lockfile, with `ELECTRON_SKIP_BINARY_DOWNLOAD=1` (the upstream desktop workspace would otherwise download Electron), `HUSKY=0`, `CI=1` and Yarn telemetry off.
6. Copies the overlay into the clone: the `overlay/*.ts(x)` sources into `web/src-vams/` (with the upstream `web/src/tsconfig.json`) and `overlay/vams.webpack.config.ts` into `web/`. No upstream file is modified; the script stops if `git status` reports a changed tracked file after the build.
7. Builds with `corepack yarn webpack --mode production --config web/vams.webpack.config.ts`.
8. Rewrites `index.html` with `postprocessIndexHtml.js` and checks the result: no inline `<script>`, `<meta name="referrer" content="no-referrer">` first in `<head>`, `vams-init.js` present, no `.map` file, no font embedded as a `data:` URI. Any failed check stops the install.
9. Copies the build to `web/public/viewers/robotics/` with the upstream license as `THIRD_PARTY_LICENSE_LICHTBLICK.txt` and `SOURCE.txt` (repository, tag, commit), then deletes the clone.

A failure exits non-zero, which fails `npm install`. To skip the viewer, set `"enabled": false` on `robotics-data-viewer` in `web/src/visualizerPlugin/config/viewerConfig.json`.

The clone uses about 1.2 GiB while the script runs; the build is about 41 MiB in about 220 files.

## Overlay

The VAMS files compiled with the upstream sources are Apache-2.0 and live in `overlay/`:

| File                               | Purpose                                                                                                                                                                                                                                                                      |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `entrypoint.tsx`                   | Registers only the two VAMS data sources. Moves the deep link from the URL fragment into the query after the app has loaded its fonts, and replaces the document URL with one without the deep link once the app has read it.                                                |
| `VamsRemoteDataSourceFactory.ts`   | `ds=vams-remote`: `.mcap` (one or more files on one timeline) and `.bag` (one file), read with HTTP Range requests through the upstream remote-file workers. Names the player by file name.                                                                                  |
| `VamsDownloadDataSourceFactory.ts` | `ds=vams-download`: `.db3` (one file, or the files of a ROS 2 split bag) and `.ulg`/`.ulog` (one file), downloaded whole and opened with the upstream file workers. Refuses a selection larger than `ds.maxBytes` (default 1 GiB) by its `Content-Length` before reading it. |
| `ScrubbedIterableSource.ts`        | Wraps each source so error text, alerts and the initialization result name the file instead of quoting its presigned URL.                                                                                                                                                    |
| `vamsSourceSafety.ts`              | Pure helpers (no Lichtblick imports, unit-tested from `web/`): file names from URLs, URL scrubbing, the whole-file download and its limit, and taking the deep-link URLs out of the parameters the app would save as a recent source.                                        |
| `vams.webpack.config.ts`           | Builds the upstream web app with the VAMS entrypoint under `/viewers/robotics/`, without production source maps, and with `.woff`/`.woff2` fonts emitted as files (the VAMS CSP has `font-src 'self'` and no `data:`).                                                       |

## Deep link

The host component builds the iframe `src` (`roboticsDeepLink.ts`); every presigned URL is in the fragment, which a browser never sends:

```
/viewers/robotics/index.html#ds=vams-remote&ds.url=<encoded URL>[&ds.url=<encoded URL>...]
/viewers/robotics/index.html#ds=vams-download&ds.maxBytes=<bytes>&ds.url=<encoded URL>[...]
```

## Content Security Policy

Lichtblick needs `'unsafe-eval'` in `script-src`, so the viewer carries `featuresEnabledRestriction: ["ALLOWUNSAFEEVAL"]` and is offered only when the deployment sets `app.webUi.allowUnsafeEvalFeatures`. The build adds no CSP source: its inline script is moved to `vams-init.js`, its fonts are files, and it fetches only from its own origin and the presigned Amazon S3 URLs.

## Upgrading Lichtblick

Change `LICHTBLICK_TAG` and `LICHTBLICK_COMMIT` in `roboticsViewerInstall.js` together, then check that the overlay still compiles against the upstream classes it uses (`IterablePlayer`, `WorkerSerializedIterableSource`, `WorkerIterableSource`, the four worker entry points, `main` from `@lichtblick/suite-web`) and that the upstream page template still has the inline script `postprocessIndexHtml.js` moves. Re-check what the app writes to the address bar and to browser storage when it opens a deep link. Update the version in `NOTICE.md`.

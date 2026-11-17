/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

// Guards the three places viewer capability is declared against each other:
// the registry config, the loader code that actually parses each format, and the
// catalog table + CSP section in visualizerPlugin/CLAUDE.md. All three drifted in
// separate directions before this test existed — an extension a loader handles but
// the config omits makes the viewer unreachable, and an extension the config
// declares but no loader parses is a viewer that never finishes loading.

import fs from "fs";
import path from "path";
import viewerConfig from "./viewerConfig.json";
import addonViewerConfig from "./addonViewerConfig.json";
import {
    OCCT_FORMATS,
    THREE_MESH_FORMATS,
    USD_FORMATS,
} from "../viewers/ThreeJSViewerPlugin/utils/fileLoaders";
import { admitsCompareSelection, admitsVisualizeSelection } from "../core/viewerSelection";
import { NO_EXTENSION, WILDCARD_EXTENSION } from "../core/extensionMatching";
import { VIEWER_COMPONENTS } from "../viewers/manifest";
import { ADDON_DEPENDENCY_MANAGERS, ADDON_VIEWER_COMPONENTS } from "../viewers/addonManifest";
import {
    ALL_DEPENDENCY_MANAGERS,
    ALL_VIEWER_COMPONENTS,
    ALL_VIEWER_CONFIGS,
} from "../core/viewerSources";

// The upstream-shipped viewers: core (viewerConfig.json) plus add-on (addonViewerConfig.json).
const viewers: any[] = [...(viewerConfig as any).viewers, ...(addonViewerConfig as any).viewers];
const byId = (id: string) => {
    const found = viewers.find((viewer) => viewer.id === id);
    if (!found)
        throw new Error(`No viewer with id ${id} in viewerConfig.json or addonViewerConfig.json`);
    return found;
};

/** Formats in `formats` (no leading dot) that `declared` does not list. */
const missingFrom = (declared: string[], formats: string[]): string[] =>
    formats.filter((format) => !declared.includes(`.${format}`));

describe("viewerConfig vs the loaders", () => {
    it("offers the Three.js viewer for every CAD format its loader routes to OCCT", () => {
        expect(missingFrom(byId("threejs-viewer").supportedExtensions, OCCT_FORMATS)).toEqual([]);
    });

    it("offers the Three.js viewer for every USD format its loader routes to USDLoader", () => {
        expect(missingFrom(byId("threejs-viewer").supportedExtensions, USD_FORMATS)).toEqual([]);
    });

    it("declares only formats a Three.js loader parses", () => {
        // Every declared extension must reach a loader, or the viewer never finishes loading.
        const parsed = [...THREE_MESH_FORMATS, ...USD_FORMATS, ...OCCT_FORMATS];
        const declared = byId("threejs-viewer").supportedExtensions.map((ext: string) =>
            ext.replace(/^\./, "")
        );
        expect(declared.filter((ext: string) => !parsed.includes(ext))).toEqual([]);
    });

    it("missingFrom reports a gap when one exists", () => {
        // Positive control for the assertion above: the same check against a list
        // that omits the OCCT formats must not come back empty.
        expect(missingFrom([".stp"], OCCT_FORMATS)).toContain("igs");
    });

    it("declares only the columnar formats the viewer can parse", () => {
        // ColumnarViewerComponent dispatches on .fcs and .csv. A declared format
        // with no parser reaches papaparse and yields zero columns.
        const columnar = byId("columnar-viewer");
        expect(columnar.supportedExtensions.sort()).toEqual([".csv", ".fcs"]);
        expect(columnar.supportedExtensions).not.toContain(".rds");
    });

    it("offers the SuperSplat Editor for the single-file formats its import handler opens", () => {
        // SuperSplat's file-handler.ts (v3.7.0) also imports .lcc2 scenes, unbundled SOG
        // (meta.json with .webp siblings) and .ssproj editor projects. VAMS hands the editor one
        // presigned URL per view; those inputs stay unlisted until they are proven through that
        // hand-off (an .ssproj is an editor save, not an asset format).
        const supersplat = byId("supersplat-viewer");
        expect([...supersplat.supportedExtensions].sort()).toEqual([
            ".ksplat",
            ".lcc",
            ".ply",
            ".sog",
            ".splat",
            ".spz",
        ]);
        expect(supersplat.supportsMultiFile).toBe(false);
    });
});

describe("viewerConfig vs the default Content Security Policy", () => {
    // rhino3dm, which Online3DViewer loads for .3dm, compiles with eval; the default CSP refuses it
    // and the viewer then fails with "Failed to load rhino3dm" (#921). Offered only with
    // ALLOWUNSAFEEVAL, a default deployment takes the standard "no compatible viewer" path instead.
    it("offers .3dm only from a viewer gated on ALLOWUNSAFEEVAL", () => {
        const offering = viewers.filter((viewer) => viewer.supportedExtensions.includes(".3dm"));
        expect(offering.map((viewer) => viewer.id)).toEqual(["online3d-viewer-rhino"]);
        expect(offering[0].featuresEnabledRestriction).toContain("ALLOWUNSAFEEVAL");
    });

    it("keeps the other Online 3D Viewer formats ungated, rendered by the same component", () => {
        const online3d = byId("online3d-viewer");
        expect(online3d.featuresEnabledRestriction ?? []).toEqual([]);
        expect([...online3d.supportedExtensions].sort()).toEqual([".amf", ".bim", ".off", ".wrl"]);
        expect(byId("online3d-viewer-rhino").componentPath).toBe(online3d.componentPath);
    });
});

describe("viewerConfig vs the selection paths", () => {
    // The Text Diff Viewer renders only `compareFiles`, which the visualize path never passes.
    // Offered there, it fails at once with "needs exactly two files"; it must be compare-only.
    it("declares the text diff viewer compare-only", () => {
        const textDiff = byId("text-diff-viewer");
        expect(textDiff.compareMode.enabled).toBe(true);
        expect(textDiff.compareMode.compareOnly).toBe(true);
        // Compare file count is governed by minFiles/maxFiles, not the visualize multi-file flag.
        expect(textDiff.supportsMultiFile).toBe(false);
    });

    const compareOnly = viewers.filter((viewer) => viewer.compareMode?.compareOnly);

    it.each(compareOnly.map((viewer) => [viewer.id, viewer]))(
        "%s is never a visualize option but is offered in compare mode",
        (_id, config) => {
            const [ext] = config.supportedExtensions;
            expect(admitsVisualizeSelection(config, [ext], true)).toBe(false);
            expect(admitsVisualizeSelection(config, [ext], false)).toBe(false);
            expect(
                admitsCompareSelection(config, [ext], {
                    fileCount: config.compareMode.minFiles,
                    shape: "different-files",
                })
            ).toBe(true);
        }
    );
});

describe("viewerConfig supportedExtensions values", () => {
    // Matching compares lowercased file tokens against these strings exactly, so a value that is not
    // a lowercase ".ext", the "*" wildcard or the "(none)" no-extension token never matches anything.
    // A dotfile name declared as an extension (".gitignore") is a case in point: the dotfile rule
    // gives `.gitignore` no extension, so it is matched by "(none)", never by ".gitignore".
    it.each(viewers.map((viewer) => [viewer.id, viewer]))(
        "%s declares only '.ext', '*' or '(none)'",
        (_id, viewer) => {
            const invalid = (viewer.supportedExtensions as string[]).filter(
                (ext) =>
                    !(
                        ext === WILDCARD_EXTENSION ||
                        ext === NO_EXTENSION ||
                        /^\.[a-z0-9_]+$/.test(ext)
                    )
            );
            expect(invalid).toEqual([]);
        }
    );

    it("lists the no-extension token only on the two text viewers (#1198)", () => {
        expect(
            viewers
                .filter((viewer) => viewer.supportedExtensions.includes(NO_EXTENSION))
                .map((viewer) => viewer.id)
                .sort()
        ).toEqual(["text-diff-viewer", "text-viewer"]);
    });
});

describe("viewerConfig vs the browser's media element", () => {
    // VideoViewerComponent hands the presigned URL to a native <video> element, which has no
    // demuxer for AVI, FLV or ASF/WMV in Chrome, Edge, Firefox or Safari. A declared container
    // the element cannot open auto-selects the Video Player and always ends in "Failed to load
    // video file".
    const UNPLAYABLE_VIDEO_CONTAINERS = [".avi", ".flv", ".wmv"];

    it("does not offer the video player for a container no browser can open", () => {
        const video: string[] = byId("video-viewer").supportedExtensions;
        expect(UNPLAYABLE_VIDEO_CONTAINERS.filter((ext) => video.includes(ext))).toEqual([]);
        // The exact list: .mov and .mkv stay because browsers play them when they support the
        // codecs inside; a new container is added here deliberately, not by editing the config
        // alone.
        expect([...video].sort()).toEqual([".m4v", ".mkv", ".mov", ".mp4", ".webm"]);
    });

    it("offers no viewer but the preview viewer for those containers", () => {
        // isViewableExtension shows the viewer (eye) icon when ANY non-preview viewer matches, so a
        // second viewer declaring one of these containers, or a non-preview wildcard, brings the
        // dead icon back. Matching lowercases the file's extension, so the declared entries are
        // compared lowercased too.
        const offering = viewers
            .filter((viewer) => !viewer.isPreviewViewer)
            .filter((viewer) =>
                (viewer.supportedExtensions as string[]).some((ext) => {
                    const declared = ext.toLowerCase();
                    return declared === "*" || UNPLAYABLE_VIDEO_CONTAINERS.includes(declared);
                })
            )
            .map((viewer) => viewer.id);
        expect(offering).toEqual([]);
    });
});

describe("viewer manifests", () => {
    // PluginRegistry resolves a viewer's componentPath through the manifest of the same tier, so
    // an entry configured in one file and listed in the other manifest still loads but places a
    // viewer on the wrong side of the core/add-on boundary.
    it("lists every core viewer's component in manifest.ts", () => {
        const missing = (viewerConfig as any).viewers
            .map((viewer: any) => viewer.componentPath)
            .filter((componentPath: string) => !(componentPath in VIEWER_COMPONENTS));
        expect(missing).toEqual([]);
    });

    it("lists every add-on viewer's component in addonManifest.ts", () => {
        const addonViewers = (addonViewerConfig as any).viewers;
        expect(addonViewers.length).toBeGreaterThan(0);
        const missing = addonViewers
            .map((viewer: any) => viewer.componentPath)
            .filter((componentPath: string) => !(componentPath in ADDON_VIEWER_COMPONENTS));
        expect(missing).toEqual([]);
    });
});

describe("licensed viewers are add-on viewers", () => {
    // A licensed viewer (VNTANA, VEERUM) needs a vendor package the deployment owner licenses, so it
    // ships disabled and lives in the add-on files rather than in the core ones. Moving it there
    // must not change what the merged registry offers.
    const LICENSED = /\(LICENSED VIEWER\)/;
    const LICENSED_IDS = ["vntana-viewer", "veerum-viewer"];
    const coreViewers: any[] = (viewerConfig as any).viewers;
    const addonViewers: any[] = (addonViewerConfig as any).viewers;

    it("the core config and manifest hold no licensed viewer", () => {
        expect(coreViewers.filter((viewer) => LICENSED.test(viewer.description))).toEqual([]);
        const coreText =
            fs.readFileSync(path.join(__dirname, "viewerConfig.json"), "utf8") +
            fs.readFileSync(path.join(__dirname, "..", "viewers", "manifest.ts"), "utf8");
        expect(coreText).not.toMatch(/vntana|veerum/i);
    });

    it("[control] the licensed-viewer marker matches the licensed viewers", () => {
        const marked = addonViewers.filter((viewer) => LICENSED.test(viewer.description));
        expect(marked.map((viewer) => viewer.id).sort()).toEqual([...LICENSED_IDS].sort());
    });

    it.each(LICENSED_IDS)("%s is registered from the add-on files, disabled", (id) => {
        const merged = ALL_VIEWER_CONFIGS.filter((viewer) => viewer.id === id);
        expect(merged).toHaveLength(1);
        const viewer: any = merged[0];
        expect(addonViewers.map((entry) => entry.id)).toContain(id);
        expect(viewer.enabled).toBe(false);
        expect(viewer.priority).toBe(2);
        expect(viewer.componentPath in ADDON_VIEWER_COMPONENTS).toBe(true);
        expect(viewer.dependencyManager in ADDON_DEPENDENCY_MANAGERS).toBe(true);
    });

    it.each(LICENSED_IDS)(
        "%s resolves its component and dependency manager through the merged maps",
        (id) => {
            // Enabling the viewer is flipping `enabled` in addonViewerConfig.json; PluginRegistry
            // then loads it through these two merged maps, exactly as it did from the core files.
            const viewer: any = ALL_VIEWER_CONFIGS.find((entry) => entry.id === id);
            expect(ALL_VIEWER_COMPONENTS[viewer.componentPath]).toBe(
                (ADDON_VIEWER_COMPONENTS as Record<string, string>)[viewer.componentPath]
            );
            expect(ALL_DEPENDENCY_MANAGERS[viewer.dependencyManager]).toBe(
                (ADDON_DEPENDENCY_MANAGERS as Record<string, string>)[viewer.dependencyManager]
            );
        }
    );

    it("a core viewer that ties with an add-on viewer on extension and priority sorts first", () => {
        // PluginRegistry sorts compatible viewers by priority with a stable sort, so a tie is
        // decided by registration order. The add-on viewers register after every core viewer, so a
        // tie keeps resolving to the core viewer (cesium-viewer ahead of veerum-viewer for .json).
        const index = new Map(ALL_VIEWER_CONFIGS.map((viewer, i) => [viewer.id, i]));
        const addonIds = new Set(addonViewers.map((viewer) => viewer.id));
        const inverted: string[] = [];
        for (const addon of addonViewers) {
            for (const core of coreViewers) {
                const tie =
                    core.priority === addon.priority &&
                    core.supportedExtensions.some((ext: string) =>
                        addon.supportedExtensions.includes(ext)
                    );
                const coreAt = index.get(core.id) ?? -1;
                const addonAt = index.get(addon.id) ?? -1;
                if (tie && !addonIds.has(core.id) && coreAt > addonAt) {
                    inverted.push(`${addon.id} before ${core.id}`);
                }
            }
        }
        expect(inverted).toEqual([]);
        expect(index.get("cesium-viewer")).toBeDefined();
        expect(index.get("cesium-viewer") ?? Infinity).toBeLessThan(
            index.get("veerum-viewer") ?? -1
        );
    });
});

describe("visualizerPlugin/CLAUDE.md catalog", () => {
    const claudeMd = fs.readFileSync(path.join(__dirname, "..", "CLAUDE.md"), "utf8");

    interface Row {
        id: string;
        extensions: string;
        status: string;
    }

    // Catalog rows look like: | `id` | Name | category | .a, .b | status |
    const rows: Row[] = claudeMd
        .split("\n")
        .filter((line) => /^\|\s*`[a-z0-9-]+`\s*\|/.test(line))
        .map((line) => {
            const cells = line.split("|").map((cell) => cell.trim());
            return {
                id: cells[1].replace(/`/g, ""),
                extensions: cells[4],
                status: cells[5],
            };
        })
        // Field-reference tables use the same shape; keep the rows whose first
        // cell is a real viewer id.
        .filter((row) => viewers.some((viewer) => viewer.id === row.id));

    it("finds a row for every configured viewer", () => {
        expect(rows.length).toBeGreaterThan(0);
        const documented = rows.map((row) => row.id).sort();
        expect(documented).toEqual(viewers.map((viewer) => viewer.id).sort());
    });

    it.each(rows.map((row) => [row.id, row] as [string, Row]))(
        "%s's documented extensions exist in the config",
        (id, row) => {
            const configured: string[] = byId(id).supportedExtensions;
            if (/wildcard/.test(row.extensions)) {
                expect(configured).toEqual(["*"]);
                return;
            }
            // Strip parentheticals ("(plaintext only)") before reading the list.
            const documented = row.extensions
                .replace(/\([^)]*\)/g, "")
                .split(",")
                .map((token) => token.trim())
                .filter((token) => token.startsWith("."));
            expect(documented.length).toBeGreaterThan(0);
            // Every documented extension must be configured. Rows that end in
            // "etc." are deliberately partial, so the reverse only holds when the
            // row claims to be complete.
            expect(documented.filter((ext) => !configured.includes(ext))).toEqual([]);
            if (!/etc\./.test(row.extensions)) {
                expect(configured.filter((ext) => !documented.includes(ext))).toEqual([]);
            }
        }
    );

    it.each(rows.map((row) => [row.id, row] as [string, Row]))(
        "%s documents files with no extension exactly when it lists the token",
        (id, row) => {
            const configured: string[] = byId(id).supportedExtensions;
            expect(/no extension/.test(row.extensions)).toBe(configured.includes(NO_EXTENSION));
        }
    );

    it.each(rows.map((row) => [row.id, row] as [string, Row]))(
        "%s's documented status matches its enabled flag and feature gates",
        (id, row) => {
            const config = byId(id);
            expect(/\bdisabled\b/.test(row.status)).toBe(config.enabled === false);
            const gates: string[] = config.featuresEnabledRestriction || [];
            gates.forEach((gate) => expect(row.status).toContain(gate));
            ["ALLOWUNSAFEEVAL", "PHYSNA_ADDON"].forEach((gate) => {
                if (row.status.includes(gate)) expect(gates).toContain(gate);
            });
        }
    );

    // "These viewers require ALLOWUNSAFEEVAL" is the claim that decides whether an
    // engineer investigates a viewer failing in a default deployment, so the list
    // has to be the set the config actually gates.
    describe("the ALLOWUNSAFEEVAL section", () => {
        const section = claudeMd.slice(claudeMd.indexOf("## CSP / `unsafe-eval`"));
        const bulletNames = section
            .split("\n")
            .filter((line) => /^-\s{3}\S/.test(line))
            .map((line) => line.replace(/^-\s+/, "").trim().toLowerCase());

        const gatedNames = viewers
            .filter((viewer) =>
                (viewer.featuresEnabledRestriction || []).includes("ALLOWUNSAFEEVAL")
            )
            .map((viewer) => viewer.name.toLowerCase());

        /** Compares names ignoring any parenthetical qualifier on either side. */
        const bare = (name: string) => name.replace(/\([^)]*\)/g, "").trim();
        const matches = (bullet: string, names: string[]) =>
            names.some(
                (name) => bare(name).startsWith(bare(bullet)) || bare(bullet).startsWith(bare(name))
            );

        it("lists exactly the gated viewers", () => {
            expect(bulletNames.length).toBe(gatedNames.length);
            bulletNames.forEach((bullet) => expect(matches(bullet, gatedNames)).toBe(true));
            gatedNames.forEach((name) => expect(matches(name, bulletNames)).toBe(true));
        });

        it("would reject a viewer that is described as gated but is not", () => {
            // Positive control: the entry this section used to carry. The Three.js
            // viewer has no featuresEnabledRestriction — gating it would take its
            // mesh formats down with the CAD ones.
            expect(matches("three.js cad-format loaders", gatedNames)).toBe(false);
        });
    });
});

describe("viewer counts in the documentation", () => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..", "..");
    const read = (relative: string) => fs.readFileSync(path.join(repoRoot, relative), "utf8");
    const total = viewers.length;
    const gated = viewers.filter(
        (viewer) => viewer.enabled === false || (viewer.featuresEnabledRestriction ?? []).length > 0
    ).length;
    const viewersPage = read("documentation/docusaurus-site/docs/concepts/viewers.md");

    it("[control] counts every shipped viewer entry once", () => {
        expect(total).toBe(
            (viewerConfig as any).viewers.length + (addonViewerConfig as any).viewers.length
        );
    });

    it("the viewers page states the shipped total and how many are always available", () => {
        expect(viewersPage).toContain(`**${total} built-in viewer plugins**`);
        expect(viewersPage).toContain(`lists all ${total};`);
        const words = [
            "Zero",
            "One",
            "Two",
            "Three",
            "Four",
            "Five",
            "Six",
            "Seven",
            "Eight",
            "Nine",
            "Ten",
            "Eleven",
            "Twelve",
            "Thirteen",
            "Fourteen",
            "Fifteen",
            "Sixteen",
            "Seventeen",
            "Eighteen",
            "Nineteen",
            "Twenty",
        ];
        expect(viewersPage).toContain(`${words[total - gated]} are available in every deployment`);
        expect(viewersPage).toContain(
            `The remaining ${words[gated].toLowerCase()} are conditional`
        );
    });

    it("the viewers page's master table has one row per viewer", () => {
        const section = viewersPage
            .split("## Master viewer table")[1]
            .split("## File extension")[0];
        const rows = section.split("\n").filter((line) => line.startsWith("| **"));
        expect(rows).toHaveLength(total);
    });

    it.each([
        "README.md",
        "documentation/docusaurus-site/docs/index.mdx",
        "documentation/docusaurus-site/docs/additional/viewer-plugins.md",
        "documentation/docusaurus-site/docs/concepts/files-and-versions.md",
        "documentation/docusaurus-site/docs/overview/benefits.md",
        "documentation/docusaurus-site/docs/overview/solution-overview.md",
        "documentation/docusaurus-site/docs/overview/use-cases.md",
        "documentation/docusaurus-site/docs/user-guide/web-interface.md",
    ])("%s quotes the shipped total when it quotes a viewer count", (file) => {
        const counts = [
            ...read(file).matchAll(/\b(\d+) (?:built-in |interactive )?viewer(?: plugins|s)?\b/g),
        ].map((match) => Number(match[1]));
        expect(counts.length).toBeGreaterThan(0);
        for (const count of counts) expect(count).toBe(total);
    });
});

describe("TIFF viewers side by side (#1599 with #1576)", () => {
    const image = byId("tiff-tga-viewer");
    const map = byId("geotiff-map-viewer");
    const offered = (extensions: string[], isMultiFile: boolean) =>
        viewers
            .filter(
                (v) => !v.isPreviewViewer && admitsVisualizeSelection(v, extensions, isMultiFile)
            )
            .sort((a, b) => a.priority - b.priority)
            .map((v) => v.name);

    it("gives the two viewers distinct names and ids", () => {
        expect(image.name).not.toBe(map.name);
        expect(image.id).not.toBe(map.id);
        expect(map.name).toBe("GeoTIFF Map Viewer");
    });

    it("offers both for one .tif and .tiff file, the image viewer first", () => {
        for (const ext of [".tif", ".tiff"]) {
            expect(offered([ext], false)).toEqual(["TIFF/TGA Image Viewer", "GeoTIFF Map Viewer"]);
        }
    });

    it("offers only the map viewer for several TIFF files", () => {
        expect(offered([".tif", ".tif"], true)).toEqual(["GeoTIFF Map Viewer"]);
    });

    it("never hides the map viewer behind the image viewer's decode limits", () => {
        // The limits are enforced inside the image viewer (it shows a notice); selection is by
        // extension only, so a file above 100 megapixels still reaches the map viewer.
        expect(image.customParameters.maxDecodePixels).toBe(100000000);
        expect(map.priority).toBeGreaterThanOrEqual(image.priority);
        expect(offered([".tif"], false)).toContain("GeoTIFF Map Viewer");
        expect(map.customParameters?.maxDecodePixels).toBeUndefined();
    });

    it("limits the map viewer to the location services feature, and the image viewer to none", () => {
        expect(map.featuresEnabledRestriction).toEqual(["LOCATIONSERVICES"]);
        expect(image.featuresEnabledRestriction).toBeUndefined();
    });
});

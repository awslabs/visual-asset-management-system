/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Loads a model together with the files it references, per format:
 *
 * - glTF: buffer and image URIs are read from the JSON and rewritten to blob URLs.
 * - OBJ: `mtllib` libraries are downloaded and parsed with MTLLoader; each library's texture
 *   names (`map_*`, `bump`, `disp`, `norm`, `refl`) are resolved relative to that library.
 * - COLLADA: image `<init_from>` names are read from the document before parsing.
 * - 3DS and FBX: texture names live in binary chunks, so the model is parsed once with a manager
 *   that records what the loader asks for, those files are downloaded, and it is parsed again
 *   when any of them loaded.
 * - USD: see usdDependencies.ts (in-memory .usdz package).
 * - GLB, STL, PLY, 3MF and CAD formats are self-contained.
 *
 * Every loader receives a LoadingManager whose URL modifier maps resolved names to blob URLs and
 * answers anything else with a placeholder, so no request leaves for an unresolved file.
 */

import {
    AssetDependencyResolver,
    createLoadingManager,
    describeUnloaded,
    ResolvedDependency,
} from "./assetDependencyResolver";
import { LoaderResult, loadFile, USD_FORMATS } from "./fileLoaders";
import { resolveGLTFDependencies } from "./gltfDependencyLoader";
import { buildUsdPackage } from "./usdDependencies";

export interface DependencyLoadRequest {
    arrayBuffer: ArrayBuffer;
    /** Lower-case extension without the dot. */
    extension: string;
    /** Asset file key of the model. */
    fileKey: string;
    fileName: string;
    resolver: AssetDependencyResolver;
    /** Progress text for the viewer's loading spinner. */
    onProgress?: (message: string) => void;
}

export interface DependencyLoadResult extends LoaderResult {
    /** Referenced files downloaded for this model. */
    dependencyCount: number;
}

/** Material library names an OBJ file declares, as OBJLoader reads them (rest of the line). */
export function extractObjMaterialLibraries(objText: string): string[] {
    const libraries: string[] = [];
    for (const rawLine of objText.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (/^mtllib\s/.test(line)) {
            const name = line.substring(7).trim();
            if (name && !libraries.includes(name)) {
                libraries.push(name);
            }
        }
    }
    return libraries;
}

const MTL_TEXTURE_KEYWORDS = [
    "map_kd",
    "map_ks",
    "map_ke",
    "map_ka",
    "map_d",
    "map_bump",
    "map_ns",
    "map_pr",
    "map_pm",
    "map_ps",
    "bump",
    "disp",
    "norm",
    "refl",
];

/**
 * Texture names an MTL file references. Options are removed the way MTLLoader removes them
 * (`-bm` takes one value, `-s` and `-o` three), so each name matches what the loader requests.
 */
export function extractMtlTextureRefs(mtlText: string): string[] {
    const refs: string[] = [];
    for (const rawLine of mtlText.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (line === "" || line.startsWith("#")) {
            continue;
        }
        const space = line.search(/\s/);
        if (space < 0) {
            continue;
        }
        const keyword = line.slice(0, space).toLowerCase();
        if (!MTL_TEXTURE_KEYWORDS.includes(keyword)) {
            continue;
        }
        const items = line.slice(space).trim().split(/\s+/);
        const removeOption = (flag: string, count: number) => {
            const index = items.indexOf(flag);
            if (index >= 0) {
                items.splice(index, count + 1);
            }
        };
        removeOption("-bm", 1);
        removeOption("-s", 3);
        removeOption("-o", 3);
        const name = items.join(" ").trim();
        if (name && !refs.includes(name)) {
            refs.push(name);
        }
    }
    return refs;
}

/**
 * Image file names a COLLADA document names: the first `<init_from>` of each `<image>` (COLLADA
 * 1.4 text, or the `<ref>` inside it in 1.5), exactly as ColladaLoader reads them.
 */
export function extractColladaImageRefs(colladaText: string): string[] {
    const refs: string[] = [];
    let document: Document;
    try {
        document = new DOMParser().parseFromString(colladaText, "application/xml");
    } catch {
        return refs;
    }
    const images = Array.from(document.getElementsByTagName("image"));
    for (const image of images) {
        const initFrom = image.getElementsByTagName("init_from")[0];
        const name = initFrom?.textContent?.trim();
        if (name && !refs.includes(name)) {
            refs.push(name);
        }
    }
    return refs;
}

/** One MaterialCreator-like object over several MTL libraries; the first that defines a name wins. */
export function combineMaterialCreators(creators: any[]): any {
    if (creators.length <= 1) {
        return creators[0];
    }
    return {
        create(name: string) {
            const owner = creators.find((creator) => creator.materialsInfo?.[name] !== undefined);
            return owner ? owner.create(name) : undefined;
        },
    };
}

/** Releases the geometries, materials and textures of a parsed object that is being discarded. */
export function disposeObjectTree(object: any): void {
    object?.traverse?.((node: any) => {
        node.geometry?.dispose?.();
        const materials = Array.isArray(node.material) ? node.material : [node.material];
        materials.forEach((material: any) => {
            if (!material) {
                return;
            }
            Object.keys(material).forEach((key) => {
                if (material[key]?.isTexture) {
                    material[key].dispose();
                }
            });
            material.dispose?.();
        });
    });
}

function unique(values: string[]): string[] {
    return Array.from(new Set(values));
}

/** Shown instead of an empty canvas when a USD file opens but nothing in it can be drawn. */
export const USD_NO_GEOMETRY_MESSAGE =
    "This USD scene opened, but the viewer found nothing in it to display. The scene may use " +
    "USD features this viewer cannot combine yet, such as sublayers, variants or payloads. " +
    "Flattening the scene into a single file usually lets it display here; a desktop USD " +
    "viewer shows the full scene.";

/** True when the object holds at least one mesh, point cloud or line to draw. */
export function hasDrawableGeometry(object: any): boolean {
    let found = false;
    object?.traverse?.((node: any) => {
        if (node.isMesh || node.isPoints || node.isLine) {
            found = true;
        }
    });
    return found;
}

/** Loads a model and the files it references (see the module comment for each format). */
export async function loadWithDependencies(
    request: DependencyLoadRequest
): Promise<DependencyLoadResult> {
    const { arrayBuffer, extension, fileKey, fileName, resolver, onProgress } = request;
    const bundle = (window as any).THREEBundle;
    const THREE = bundle?.THREE;

    const progress = (current: number, total: number) =>
        onProgress?.(
            `Loading ${total} referenced file${
                total === 1 ? "" : "s"
            } for ${fileName} (${current}/${total})...`
        );
    const managerFor = (
        resolved: Map<string, ResolvedDependency> | ResolvedDependency[] = [],
        baseFileKey = fileKey
    ) => createLoadingManager(THREE, resolver.createUrlModifier(resolved, baseFileKey));

    let result: LoaderResult;

    if (extension === "gltf") {
        const rewritten = await resolveGLTFDependencies(arrayBuffer, fileKey, resolver, progress);
        result = await loadFile(rewritten.gltfArrayBuffer, extension, fileName, managerFor());
    } else if (extension === "obj") {
        let materials: any;
        const text = new TextDecoder().decode(arrayBuffer);
        const libraries = extractObjMaterialLibraries(text);
        if (libraries.length > 0 && bundle?.MTLLoader) {
            const libraryResult = await resolver.resolveDependencies(fileKey, libraries, {
                depth: 1,
                onProgress: progress,
            });
            const creators: any[] = [];
            for (const library of libraries) {
                const dependency = libraryResult.resolved.get(library);
                if (!dependency) {
                    continue;
                }
                const mtlText = new TextDecoder().decode(dependency.bytes);
                // A library's textures are named relative to the library, not the OBJ.
                const textures = await resolver.resolveDependencies(
                    dependency.key,
                    extractMtlTextureRefs(mtlText),
                    { depth: 2, onProgress: progress }
                );
                const loader = new bundle.MTLLoader(managerFor(textures.resolved, dependency.key));
                const creator = loader.parse(mtlText, "");
                creator.preload();
                creators.push(creator);
            }
            materials = combineMaterialCreators(creators);
        }
        result = await loadFile(arrayBuffer, extension, fileName, managerFor(), { materials });
    } else if (extension === "dae") {
        const refs = extractColladaImageRefs(new TextDecoder().decode(arrayBuffer));
        const textures = await resolver.resolveDependencies(fileKey, refs, {
            depth: 1,
            onProgress: progress,
        });
        result = await loadFile(arrayBuffer, extension, fileName, managerFor(textures.resolved));
    } else if (extension === "3ds" || extension === "fbx") {
        const requested: string[] = [];
        const probe = await loadFile(
            arrayBuffer,
            extension,
            fileName,
            createLoadingManager(THREE, resolver.createRecordingModifier(requested))
        );
        const refs = unique(requested);
        const textures =
            refs.length === 0
                ? null
                : await resolver.resolveDependencies(fileKey, refs, {
                      depth: 1,
                      onProgress: progress,
                  });
        if (!textures || textures.resolved.size === 0) {
            // The probe already drew every requested texture as the placeholder, which is what a
            // second parse would draw when none of them loaded.
            result = probe;
        } else {
            disposeObjectTree(probe.object);
            result = await loadFile(
                arrayBuffer,
                extension,
                fileName,
                managerFor(textures.resolved)
            );
        }
    } else if (USD_FORMATS.includes(extension) && bundle?.fflate) {
        const usdPackage = await buildUsdPackage({
            rootKey: fileKey,
            rootBytes: new Uint8Array(arrayBuffer),
            resolver,
            fflate: bundle.fflate,
            USDCParser: bundle.USDCParser,
            onProgress: progress,
        });
        result = await loadFile(usdPackage.buffer, extension, fileName, managerFor());
    } else {
        result = await loadFile(arrayBuffer, extension, fileName, managerFor());
    }

    const unloaded = resolver.notLoaded();

    if (USD_FORMATS.includes(extension) && result.object && !hasDrawableGeometry(result.object)) {
        disposeObjectTree(result.object);
        // The referenced files that were not loaded travel with the error, so the viewer can still
        // list them (for example the sublayers that hold the scene's content).
        throw Object.assign(new Error(USD_NO_GEOMETRY_MESSAGE), {
            warnings: describeUnloaded(unloaded),
        });
    }
    return {
        ...result,
        warnings: unloaded.length > 0 ? describeUnloaded(unloaded) : result.warnings,
        dependencyCount: resolver.resolvedCount,
    };
}

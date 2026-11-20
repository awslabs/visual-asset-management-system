/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { RasterFileRef } from "./presignedUrl";
import { MapLike, RasterLayerEntry, RasterLayerManager } from "./RasterLayerManager";
import {
    PrepareOptions,
    PreparedRaster,
    prepareRaster,
    FileIdentity,
    fileIdentity,
    rasterId,
    rasterLabel,
} from "./rasterResolver";
import { registerCogProtocol } from "./cogProtocol";

/** A raster that could not be shown, with the sentence to show for it. */
export interface RasterNotice {
    id: string;
    label: string;
    text: string;
    kind: "error" | "warning";
}

export interface UseRasterLayers {
    entries: RasterLayerEntry[];
    notices: RasterNotice[];
    /** Number of rasters being prepared right now. */
    busy: number;
    addFile: (file: RasterFileRef, options?: PrepareOptions) => Promise<boolean>;
    remove: (id: string) => void;
    setOpacity: (id: string, opacity: number) => void;
    move: (id: string, direction: "up" | "down") => void;
    /** Frames the raster; false while its extent is not known yet. */
    zoomTo: (id: string) => boolean;
    dismissNotice: (id: string) => void;
    /** Hides or shows a raster; it stays in the list while hidden. */
    setVisible: (id: string, visible: boolean) => Promise<boolean>;
    /** Draws a raster that failed again, from a fresh download link. */
    retry: (id: string) => Promise<boolean>;
}

/** Rounds of identity lookups for one file, in case files of the same name are listed meanwhile. */
const MAX_IDENTITY_ROUNDS = 3;

export interface UseRasterLayersOptions {
    /** Id of the first layer rasters must sit below; evaluated each time layers are placed. */
    beforeLayerId?: () => string | undefined;
}

/**
 * The raster layers on one map: one implementation for the file viewer and the Search map. The map is
 * given once it has loaded; layers are removed and their blob URLs revoked when the map goes away or
 * the component unmounts.
 */
export function useRasterLayers(
    map: MapLike | null,
    options: UseRasterLayersOptions = {}
): UseRasterLayers {
    const [entries, setEntries] = useState<RasterLayerEntry[]>([]);
    const [notices, setNotices] = useState<RasterNotice[]>([]);
    const [busy, setBusy] = useState(0);
    const manager = useRef<RasterLayerManager | null>(null);
    // What the API said about each file asked about on this map, by raster id (see addFile).
    const identities = useRef(new Map<string, FileIdentity>());
    const mounted = useRef(true);
    const beforeLayerId = useRef(options.beforeLayerId);
    beforeLayerId.current = options.beforeLayerId;

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    useEffect(() => {
        if (!map) return undefined;
        const created = new RasterLayerManager(map, {
            beforeLayerId: () => beforeLayerId.current?.(),
            onChange: (next) => {
                if (mounted.current) setEntries(next);
            },
        });
        manager.current = created;
        identities.current = new Map();
        return () => {
            created.dispose();
            if (manager.current === created) manager.current = null;
        };
    }, [map]);

    const notice = useCallback((entry: RasterNotice) => {
        if (!mounted.current) return;
        setNotices((current) => [...current.filter((n) => n.id !== entry.id), entry]);
    }, []);

    const addFile = useCallback(
        async (file: RasterFileRef, prepareOptions?: PrepareOptions): Promise<boolean> => {
            const id = rasterId(file);
            const label = rasterLabel(file.key);
            if (!manager.current) return false;
            if (manager.current.has(id) || manager.current.hasFile(file)) return true;
            setNotices((current) => current.filter((n) => n.id !== id));
            setBusy((count) => count + 1);
            try {
                const prepared = await prepareRaster(file, prepareOptions);
                if ("refused" in prepared && prepared.refused) {
                    notice({ id, label, text: prepared.message, kind: "error" });
                    return false;
                }
                const ready = prepared as PreparedRaster;
                if (ready.mode === "tile") await registerCogProtocol();
                // The map may have gone while the file was being read.
                if (!mounted.current || !manager.current) return false;
                // A source and its COG are one layer: the COG's id is what is shown. The same file
                // can be named by its full stored key (file manager) or its path inside the asset
                // (the source's COG link), and pinned or not: when a listed file has the same name,
                // ask the API who each file is (its path and latest version) and compare those.
                // Files listed while the answers were awaited are checked too (bounded).
                for (let round = 0; round < MAX_IDENTITY_ROUNDS; round += 1) {
                    const listed = manager.current.possibleDuplicates(ready.file);
                    if (listed.length === 0) break;
                    const pending = [ready.file, ...listed]
                        .map((candidate) => ({ candidate, id: rasterId(candidate) }))
                        .filter(({ id }) => !identities.current.has(id));
                    if (pending.length === 0) break;
                    const answers = await Promise.all(
                        pending.map(({ candidate }) => fileIdentity(candidate))
                    );
                    pending.forEach(({ id }, index) => identities.current.set(id, answers[index]));
                    if (!mounted.current || !manager.current) return false;
                }
                if (!mounted.current || !manager.current) return false;
                const known = identities.current;
                const result = manager.current.add(ready, undefined, (candidate) =>
                    known.get(rasterId(candidate))
                );
                if (!result.ok) {
                    notice({ id, label, text: result.message, kind: "warning" });
                    return false;
                }
                if (ready.usedCog && ready.staleCog) {
                    notice({
                        id: `${ready.id}#stale`,
                        label: ready.label,
                        text: "COG is from an earlier version of the source",
                        kind: "warning",
                    });
                }
                return true;
            } catch (error: any) {
                if (error?.name === "AbortError") return false;
                notice({ id, label, text: "This raster could not be shown.", kind: "error" });
                return false;
            } finally {
                if (mounted.current) setBusy((count) => count - 1);
            }
        },
        [notice]
    );

    return {
        entries,
        notices,
        busy,
        addFile,
        remove: useCallback((id: string) => manager.current?.remove(id), []),
        setOpacity: useCallback(
            (id: string, opacity: number) => manager.current?.setOpacity(id, opacity),
            []
        ),
        move: useCallback(
            (id: string, direction: "up" | "down") => manager.current?.move(id, direction),
            []
        ),
        setVisible: useCallback(
            (id: string, visible: boolean) =>
                manager.current?.setVisible(id, visible) ?? Promise.resolve(false),
            []
        ),
        retry: useCallback(
            (id: string) => manager.current?.retry(id) ?? Promise.resolve(false),
            []
        ),
        zoomTo: useCallback((id: string) => manager.current?.zoomTo(id) ?? false, []),
        dismissNotice: useCallback(
            (id: string) => setNotices((current) => current.filter((n) => n.id !== id)),
            []
        ),
    };
}

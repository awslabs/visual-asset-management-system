/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { MutableRefObject, useEffect, useRef } from "react";

/**
 * The scene tree height the user picked with SceneTreeResizeHandle, or null while the tree follows
 * the selection. The viewer holds it, so it outlives the scene graph when the panel switches to
 * another tab or is hidden, and the scene graph starts from it when it mounts again.
 */
export type KeptSceneTreeHeight = MutableRefObject<number | null>;

/**
 * Keeps the scene tree height the user picked for the Three.js and Needle USD viewers while they
 * show the same files. A change of fileKey (another file or version) clears it, so the tree starts at
 * its default again, as it does in a viewer that is opened again.
 */
export const useKeptSceneTreeHeight = (fileKey: string): KeptSceneTreeHeight => {
    const keptTreeHeight = useRef<number | null>(null);

    useEffect(() => {
        keptTreeHeight.current = null;
    }, [fileKey]);

    return keptTreeHeight;
};

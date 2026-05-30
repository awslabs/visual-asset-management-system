/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Shared types for the That Open Engine (web-ifc) IFC/BIM viewer plugin.
// The That Open library objects themselves are typed as `any` because the
// library is loaded at runtime from the window global (window.ThatOpenWebIfcBundle)
// rather than imported, so its compile-time types are intentionally unavailable
// in the core web build (same approach as the Three.js viewer plugin).

/** A node in the IFC spatial / category tree shown in the Model Tree tab. */
export interface SpatialNode {
    /** web-ifc local id of the element this node represents (if any). */
    localId: number | null;
    /** Display label (e.g., "Level 1", "IfcWall", element name). */
    name: string;
    /** IFC category / class (e.g., "IFCWALLSTANDARDCASE") when known. */
    category?: string;
    /** Child nodes. */
    children: SpatialNode[];
    /** True while this node (and descendants) are visible in the scene. */
    visible: boolean;
}

/** A single IFC property within a property set, shown in the Properties tab. */
export interface IfcProperty {
    name: string;
    value: string;
}

/** A named IFC property set (Pset) for the currently selected element. */
export interface PropertySet {
    name: string;
    properties: IfcProperty[];
}

/** Properties payload for the currently selected element. */
export interface SelectedElement {
    localId: number;
    name: string;
    category: string;
    propertySets: PropertySet[];
}

/**
 * Mutable handle to the live That Open viewer objects, stored in a ref by the
 * main component and passed to the panel/tools. All values are `any` for the
 * reason described at the top of this file.
 */
export interface IfcViewerInstance {
    components: any; // OBC.Components
    world: any; // OBC.World (SimpleScene/SimpleCamera/SimpleRenderer)
    fragments: any; // OBC.FragmentsManager
    model: any; // the loaded Fragments model
    highlighter: any; // OBF.Highlighter (drives 3D selection highlight)
    /** IFC schema string detected from the model (e.g., "IFC4"). */
    schema: string;
}

/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Cesium module wrapper
 *
 * This file provides access to the dynamically loaded Cesium library.
 * Import from this file instead of directly from 'cesium' package.
 *
 * Note: Cesium must be loaded via CesiumDependencyManager before using this module.
 * All exports use property getters to access window.Cesium at runtime.
 */

// Helper to get Cesium from window
function getCesiumLib() {
    return (window as any).Cesium;
}

// Create an object with getters for all Cesium exports
const CesiumWrapper: any = {};

// Define getters for each Cesium class/function
Object.defineProperty(CesiumWrapper, "Viewer", {
    get: () => getCesiumLib()?.Viewer,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Cesium3DTileset", {
    get: () => getCesiumLib()?.Cesium3DTileset,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Resource", {
    get: () => getCesiumLib()?.Resource,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Ion", {
    get: () => getCesiumLib()?.Ion,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "SingleTileImageryProvider", {
    get: () => getCesiumLib()?.SingleTileImageryProvider,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Rectangle", {
    get: () => getCesiumLib()?.Rectangle,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "ImageryLayer", {
    get: () => getCesiumLib()?.ImageryLayer,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "createWorldTerrainAsync", {
    get: () => getCesiumLib()?.createWorldTerrainAsync,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "CameraEventType", {
    get: () => getCesiumLib()?.CameraEventType,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "KeyboardEventModifier", {
    get: () => getCesiumLib()?.KeyboardEventModifier,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Cartesian3", {
    get: () => getCesiumLib()?.Cartesian3,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Math", {
    get: () => getCesiumLib()?.Math,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "HeadingPitchRange", {
    get: () => getCesiumLib()?.HeadingPitchRange,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Cesium3DTileStyle", {
    get: () => getCesiumLib()?.Cesium3DTileStyle,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Color", {
    get: () => getCesiumLib()?.Color,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Cartographic", {
    get: () => getCesiumLib()?.Cartographic,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "ScreenSpaceEventHandler", {
    get: () => getCesiumLib()?.ScreenSpaceEventHandler,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "ScreenSpaceEventType", {
    get: () => getCesiumLib()?.ScreenSpaceEventType,
    enumerable: true,
});

Object.defineProperty(CesiumWrapper, "Entity", {
    get: () => getCesiumLib()?.Entity,
    enumerable: true,
});

// Named exports
export const Viewer = CesiumWrapper.Viewer;
export const Cesium3DTileset = CesiumWrapper.Cesium3DTileset;
export const Resource = CesiumWrapper.Resource;
export const Ion = CesiumWrapper.Ion;
export const SingleTileImageryProvider = CesiumWrapper.SingleTileImageryProvider;
export const Rectangle = CesiumWrapper.Rectangle;
export const ImageryLayer = CesiumWrapper.ImageryLayer;
export const createWorldTerrainAsync = CesiumWrapper.createWorldTerrainAsync;
export const CameraEventType = CesiumWrapper.CameraEventType;
export const KeyboardEventModifier = CesiumWrapper.KeyboardEventModifier;
export const Cartesian3 = CesiumWrapper.Cartesian3;
export const Math = CesiumWrapper.Math;
export const HeadingPitchRange = CesiumWrapper.HeadingPitchRange;
export const Cesium3DTileStyle = CesiumWrapper.Cesium3DTileStyle;
export const Color = CesiumWrapper.Color;
export const Cartographic = CesiumWrapper.Cartographic;
export const ScreenSpaceEventHandler = CesiumWrapper.ScreenSpaceEventHandler;
export const ScreenSpaceEventType = CesiumWrapper.ScreenSpaceEventType;
export const Entity = CesiumWrapper.Entity;

// Export default as the wrapper object
export default CesiumWrapper;

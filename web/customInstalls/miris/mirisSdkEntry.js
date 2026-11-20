/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Webpack entry for the Miris Spatial Streaming SDK bundle (webpack.config.js), served from
// /viewers/miris/ and imported at runtime by MirisStreamViewerPlugin/dependencies.ts.
//
// The custom elements (<miris-scene>, <miris-camera>, <miris-stream>, <miris-group>, <miris-grid>)
// register when @miris-inc/components evaluates, which `defineElements()` loads as a separate chunk
// with one @miris-inc/core and one three instance.

export const defineElements = () =>
    import(/* webpackChunkName: "miris-components" */ "@miris-inc/components");

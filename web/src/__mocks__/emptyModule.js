// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
// Jest stub for modules that can't run under jest (Vite ?worker imports, the monaco-editor
// UMD bundle). Component tests mock @monaco-editor/react separately, so nothing here is used
// at render time. Default export is a no-op constructor (worker) + empty named surface.
module.exports = new Proxy(function () {}, {
    get: () => () => {},
});

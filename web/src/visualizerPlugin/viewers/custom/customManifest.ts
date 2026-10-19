/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
// do not conflict on upgrade.
//
// Viewer manifest for the viewers a fork adds, in the shape of ../manifest.ts. Each viewer lives in
// viewers/custom/<Name>Plugin/ and its entry in config/customViewerConfig.json; the keys here are
// that entry's componentPath / dependencyManager, the values the path below viewers/, e.g.
//   "./viewers/custom/WidgetViewerPlugin/WidgetViewerComponent":
//       "custom/WidgetViewerPlugin/WidgetViewerComponent",
// A key that repeats a core or add-on manifest key is ignored: the core entry wins.

export const CUSTOM_VIEWER_COMPONENTS: Record<string, string> = {};

export const CUSTOM_DEPENDENCY_MANAGERS: Record<string, string> = {};

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Shared Tailwind class strings for orchestration controls, tuned to visually match the Cloudscape
 * components used elsewhere in the app (button sizing/radius/colors, input/select borders) without
 * importing Cloudscape into this module. Reused across the pipeline/workflow/execution pages so the
 * controls look consistent with the rest of the site.
 */

// Primary action button (e.g. Create Workflow, Save).
export const btnPrimary =
    "inline-flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm font-bold rounded-lg " +
    "bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed";

// Secondary/normal button (Cancel, Back, Load more).
export const btnSecondary =
    // orch-outline opts this into `border-style: solid` (Tailwind preflight is disabled module-wide, so
    // a border utility alone paints nothing — see src/styles/tailwind.css).
    "orch-outline inline-flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm font-bold " +
    "rounded-lg border border-border-input bg-surface text-text-primary hover:bg-surface-hover " +
    "disabled:opacity-50 disabled:cursor-not-allowed";

// Destructive confirm button (Archive, Abort, Permanent Delete): the red the confirm dialogs use.
export const btnDanger =
    "px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 dark:bg-red-700 dark:hover:bg-red-600 " +
    "disabled:opacity-50 disabled:cursor-not-allowed";

// Filter/select control in a toolbar.
export const control =
    "px-3 py-1.5 text-sm border border-border-input rounded-lg bg-surface-input text-text-primary " +
    "focus:outline-none focus:ring-2 focus:ring-blue-500";

// Confirming action that starts something (Launch). Same geometry as btnPrimary, green fill.
export const btnSuccess =
    "inline-flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm font-bold rounded-lg " +
    "bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed";

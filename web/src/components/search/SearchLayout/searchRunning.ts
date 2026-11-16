/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While a search runs the page's controls stay focusable: a control that turns `disabled` under
 * keyboard focus hands focus to the page body. Fields and selects turn `readOnly` instead, and a
 * button stays disabled with this reason, which Cloudscape renders as `aria-disabled` with a
 * description and a tooltip. The Search button announces the same text through its `loadingText`.
 */
export const SEARCH_RUNNING = "Searching...";

/** A button's `disabledReason` while a search runs; none otherwise. */
export const searchRunningReason = (loading: boolean): string | undefined =>
    loading ? SEARCH_RUNNING : undefined;

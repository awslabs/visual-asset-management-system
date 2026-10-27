/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Row actions are inline links in a fixed-width Actions column that stays pinned as the table's
 * last column (`stickyColumns`), and the other columns wrap (`wrapLines`), so the actions sit on one
 * line and on screen at 1280-1920 px even when long ids make the table scroll sideways. Bordered
 * buttons did not fit: two of them need more than 200 px with the cell padding and the gap.
 */
export const ROW_ACTIONS_STICKY_COLUMNS = { first: 0, last: 1 };

/**
 * The padding of a VAMS list page's content (the convention of the Compliance Schemas, Metadata
 * Schema, Tags and auth pages): the compliance pages use it so their tables do not run into the
 * window's edges.
 */
export const VAMS_PAGE_PADDING = { top: "m", horizontal: "l" } as const;

/**
 * Minimum width (px) of a compliance State column: the widest state label, "Quarantined", with
 * its icon and the cell padding (about 130 px). The other columns wrap (`wrapLines`), and a State
 * column narrower than its longest word broke it mid-word ("Qua / rantine / d") at 1440 px.
 */
export const STATE_COLUMN_MIN_WIDTH = 150;

/**
 * Minimum width (px) of a timestamp column headed by a long word, such as "Quarantined At": the
 * header word with the sort control and the padding, so it is neither broken nor clipped.
 */
export const TIMESTAMP_COLUMN_MIN_WIDTH = 170;

/**
 * Minimum column width (px) of the database overview's summary tiles: the longest tile label word
 * ("Quarantined", "Non-Compliant") in the key-label style plus the grid's padding. Below it the
 * labels broke mid-word ("Quarantin / ed"); the tiles flow onto a second row instead.
 */
export const SUMMARY_TILE_MIN_WIDTH = 150;

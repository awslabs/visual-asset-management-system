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

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { DatePickerProps } from "@cloudscape-design/components";

/**
 * The strings a Cloudscape DatePicker needs for its icon-only calendar button, which otherwise has
 * no accessible name (the app has no Cloudscape I18nProvider). `what` names the date the picker
 * holds, for example "date" or "start date"; the name also says which date is selected.
 */
export const datePickerStrings = (what = "date"): DatePickerProps.I18nStrings => ({
    openCalendarAriaLabel: (selectedDate: string | null) =>
        selectedDate ? `Choose ${what}, selected date is ${selectedDate}` : `Choose ${what}`,
});

/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from "react";
import { SearchProviderRegistry } from "./SearchProviderRegistry";
import type { SearchProviderConfig } from "./types";

export interface SearchProvidersState {
    providers: SearchProviderConfig[];
}

/**
 * The providers a host offers, read once per mount from the registry's gate. The feature switches
 * are captured at mount like everywhere else on the search page; a remount re-reads them. The read
 * is synchronous, so there is no loading state to wait on.
 */
export function useSearchProvidersReady(): SearchProvidersState {
    const [providers] = useState(() =>
        SearchProviderRegistry.getInstance().getAvailableProviders()
    );
    return { providers };
}

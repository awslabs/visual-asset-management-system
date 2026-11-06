/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createContext, useContext } from "react";
import { appCache } from "../services/appCache";
import { normalizeFeaturesEnabled } from "../common/constants/featuresEnabled";

/**
 * The runtime configuration as the auth shell holds it: the amplify config at first, then the same
 * object widened with the secure config (`featuresEnabled`, deployment URLs) once that fetch lands.
 * `undefined` means no provider is mounted; `null` means a provider with no config yet.
 */
export const RuntimeConfigContext = createContext<any>(undefined);

/** The live runtime config, falling back to the cached one where no provider is mounted. */
export function useRuntimeConfig(): any {
    const config = useContext(RuntimeConfigContext);
    return config === undefined ? appCache.getItem("config") : config;
}

/** The deployment's feature switches, re-evaluated whenever the runtime config changes. */
export function useFeaturesEnabled(): string[] {
    return normalizeFeaturesEnabled(useRuntimeConfig()?.featuresEnabled);
}

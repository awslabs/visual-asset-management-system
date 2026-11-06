/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createContext, useContext } from "react";
import Synonyms from "../synonyms";

/**
 * Whether the asset page is showing an archived asset. The asset page provides it from the
 * fetched record; every panel under it reads its data with `showArchived` and hides its write,
 * download and viewer actions while it is true. Outside the asset page it is false.
 */
export const ArchivedAssetContext = createContext<boolean>(false);

export const useAssetArchived = (): boolean => useContext(ArchivedAssetContext);

/** The text shown in place of an archived asset's download, preview and viewer actions. */
export const ARCHIVED_TRANSFER_REASON = `Unarchive the ${Synonyms.asset} to download or view its files.`;

/** The text shown in place of an archived asset's write actions. */
export const ARCHIVED_WRITE_REASON = `This ${Synonyms.asset} is archived. Unarchive it to make changes.`;

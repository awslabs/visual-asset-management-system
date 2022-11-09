/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fetchAllAssets, fetchDatabaseAssets } from "../services/APIService";
import CreateUpdateAsset from "../components/createupdate/CreateUpdateAsset";
import { AssetListDefinition } from "../components/list/list-definitions/AssetListDefinition";
import ListPage from "./ListPage";

export default function Assets() {
  return (
    <ListPage
      singularName={"asset"}
      singularNameTitleCase={"Asset"}
      pluralName={"assets"}
      pluralNameTitleCase={"Assets"}
      listDefinition={AssetListDefinition}
      CreateNewElement={CreateUpdateAsset}
      fetchAllElements={fetchAllAssets}
      fetchElements={fetchDatabaseAssets}
    />
  );
}

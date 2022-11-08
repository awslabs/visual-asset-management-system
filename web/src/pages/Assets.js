import React from "react";
import { fetchAssets } from "../services/APIService";
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
      fetchElements={fetchAssets}
    />
  );
}

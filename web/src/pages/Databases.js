import React from "react";
import { fetchDatabases } from "../services/APIService";
import CreateDatabase from "../components/createupdate/CreateDatabase";
import { DatabaseListDefinition } from "../components/list/list-definitions/DatabaseListDefinition";
import ListPage from "./ListPage";

export default function Databases() {
  return (
    <ListPage
      singularName={"database"}
      singularNameTitleCase={"Database"}
      pluralName={"databases"}
      pluralNameTitleCase={"Databases"}
      listDefinition={DatabaseListDefinition}
      CreateNewElement={CreateDatabase}
      fetchElements={fetchDatabases}
    />
  );
}

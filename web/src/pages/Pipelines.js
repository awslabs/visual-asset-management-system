import React from "react";
import { fetchPipelines } from "../services/APIService";
import CreatePipeline from "../components/createupdate/CreatePipeline";
import { PipelineListDefinition } from "../components/list/list-definitions/PipelineListDefinition";
import ListPage from "./ListPage";

export default function Pipelines() {
  return (
    <ListPage
      singularName={"pipeline"}
      singularNameTitleCase={"Pipeline"}
      pluralName={"pipelines"}
      pluralNameTitleCase={"Pipelines"}
      listDefinition={PipelineListDefinition}
      CreateNewElement={CreatePipeline}
      fetchElements={fetchPipelines}
    />
  );
}

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fetchAllPipelines, fetchDatabasePipelines } from "../services/APIService";
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
            fetchElements={fetchDatabasePipelines}
            fetchAllElements={fetchAllPipelines}
        />
    );
}

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { fetchAllPipelines, fetchDatabasePipelines } from "../services/APIService";
import CreatePipeline from "../components/createupdate/CreatePipeline";
import { PipelineListDefinition } from "../components/list/list-definitions/PipelineListDefinition";
import ListPage from "./ListPage";
import { anyRoleOf } from "../FedAuth/roles";

export default function Pipelines() {
    const [create, setCreate] = useState(false);
    useEffect(() => {
        anyRoleOf(["create-pipeline", "super-admin"]).then((result) => {
            setCreate(result);
        });
    }, []);
    return (
        <ListPage
            singularName={"pipeline"}
            singularNameTitleCase={"Pipeline"}
            pluralName={"pipelines"}
            pluralNameTitleCase={"Pipelines"}
            listDefinition={PipelineListDefinition}
            CreateNewElement={(create && CreatePipeline) || undefined}
            fetchElements={fetchDatabasePipelines}
            fetchAllElements={fetchAllPipelines}
        />
    );
}

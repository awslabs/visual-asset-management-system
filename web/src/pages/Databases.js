/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fetchAllDatabases } from "../services/APIService";
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
            fetchAllElements={fetchAllDatabases}
            fetchElements={fetchAllDatabases}
        />
    );
}

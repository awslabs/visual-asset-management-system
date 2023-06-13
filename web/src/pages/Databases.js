/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { fetchAllDatabases } from "../services/APIService";
import CreateDatabase from "../components/createupdate/CreateDatabase";
import { DatabaseListDefinition } from "../components/list/list-definitions/DatabaseListDefinition";
import ListPage from "./ListPage";

import { anyRoleOf } from "../FedAuth/roles";

export default function Databases() {
    const [createDatabase, setCreateDatabase] = useState(false);
    useEffect(() => {
        anyRoleOf(["create-database", "super-admin"]).then((result) => {
            setCreateDatabase(result);
        });
    }, []);
    return (
        <ListPage
            singularName={"database"}
            singularNameTitleCase={"Database"}
            pluralName={"databases"}
            pluralNameTitleCase={"Databases"}
            listDefinition={DatabaseListDefinition}
            CreateNewElement={(createDatabase && CreateDatabase) || undefined}
            fetchAllElements={fetchAllDatabases}
            fetchElements={fetchAllDatabases}
            editEnabled={true}
        />
    );
}

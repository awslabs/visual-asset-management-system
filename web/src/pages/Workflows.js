/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { fetchAllWorkflows, fetchDatabaseWorkflows } from "../services/APIService";
import { WorkflowListDefinition } from "../components/list/list-definitions/WorkflowListDefinition";
import ListPage from "./ListPage";
import DatabaseSelectorWithModal from "../components/selectors/DatabaseSelectorWithModal";

export default function Workflows(props) {
    const { databaseId } = useParams();
    const navigate = useNavigate();
    const [openModal, setOpenModal] = useState(false);

    const createNewWorkflow = () => {
        if (databaseId) {
            navigate(`/databases/${databaseId}/workflows/create`);
        } else {
            setOpenModal(true);
        }
    };

    const handleSelectWorkflowDatabase = (event) => {
        const newDatabaseId = event?.detail?.selectedOption?.value;
        navigate(`/databases/${newDatabaseId}/workflows/create`);
    };

    return (
        <>
            <ListPage
                singularName={"workflow"}
                singularNameTitleCase={"Workflow"}
                pluralName={"workflows"}
                pluralNameTitleCase={"Workflows"}
                listDefinition={WorkflowListDefinition}
                fetchElements={fetchDatabaseWorkflows}
                fetchAllElements={fetchAllWorkflows}
                onCreateCallback={createNewWorkflow}
            />
            {!databaseId && (
                <DatabaseSelectorWithModal
                    open={openModal}
                    setOpen={setOpenModal}
                    onSelectorChange={handleSelectWorkflowDatabase}
                />
            )}
        </>
    );
}

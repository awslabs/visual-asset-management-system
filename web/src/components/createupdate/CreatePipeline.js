/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { PipelineFormDefinition } from "./form-definitions/PipelineFormDefinition";
import PipelineEntity from "./entity-types/PipelineEntity";
import CreateUpdateElement from "./CreateUpdateElement";
import { actionTypes } from "./form-definitions/types/FormDefinition";

export default function CreatePipeline(props) {
    const { open, setOpen, setReload, databaseId } = props;

    return (
        <CreateUpdateElement
            open={open}
            setOpen={setOpen}
            setReload={setReload}
            formDefinition={PipelineFormDefinition}
            formEntity={PipelineEntity}
            actionType={actionTypes.CREATE}
            databaseId={databaseId}
        />
    );
}

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { EntityPropTypes } from "./EntityPropTypes";

export default function PipelineEntity(props) {
    const {
        pipelineId,
        databaseId,
        description,
        pipelineType,
        pipelineExecutionType,
        waitForCallback,
        externalContainerUri,
        externalLambdaName,
        assetType,
        outputType,
        inputParameters,
    } = props;
    this.pipelineId = pipelineId;
    this.databaseId = databaseId;
    this.description = description;
    this.pipelineType = pipelineType;
    this.pipelineExecutionType = pipelineExecutionType;
    this.waitForCallback = waitForCallback;
    this.externalContainerUri = externalContainerUri;
    this.externalLambdaName = externalLambdaName;
    this.assetType = assetType;
    this.outputType = outputType;
    this.inputParameters = inputParameters;
}

PipelineEntity.propTypes = {
    pipelineId: EntityPropTypes.ENTITY_ID,
    databaseId: EntityPropTypes.ENTITY_ID,
    description: EntityPropTypes.STRING_256,
    pipelineType: EntityPropTypes.STRING_32,
    pipelineExecutionType: EntityPropTypes.STRING_32,
    waitForCallback: EntityPropTypes.BOOLEAN,
    assetType: EntityPropTypes.FILE_TYPE,
    outputType: EntityPropTypes.FILE_TYPE,
    containerUri: EntityPropTypes.CONTAINER_URI,
    lambdaName: EntityPropTypes.LAMBDA_NAME,
    inputParameters: EntityPropTypes.JSON_STRING,
};

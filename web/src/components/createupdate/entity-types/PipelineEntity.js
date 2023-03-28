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
        externalContainerUri,
        externalLambdaName,
        assetType,
        outputType,
    } = props;
    this.pipelineId = pipelineId;
    this.databaseId = databaseId;
    this.description = description;
    this.pipelineType = pipelineType;
    this.externalContainerUri = externalContainerUri;
    this.externalLambdaName = externalLambdaName;
    this.assetType = assetType;
    this.outputType = outputType;
}

PipelineEntity.propTypes = {
    pipelineId: EntityPropTypes.ENTITY_ID,
    databaseId: EntityPropTypes.ENTITY_ID,
    description: EntityPropTypes.STRING_256,
    pipelineType: EntityPropTypes.STRING_32,
    assetType: EntityPropTypes.FILE_TYPE,
    outputType: EntityPropTypes.FILE_TYPE,
    containerUri: EntityPropTypes.CONTAINER_URI,
    lambdaName: EntityPropTypes.LAMBDA_NAME,
};

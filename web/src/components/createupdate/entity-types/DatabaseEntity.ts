/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { EntityPropTypes } from "./EntityPropTypes";

export default function DatabaseEntity(props) {
    const { databaseId, description } = props;
    this.databaseId = databaseId;
    this.description = description;
}

DatabaseEntity.propTypes = {
    databaseId: EntityPropTypes.ENTITY_ID,
    description: EntityPropTypes.STRING_256,
};

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { EntityPropTypes } from "./EntityPropTypes";

interface DatabaseEntity {
    databaseId: any;
    description: any;
}

export default function DatabaseEntity(this: DatabaseEntity, props: any) {
    const { databaseId, description } = props;
    this.databaseId = databaseId;
    this.description = description;
}

(DatabaseEntity as any).propTypes = {
    databaseId: EntityPropTypes.ENTITY_ID,
    description: EntityPropTypes.STRING_256,
};

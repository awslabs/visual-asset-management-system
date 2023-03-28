/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";
import ControlDefinition from "./ControlDefinition";
import { ENTITY_TYPES_NAMES } from "../../entity-types/EntitieTypes";

export const actionTypes = {
    CREATE: "CREATE",
    UPDATE: "UPDATE",
};

export default function FormDefinition(props) {
    const {
        entityType,
        controlDefinitions,
        singularName,
        singularNameTitleCase,
        pluralName,
        customSubmitFunction,
        transformForUpdate,
    } = props;
    this.entityType = entityType;
    this.controlDefinitions = controlDefinitions;
    this.singularName = singularName;
    this.singularNameTitleCase = singularNameTitleCase;
    this.pluralName = pluralName;
    this.customSubmitFunction = customSubmitFunction;
    this.transformForUpdate = transformForUpdate;
}

FormDefinition.propTypes = {
    entityType: PropTypes.oneOf(Object.values(ENTITY_TYPES_NAMES)).isRequired,
    controlDefinitions: PropTypes.arrayOf(ControlDefinition).isRequired,
    singularName: PropTypes.string.isRequired,
    singularNameTitleCase: PropTypes.string.isRequired,
    pluralName: PropTypes.string.isRequired,
    //@todo need function signature
    customSubmitFunction: PropTypes.func.isRequired,
    transformForUpdate: PropTypes.func,
};

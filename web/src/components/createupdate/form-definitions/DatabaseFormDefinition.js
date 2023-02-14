/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import FormDefinition from "./types/FormDefinition";
import ControlDefinition from "./types/ControlDefinition";
import { Input, Textarea } from "@cloudscape-design/components";
import ElementDefinition from "./types/ElementDefinition";
import {ENTITY_TYPES_NAMES} from "../entity-types/EntitieTypes";

export const DatabaseFormDefinition = new FormDefinition({
  entityType: ENTITY_TYPES_NAMES.DATABASE,
  singularName: "database",
  pluralName: "databases",
  singularNameTitleCase: "Database",
  controlDefinitions: [
    new ControlDefinition({
      label: "Database Name",
      id: "databaseId",
      constraintText:
        "Required. All lower case, no special chars or spaces except - and _ only letters for first character min 4 and max 64",
      elementDefinition: new ElementDefinition({
        formElement: Input,
        elementProps: { autoFocus: true },
      }),
    }),
    new ControlDefinition({
      label: "Database Description",
      id: "description",
      constraintText: "Required. Max 256 characters.",
      elementDefinition: new ElementDefinition({
        formElement: Textarea,
        elementProps: { rows: 4 },
      }),
    }),
  ],
});

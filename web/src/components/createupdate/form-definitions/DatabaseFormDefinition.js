import React from "react";
import FormDefinition from "./types/FormDefinition";
import ControlDefinition from "./types/ControlDefinition";
import { Input, Textarea } from "@awsui/components-react";
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
        "Required. No spaces, start with letter, only special chars _ and -. 3-64 characters.",
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

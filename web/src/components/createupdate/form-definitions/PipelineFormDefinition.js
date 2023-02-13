/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import FormDefinition from "./types/FormDefinition";
import ControlDefinition from "./types/ControlDefinition";
import { Input, Select, Textarea } from "@cloudscape-design/components";
import ElementDefinition from "./types/ElementDefinition";
import DatabaseSelector from "../../selectors/DatabaseSelector";
import {
  columnarFileFormats,
  modelFileFormats,
  cadFileFormats,
  archiveFileFormats,
} from "../../../common/constants/fileFormats";
import OptionDefinition from "./types/OptionDefinition";
import { ENTITY_TYPES_NAMES } from "../entity-types/EntitieTypes";

const fileTypeOptions = modelFileFormats
  .concat(columnarFileFormats)
  .concat(cadFileFormats)
  .concat(archiveFileFormats)
  .map((fileType) => {
    return new OptionDefinition({
      value: fileType,
      label: fileType,
    });
  });

const pipelineTypeOptions = [
  {
    label: "SageMaker",
    value: "SageMaker",
  },
  {
    label: "Lambda",
    value: "Lambda",
  },
];

export const PipelineFormDefinition = new FormDefinition({
  entityType: ENTITY_TYPES_NAMES.PIPELINE,
  singularName: "pipeline",
  pluralName: "pipelines",
  singularNameTitleCase: "Pipeline",
  controlDefinitions: [
    new ControlDefinition({
      label: "Pipeline Name",
      id: "pipelineId",
      constraintText:
        "Required. All lower case, no special chars or spaces except - and _ only letters for first character min 4 and max 64. For sagemaker pipelines _ are not allowed.",
      elementDefinition: new ElementDefinition({
        formElement: Input,
        elementProps: { autoFocus: true },
      }),
    }),
    new ControlDefinition({
      label: "Database Name",
      id: "databaseId",
      constraintText: "Required.",
      elementDefinition: new ElementDefinition({
        formElement: DatabaseSelector,
        elementProps: {},
      }),
    }),
    new ControlDefinition({
      label: "Pipeline Type",
      id: "pipelineType",
      constraintText: "",
      options: pipelineTypeOptions,
      defaultOption: pipelineTypeOptions[0],
      elementDefinition: new ElementDefinition({
        formElement: Select,
        elementProps: {
          disabled: false,
        },
      }),
    }),
    new ControlDefinition({
      label: "Description",
      id: "description",
      constraintText: "Required. Max 256 characters.",
      elementDefinition: new ElementDefinition({
        formElement: Textarea,
        elementProps: { rows: 4 },
      }),
    }),
    new ControlDefinition({
      label: "Input Filetype",
      id: "assetType",
      constraintText: "Required.",
      options: fileTypeOptions,
      elementDefinition: new ElementDefinition({
        formElement: Select,
        elementProps: {},
      }),
    }),
    new ControlDefinition({
      label: "Output Filetype",
      id: "outputType",
      constraintText: "Required.",
      options: fileTypeOptions,
      elementDefinition: new ElementDefinition({
        formElement: Select,
        elementProps: {},
      }),
    }),
  ],
});

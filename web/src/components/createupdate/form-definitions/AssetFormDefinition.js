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
import PipelineSelector from "../../selectors/PipelineSelector";
import AssetFilesUploadGroup from "../../form/AssetFilesUploadGroup";
import { Cache, Storage } from "aws-amplify";
import { ENTITY_TYPES_NAMES } from "../entity-types/EntitieTypes";
import { validateEntityId } from "../entity-types/EntityPropTypes";

export const AssetFormDefinition = new FormDefinition({
  entityType: ENTITY_TYPES_NAMES.ASSET,
  singularName: "asset",
  pluralName: "assets",
  singularNameTitleCase: "Asset",
  customSubmitFunction: async (formValues, formErrors) => {
    const newFormValues = Object.assign({}, formValues);
    const newFormErrors = Object.assign({}, formErrors);

    if (formValues?.assetId === null) {
      newFormErrors.assetId = "Invalid value for assetId. Value cannot be empty.";
      return { success: false, values: newFormValues, errors: newFormErrors };
    } else if (!validateEntityId(formValues?.assetId)) {
      // enforce client-side assetId validation
      newFormErrors.assetId = "Invalid value for workflowId. Expected a valid entity id.";
      return { success: false, values: newFormValues, errors: newFormErrors };
    }
    newFormErrors.assetId = "";
    if (formValues?.databaseId === null) {
      newFormErrors.databaseId = "Invalid value for databaseId. Value cannot be empty.";
      return { success: false, values: newFormValues, errors: newFormErrors };
    }
    newFormErrors.databaseId = "";
    let assetId = formValues?.assetId;
    let databaseId = formValues?.databaseId;
    if (assetId.value) assetId = assetId.value;
    if (databaseId.value) databaseId = databaseId.value;
    const assetFile = formValues?.Asset;
    const previewFile = formValues?.Preview;
    if (assetFile === null) {
      newFormErrors.Asset = "Must choose local asset file to upload.";
      return { success: false, values: newFormValues, errors: newFormErrors };
    }
    newFormErrors.Asset = "";
    if (previewFile === null) {
      newFormErrors.Preview = "Must choose local preview file to upload.";
      return { success: false, values: newFormValues, errors: newFormErrors };
    }
    newFormErrors.Preview = "";
    const assetExtension = `.${assetFile?.name.split(".").pop()}`;
    const assetFileKey = `${assetId}${assetExtension}`;
    const previewExtension = `.${previewFile?.name.split(".").pop()}`;
    const previewFileKey = `${assetId}${previewExtension}`;
    const config = Cache.getItem("config");
    newFormValues.bucket = config.bucket;
    newFormValues.key = assetFileKey;
    newFormValues.previewLocation = {
      Bucket: config.bucket,
      Key: previewFileKey,
    };
    newFormValues.assetType = assetExtension;
    const metadata = {
      assetId: assetId,
      databaseId: databaseId,
    };
    const assetUploaded = await Storage.put(assetFileKey, assetFile, {
      metadata: metadata,
    });
    if (!assetUploaded.key) {
      newFormErrors.Asset = `Asset upload failed. Error: ${assetUploaded}`;
      return { success: false, values: newFormValues, errors: newFormErrors };
    }
    newFormErrors.Asset = "";
    const previewUploaded = await Storage.put(previewFileKey, previewFile);
    if (!previewUploaded.key) {
      newFormErrors.Preview = `Asset upload failed. Error: ${previewUploaded}`;
      return { success: false, values: newFormValues, errors: newFormErrors };
    }
    newFormErrors.Preview = "";
    return { success: true, values: newFormValues, errors: newFormErrors };
  },
  /**
   *
   * @param {AssetEntity} assetObject
   */
  formatForUpdate: (assetObject) => {
    /** @type {AssetEntity} */
    const formValuesObject = Object.assign({}, assetObject);
  },
  controlDefinitions: [
    new ControlDefinition({
      label: "Asset Name",
      id: "assetId",
      constraintText:
        "Required. All lower case, no special chars or spaces except - and _ only letters for first character min 4 and max 64.",
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
      label: "Is Distributable",
      id: "isDistributable",
      constraintText: "Required.",
      options: [
        { label: "Yes", value: true },
        { label: "No", value: false },
      ],
      elementDefinition: new ElementDefinition({
        formElement: Select,
      }),
    }),
    new ControlDefinition({
      label: "Description",
      id: "description",
      constraintText: "Required. Max 256 characters.",
      elementDefinition: new ElementDefinition({
        formElement: Textarea,
        elementProps: { rows: 2 },
      }),
    }),
    new ControlDefinition({
      label: "Pipelines",
      id: "specifiedPipelines",
      constraintText: "",
      elementDefinition: new ElementDefinition({
        formElement: PipelineSelector,
        elementProps: {
          isMulti: true,
        },
      }),
    }),
    new ControlDefinition({
      label: "Comment",
      id: "Comment",
      constraintText: "",
      elementDefinition: new ElementDefinition({
        formElement: Input,
        elementProps: {},
      }),
    }),
    new ControlDefinition({
      label: "Upload Files",
      id: "uploadFiles",
      constraintText: "Required.",
      elementDefinition: new ElementDefinition({
        formElement: AssetFilesUploadGroup,
        elementProps: {},
      }),
    }),
  ],
});

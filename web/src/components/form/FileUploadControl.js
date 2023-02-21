/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext, useEffect, useState } from "react";
import {
  Button,
  FormField,
  Grid,
  SpaceBetween,
  TextContent,
} from "@cloudscape-design/components";
import { AssetContext } from "../../context/AssetContex";

const generateUUID = () => {
  return ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, (c) =>
    (
      c ^
      (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))
    ).toString(16)
  );
};

//@link https://stackoverflow.com/questions/10420352/converting-file-size-in-bytes-to-human-readable-string/10420404
export const formatFileSize = (bytes, si = false, dp = 1) => {
  const thresh = si ? 1000 : 1024;
  if (Math.abs(bytes) < thresh) {
    return bytes + " B";
  }
  const units = si
    ? ["kB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
    : ["KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB"];
  let u = -1;
  const r = 10 ** dp;
  do {
    bytes /= thresh;
    ++u;
  } while (
    Math.round(Math.abs(bytes) * r) / r >= thresh &&
    u < units.length - 1
  );

  return bytes.toFixed(dp) + " " + units[u];
};

const FileUploadControl = (props) => {
  const { disabled, controlName, fileFormats } = props;
  const { formValues, setFormValues, formErrors } = useContext(AssetContext);
  const [file, setFile] = useState(null);
  const [inputId, setInputId] = useState(generateUUID());

  useEffect(() => {
    if (file) {
      const newFormValues = Object.assign({}, formValues);
      newFormValues[controlName] = file;
      setFormValues(newFormValues);
    }
  }, [file]);
  let uploadInterval;
  return (
    <>
      <FormField errorText={formErrors[controlName]}>
        <input
          type="file"
          accept={fileFormats}
          style={{ display: "none" }}
          id={inputId}
          onChange={(e) => {
            setFile(e.target.files[0]);
          }}
        />
        <SpaceBetween size="l">
          <Button
            disabled={disabled}
            variant="normal"
            multiple
            type="file"
            iconName="upload"
            onClick={(e) => {
              document.getElementById(inputId).click();
            }}
          >
            Choose File
          </Button>
          <Grid
            gridDefinition={[
              { colspan: { default: "6" } },
              { colspan: { default: "6" } },
            ]}
          >
            {file && (
              <>
                <TextContent>
                  <strong>Filename:</strong>
                  <br />
                  <small>Size</small>
                  <br />
                  <small>Last Modified:</small>
                </TextContent>
                <TextContent>
                  <strong>{file?.name}</strong>
                  <br />
                  <small>{formatFileSize(file?.size)}</small>
                  <br />
                  <small>
                    {file?.lastModified.toLocaleString("en-US", {
                      timeZone: "UTC",
                    })}
                  </small>
                </TextContent>
              </>
            )}
          </Grid>
        </SpaceBetween>
      </FormField>
    </>
  );
};

export default FileUploadControl;

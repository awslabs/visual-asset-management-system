/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { fetchDatabasePipelines } from "../../services/APIService";
import { Select, Multiselect } from "@cloudscape-design/components";

const PipelineSelector = (props) => {
  const { databaseId, isMulti } = props;
  const [reload, setReload] = useState(true);
  const [allItems, setAllItems] = useState([]);

  useEffect(() => {
    const getData = async () => {
      const items = await fetchDatabasePipelines({databaseId: databaseId});
      if (items !== false && Array.isArray(items)) {
        setReload(false);
        setAllItems(items);
      }
    };
    if (reload) {
      getData();
    }
  }, [reload, databaseId]);

  const SelectControl = (props) => {
    const { isMulti } = props;
    if (isMulti) {
      return <Multiselect {...props} />;
    }
    return <Select {...props} />;
  };

  return (
    <>
      {allItems.length > 0 && (
        <SelectControl
          {...props}
          isMulti={isMulti}
          options={allItems.map((item) => {
            return {
              label: item.pipelineId,
              value: item.pipelineId,
              tags: [
                `input:${item.assetType}`,
                `output:${item.outputType}`,
                `type:${item.pipelineType}`,
              ],
            };
          })}
          filteringType="auto"
          selectedAriaLabel="Selected"
        />
      )}
    </>
  );
};

export default PipelineSelector;

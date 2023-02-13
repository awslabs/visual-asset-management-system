/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { fetchAllDatabases } from "../../services/APIService";
import { Select } from "@cloudscape-design/components";

const DatabaseSelector = (props) => {
  const [reload, setReload] = useState(true);
  const [allItems, setAllItems] = useState([]);

  useEffect(() => {
    const getData = async () => {
      const items = await fetchAllDatabases();
      if (items !== false && Array.isArray(items)) {
        setReload(false);
        setAllItems(items);
      }
    };
    if (reload) {
      getData();
    }
  }, [reload]);

  return (
    <Select
      {...props}
      options={allItems.map((item) => {
        return {
          label: item.databaseId,
          value: item.databaseId,
        };
      })}
      filteringType="auto"
      selectedAriaLabel="Selected"
    />
  );
};

export default DatabaseSelector;

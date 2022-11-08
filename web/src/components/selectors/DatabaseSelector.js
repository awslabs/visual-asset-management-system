import React, { useEffect, useState } from "react";
import { fetchDatabases } from "../../services/APIService";
import { Select } from "@awsui/components-react";

const DatabaseSelector = (props) => {
  const [reload, setReload] = useState(true);
  const [allItems, setAllItems] = useState([]);

  useEffect(() => {
    const getData = async () => {
      const items = await fetchDatabases();
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

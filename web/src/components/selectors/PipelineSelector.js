import React, { useEffect, useState } from "react";
import { fetchPipelines } from "../../services/APIService";
import { Select, Multiselect } from "@awsui/components-react";

const PipelineSelector = (props) => {
  const { databaseId, isMulti } = props;
  const [reload, setReload] = useState(true);
  const [allItems, setAllItems] = useState([]);

  useEffect(() => {
    const getData = async () => {
      const items = await fetchPipelines(databaseId);
      if (items !== false && Array.isArray(items)) {
        setReload(false);
        setAllItems(items);
      }
    };
    if (reload) {
      getData();
    }
  }, [reload]);

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

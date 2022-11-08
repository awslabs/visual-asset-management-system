import { Modal } from "@awsui/components-react";
import React, { useState } from "react";
import AssetSelector from "./AssetSelector";

export default function AssetSelectorWithModal(props) {
  const { pathViewType } = props;
  const [open, setOpen] = useState(true);

  const handleClose = () => {
    setOpen(false);
    window.location = "/assets";
  };

  return (
    <Modal
      onDismiss={handleClose}
      visible={open}
      closeAriaLabel="Close modal"
      size="medium"
      header="Select Asset"
    >
      <AssetSelector pathViewType={pathViewType} />
    </Modal>
  );
}

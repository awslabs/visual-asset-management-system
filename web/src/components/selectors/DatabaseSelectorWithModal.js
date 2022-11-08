import { Modal } from "@awsui/components-react";
import React from "react";
import DatabaseSelector from "./DatabaseSelector";

export default function DatabaseSelectorWithModal(props) {
  const { open, setOpen, onSelectorChange } = props;

  const handleClose = () => {
    setOpen(false);
  };

  return (
    <Modal
      onDismiss={handleClose}
      visible={open}
      closeAriaLabel="Close modal"
      size="medium"
      header="Select Database"
    >
      <DatabaseSelector onChange={onSelectorChange} />
    </Modal>
  );
}

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Modal } from "@cloudscape-design/components";
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

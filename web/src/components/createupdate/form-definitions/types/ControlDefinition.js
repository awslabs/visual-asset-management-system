/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";
import ElementDefinition from "./ElementDefinition";
import OptionDefinition from "./OptionDefinition";

export default function ControlDefinition(props) {
  const {
    label,
    id,
    constraintText,
    elementDefinition,
    options,
    defaultOption,
  } = props;
  this.id = id;
  this.label = label;
  this.constraintText = constraintText;
  this.elementDefinition = elementDefinition;
  this.options = options;
  this.defaultOption = defaultOption;
}

ControlDefinition.propTypes = {
  label: PropTypes.string.isRequired,
  id: PropTypes.string.isRequired,
  constraintText: PropTypes.string.isRequired,
  elementDefinition: PropTypes.instanceOf(ElementDefinition),
  options: PropTypes.arrayOf(OptionDefinition),
  defaultOption: PropTypes.instanceOf(OptionDefinition),
};

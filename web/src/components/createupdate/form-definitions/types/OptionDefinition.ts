/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";

export default function OptionDefinition(props) {
    const { label, value } = props;
    this.label = label;
    this.value = value;
}

OptionDefinition.propTypes = {
    label: PropTypes.string.isRequired,
    value: PropTypes.string.isRequired,
};

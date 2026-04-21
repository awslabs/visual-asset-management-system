/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";

export default function FilterDefinition(props) {
    const { name, placeholder } = props;
    this.name = name;
    this.placeholder = placeholder;
}

FilterDefinition.propTypes = {
    name: PropTypes.string.isRequired,
    placeholder: PropTypes.string.isRequired,
};

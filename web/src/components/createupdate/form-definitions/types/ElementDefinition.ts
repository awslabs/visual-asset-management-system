/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";

export default function ElementDefinition(props) {
    const { formElement, elementProps } = props;
    this.FormElement = formElement;
    this.elementProps = elementProps;
}

ElementDefinition.propTypes = {
    FormElement: PropTypes.element.isRequired,
    elementProps: PropTypes.object,
};

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";

interface OptionDefinition {
    label: any;
    value: any;
}

interface OptionDefinitionConstructor {
    new (props: any): OptionDefinition;
    propTypes: any;
}

function OptionDefinitionImpl(this: OptionDefinition, props: any) {
    const { label, value } = props;
    this.label = label;
    this.value = value;
}

(OptionDefinitionImpl as any).propTypes = {
    label: PropTypes.string.isRequired,
    value: PropTypes.string.isRequired,
};

const OptionDefinition = OptionDefinitionImpl as unknown as OptionDefinitionConstructor;

export default OptionDefinition;
export type { OptionDefinition };

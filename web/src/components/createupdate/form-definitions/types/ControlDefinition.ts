/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";
import ElementDefinition from "./ElementDefinition";
import OptionDefinition from "./OptionDefinition";

interface ControlDefinition {
    id: any;
    label: any;
    constraintText: any;
    elementDefinition: any;
    options: any;
    defaultOption: any;
    appearsWhen: any;
}

interface ControlDefinitionConstructor {
    new (props: any): ControlDefinition;
    propTypes: any;
}

function ControlDefinitionImpl(this: ControlDefinition, props: any) {
    const { label, id, constraintText, elementDefinition, options, defaultOption, appearsWhen } =
        props;
    this.id = id;
    this.label = label;
    this.constraintText = constraintText;
    this.elementDefinition = elementDefinition;
    this.options = options;
    this.defaultOption = defaultOption;
    this.appearsWhen = appearsWhen;
}

(ControlDefinitionImpl as any).propTypes = {
    label: PropTypes.string.isRequired,
    id: PropTypes.string.isRequired,
    constraintText: PropTypes.string.isRequired,
    elementDefinition: PropTypes.instanceOf(ElementDefinition as any),
    options: PropTypes.arrayOf(OptionDefinition as any),
    defaultOption: PropTypes.instanceOf(OptionDefinition as any),
    appearsWhen: PropTypes.array,
};

const ControlDefinition = ControlDefinitionImpl as unknown as ControlDefinitionConstructor;

export default ControlDefinition;
export type { ControlDefinition };

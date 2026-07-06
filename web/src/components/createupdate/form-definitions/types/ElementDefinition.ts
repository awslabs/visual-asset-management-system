/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import PropTypes from "prop-types";

interface ElementDefinition {
    FormElement: any;
    elementProps: any;
}

interface ElementDefinitionConstructor {
    new (props: any): ElementDefinition;
    propTypes: any;
}

function ElementDefinitionImpl(this: ElementDefinition, props: any) {
    const { formElement, elementProps } = props;
    this.FormElement = formElement;
    this.elementProps = elementProps;
}

(ElementDefinitionImpl as any).propTypes = {
    FormElement: PropTypes.element.isRequired,
    elementProps: PropTypes.object,
};

const ElementDefinition = ElementDefinitionImpl as unknown as ElementDefinitionConstructor;

export default ElementDefinition;
export type { ElementDefinition };

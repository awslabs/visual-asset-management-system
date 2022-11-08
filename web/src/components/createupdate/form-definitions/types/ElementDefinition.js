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

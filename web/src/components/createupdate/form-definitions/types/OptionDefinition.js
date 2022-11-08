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

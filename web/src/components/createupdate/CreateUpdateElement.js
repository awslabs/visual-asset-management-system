/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  Box,
  Button,
  Form,
  FormField,
  Modal,
  SpaceBetween,
} from "@cloudscape-design/components";
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import FormDefinition from "./form-definitions/types/FormDefinition";
import { EntityPropTypes } from "./entity-types/EntityPropTypes";
import { AssetContext } from "../../context/AssetContex";
import { ACTION_TYPES } from "../../common/constants/actions";
import { ACTIONS, createUpdateElements } from "../../services/APIService";

const actionStrings = {
  CREATE: {
    lowerCase: "create",
    titleCase: "Create",
    failMessageWord: "creating",
  },
  UPDATE: {
    lowerCase: "update",
    titleCase: "Update",
    failMessageWord: "updating",
  },
};

export default function CreateUpdateElement(props) {
  const {
    open,
    setOpen,
    setReload,
    formDefinition,
    formEntity,
    databaseId,
    elementId,
    actionType,
  } = props;
  const {
    entityType,
    pluralName,
    singularName,
    singularNameTitleCase,
    controlDefinitions,
    customSubmitFunction,
  } = formDefinition;
  //state
  const [loadingSubmit, setLoadingSubmit] = useState(false);
  const [readySubmit, setReadySubmit] = useState(false);
  const [loadingElement, setLoadingElement] = useState(
    actionType === ACTION_TYPES.UPDATE
  );
  const [submitUpdateError, setSubmitUpdateError] = useState("");
  //populate blank form values based on entity definition
  const startingValues = Object.keys(formEntity.propTypes).reduce(
    (acc, cur) => {
      if (formEntity.propTypes[cur] === EntityPropTypes.ENTITY_ID_ARRAY) {
        acc[cur] = [];
      } else {
        acc[cur] = null;
      }
      if (databaseId && cur === "databaseId") {
        acc[cur] = { label: databaseId, value: databaseId };
      }
      return acc;
    },
    {}
  );
  const [formValues, setFormValues] = useState(startingValues);
  //each validatable prop needs a corresponding error message
  const startingErrors = Object.keys(formEntity.propTypes).reduce(
    (acc, cur) => {
      acc[cur] = "";
      return acc;
    },
    {}
  );
  const [formErrors, setFormErrors] = useState(startingErrors);

  useEffect(() => {
    const getData = async () => {
      const element = await ACTIONS[ACTION_TYPES.READ][entityType](
        databaseId,
        elementId
      );
      if (element) {
        setFormValues(element);
      }
      setLoadingElement(false);
    };
    if (loadingElement) {
      getData();
    }
  }, [loadingElement]);

  /**
   * clear form on close
   */
  const handleClose = () => {
    setFormValues(startingValues);
    setFormErrors(startingErrors);
    setOpen(false);
  };

  useEffect(() => {
    if (readySubmit) {
      submitFormAfterReady();
    }
  }, [readySubmit]);

  /**
   * Validates each form value against its validators from
   * the entity type passed to the control.
   * @param isSubmit - in submit scenarios, we want to add new error messages
   * @returns {boolean} - true = valid, false = one or more invalid
   */
  const validateForm = ({ isSubmit }) => {
    if (open) {
      const newFormErrors = Object.assign({}, formErrors);
      const formPropNames = Object.keys(formValues);
      for (let i = 0; i < formPropNames.length; i++) {
        const formPropName = formPropNames[i];
        //ignore values without validation on entity
        if (formEntity?.propTypes[formPropName]) {
          const validateResults = formEntity.propTypes[formPropName](
            formValues,
            formPropName
          );
          //if we find an error, return false and set error message for value
          //otherwise, the value validates and we clear any previous
          //error message.
          if (validateResults instanceof Error) {
            if (isSubmit) {
              newFormErrors[formPropName] = validateResults.message;
              setFormErrors(newFormErrors);
            }
            return false;
          } else {
            newFormErrors[formPropName] = "";
            setFormErrors(newFormErrors);
          }
        }
      }
      setFormErrors(newFormErrors);
      return true;
    }
  };

  const handleUpdateFormValues = (prop, value) => {
    const newFormValues = Object.assign({}, formValues);
    newFormValues[prop] = value;
    setFormValues(newFormValues);
  };

  const submitFormAfterReady = async () => {
    try {
      if (validateForm({ isSubmit: true })) {
        const formattedFormValues = Object.keys(formValues).reduce(
          (acc, cur) => {
            if (Array.isArray(formValues[cur])) {
              acc[cur] = formValues[cur].map((item) => item.value || item);
            } 
            // for isDistributable, formValues[cur].value is false, so we should check if it is neither undefined nor null instead
            else if (formValues[cur] && (formValues[cur].value !== undefined && formValues[cur].value !== null)) {
              acc[cur] = formValues[cur].value;
            } else {
              acc[cur] = formValues[cur];
            }
            return acc;
          },
          {}
        );
        const config = {
          body: formattedFormValues,
        };
        const result = await createUpdateElements({pluralName: pluralName, config: config});
        if (result !== false && Array.isArray(result)) {
          setReadySubmit(false);
          if (result[0] === false) {
            setSubmitUpdateError(`Unable to ${actionStrings[actionType].lowerCase} ${singularName}. Error: ${result[1]}`);
          } else {
            handleClose();
            setReload(true);
          }
        }
        setReadySubmit(false);
        setLoadingSubmit(false);
      } else {
        setReadySubmit(false);
        setLoadingSubmit(false);
      }
    } catch (error) {
      setReadySubmit(false);
      setLoadingSubmit(false);
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    let customSubmitResults = {};
    if (customSubmitFunction) {
      customSubmitResults = await customSubmitFunction(formValues, formErrors);
      setFormValues(customSubmitResults?.values);
      setFormErrors(customSubmitResults?.errors);
      if (customSubmitResults?.success === true) {
        setReadySubmit(true);
      } else {
        setLoadingSubmit(false);
      }
    } else {
      setReadySubmit(true);
    }
  };

  return (
    <AssetContext.Provider
      value={{
        formValues,
        setFormValues,
        formErrors,
      }}
    >
      <Modal
        onDismiss={() => handleClose()}
        visible={open}
        closeAriaLabel="Close modal"
        size="medium"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                onClick={handleClose}
                disabled={loadingSubmit}
                variant="link"
              >
                Cancel
              </Button>
              <Button
                onClick={(e) => {
                  setLoadingSubmit(true);
                  handleSubmit(e);
                }}
                disabled={loadingSubmit}
                variant="primary"
              >
                {actionStrings[actionType].titleCase}
                &nbsp;
                {singularNameTitleCase}
              </Button>
            </SpaceBetween>
          </Box>
        }
        header={`${actionStrings[actionType].titleCase} ${singularNameTitleCase}`}
      >
        <Form errorText={submitUpdateError}>
          <SpaceBetween direction="vertical" size="s">
            {controlDefinitions.map((controlDefinition, i) => {
              const {
                label,
                id,
                constraintText,
                elementDefinition,
                options,
                defaultOption,
              } = controlDefinition;
              const { FormElement, elementProps } = elementDefinition;
              if (!formValues[id] && defaultOption) {
                handleUpdateFormValues(id, defaultOption);
              }
              return (
                <div key={i}>
                  <FormField
                    constraintText={constraintText}
                    label={label}
                    errorText={formErrors[id]}
                  >
                    <FormElement
                      placeholder={label}
                      disabled={loadingSubmit}
                      name={id}
                      value={formValues[id]}
                      selectedOption={
                        typeof formValues[id] === "object"
                          ? formValues[id]
                          : options
                          ? options.find(
                              (option) => option.value === formValues[id]
                            )
                          : {
                              value: formValues[id],
                            }
                      }
                      selectedOptions={
                        Array.isArray(formValues[id]) &&
                        formValues[id].map((value) => {
                          if (typeof value === "object") return value;
                          return {
                            label: formValues[id],
                            value: formValues[id],
                          };
                        })
                      }
                      options={options}
                      onChange={({ detail }) => {
                        handleUpdateFormValues(
                          id,
                          detail.selectedOptions ||
                            detail.selectedOption ||
                            detail.value
                        );
                      }}
                      {...elementProps}
                    />
                  </FormField>
                </div>
              );
            })}
          </SpaceBetween>
        </Form>
      </Modal>
    </AssetContext.Provider>
  );
}

CreateUpdateElement.propTypes = {
  open: PropTypes.bool.isRequired,
  setOpen: PropTypes.func.isRequired,
  setReload: PropTypes.func.isRequired,
  formDefinition: PropTypes.instanceOf(FormDefinition),
  formEntity: EntityPropTypes.ENTITY,
  databaseId: EntityPropTypes.ENTITY_ID,
  elementId: EntityPropTypes.ENTITY_ID,
  actionType: PropTypes.oneOf(Object.values(ACTION_TYPES)).isRequired,
};

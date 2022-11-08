import React from "react";
import { DatabaseFormDefinition } from "./form-definitions/DatabaseFormDefinition";
import DatabaseEntity from "./entity-types/DatabaseEntity";
import CreateUpdateElement from "./CreateUpdateElement";
import { actionTypes } from "./form-definitions/types/FormDefinition";

export default function CreateDatabase(props) {
  const { open, setOpen, setReload } = props;

  return (
    <CreateUpdateElement
      open={open}
      setOpen={setOpen}
      setReload={setReload}
      formDefinition={DatabaseFormDefinition}
      formEntity={DatabaseEntity}
      actionType={actionTypes.CREATE}
    />
  );
}

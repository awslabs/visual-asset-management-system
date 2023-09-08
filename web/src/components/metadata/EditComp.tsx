/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { SchemaContextData } from "../../pages/MetadataSchema";
import {
    Checkbox,
    DatePicker,
    FormField,
    Input,
    Select,
    Textarea,
} from "@cloudscape-design/components";
import { MapLocationSelectorModal2 } from "./MapLocationSelector";
import { TableRow, Metadata } from "./ControlledMetadata";
import { useEffect, useState } from "react";
import { validateNonZeroLengthTextAsYouType } from "../../pages/AssetUpload/validations";

export interface EditCompProps {
    item: TableRow;
    schema: SchemaContextData;
    controlledLists: any;
    controlData: any;
    setValue: (row: string | undefined) => void;
    currentValue: string;
    metadata: Metadata;
    showErrors?: boolean;
    setValid: (v: boolean) => void;
}
export function EditComp({
    item,
    setValue,
    controlledLists,
    currentValue,
    metadata,
    schema,
    controlData,
    showErrors,
    setValid,
}: EditCompProps) {
    const [validationText, setValidationText] = useState("");
    const disabled = !item.dependsOn.every((x: string) => metadata[x] && metadata[x] !== "");
    const required = !!schema.schemas.find((s) => s.field === item.name)?.required;

    useEffect(() => {
        switch (item.type) {
            case "string":
            case "textarea": {
                setValidationText(validateNonZeroLengthTextAsYouType(item.value) || "");
                break;
            }
            case "boolean": {
                setValidationText(
                    item.value === "true" || item.value === "false" ? "" : "Required field."
                );
                break;
            }
            case "number": {
                setValidationText(Number.isNaN(Number(item.value)) ? "Value must be a number" : "");
                break;
            }
            case "controlled-list":
            case "inline-controlled-list": {
                setValidationText(!item.value ? "You must select an option." : "");
                break;
            }
            case "location": {
                setValidationText(!item.value ? "You must select a location." : "");
                break;
            }
            case "date": {
                setValidationText(!item.value ? "You must enter a valid date." : "");
                break;
            }
        }
    }, [item.type, item.value]);

    useEffect(() => {
        setValid(!validationText || !required);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [validationText, required]);

    if (item.type === "string") {
        return (
            <FormField errorText={required && showErrors && validationText}>
                <Input
                    value={item.value}
                    disabled={disabled}
                    onChange={(event) => {
                        setValue(event.detail.value);
                    }}
                />
            </FormField>
        );
    }

    if (item.type === "textarea") {
        return (
            <FormField errorText={required && showErrors && validationText}>
                <Textarea
                    disabled={disabled}
                    onChange={({ detail }) => setValue(detail.value)}
                    value={item.value}
                />
            </FormField>
        );
    }

    if (item.type === "inline-controlled-list") {
        const options = item.inlineValues.map((label: string) => ({
            label,
            value: label,
        }));
        let selectedOption = {
            label: currentValue,
            value: currentValue,
        };
        if (options.length === 1 && currentValue !== options[0].value) {
            selectedOption = options[0];
            setValue(selectedOption.value);
        }
        return (
            <FormField errorText={required && showErrors && validationText}>
                <Select
                    options={options}
                    selectedOption={selectedOption}
                    disabled={disabled || options.length === 1}
                    expandToViewport
                    filteringType="auto"
                    onChange={(e) => setValue(e.detail.selectedOption.value)}
                />
            </FormField>
        );
    }

    if (item.type === "controlled-list" && item.dependsOn.length === 0) {
        const options = controlledLists[item.name].data.map((x: any) => ({
            label: x[item.name],
            value: x[item.name],
        }));
        let selectedOption = {
            label: currentValue,
            value: currentValue,
        };
        if (options.length === 1 && currentValue !== options[0].value) {
            selectedOption = options[0];
            setValue(selectedOption.value);
        }
        return (
            <FormField errorText={required && showErrors && validationText}>
                <Select
                    options={options}
                    selectedOption={selectedOption}
                    expandToViewport
                    disabled={disabled}
                    filteringType="auto"
                    onChange={(e) => setValue(e.detail.selectedOption.value)}
                />
            </FormField>
        );
    }
    if (item.type === "controlled-list" && item.dependsOn.length > 0) {
        const options = controlledLists[item.name].data
            .filter((x: any) => {
                return item.dependsOn.every((y: string) => {
                    return x[y] === metadata[y];
                });
            })
            .map((x: any) => ({
                label: x[item.name],
                value: x[item.name],
            }));
        let selectedOption = {
            label: currentValue,
            value: currentValue,
        };
        if (options.length === 1 && currentValue !== options[0].value) {
            selectedOption = options[0];
            setValue(selectedOption.value);
        }

        return (
            <FormField errorText={required && showErrors && validationText}>
                <Select
                    options={options}
                    selectedOption={selectedOption}
                    disabled={disabled || options.length === 1}
                    expandToViewport
                    filteringType="auto"
                    onChange={(e) => setValue(e.detail.selectedOption.value)}
                />
            </FormField>
        );
    }
    if (item.type === "location") {
        let currentValueInit = {
            loc: [-95.37019986475366, 29.767650706163337], // Houston
            zoom: 5,
        };

        if (!currentValue) {
            const schemaItem = schema.schemas.find((x) => x.field === item.name);
            if (schemaItem && schemaItem.dependsOn) {
                const controlDataItem = controlData.find((x: any) =>
                    schemaItem.dependsOn.every((y: string) => x[y] === metadata[y])
                );
                if (controlDataItem) {
                    currentValueInit = {
                        loc: [
                            controlDataItem[schemaItem.longitudeField!],
                            controlDataItem[schemaItem.latitudeField!],
                        ],
                        zoom: controlDataItem[schemaItem.zoomLevelField!],
                    };
                }
            }
        }

        return (
            <FormField errorText={required && showErrors && validationText}>
                <MapLocationSelectorModal2
                    json={currentValue ? currentValue : JSON.stringify(currentValueInit)}
                    setJson={(json) => {
                        setValue(json);
                    }}
                    disabled={disabled}
                />
            </FormField>
        );
    }

    if (item.type === "number") {
        return (
            <FormField errorText={required && showErrors && validationText}>
                <Input
                    value={item.value}
                    inputMode="numeric"
                    disabled={disabled}
                    onChange={(event) => {
                        setValue(event.detail.value);
                    }}
                />
            </FormField>
        );
    }

    if (item.type === "boolean") {
        // checkbox
        return (
            <FormField errorText={required && showErrors && validationText}>
                <Checkbox
                    checked={item.value === "true"}
                    disabled={disabled}
                    onChange={(e) => {
                        setValue(e.detail.checked ? "true" : "false");
                    }}
                />
            </FormField>
        );
    }

    if (item.type === "date") {
        return (
            <FormField errorText={required && showErrors && validationText}>
                <DatePicker
                    onChange={({ detail }) => setValue(detail.value)}
                    value={item.value}
                    disabled={disabled}
                    openCalendarAriaLabel={(selectedDate) =>
                        "Choose date" + (selectedDate ? `, selected date is ${selectedDate}` : "")
                    }
                    nextMonthAriaLabel="Next month"
                    placeholder="YYYY/MM/DD"
                    previousMonthAriaLabel="Previous month"
                    todayAriaLabel="Today"
                />
            </FormField>
        );
    }

    return null;
}

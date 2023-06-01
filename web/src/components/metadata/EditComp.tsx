import { SchemaContextData } from "../../pages/MetadataSchema";
import { Checkbox, DatePicker, Input, Select, Textarea } from "@cloudscape-design/components";
import MapLocationSelectorModal from "../interactive/MapLocationSelector";
import React from "react";
import { TableRow, Metadata } from "./ControlledMetadata";

interface EditCompProps {
    item: TableRow;
    schema: SchemaContextData;
    controlledLists: any;
    controlData: any;
    setValue: (row: string | undefined) => void;
    currentValue: string;
    metadata: Metadata;
}
export function EditComp({
    item, setValue, controlledLists, currentValue, metadata, schema, controlData,
}: EditCompProps) {
    const disabled = !item.dependsOn.every((x: string) => metadata[x] && metadata[x] !== "");

    if (item.type === "string") {
        return (
            <Input
                value={item.value}
                disabled={disabled}
                onChange={(event) => {
                    setValue(event.detail.value);
                }} />
        );
    }

    if (item.type === "textarea") {
        return (
            <Textarea
                disabled={disabled}
                onChange={({ detail }) => setValue(detail.value)}
                value={item.value} />
        );
    }

    if (item.type === "controlled-list" && item.dependsOn.length === 0) {
        return (
            <Select
                options={controlledLists[item.name].data.map((x: any) => ({
                    label: x[item.name],
                    value: x[item.name],
                }))}
                selectedOption={{
                    label: currentValue,
                    value: currentValue,
                }}
                expandToViewport
                disabled={disabled}
                filteringType="auto"
                onChange={(e) => setValue(e.detail.selectedOption.value)} />
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
        return (
            <Select
                options={options}
                selectedOption={{
                    label: currentValue,
                    value: currentValue,
                }}
                disabled={disabled}
                expandToViewport
                filteringType="auto"
                onChange={(e) => setValue(e.detail.selectedOption.value)} />
        );
    }
    if (item.type === "location") {
        console.log("location current value", currentValue);

        let loc: [number, number] | null;
        let zoom = 5;

        if (!currentValue) {
            const schemaItem = schema.schemas.find((x) => x.field === item.name);
            if (schemaItem) {
                const controlDataItem = controlData.find((x: any) => schemaItem.dependsOn.every((y: string) => x[y] === metadata[y])
                );
                if (controlDataItem) {
                    loc = [
                        controlDataItem[schemaItem.longitudeField],
                        controlDataItem[schemaItem.latitudeField],
                    ];
                    zoom = controlDataItem[schemaItem.zoomLevelField];
                } else {
                    loc = null;
                }
                // schemaItem.latitudeField
                // schemaItem.longitudeField
                // schemaItem.zoomLevelField
            } else {
                loc = null;
            }
        } else {
            try {
                const parsed: any = JSON.parse(currentValue);
                loc = parsed.loc;
                zoom = parsed.zoom;
            } catch {
                loc = null;
            }
        }

        console.log("location parsed value", loc, zoom, schema);
        return (
            <MapLocationSelectorModal
                location={loc}
                disabled={disabled}
                initialZoom={zoom}
                setLocation={(loc: number[], zoom: number) => {
                    setValue(JSON.stringify({ loc, zoom }));
                }} />
        );
    }

    if (item.type === "number") {
        return (
            <Input
                value={item.value}
                inputMode="numeric"
                disabled={disabled}
                onChange={(event) => {
                    setValue(event.detail.value);
                }} />
        );
    }

    if (item.type === "boolean") {
        // checkbox
        return (
            <Checkbox
                checked={item.value === "true"}
                disabled={disabled}
                onChange={(e) => {
                    setValue(e.detail.checked ? "true" : "false");
                }} />
        );
    }

    if (item.type === "date") {
        return (
            <DatePicker
                onChange={({ detail }) => setValue(detail.value)}
                value={item.value}
                disabled={disabled}
                openCalendarAriaLabel={(selectedDate) => "Choose date" + (selectedDate ? `, selected date is ${selectedDate}` : "")}
                nextMonthAriaLabel="Next month"
                placeholder="YYYY/MM/DD"
                previousMonthAriaLabel="Previous month"
                todayAriaLabel="Today" />
        );
    }

    return null;
}

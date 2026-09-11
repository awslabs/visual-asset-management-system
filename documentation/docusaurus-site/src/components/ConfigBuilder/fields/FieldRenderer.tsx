/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import type { ConfigShape, FieldMeta, Rule } from "../types";
import { getByPath } from "../pathUtils";
import BooleanField from "./BooleanField";
import TextField from "./TextField";
import StringArrayField from "./StringArrayField";
import IpRangeListField from "./IpRangeListField";
import PresignedUrlRestrictionsField from "./PresignedUrlRestrictionsField";
import ExternalBucketsField from "./ExternalBucketsField";
import styles from "../styles.module.css";

interface Props {
    field: FieldMeta;
    config: ConfigShape;
    /** Error-severity rules whose fieldPaths include this field. */
    fieldErrors: Rule[];
    onChange: (path: string, value: unknown) => void;
}

export default function FieldRenderer({ field, config, fieldErrors, onChange }: Props) {
    const value = getByPath(config, field.path);
    const invalid = fieldErrors.length > 0;
    const set = (next: unknown) => onChange(field.path, next);

    let control: React.ReactNode;
    switch (field.input) {
        case "boolean":
            control = <BooleanField field={field} value={value} onChange={set} />;
            break;
        case "string-array":
            control = <StringArrayField field={field} value={value} onChange={set} />;
            break;
        case "ip-range-list":
            control = <IpRangeListField field={field} value={value} onChange={set} />;
            break;
        case "presigned-url-restrictions":
            control = <PresignedUrlRestrictionsField field={field} value={value} onChange={set} />;
            break;
        case "external-buckets":
            control = <ExternalBucketsField field={field} value={value} onChange={set} />;
            break;
        case "text":
        case "number":
        case "select":
        default:
            control = <TextField field={field} value={value} invalid={invalid} onChange={set} />;
            break;
    }

    return (
        <div>
            {control}
            {fieldErrors.map((rule) => (
                <small key={rule.id} className={styles.fieldError}>
                    ⚠ {rule.message}
                </small>
            ))}
        </div>
    );
}

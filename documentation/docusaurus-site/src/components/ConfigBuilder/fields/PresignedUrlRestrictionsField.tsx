/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import clsx from "clsx";
import type { FieldMeta } from "../types";
import { isCidr, isVpceId } from "./presignedUrlFormats";
import styles from "../styles.module.css";

interface Restrictions {
    allowedIpRanges: string[];
    allowedVpceIds: string[];
}

interface Props {
    field: FieldMeta;
    value: Restrictions | null;
    onChange: (value: Restrictions) => void;
}

const EMPTY: Restrictions = { allowedIpRanges: [], allowedVpceIds: [] };

/** The two restriction dimensions, rendered in order. */
const LISTS: {
    key: keyof Restrictions;
    label: string;
    dimension: string;
    placeholder: string;
    addLabel: string;
    formatHint: string;
    isValid: (entry: string) => boolean;
}[] = [
    {
        key: "allowedIpRanges",
        label: "Allowed IP ranges (CIDR)",
        dimension: "IP range",
        placeholder: "203.0.113.0/24",
        addLabel: "+ Add IP range",
        formatHint: "Enter an IPv4 or IPv6 CIDR — an address and a prefix length.",
        isValid: isCidr,
    },
    {
        key: "allowedVpceIds",
        label: "Allowed VPC endpoint IDs",
        dimension: "VPC endpoint",
        placeholder: "vpce-0123456789abcdef0",
        addLabel: "+ Add VPC endpoint ID",
        formatHint: "Enter a VPC endpoint ID — vpce- followed by at least eight hex digits.",
        isValid: isVpceId,
    },
];

/**
 * Editor for app.assetBuckets.presignedUrlNetworkRestrictions — two string
 * lists (allowed CIDR ranges and allowed VPC endpoint IDs). A request arrives
 * either over the public path or through a VPC endpoint, so config.ts rejects a
 * block that sets both lists. Whichever list is in use replaces the other's add
 * button with the reason it is unavailable, and entries that do not match the
 * format config.ts accepts are marked as they are typed. Both lists empty means
 * no restriction.
 */
export default function PresignedUrlRestrictionsField({ field, value, onChange }: Props) {
    const restrictions: Restrictions = value ?? EMPTY;
    const ipRanges = restrictions.allowedIpRanges ?? [];
    const vpceIds = restrictions.allowedVpceIds ?? [];
    const counts: Record<keyof Restrictions, number> = {
        allowedIpRanges: ipRanges.length,
        allowedVpceIds: vpceIds.length,
    };

    const updateList = (key: keyof Restrictions, next: string[]) =>
        onChange({
            ...restrictions,
            allowedIpRanges: ipRanges,
            allowedVpceIds: vpceIds,
            [key]: next,
        });

    return (
        <div className={styles.field}>
            <label className={styles.fieldLabel}>{field.label}</label>
            {counts.allowedIpRanges > 0 && counts.allowedVpceIds > 0 && (
                <small className={styles.fieldError}>
                    ⚠ Set one list or the other, not both. Remove every entry from one list to
                    choose whether presigned URLs are restricted by IP range or by VPC endpoint.
                </small>
            )}
            {LISTS.map((list, index) => {
                const other = LISTS[1 - index];
                const items = list.key === "allowedIpRanges" ? ipRanges : vpceIds;
                const otherInUse = counts[other.key] > 0;
                const malformed = items.some((item) => item !== "" && !list.isValid(item));

                return (
                    <div className={styles.field} key={list.key}>
                        <label className={styles.fieldLabel}>{list.label}</label>
                        {items.map((item, itemIndex) => (
                            <div className={styles.listRow} key={itemIndex}>
                                <input
                                    className={clsx(
                                        styles.input,
                                        styles.listInput,
                                        item !== "" && !list.isValid(item) && styles.inputInvalid
                                    )}
                                    placeholder={list.placeholder}
                                    value={item}
                                    onChange={(e) => {
                                        const copy = [...items];
                                        copy[itemIndex] = e.target.value;
                                        updateList(list.key, copy);
                                    }}
                                />
                                <button
                                    type="button"
                                    className={styles.iconButton}
                                    onClick={() =>
                                        updateList(
                                            list.key,
                                            items.filter((_, i) => i !== itemIndex)
                                        )
                                    }
                                    aria-label="Remove"
                                >
                                    Remove
                                </button>
                            </div>
                        ))}
                        {otherInUse ? (
                            <small className={styles.fieldHelp}>
                                Restriction is by {other.dimension}; remove those entries to
                                restrict by {list.dimension} instead.
                            </small>
                        ) : (
                            <button
                                type="button"
                                className={styles.addButton}
                                onClick={() => updateList(list.key, [...items, ""])}
                            >
                                {list.addLabel}
                            </button>
                        )}
                        {malformed && (
                            <small className={styles.fieldError}>⚠ {list.formatHint}</small>
                        )}
                    </div>
                );
            })}
            {field.help && <small className={styles.fieldHelp}>{field.help}</small>}
        </div>
    );
}

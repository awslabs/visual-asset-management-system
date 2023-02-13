import React, { useEffect, useState } from "react";
import { API } from "aws-amplify";
import { Box, Button, Header, Table, Input } from "@cloudscape-design/components";

class MetadataApi {
    version!: string;
    metadata!: Metadata;
}
class Metadata {
    [key: string]: string;
}

class TableRow {
    idx!: number;
    name: string | null | undefined;
    description: string | null | undefined;
    type: string | null | undefined;
}

const put = async (assetId: string, record: Metadata) => {
    if(Object.keys(record).length < 1) {
        return;
    }
    return API.put("api", "metadata/" + assetId, {
        body: {
            version: "1",
            metadata: record,
        },
    });
};
const get = async (assetId: string): Promise<object> => {
    return API.get("api", "metadata/" + assetId, {});
};

class MetadataInputs {
    assetId!: string;
    databaseId!: string;
}

const MetadataTable = ({ assetId, databaseId }: MetadataInputs) => {
    const tableRowToMeta = (rows: TableRow[]): Metadata => {
        const result: Metadata = {};
        rows.forEach((row) => {
            if (row && row.name && row.description) {
                result[row.name] = row.description;
            }
        });
        return result;
    };

    const metaToTableRow = (meta: Metadata) =>
        Object.keys(meta)
            .filter((key) => key !== "pk" && key != "sk")
            .map((key, idx): TableRow => {
                return {
                    idx: idx,
                    name: key,
                    description: meta[key],
                    type: "string",
                };
            });

    const [items, setItems] = useState<TableRow[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!loading) {
            return;
        }

        get(assetId)
            .catch((x) => {
                console.log("catch", x.response.status);
                // if 404 , then set an initial status to empty
                if (x.response.status === 404) {
                    setLoading(false);
                }
            })
            .then((result) => {
                setLoading(false);
                setItems(metaToTableRow((result as MetadataApi).metadata));
            });
    }, [loading, items]);

    const HeaderControls = () => {
        return (
            <div
                style={{
                    width: "calc(100% - 40px)",
                    textAlign: "right",
                    position: "absolute",
                }}
            >
                <Button
                    onClick={() => {
                        const next = [...items];
                        next.unshift({
                            idx: -1,
                            name: null,
                            description: null,
                            type: "string",
                        });
                        for (let i = 0; i < next.length; i++) {
                            next[i].idx = i;
                        }
                        setItems(next);
                    }}
                    disabled={
                        items.filter((x) => x.name === null || x.description === null).length > 0
                    }
                    variant="primary"
                >
                    Add Row
                </Button>
            </div>
        );
    };

    const validationFunction = (field1: string, reqs: string[]) => {
        const field = field1 as keyof TableRow;
        return (item: TableRow, value: "name" | "description" | null | undefined) => {
            if (value === undefined || value === null) {
                return;
            }
            if (reqs.indexOf("non-empty") > -1) {
                if (value.length < 1) {
                    return "Field must not be empty";
                }
            }
            if (reqs.indexOf("unique") > -1) {
                if (
                    items.filter((item) => item[field] === value).length > 0 &&
                    item[field] !== value
                ) {
                    return (
                        "Field name must be unique, " +
                        value +
                        " is already in use by another field"
                    );
                }
            }
        };
    };

    return (
        <div style={{ width: "100%", marginTop: "8px" }}>
            <Table
                header={
                    <>
                        <HeaderControls />
                        <Header counter={items.length + ""}>Metadata</Header>
                    </>
                }
                loading={loading}
                loadingText="Loading..."
                submitEdit={async (blah, column, newValue) => {
                    const item: TableRow = blah as TableRow;
                    console.log("submitEdit", item, column, newValue);
                    const next: TableRow[] = [...items];
                    switch (column.id) {
                        case "description":
                            next[item.idx].description = newValue as string;
                            break;
                        case "name":
                            next[item.idx].name = newValue as string;
                            break;
                    }
                    setItems(next);

                    await put(assetId, tableRowToMeta(next));
                }}
                items={items}
                columnDefinitions={[
                    {
                        id: "name",
                        header: "Variable name",
                        cell: (item) => {
                            return item.name;
                        },
                        editConfig: {
                            ariaLabel: "Name",
                            editIconAriaLabel: "editable",
                            errorIconAriaLabel: "Name Error",
                            validation: validationFunction("name", ["non-empty", "unique"]),
                            editingCell: (item, { currentValue, setValue }) => {
                                return (
                                    <Input
                                        autoFocus={true}
                                        value={currentValue ?? item.name}
                                        onChange={(event) => {
                                            setValue(event.detail.value);
                                        }}
                                    />
                                );
                            },
                        },
                    },
                    {
                        id: "description",
                        header: "Description",
                        cell: (e) => e.description,
                        editConfig: {
                            ariaLabel: "Description",
                            editIconAriaLabel: "editable",
                            errorIconAriaLabel: "Description Error",
                            validation: validationFunction("description", ["non-empty"]),
                            editingCell: (item, { currentValue, setValue }) => {
                                return (
                                    <Input
                                        autoFocus={true}
                                        value={currentValue ?? item.description}
                                        onChange={(event) => {
                                            const next = [...items];
                                            next[item.idx]["description"] = event.detail.value;
                                            setValue(event.detail.value);
                                            setItems(next);
                                        }}
                                    />
                                );
                            },
                        },
                    },
                ]}
            />
        </div>
    );
};

export default MetadataTable;

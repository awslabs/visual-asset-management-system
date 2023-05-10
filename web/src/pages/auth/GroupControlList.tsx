import { MultiselectProps, FormField, Multiselect } from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { useState, useEffect } from "react";

interface GroupControlListProps {
    selectedGroups: MultiselectProps.Option[];
    setSelectedGroups: (selectedGroups: MultiselectProps.Option[]) => void;
    selectedGroupList: string[];
    disabled: boolean;
    label: string;
    description: string;
    errorText: () => string | null;
}

function GroupControList({
    selectedGroups,
    setSelectedGroups,
    selectedGroupList,
    disabled,
    label,
    description,
    errorText,
}: GroupControlListProps) {
    const [loadingGroups, setLoadingGroups] = useState(true);
    const [groupOptions, setGroupOptions] = useState<MultiselectProps.Option[]>([]);
    useEffect(() => {
        if (!loadingGroups) return;
        API.get("api", `auth/groups`, {}).then((res) => {
            const opts: MultiselectProps.Option[] = res.claims.map((value: string) => ({
                value,
                label: value,
                description: "Unlabeled group",
            }));
            setGroupOptions(opts);
            setSelectedGroups(
                opts.filter(
                    (x) =>
                        selectedGroupList &&
                        x.value !== undefined &&
                        selectedGroupList.indexOf(x.value) > -1
                )
            );
            setLoadingGroups(false);
        });
    }, [loadingGroups, selectedGroupList, setSelectedGroups]);

    return (
        <FormField label={label} description={description} errorText={errorText()}>
            <Multiselect
                selectedOptions={selectedGroups}
                disabled={disabled}
                onChange={({ detail }) =>
                    setSelectedGroups(detail.selectedOptions as MultiselectProps.Option[])
                }
                deselectAriaLabel={(e) => `Remove ${e.label}`}
                loadingText={loadingGroups ? "Loading..." : undefined}
                options={groupOptions}
                filteringType="auto"
                placeholder="Choose options"
                selectedAriaLabel="Selected"
            />
        </FormField>
    );
}

export default GroupControList;

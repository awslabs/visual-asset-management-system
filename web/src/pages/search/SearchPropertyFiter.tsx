import * as React from "react";
import PropertyFilter, { PropertyFilterProps } from "@cloudscape-design/components/property-filter";
import Synonyms from "../../synonyms";
import { Query } from "@cloudscape-design/components/property-filter/interfaces";

interface SearchPropertyFiterProps {}
function SearchPropertyFilter(props: SearchPropertyFiterProps) {
    const [query, setQuery] = React.useState<Query>({
        tokens: [],
        operation: "and",
    });
    return (
        <PropertyFilter
            onChange={({ detail }) => setQuery(detail)}
            query={query}
            i18nStrings={{
                filteringAriaLabel: `Find ${Synonyms.Asset}`,
                filteringPlaceholder: `Find ${Synonyms.Asset}`,
            }}
            countText="5 matches"
            expandToViewport
            filteringOptions={[
                {
                    propertyKey: "instanceid",
                    value: "i-2dc5ce28a0328391",
                },
                {
                    propertyKey: "instanceid",
                    value: "i-d0312e022392efa0",
                },
                {
                    propertyKey: "instanceid",
                    value: "i-070eef935c1301e6",
                },
                {
                    propertyKey: "instanceid",
                    value: "i-3b44795b1fea36ac",
                },
                { propertyKey: "state", value: "Stopped" },
                { propertyKey: "state", value: "Stopping" },
                { propertyKey: "state", value: "Pending" },
                { propertyKey: "state", value: "Running" },
                {
                    propertyKey: "instancetype",
                    value: "t3.small",
                },
                {
                    propertyKey: "instancetype",
                    value: "t2.small",
                },
                { propertyKey: "instancetype", value: "t3.nano" },
                {
                    propertyKey: "instancetype",
                    value: "t2.medium",
                },
                {
                    propertyKey: "instancetype",
                    value: "t3.medium",
                },
                {
                    propertyKey: "instancetype",
                    value: "t2.large",
                },
                { propertyKey: "instancetype", value: "t2.nano" },
                {
                    propertyKey: "instancetype",
                    value: "t2.micro",
                },
                {
                    propertyKey: "instancetype",
                    value: "t3.large",
                },
                {
                    propertyKey: "instancetype",
                    value: "t3.micro",
                },
                { propertyKey: "averagelatency", value: "17" },
                { propertyKey: "averagelatency", value: "53" },
                { propertyKey: "averagelatency", value: "73" },
                { propertyKey: "averagelatency", value: "74" },
                { propertyKey: "averagelatency", value: "107" },
                { propertyKey: "averagelatency", value: "236" },
                { propertyKey: "averagelatency", value: "242" },
                { propertyKey: "averagelatency", value: "375" },
                { propertyKey: "averagelatency", value: "402" },
                { propertyKey: "averagelatency", value: "636" },
                { propertyKey: "averagelatency", value: "639" },
                { propertyKey: "averagelatency", value: "743" },
                { propertyKey: "averagelatency", value: "835" },
                { propertyKey: "averagelatency", value: "981" },
                { propertyKey: "averagelatency", value: "995" },
            ]}
            filteringProperties={[
                {
                    key: "instanceid",
                    operators: ["=", "!=", ":", "!:"],
                    propertyLabel: "Instance ID",
                    groupValuesLabel: "Instance ID values",
                },
                {
                    key: "state",
                    operators: ["=", "!=", ":", "!:"],
                    propertyLabel: "State",
                    groupValuesLabel: "State values",
                },
                {
                    key: "instancetype",
                    operators: ["=", "!=", ":", "!:"],
                    propertyLabel: "Instance type",
                    groupValuesLabel: "Instance type values",
                },
                {
                    key: "averagelatency",
                    operators: ["=", "!=", ">", "<", "<=", ">="],
                    propertyLabel: "Average latency",
                    groupValuesLabel: "Average latency values",
                },
            ]}
        />
    );
}

export default SearchPropertyFilter;

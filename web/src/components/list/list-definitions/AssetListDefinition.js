import React from "react";
import ListDefinition from "./types/ListDefinition";
import { Link } from "@awsui/components-react";
import ColumnDefinition from "./types/ColumnDefinition";

export const AssetListDefinition = new ListDefinition({
  pluralName: "assets",
  pluralNameTitleCase: "Assets",
  visibleColumns: ["assetId", "databaseId", "description", "assetType"],
  filterColumns: [
    { name: "databaseId", placeholder: "Database" },
    { name: "assetType", placeholder: "Type" },
  ],
  elementId: "assetId",
  deleteRoute: "database/{databaseId}/assets/{assetId}",
  columnDefinitions: [
    new ColumnDefinition({
      id: "assetId",
      header: "Asset",
      cellWrapper: (props) => {
        const { item } = props;
        console.log(item);
        return (
          <Link href={`/databases/${item.databaseId}/assets/${item.assetId}`}>
            {item?.assetName}
          </Link>
        );
      },
      sortingField: "assetId",
    }),
    new ColumnDefinition({
      id: "databaseId",
      header: "Database",
      cellWrapper: (props) => {
        const { item } = props;
        return (
          <Link href={`/databases/${item.databaseId}/assets/`}>
            {props.children}
          </Link>
        );
      },
      sortingField: "databaseId",
    }),
    new ColumnDefinition({
      id: "description",
      header: "Description",
      cellWrapper: (props) => <>{props.children}</>,
      sortingField: "description",
    }),
    new ColumnDefinition({
      id: "assetType",
      header: "Type",
      cellWrapper: (props) => <>{props.children}</>,
      sortingField: "assetType",
    }),
  ],
});

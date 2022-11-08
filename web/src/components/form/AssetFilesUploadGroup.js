import React from "react";
import { Grid, TextContent } from "@awsui/components-react";
import {
  cadFileFormats,
  columnarFileFormats,
  previewFileFormats,
  modelFileFormats,
  archiveFileFormats,
} from "../../common/constants/fileFormats";
import FileUploadControl from "./FileUploadControl";

const AssetFilesUploadGroup = (props) => {
  const { disabled } = props;
  return (
    <Grid
      gridDefinition={[
        { colspan: { default: "6" } },
        { colspan: { default: "6" } },
      ]}
    >
      <div>
        <TextContent>Asset File</TextContent>
        <FileUploadControl
          disabled={disabled}
          controlName={"Asset"}
          fileFormats={modelFileFormats
            .concat(
              columnarFileFormats.concat(
                cadFileFormats.concat(archiveFileFormats)
              )
            )
            .join(",")}
        />
      </div>
      <div>
        <TextContent>Preview File</TextContent>
        <FileUploadControl
          disabled={disabled}
          controlName={"Preview"}
          fileFormats={previewFileFormats.join(",")}
        />
      </div>
    </Grid>
  );
};

export default AssetFilesUploadGroup;

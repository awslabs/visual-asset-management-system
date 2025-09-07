# Changelog

All notable changes to this project will be documented in this file. See [standard-version](https://github.com/conventional-changelog/standard-version) for commit guidelines.

## [2.2.0] (2025-09-31)

This version includes significant enhancements to VAMS infrastructure, a complete overhaul of asset management APIs/Backend/UI, addition of supproting external IDP authentication, and various bug fixes. Key improvements include more flexible naming conventions, separation of assets and files, enhanced file management capabilities, new asset versioning, new use-case pipelines, global worksflows/pipelines, and improved upload/download functionality.

### ⚠ BREAKING CHANGES

-   CDK Configuration files must be updated to include the new required fields. See ConfigurationGuide.md and template configuration files for new formats.
-   Asset and Database DynamoDB table fields and formats have changed which require using the migration scripts after CDK deployment to update the new field values. See /infra/deploymentDataMigration/v2.1_to_v2.2/upgrade/v2.2_to_v2.3_migration_README.md for details on using the migration scripts to upgrade your DynamoDB databases after deployment.
-   Due to VPC subnet breakout changes, this may break existing deployments. It is recommended to use an A/B deployment if you run into subnet configuration issues.
-   Due to Cognito changes, a new Cognito user pool may be generated on stack deployment. To migrate existing users from the previous user pool, follow the following blog instructions: https://aws.amazon.com/blogs/security/approaches-for-migrating-users-to-amazon-cognito-user-pools/

**Recommended Upgrade Path:** A/B Stack Deployment with data migration using staging bucket configuration and upgrade migration scripts for DynamoDB tables in `./infra/upgradeMigrationScripts`

### Features

-   Database, Pipeline, Workflow, Tag, Tag Types, Role, and Constraints id/names no longer need to follow as strict regex guidelines. New Regex: ^[-_a-zA-Z0-9]{3,63}$
-   AssetId no longer needs to follow as strict regex guidelines. New Regex (regular filename regex): ^(?!._[<>:"\/\\|?_])(?!.\*[.\s]$)[\w\s.,\'-]{1,254}[^.\s]$'
-   File paths no longer need to follow as strict regex guidelines and now allow for deep pathing. Some restrictions apply to specific input paths for auxiliary asset previews and pipeline output paths.
-   The asset upload API and backend along with many associated supporting asset API backends have been rewritten to support new features, security, and performance improvements.
    -   The old uploadAsset, uploadAssetWorkflow, and s3scoped access APIs and backend have been removed
    -   A new uploadFile (initialize, complete, createFolder), createAsset, and assetService (edit asset) have been created to support separation of assets and files. UploadFile now fully supports S3 Signed URL uploads for better security and performance (replaces providing UI with scoped S3 access).
    -   ScopedS3Access removal provides benefits as previous implementations had issues with scoped role timeouts, different authentication implementations in VAMS, parallelization issues which prevented file validation, asset file overwrite issues, and more.
    -   New AssetUploads DynamoDB created to track uploads between initializations and completions
    -   IngestAsset API, intended for backend data system ingresses, wraps the new APIs as an all-in-one API caller.
    -   UploadFile is now split into two stages for upload which allow for multiple files and multiple parts per file to be specified for better performant uploads of large files
    -   Assets now are better built to support a range of different files, including no files. The separation allows for better reliance on S3 functionalities to support file versioning.
    -   AssetType on assets are now specified as "none" (no files on asset), "folder" (multiple files on an asset), or single file extension (single file on asset and provides the extension, as before)
    -   File Uploads will go to a temporary S3 location on stage 1 while stage 2 upload completions performs checks, including for malicious file extensions or MIME types, before moving files into an asset for versioning
    -   File uploads restricted to 10 upload initializations (stage 1) per-user per-minute to minimize DDoS possibility and maximize system availability
    -   UploadFile now supports upload types for assetFiles and assetPreview to better support the separation of the uploads. This will allow for future enhancement support of adding filePreviews, separate of assets.
    -   Workflow execution final steps which return files to an asset are now rigged to use the new uploadFile lambda to support all file checks before versioning as part of an asset and to now support pipelines that return asset previews. This process follows an alternate external upload stage where presigned URLs are not needed due to the direct access nature of pipelines into the assets bucket (still uses temporary locations for security).
    -   AssetFiles API now brings back additional information for each file such as size, version, version created, and if the file is a versioned prefix folder or a file
    -   Support for empty asset creation and/or throughout life cycle of an asset (uploads no longer required during asset creation)
    -   Asset uploads in the UI now keep their original filenames and no longer change them to the asset name.
    -   The concept of "primary file" in an asset has been removed to support assets being truly multi-file
    -   New File URL Sharing action/modal for files in the file manager that generates presigned URLs for all files or folder selected
-   **Web** The front-end asset upload has been heavily modified to support the new backend asset changes
    -   Now supports choosing multiple files and/or entire folders
    -   Files now keep their original names and are no longer changed to the assetName
    -   Supports the presigned URL and multi-stage API calls needed now for an upload (including support for splitting large files into multiple parts for parallel upload)
    -   Supports stage and file error recovery options, including proceeding with certain failed uploads that will be discarded
    -   Comments are no longer a supported field as part of upload, as this functionality has been moved to creating asset versions
-   The assetFiles API now supports additional paths for functionality including `../fileInfo`, `../createFolder`, `../moveFile`, `../copyFile`, `../archiveFile`, `../unarchiveFile`, `../deleteFile`, `../deleteAssetPreview`, `../deleteAuxiliaryPreviewAssetFiles`,`../revertFileVersion`. ListFiles now provides additional data about each file.
-   **Web** The front-end asset download for multiple files has been updated to support downloading an entire folder's worth of files in parallel
    -   Note: This still fetches individual files based on their presigned URL for automation, it does not pre-ZIP files on a server and may still cause issues if hundreds or thousands of files need to be downloaded
-   **Web** The asset viewer file manager has been rewritten to support new features and richer user experience
    -   Instead of having a separate redundant icon view of files in the right pane of the file manager, it now shows file information such as file name, path, size, and any version information. For top-level asset nodes and image files, this will show the Preview file or actual file (image type files) now. This supports preview files now for both assets and files. See DeveloperGuide.md for documentation on preview file support (non-auxilliary).
    -   Added buttons for various downloads of files and folders
    -   Added ability and button to create sub-folders in an asset
    -   ViewAsset button still shown on files for asset 3D visualization, file-specific metadata, and file versioning
-   **Web** Execute Workflow in View Assets now allows the user to choose which file on the asset will be processed due to the new multifile support implementation of assets
-   **Web** Enhanced asset file management capabilities with comprehensive file operations:
    -   Added new API endpoints for file operations: fileInfo, moveFile, copyFile, archiveFile, unarchive, deleteFile, getVersion, getVersions, revertFileVersion
    -   Implemented file versioning with UI for showing files, knowing what version you are looking at, and reverting to a version
    -   Implemented file archiving which uses S3 delete markers (versus a permanent delete that removes the entire file)
    -   Added support for cross-asset file copying with proper permission validation (must stay within the same VAMS database)
    -   Implemented detailed file metadata retrieval including size, storage class, and version history
    -   Added permanent file deletion with safety confirmation to prevent accidental data loss
    -   Implemented proper error handling and validation for all file operations
    -   Asset files and versions will now show a flag for archived files and indicate if the asset is part of the current version files' version
-   **Web** (Breaking Change) All new asset versioning capability and version comparisons
    -   Asset versions must now be manually created and will no longer auto-create when editing the asset or uploading files
    -   New APIs are defined for asset versioning for create, get, and revert options
    -   Asset table has changed fields and new asset version and asset file versions tables are created which require a database migration script to be used when upgrading from a previous VAMS version
    -   Displayed as Versions tab under Asset Viewer and labels throughout (such as on file versions) to show file versions included in the current asset version (or mismatched)
-   **Web** New tabbed design for Viewing Asset
    -   Moved comments page for assets to now be a tab under view assets
-   Assets as a whole now support both permanent deletion and archiving
    -   Note: currently unarchiving an asset as a whole doesn't exist yet
-   Turned off the wireframe view for the 3DOnlineViewer for viewing models
-   Disabled for now the ability to see/view assets in Workflow Editor and the ability to Execute Workflows from Workflow Editor (doesn't fit the current functionality implementation)
-   Updated API and associated viewers/files for aux asset streaming endpoint from /auxiliaryPreviewAssets/stream/{proxy+} to /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}
    -   Added additional validation checks to make sure users only stream assets that belong to the asset ID provided
-   Subscription emails for assets will now trigger any time an asset itself changes or versions, or one of its files changes
-   Added infra configuration option for basic GovCloud IL6 compliance checks for features/services enabled or disabled
-   Added presignedUrlTimeoutSeconds configuration to infra config, moved credTokenTimeoutSeconds under useCognito configuration option (previously used one configuration value for both)
-   Changed metadata API paths (to standardize) from '/metadata/{databaseId}/{assetId}/' to '/database/{databaseId}/assets/{assetId}/metadata'
-   Created new DynamoDB table for workflow executions, migration of old jobs will not occur. The new table has better fields for tracking of workflow database ID to now cause conflicts of same name between databases.
-   Added capability to support multiple S3 buckets for assets now for a solution
    -   (Breaking Change) New CDK configuration options are added and required for defining asset buckets (created with solution or external load), see DeveloperGuide and ConfigurationGuide for details
    -   **Web** Databases now allow you to select which bucket/prefix the database will use for its assets
    -   New DynamoDB table for S3 Asset Buckets is set up with CDK deployment to define available asset S3 buckets
    -   Direct changes to asset S3 buckets are allowed and will be synced back to VAMS. New asset prefix files will create new assets and databases based on configuration details defined. File changes within an asset will be indexed and pulled with any new API requests involving asset file operations
    -   **Web** Pipelines in the navigation menu is now under "Orchestrate and Automate"
-   Standardized API route paths that had /databases* (plural) to /database* (singular)
-   Changed VPC subnet to now break out subnets for isolated, private, and public. Previously, only private (which was actually isolated) and public existed.
    -   For those using external VPC and subnet import configuration, previously private subnet IDs should now be copied into isolated subnets configuration option.
-   Added a new use-case pipeline and configuration option for `RapidPipeline` that optimizes 3D assets using mesh decimation & remeshing, texture baking, UV aggregation, and more.
    -   RapidPipeline can also convert assets between GLTF, GLB, USD, OBJ, FBX, VRM, STL, and PLY file types.
    -   Pipeline can be called by registering 'vamsExecuteRapidPipeline' lambda function with VAMS pipelines / workflows.
-   Updated backend infrastructure configuration options and functionality to support External OAuth IDP systems besides AWS Cognito. Includes many additional infrastructure configuration settings.
-   **Web** Added web support for External OAuth IDP configuration.
-   Added configuration option `addStackCloudTrailLogs` for creating AWS CloudTrail log groups and trails for the stack. This is defaulted to `true`.
-   Added configuration option `useAlb.addAlbS3SpecialVpcEndpoint` for creating the special S3 VPC Interface Endpoint for ALB deployment configurations. This is defaulted to `true`. See documentation for this setting if turned off.
-   **Web** Added infrastructure configuration option `webUi.optionalBannerHtmlMessage` for adding a persistent banner (HTML) message at the top of the WebUI.
-   **Web** Added capability to define which tag types are required to be added to an asset. If tag types are required, at least one of the defined tags on the tag type must always be included on the asset.
-   The ingestAsset API now supports passing in tags (to support required tag types).
-   Changed UserId to no longer need to be an email, added a new LoginProfile table to track user emails for notification service which gets updated from JWT tokens or organization custom logic for retrieving user emails. New API for updating LoginProfile added to web login.
-   Enabled Cognito user pool optional Multi-Factor Authentication (MFA) for created accounts across TOTP or SMS. **Note:** SMS sending requires additional AWS Cognito / SNS setup to a SNS production account and origination identity (if sending to US phone #'s).
    -   Added backend broken out custom logic and flag to know if a user is logged in with MFA or not. For external OAuth IDP implementations, this logic must be tailored to the IDP system.
-   Enabled ability for a VAMS external IDP authentication system to report back if a user is logged in via MFA through an additional MFA IDP scope request. This can be configured via infrastructure configuration script by specifying a specific scope for MFA. Leaving this configuration null turns off external IDP MFA support.
    -   **Web** The external IDP login will show a MFA login button if a mfa scope configuration request is defined.
-   **Web** Added capability to set on a role if it requires the logged in user to have authenticated via MFA in order for any constraints against that role to take effect. If MFA is not turned on in the selected authentication system, this would effectively disable the role as no user would satisfy the criteria.
-   Added new feature that gives users the ability to edit pipelines after initial creation. Users also have the option to update all workflows that contain the edited pipeline. The EDIT feature can be found as a button on the Pipelines page.
-   **Web** Added a new file viewer for video files using the HTML5 video player component. You can now view and stream files of types: ".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v"
-   **Web** Added a new file viewer for audio files using the HTML5 audio player component. You can now view and stream files of types: ".mp3", ".wav", ".ogg", ".aac", ".flac", ".m4a"
-   Added a new use-case pipeline and configuration option for `ModelOps` complex tasks such as file format conversions, optimizations for 3D assets, and generating image captures of 3D models.
    -   VAMS pipeline registration `inputParameters` will define for each pipeline registration what the output file extension type(s) will be. ModelOps can output multiple file types in one execution. Pipeline can be called by registering 'vamsExecuteModelOps' lambda function with VAMS pipelines / workflows.
-   Pipelines and workflows can now be created under a GLOBAL database. GLOBAL database workflows can be executed across all assets across all databases. The GLOBAL database is a reserved keyword now which implies that an entity applies to all databases (right now only workflows/pipelines) This now allows for the capability of registering pipelines/workflows automatically as part of use-case pipeline deployments as a database no longer needs to exist (GLOBAL).
-   Asset links backend APIs were re-written to accomodate tracking databaseId with to/from assets, tracking tags on asset links, and now tracking metadata against a asset links
    -   New dynamoDB tables are created for these new trackings, one of which requires a data migration script to move data from the old tables.
-   **Web** Asset Links under View Asset is now part of the tabbing window under "Relationships"
-   **Web** Asset links / relationships now has a new look similar to the new file manager to track relationships. This is in both View Assets and on Asset upload for new assets.
    -   Metadata key/pair values can now be tracked against asset links with String and XYZ type. Translation, Rotation, and Scale are hard- XYZ typed fields that can be used before adding custom metadata
    -   Ability to see child sub-trees from all child assets recursively down
-   **Web** Add file primary type attribute viewing and setting such as "primary", "lod1" - "lod5", and a custom primary type. These can be set on any file and are saved as metadata in S3 on the file. This is useful for identifying what the primary files are as part of an asset and if they are the prime or a particular level of detail (lod), or other designation. There is no logic tied to this value yet in VAMS but can be used for visual identification or in custom logic implementations.
-   **Web** Added option to show asset and file preview thumbnails on the Asset Search page.
-   Added a '/api/version' GET API path (NoOp authorizer) to get back the version of the current deployment of VAMS. This is stored in the config.ts file during CDK deployment and should be updated with VAMS version rollouts.
    -   Added '/database/{databaseId}/assets/{assetId}/setPrimaryFile' API endpoint to support this and returns this value as part of listing files and returning file information as part of those repsective APIs.
-   Added feature in CDK configuration to allow for unsafe-eval web features. This is turned off by default as it may requires an organizations security team to evaluate this before enabling. This is implemented to allow for future plugins and libraries that require this flag to be enabled in the web browser.
-   Add CDK Configuration options for API Rate and Burst limits to prevent denial of service situations. Adjust based on your traffic and your AWS account limits for both API Gateway and Lambda invocation allowances. Default configuration set 50 API requests per second and 100 bursts per second.
-   VendedLogs CloudWatch log groups have been set as a default retention of 1 year in the core CDK stack
-   (Draft Implementation) Started overhaul of lambda backend unit tests that were previously outdated and non-functioning. Unit tests as of 2.2 still have many non-functioning (skipped) tests that will need to be corrected. Passed tests will also need additional validation and coverage evaluation.

### Bug Fixes

-   Fixed permission caching in lambdas to actually reset caches after 30 seconds per lambda per user. Currently since v2.0 caches have been invalidating inconsistently.
-   Fixed opensearch lambda event source mapping for regions that don't support event source tagging yet (i.e., GovCloud) [bug introduced in v2.1.0 with CDK version upgrade].
-   Additional checks are made for valid parameter data in the asset deletion/archiving service.
-   Fixed local web local development support, updated documentation for new local development processes.
-   Fixed numerous lambda functions that were not adhering to the VPC/subnet configuration options for placing behind a VPC from v2.0 update.
-   Fixed more validation bugs to ensure API fields that take in arrays are actually arrays.
-   Miscellaneous minor bug fixes across web and backend components.
-   Fixed some multi-file/folder upload issues in UploadAssetWorkflow, Path Validation, and ScopedS3 retries
-   Fixed bug where asset search results using OpenSearch were not paginiating correctly when total results went over 100
-   Fixed bug where asset search result filters for database may restrict what users can search on based on previous results returned
-   Fixed scrolling issues with Firefox browser
-   Fixed bug with PointClouder viewer / pipeline from executing and showing final outputs
-   Fixed various bugs with asset comments editing and deleting
-   Fixed various UI and backend bugs related to the asset management overhaul
-   Fixed fullscreen mode on visualizers not working on certain visualizers after exiting from a previous fullscreen session; removed compact mode as this had no benefit.
-   Fixed OpenSearch search to exclude `bool_` fields that may get added to dynamodb tables it indexes. Wildcard searches in query don't work on bool fields.
-   Added check to metadataschema creation API to make sure the database ID exists before creating the schema

### Chores

-   Added more input variables for use in pipeline lambdas called such as bucketAssetAuxiliary, bucketAsset, and inputAssetFileKey. This is in addition to the predetermined "easy" paths setup for pipeline use
-   Added more error checks and outer workflow abort procedures for workflows/pipelines in use-case pipelines
-   Updated auxiliary asset handling to match the new asset location keys and handling
-   Updated workflow execution to handle new asset location keys, bucket, and handling
-   Created new DynamoDB workflow executions table (old one will remain as deprecated to not lose data) to store better format for lambda storage and retrieval
-   Modified workflow executions API to '/database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}' and also added '/database/{databaseId}/assets/{assetId}/workflows/executions/' to get all executions for an asset
-   Subscription SNS topics now store databaseId along with assetId in the topic name to prevent future conflicts
-   Create/Execute workflow backend update to support new asset management file/bucket/prefix locations, to be more dynamic based on the calling asset and file
-   Updated S3 asset bucket event notifications to be a SNS->SQS fan-out for bucket sync/indexing and other bucket subscriptions like for the PcPotreePreview pipeline
-   Cleaned up and removed backend and UI files and components that were no longer needed and/or deprecated related to assets
-   **Web** Cleaned up unused web files and consolidated functionalities for authentication and amplify configuration setting.
-   Upgraded lambda and all associated libraries (including use-case pipelines) to use Python 3.12 runtimes.
-   Upgraded infrastructure NPM package dependencies. Note: This switches CDK to use Node 20.x runtimes for Lambda's used for CustomResources or S3 Bucket deployments.
-   Optimized some backend lambda initialization code in various functions and globally in the casbin authorization functions for cold start performance improvement.
-   Updated S3 bucket name for WebAppAccessLogs for ALB deployment (to be based on the domain name used `<ALBDomainName>-webappaccesslogs`) to help with organization policy exceptions.
-   Added scripts and documentation for external oauth IDP and API local development servers.
-   **Web** Turned on amplify gen1 Secure Cookie storage option.
-   Updated GenAIMetadataLabeling pipeline container to use the latest blender version when deploying due to Alpine APK restrictions on holding earlier versions.
-   Switched web API calls to use Cognito user access token for all requests authorizations instead of Id token. Created separate parameter for scopedS3Access to pass in ID token for this specific API call that needs it.
-   Added logic to prefilter asset OpenSearch querying to only databases the user has access in order to increase performance for final asset permission checks for large asset databases
-   Updated CDK library dependencies to convert from alpha versions to regular implementations
-   Added Stack Formation Template descriptions
-   Added CSP header policies to ALB deployment listener on top of injecting into REACT front-end
-   Updated documentation for developer deployment machines to use Node version 20.18.1
-   Updated README documentation with new application screenshots

### Known Oustanding Issues

-   With updating to support multiple S3 buckets, there are scenarios that can occur where if there are multiple buckets/prefixes across different databases where the assetId are now the same, there will be lookup conflicts within Comments and subscriptions functionality. This can only occur right now with manual changes/updates as done directly to S3 as assetIds generated from VAMS uploads still generate unique GUIDs.
-   Using the same pipeline ID in a GLOBAL and non-GLOBAL database will cause overlap conflicts and issues.
-   There is an issue with OpenSearch recognizing asset fields as numbers if they contain all numbers instead of strings. Future updates will provide utility script to clear OpenSearch index and rebuild with the new mapping schema. Avoid using asset names or descriptions with all numbers to avoid them showing blank in asset search.

## [2.1.1] (2025-01-17)

This hotfix version includes bug fixes related to dependency tools and library updates.

This release may require a installation of the latest aws-cdk library to either your global npm or as part of your local VAMS infra folder. Please re-run "npm install" in VAMS infra to install the latest local dependencies for existing deployments.

### Bug Fixes

-   Fixed and added Poetry export plugin library used during Lambda layer building due to Poetry no longer including "export" as part of the core library.
-   Fixed Dockerfile container environment variable formats to no longer use the deprecated Docker format. `ENV KEY VALUE` -> `ENV KEY=VALUE`
-   Fixed 3D Metadata Labeling pipeline use-case to use the latest Blender version due to Alpine APK support deprecation for earlier specified versions.
-   Fixed 3D Metadata Labeling pipeline use-case state machine Lambda to not hard-code the `us-east-1` region for IAM role resource permission and use the stack-deployed region instead.
-   Updated aws-cdk dependency versions to the latest and updated GitHub CI/CD pipeline build checks

## [2.1.0] (2024-11-15)

This minor version includes changes to VAMS pipelines, use-case pipeline implementations, and v2.0 bug fixes.

Recommended Upgrade Path: A/B Stack Deployment with data migration using staging bucket configuration and upgrade migration scripts for DynamoDB tables in `./infra/upgradeMigrationScripts`

### ⚠ BREAKING CHANGES

-   Due to packaged library version upgrades in the solution, customer must make sure they are using the latest global installs of aws cli/CDK
-   Pipelines are now changed to support a new pipelineType meaning, and the old pipelineType was renamed to pipelineExecutionType.
-   Execution workflow input parameter names to pipelines have also changed, which can break existing workflows/pipelines.

Due to DynamoDB table structure changes, a A/B Stack deployment with migration script is recommended if there are existing pipelines that need to be automatically brought over.

### Features

-   Re-worked infrastructure CDK components and project directory structure to split out use-case pipelines (i.e., PotreeViewer/Visualizer Pipelines) from the rest of the lambda backend and stack infrastructures. This will allow for future upgrades that will split these components completely out into their own open-source project.
-   `PotreeViewerPipeline` (previously VisualizerPipeline) is now baselined to the new standard use-case pipeline pattern to support external state machine callbacks (i.e., from VAMS pipeline workflows)
-   -   `PreviewPotreeViewerPipeline` (previously VisualizerPipeline) can now be registered and called from VAMS pipeline workflows (suggested to be called from a preview type pipeline) via the 'vamsExecutePreviewPcPotreeViewerPipeline' lambda function.
-   Added a new use-case pipeline and configuration option for `GenAiMetadata3dLabelingPipeline` that can take in OBJ, FBX, GLB, USD, STL, PLY, DAE, and ABC files from an asset and use generative AI to analyze the file through 2D renders what keywords, tags, or other metadata the file should be associated with. Pipeline can be called by registering 'vamsExecuteGenAiMetadata3dLabelingPipeline' lambda function with VAMS pipelines / workflows.
-   Added a new use-case pipeline and configuration option for `Conversion3dBasic` that can convert between STL, OBJ, PLY, GLTF, GLB, 3MF, XAML, 3DXML, DAE, and XYZ file types. VAMS pipeline registration `outputType` will define for each pipeline registration what the output file extension type will be.
-   -   This pipeline for non-GovCloud deployments is enabled by default in the infrastructure configuration.
-   **Web** Added `pipelineExecutionType` to VAMS pipelines (previously `pipelineType`) and added a new context to `pipelineType`. Current pipeline types are `StandardFile` and `PreviewFile`. These are implemented to support future roadmap implementations of different pipeline types and auto-executions options on asset file uploads.
-   **Web** Added `inputParameters` to pipelines to allow the optional specification of a JSON object which can be used within a pipeline execution to set pipeline configuration options. This is set at the time of creating a VAMS pipeline.
-   Added `inputMetadata` to pipeline inputs which automatically pulls in asset name, description, tags, and all metadata fields of the asset to a pipeline execution. This can also be used in the future to pull through user-defined inputMetadata at the time of an execution with additional UI/UX.
-   Changed `inputPath` and `outputPath` of pipeline function execution inputs to `inputS3AssetFilePath` and `outputS3AssetFilesPath`
-   Added `outputS3AssetPreviewPath`, `outputS3AssetMetadataPath`, and `inputOutputS3AssetAuxiliaryFilesPath` pipeline execution parameter inputs to support different location paths for asset data outputs and writing to asset auxiliary temporary path locations
-   Added `outputType` for user-specified expected file extension output for pipelines based on the VAMS pipeline registration. OutputType is not enforced and is something pipelines need to work into their own business logic as appropriate.
-   -   All asset write-back locations are now temporary job execution specific to allow for better security, file checks, proper back-versioning into an asset, and to start abstracting pipelines from writing directly to assets. Once the UploadV2 process is completed in a future update, direct access by use-case pipelines to S3 asset buckets will be removed in favor of API uploads / presigned URLs for storage abstraction.
-   Updated `processWorkflowExecutionOutput` lambda function (previously `uploadAllAssets`) to also account for metadata data object outputs of pipelines to update against assets. Preview image output logic is stubbed out but will not be fully implemented until the new upload / storage process overhaul is completed in a future version.
-   Added `credTokenTimeoutSeconds` authProvider config on the infrastructure side to allow manual specification of access, ID, and pre-signed URL tokenExpiration. Extending this can fix upload timeouts for larger files or slower connections. Auth refresh tokens timeouts are fixed to 24 hours currently.
-   -   Implements a new approach for s3ScopedAccess for upload that allows tokens up to 12 hours using AssumeRoleWithWebIdentity.
-   **Web** Added PointCloud viewer and pipeline support for `.ply` file formats, moved from the 3D Mesh 3D Online Viewer
-   **Web** The asset file viewer now says `(primary)` next to the assets main/primary associated file. The primary file is what get's used right now for pipeline ingestion when launching a workflow.
-   Changed access logs S3 bucket lifecycle policy to only remove logs after 90 days
-   Added lifecycle polcies on asset and asset auxiliary bucket to remove incomplete upload parts after 14 days

### Bug Fixes

-   Fixed CreateWorkflow error seen in v2.0 (Mac/Linux builds) with updated library dependencies and setting a standardized docker platforms across the board to `linux/amd64`
-   Re-worked PreviewPotreeViewerPipeline (previously VisualizerPipeline) state machine and associated functions to properly handle errors
-   Fixed benign logger errors in OpenSearch indexing lambda function (streams)
-   Fixed existing functionality with `processWorkflowExecutionOutput` (previously `uploadAllAssets`) not working
-   Fixed pipeline execution to properly account for asset file primary key names that contain spaces. Previously, could cause pipelines to error on execution.
-   **Web** The asset file viewer now appropriately shows multiple files that are uploaded to the asset
-   **Web** Hid the `View %AssetName% Metadata` button for top-level root folder on asset details page file manager that led to a blank page. The metadata for this is already on the asset details page.
-   Fixed GovCloud deployments where v2 Lambda PreTokenGen for Cognito are not supported, reverted to v1 lambdas that only support Access Tokens (instead of both ID and Access token use for VAMS authorizers)
-   Fixed GovCloud deployments for erronouesly including a GeoServices reference that is not supported in GovCloud partition
-   Fixed KMS key IAM policy principals (for non-externally imported key setting) to include OpenSearch when using OpenSearch deployment configurations
-   Added logic to look at other claims data if "vams:\*" claims are not in the original JWT token. This is in prepartion for external IDP support and some edge case setups customers have.
-   Fixed CDK deployment bug not deploying the required VPC endpoints during particular configurations of OpenSearch Provisioned, Not using all Lambda's behind VPCs, and using the option to use VPC endpoints
-   **Web** Fixed bug where adding asset links had swapped the child/parent asset (WebUI only bug, API direct calls were not affected)
-   Fixed CDK deployment bug of encrypting the WebAppLogsBucket when deploying with ALB and KMS encryption. The WebAppLogsBucket cannot be KMS encrypted when used for ALB logging output.
-   Fixed bug for exceeding PolicyLimitSize of STS temporary role calls in S3ScopedAccess used during asset upload from the Web UI when KMS encryption is enabled.
-   Increased CustomResource lambda timeouts for OpenSearch schema deployment that caused issues intermitently during GovCloud deployments
-   Fixed bug in constraint service API that was saving constraints on POST/PUT properly but was erroring on generating a 200 response resulting in a 500 error
-   Fixed bug in OpenSearch indexing (bad logging method) during certain edge cases that prevented adding new data to the index
-   Fixed bug in CDK storageResource helper function where S3 buckets were not getting the proper resource policies applied

### Chores

-   VisualizerPipeline now re-named to PreviewPotreeViewerPipeline as the previous name was too generic and other "visualizer" or viewer pipelines may exist later
-   'visualizerAssets' S3 bucket renamed to 'assetAuxiliary'. This bucket will now be used for all pipeline or otherwise auto-generated files (previews/thumbnails) associated with assets that should not be versioned
-   'visualizerAssets/{proxy+}' API route and related function re-named to 'auxililaryPreviewAssets/stream/{proxy+}'. This function is used for retrieving auto-generated preview files that should be rapidly streamed such as the PreviewPotreeViewerPipeline files.
-   Renamed and moved `uploadAllAssets` lambda function handler. It is now `processWorkflowExecutionOutput` and moved to the `workflows` backend folder
-   Updated Workflow ListExecutions to write stopDate, startDate, and executionStatus back to DynamoDB table after an SFN fetch where the execution has stopped. This is done for performance / caching reasons.
-   Workflow executions are now limited to only 1 active running execution per workflow per asset. This helps prevent workflows from globbering each other and preventing other errors and race conditions
-   Updated a pipeline's default taskTimeout to 24 hours and taskHeartBeat to 1 hour unless otherwise specified. Previously, it defaulted to the service default which was up to a year. This helps prevent runaway asynchronous processes that never properly return and closeout workflow executions.
-   Added some external sfn token heartbeats into the new and existing use-case pipeline implementations at the end of a container run. These heartbeat locations can still be improved, but it is expected that these pipelines take longer to run.
-   Workflow executions now pass the originating execution caller's username and request context, which can be used for lambda cross-call logic
-   Created an additional Casbin API check abstraction function which can be used to consolidate API permission check logic and simplify lambda handlers. Applied to all existing API-gateway accessible lambda handlers
-   Added CDK Stack output to display all VAMS Pipeline Lambda function names for all activated use-case pipelines that can be registered within the VAMS.
-   Added error for all use-case pipeline lambdas if executed with the wrong task_token / call-back setup (synch vs asynch) in VAMS
-   Added draft lambda functions for the uploadV2 feature expected. Draft function not yet ingested into CDK for deployment.
-   Added security.txt file to website for AWS security reporting information.
-   Updated documentation on security, legal, and use notices.

### Known Oustanding Issues

-   Using s3ScopedAccess for Upload (currently in use by VAMS WebUI) can also cause synchronization issues due to race conditions between uploading and calling the asset upload APIs. Additionally handling very large file uplaods and downloads (+1TB) can cause issues. Expect a future re-write to use solely pre-signed storage URLs for upload and a 3/4-step guided API call process for this to resolve this issue, similar to `ingestAsset` API used to test the core of this new method.

## [2.0.0] (2024-6-14)

This major version represents an overhaul to the CDK constructs to support more scalable deployment configurations with many additional CDK deployment features. It adds a new VAMS permission system with new Attribute-Based Access Control (ABAC) and Role-Based Access Control (RBAC) systems. Lastly, the overhaul has added business logic features to support new data structures around asset storage.

Recommended Upgrade Path: A/B Stack Deployment with data migration using staging bucket configuration and upgrade migration scripts for DynamoDB tables in `./infra/upgradeMigrationScripts`

### Highlights

1. **CDK Infrastructure Overhaul**: This release represents a major overhaul of the CDK constructs, splitting the core logic into multiple nested stacks to support more scalable deployment configurations.
2. **Configuration System**: A new CDK configuration system has been introduced using `config.json` and `cdk.json` files. Many previously implemented features, such as OpenSearch or Location Services, can now be turned on or off.
3. **New Configuration Options**: Numerous new configuration options have been added, such as VPC/subnet management, Application Load Balancer (ALB) static web support instead of CloudFront, KMS encryption, OpenSearch configurations (including the ability to turn off OpenSearch), and more. These options can be toggled based on specific deployment requirements.
4. **Security Controls**: A major aspect of this release focuses on security tightening and controls. Implementers will now be able to deploy across AWS partitions, including GovCloud, and have more control over WAF, FIPS, Lambdas in VPCs, and Docker SSL Proxy configurations.
5. **New Access Control System**: A new Attribute-Based Access Control (ABAC) and Role-Based Access Control (RBAC) system has been implemented, replacing the previous Cognito group-based access control. This provides fine-grained access control to various VAMS resources.
6. **Asset Tagging and Linking**: A new mechanism for adding tags and tag types to assets has been introduced, along with the ability to create parent/child and related-to links between assets within the same database.
7. **Image and PointCloud Viewers**: Support for Image and PointCloud file visualizations has been added, including an infrastructure data pipeline to support viewer conversions for LAS, LAZ, and E57 input formats.
8. **Upgraded File Manager**: The web assets viewer has a new file manager UI/UX for viewing asset files and provides functionality for uploading multiple asset files within folders.
9. **Email Subscription System**: A new email subscription system has been implemented which allows VAMS users to subscribe to various data changes. Asset data objects are the first to be implemented as part of this version to allow users to receive notifications when new asset file versions are uploaded.
10. **Performance and Bug Fixes**: Various performance improvements and bug fixes have been implemented, including API input validations, optimizations for OpenSearch indexing, log group naming, unique resource naming, and workflow execution handling.
11. **Deprecations and Removals**: SageMaker pipeline types have been removed to focus development efforts on Lambda pipelines.

### ⚠ BREAKING CHANGES

-   **Possible break** CDK configuration and feature switch system using `./infra/config/config.json` file. Some backwards compatibility with existing CDK deployment commands.
-   CDK overhaul to split core logic into 10+ nested stacks means that an in-place upgrade for existing stack deployments is not possible, use A/B deployment.
-   Lambdas converted into inline code functions with layers (away from Lambda ECR-backed containers).
-   (SEO breakage) Switch Web infrastructure to use React hash router instead of web router to support ALB configuration option, which breaks search engine optimizations (SEO).
-   New ABAC/RBAC systems will require new roles and constraints to be set up to allow application access. Existing Cognito groups will no longer be recognized, and user memberships must be transferred to the role and constraint mechanisms.
-   SageMaker is no longer a pipeline type available. Existing SageMaker pipelines should be converted to be executed from a lambda pipeline.
-   Restrict VAMS workflow pipelines to only have permission to lambdas that contain `vams` in the function name by default. If you have external pipeline lambdas, please add invoke permissions for them to the appropriate workflow execution role or update your lambda function name to contain `vams`.
-   Pipelines created using the default lambda artifact sample will now need to be re-created and re-inserted into workflows due to using different database fields to store the name of these.
-   `/assets/all` (PUT) API call is deprecated in favor of using the existing `/assets` (PUT) and the newer `/ingestAsset` (POST) API.
-   Previously created workflows of pipelines that had pipeline nodes that didn't use `wait_for_callback` need to be re-created/re-saved from the VAMS UI or modified in the AWS Console to remove `TaskToken.$` from node tasks parameters if there is no callback on that node.
-   API response bodies for data retrieval calls that return several records have been standardized to `responseBody: {message: {Items, NextToken}}`.

### Features

-   Implement CDK configuration system using `./infra/config/config.json` file.
-   -   Implement local Docker package build file configuration override to support customization in `./infra/config/docker/Dockerfile-customDependencyBuildConfig` (such as in cases of HTTPS SSL proxy certificate support).
-   -   Add default template files for various configuration environments (commercial (default- config.json), GovCloud).
-   Implement new CDK environment system variables using `./infra/cdk.json` file.
-   -   Add global stack resource tagging.
-   -   Add global new role permission boundary support.
-   -   Add global new role name prefix tagging.
-   Implement feature switch system and storage for Web feature toggling (new DynamoDB table).
-   -   **Web** Load/cache enabledFeatures as part of the backend web configuration load to the frontend.
-   Implement GovCloud feature switch which toggles other features on/off based on GovCloud service support and certain best practices.
-   Implement FIPS support configuration option.
-   Implement WAF configuration option (existing WAF functionality, ability to now toggle off).
-   Implement Global VPC configuration option used for particular configuration needs.
-   -   Support new VPC/Subnet generation.
-   -   Support an option for external VPC/subnet imports (instead of new VPC generation).
-   -   -   Added implementation of LoadContext Deployment configuration to support VPC context loading before main deployment.
-   -   Support an option for auto-adding*new VPC endpoints based on other configuration switches (*with some exceptions in particular configurations that will still auto-add regardless of this flag).
-   -   Support putting all deployed lambdas behind VPC (FedRamp best practices for GovCloud).
-   Implement ALB configuration option for static WebApp delivery (replaces CloudFront when enabled).
-   -   Requirement Note: ALB tied to a registered domain that must be provided.
-   -   Support WAF (if used) to deploy globally or regionally based on ALB/CloudFront deployments.
-   -   Support for using public private subnets for ALB.
-   -   Support/Requirement for SSL/TLS ACM certificate import for ALB.
-   -   Support for optional externally imported Route53 HostedZone updating for ALB deployment.
-   Implement KMS CMK encryption configuration option for all*at-rest storage (*with some S3 bucket exceptions in particular configurations such as ALB use).
-   -   Support new key generation on stack deploy.
-   -   Support option for external CMK key import instead of new key generation.
-   -   Disable all KMS CMK keys use implemented previously when configuration feature disabled (e.g., S3 bucket SNS notification queues). Uses default/AWS-managed encryption when KMS CMK disabled.
-   Implement OpenSearch provisioned, serverless, or no (neither serverless nor provisioned enabled) open search configuration options; No open search will disable VAMS asset search functionality.
-   Implement location service configuration option and feature switch (existing location service functionality, ability to now toggle off).
-   -   **Web** Hides Map view from Assets web page when turned off.
-   Implement point cloud visualization configuration option (existing pipeline functionality, ability to now toggle off through configuration file).
-   Add VAMS upgrade migration scripts to support A/B deployments and data migration between stack deployments in `./infra/deploymentDataMigration`.
-   (Future Full-Implementation) Implement authentication provider configuration option and feature switch. Note: Currently, only the Cognito `useSaml` configuration flag is observed (moved from `saml-config.ts` file), other auth types will cause an unimplemented error.
-   Implement new initial ABAC/RBAC access control systems to allow for fine-grained access to various VAMS resources (built on the Casbin open-source library).
-   -   ABAC defines the primary constraints and access controls.
-   -   -   ABAC currently supports resources of Databases, Assets, and "APIs".
-   -   -   **Note** Databases and Assets control primary VAMS storage resources. APIs control access to top-level system functionality (administrative pages, pipelines/workflows, etc.).
-   -   RBAC roles map to ABAC constraints to allow for backward compatibility with role/group-based access systems.
-   -   ABAC constraints can also map directly to users if organizations choose to go solely with the ABAC system.
-   -   Removed the previous Cognito group and constraint system.
-   -   -   **Note** Starts to reduce dependency on Cognito functionalities.
-   -   Created default admin role and constraint groups on new VAMS deployment. Stack deployment user will be auto-added to this new role group.
-   -   All lambdas now check access against the new ABAC constraints system.
-   -   **Web** Allowed Web routes controlled by ABAC constraints.
-   -   **Web** Administrative UI pages to support roles, role membership, constraints, and constraint membership modifications.
-   Implement new tag and tag type mechanism for adding additional information on assets (tags/tag types are currently global across all databases).
-   -   **Note** Requirement that Tags must have a tag type assigned.
-   -   **Web** Ability to search tags on assets on the asset search page.
-   -   **Web** Ability to assign/unassign tags to assets on asset creation and asset editing pages.
-   -   **Web** Administrative UI pages to support system tag and tag type modifications.
-   Implement asset linking functionality to support parent/child and related-to links between assets in the same database. Limit set to 500 of any asset link types per asset.
-   -   **Web** Ability to add/remove links to assets on asset creation and asset editing pages.
-   Implement asset email notification subscriptions on asset modification.
-   -   **Note** Users must confirm the subscription for each asset subscribed to in their inbox due to the current SNS topic implementation method.
-   -   **Web** Ability to add/remove user subscription to an asset on the asset viewing page.
-   -   **Web** Administrative UI pages to support global asset email list changes.
-   Enhance asset ingestion API to support better pushing of assets from external systems into VAMS.
-   -   **Note** The current implementation does not yet support API Key implementation for authentication and must still have a JWT authentication token to validate the calling system.
-   -   **Web** Administrative UI debug pages to allow organization administrators to call the API with various JSON payload inputs from the VAMS webpage.
-   **Web** Added PointCloud viewer support with Potree Viewer and an optional infrastructure pipeline configuration option for Potree conversions for .laz, .las, and .e57 file types uploads.
-   The AssetName field now has a new restriction to only support up to 256 characters with the following regex: `^[a-zA-Z0-9\-._\s]{1,256}$`.
-   Email user IDs now follow the new restriction to only support the following regex: `^[\w\-\.\+]+@([\w-]+\.)+[\w-]{2,4}$`.
-   Implement Cognito client USER_PASSWORD_AUTH configuration option as `useUserPasswordAuthFlow` for organizations who cannot perform SRP calculations on some of their VAMS integrations. By default, this configuration option is set to false.
-   Upgrade Cognito to insert VAMS claims tokens into both ID and Access tokens, which helps with confusion on 500 service errors when using the Cognito access token for API authentication.
-   **Web** Add a new file manager viewer on the view asset page to provide a better visualization and upload experience for multiple files and folders.
-   **Web** Add a new Image viewer for image type assets (non-preview files). Preview images are still supplemental on image asset files, which can be used for thumbnails, as an example.

### Bug Fixes

-   OpenSearch indexes now properly update when asset details are changed.
-   Change certain log group names to add the `/aws/vendedlogs` prefix to fix the issue of reaching the maximum CloudWatch policy character count on AWS accounts with many current/past resource deployments.
-   Fix the unique name generator for certain resources to fix character count limit issues and be more deterministic across VAMS (re-)deployments.
-   Added additional parameter input validations for API calls and fixed various 500 service errors based on malformed requests.
-   Fix workflow execution bug that caused errors across all workflow executions that didn't use the `wait_for_callback` flag in a lambda pipeline. This bug fix requires the re-creation/re-saving of all applicable workflows from the VAMS UI or manual adjustment in the AWS Console of created state machines to remove `TaskToken.$` from tasks parameters if there is no callback. This error was due to an AWS Step Functions service logic change.
-   Fixed OpenSearch query parameters to discard `#deleted` assets during the OpenSearch query and not just as a post-processing step. This should help prevent inconsistent results when wanting to limit search results to a single or a handful of total records.
-   Fixed OpenSearch asset searching to look at the passed-in searchbar 'query' value and properly search across all asset indexed fields (including all asset metadata). Previously, this did not work at all and just returned all results, all the time.

### Chores

-   Renamed VAMS stack to 'VAMS core' and changed the overall user-stack naming scheme, updated resource naming across the board to meet the new CDK construct rebuild
-   Upgraded lambdas and custom resources to use Python 3.10 and NodeJS 18_X runtimes
-   -   Consolidated runtime container deployment constant to the code configuration file
-   Broke up CDK constructs into 10+ nested stacks for scalability, compartmentalization, and fixing stack resource limit constraints
-   -   Restructured the infra folder to meet the new nested stack and constructs breakup
-   Converted lambdas into inline code functions with layers (away from Lambda ECR-backed containers)
-   -   Split lambdas into 2 layers depending on dependency package need. This reduces deployment sizes per lambda and improves runtime performance.
-   -   Added lambda layer package reduction logic to remove test/cache data in dependencies to further reduce layer MB size
-   -   Updated/Added backend folder structure and yarn packages to support new inline support and layer support
-   Used the 'esbuild' package library instead of docker for any NodeJS lambda deployment packaging
-   **Web** Switched Web infrastructure to use React hash router instead of web router to support the ALB configuration option
-   -   **Web** Added hash route deduplication code to help prevent/notify of possible link/navigate improper usage with `#` link prefixes
-   Switched CloudFront to use OAC instead of OAI for better security and functionality support for S3 origin support
-   Implemented Service ARN/Principal switcher and constants file to support different AWS partition, region, and FIPS use deployments
-   -   Introduced the genEndpoints script to update the service ARN/principal constants file. Note: Does not have all services, so some have to be added manually back to the constants file. Use with caution.
-   Switched Pipeline Visualizer lambdas to look at the "Add Lambdas to VPC" configuration flag to determine if they are in a VPC
-   Added all-around error checking and various deployment warnings on the CDK infrastructure configuration system flags
-   Modified the stagingBucket configuration tree entry to allow for future upgrades to support more different types of staging buckets
-   **Web** Removed file viewer options from the main web menu as they don't fit with the application web flow anymore
-   Updated CDK deployment outputs to match configuration options
-   Updated prettier/lint ignore files to ignore certain configuration and CloudFormation template files
-   Updated documentation/diagrams for configuration/environment/deployment modes along with different edge-case scenario deployments such as HTTPS SSL proxy certificate support
-   -   Updated pricing information for various configuration modes
-   Updated documentation to support the new outlined features
-   Added Casbin@1.34.0 (Apache-2 License) backend library package to dependency files
-   Restricted workflow pipelines to only have permission to lambdas that contain `vams` in the function name by default
-   Workflow pipelines created using the default lambda will now generate with a part-randomized string name to prevent same-name overlap
-   -   Note: Pipelines created using the default lambda artifact sample will now need to be re-created and re-inserted into workflows due to using different database fields to store the name of these.
-   Workflows created will now generate a state machine with a part-randomized string name to prevent same-name overlap
-   Added file extension and MIME content type checks on various upload and download file APIs. Currently checking for execution or script files which will be unallowed from VAMS.
-   Fixed the asset download API (and modified some of the parameters) which previously was not working, limited s3 scoped access STS call permissions to only be able to upload files. Expect the scoped s3 call to go away entirely as upload/download is revamped in future updates.
-   Added pagination query params and max limits to all API data fetches that don't return single item results. This also standardizes the response bodies to `responseBody: {message: {Items, NextToken}}`. This should allow VAMS to grow into a larger system that can support more than 1500 assets/records.
-   -   **Web** Added client-side pagination aggregation of total results. Full REACT page view with dynamic fetching not yet implemented.
-   **Web** Changed the front-end to use the download API for generating Presigned URLs instead of using the Amplify client logic with s3ScopedAccess
-   -   Starting to phase out s3ScopedAccess by reducing permissions and logic depending on it from the Amplify/client side. Expect full deprecation of this in the future.
-   **Web** Updated the 3D Model Viewer package to v0.12.0 and related dependencies
-   **Web** File model viewer now looks at a separate constants variable for file types to use with 3D Online Viewer (<https://github.com/kovacsv/Online3DViewer>)
-   -   **Note** This allows customers who wish to accept the opencascade LPGL license to view some CAD formats. These file types are excluded by default. See the documentation on how to enable.

### Deprecation / Feature Removal

-   SageMaker pipeline types have been removed from the available pipelines to run. Existing SageMaker pipelines should now be called via a lambda execution layer. This is due to better security implementation and the focusing of development efforts on lambda executions which can launch any other needed service.
-   The `/assets/all` (PUT) API call is deprecated in favor of using the existing `/assets` (PUT) and the newer `/ingestAsset` (POST) API. Backend business logic code for generating lambda components remains for use in the workflow API currently.
-   The S3 `bucket` field is no longer a needed input or response field for working with asset APIs. The bucket will now be fetched from environment variables instead, based on solution permissions.

### Known Outstanding Issues

-   Although v2 split the monolithic stack architecture from v1.5 and below into nested stacks, CDK deployment warnings may show up with certain configuration option combinations that the maximum resource count for the API nested stack is approaching the maximum limit (1000).
-   Uploading of asset files from the UI can cause time-outs if files are too large or networks are too slow due to the current hard limitation of 1 hour STS credentials using the s3ScopedAccess method. Using s3ScopedAccess can also cause synchronization issues due to race conditions between uploading and calling the asset upload APIs. Expect a future re-write to use solely pre-signed storage URLs for upload and a 3/4-step guided API call process for this to resolve this issue, similar to `ingestAsset` API used to test the core of this new method.

## [1.4.0](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/compare/v1.3.1...v1.4.0) (2023-07-28)

### ⚠ BREAKING CHANGES

-   Support uploading folders as assets (#92)

### Features

-   Easily replace terms Asset and Database ([#88](https://github.com/awslabs/visual-asset-management-system/issues/88)) ([ec54368](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/ec54368e68ad67d79b4bc129176a2ad486a6fbd7))
-   hiding sign up ([#104](https://github.com/awslabs/visual-asset-management-system/issues/104)) ([6d63177](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/6d631777fbb59d55d561e4f8827a46b0e2a240f0))
-   Support uploading folders as assets ([#92](https://github.com/awslabs/visual-asset-management-system/issues/92)) ([a5d768d](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a5d768d1e25508a48035e56f5353c760c1efdadd))
-   **web:** improvements to metadata component ([#110](https://github.com/awslabs/visual-asset-management-system/issues/110)) ([1ad3236](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/1ad32361a0981af971a36653b2a67f3c5e706338))

### Bug Fixes

-   dependency conflict was causing downloads to fail ([#94](https://github.com/awslabs/visual-asset-management-system/issues/94)) ([4cde458](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/4cde45874d099bf72cf4a69a5da8e17ab16ae81f))
-   download asset only if they are marked as distributatble ([#106](https://github.com/awslabs/visual-asset-management-system/issues/106)) ([93f9c1b](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/93f9c1b89da9f1cd15e5eb8930c90150d80f1db4))
-   Release fixes ([#109](https://github.com/awslabs/visual-asset-management-system/issues/109)) ([d2060c2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/d2060c21dab0187d4231e5e0b66724bc561cd203))
-   repair first deployment with opensearch ([#107](https://github.com/awslabs/visual-asset-management-system/issues/107)) ([4e0ba30](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/4e0ba306295bd0bd254d3eb5ed74d4b8511b4ea2))
-   repair regression on createPipeline ([#93](https://github.com/awslabs/visual-asset-management-system/issues/93)) ([997241f](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/997241f39bed6ae9a5ce3e61a9cee80e136dad95))
-   simplify auth constraints screen ([#115](https://github.com/awslabs/visual-asset-management-system/issues/115)) ([463c8e7](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/463c8e7572d024ccc53d453d883dd55da14e2008))
-   single folder single file upload ([#95](https://github.com/awslabs/visual-asset-management-system/issues/95)) ([bb023ab](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/bb023ab5c5408a2fe219f1e7534489535626136f))

### Chores

-   **deps:** bump certifi from 2022.12.7 to 2023.7.22 in /backend ([#111](https://github.com/awslabs/visual-asset-management-system/issues/111)) ([95c2b7c](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/95c2b7c248e7cadc9cc6619bd9c2748575a961ff))
-   **deps:** bump semver from 5.7.1 to 5.7.2 ([#105](https://github.com/awslabs/visual-asset-management-system/issues/105)) ([c11edf2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/c11edf2aec5d09fe708a3fa955115a4333e0d791))

## [1.3.0](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/compare/v1.2.0...v1.3.0) (2023-06-13)

### Features

-   apigw authorizer for amplify config endpoint ([14062c7](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/14062c75ecfc27b9582f449e83cdff12bd94cb46))
-   enable cloudfront compression ([8459485](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/8459485e8bfa40644ab39ed46298df2ad687b1d2))
-   eslint now runs in ci for web and infra ([7985460](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/79854601eef67a991ec81bfe6ede6fb5feb76ff1))
-   Federated authentication using SAML ([6048fc0](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/6048fc0627d404e8dd0d6a8f7a75e3f32b190adb))
-   Fine grained authorization rule definition ([6d0646d](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/6d0646dde8e52edded01fa6ff31f2fb7c56c8915))
-   **infra:** consolidated settings for storage ([3309426](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/3309426e56e6b8805cee27784b57d5186682373a))
-   Role based access control scaffolding ([a0b57f2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a0b57f26c317386a8992a99cbd161b1a40ea4d7e))
-   Support long running pipelines with Step Functions' wait for callback feature. ([#76](https://github.com/awslabs/visual-asset-management-system/issues/76)) ([53d7c07](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/53d7c076923dd60ac49ac8b09c8df045516b7a28))
-   **web:** add new model visualizer supporting .obj, .gltf, .glb, .stl, .3ds, .ply, .fbx, .dae, .wrl, .3mf, .off, .bim file types ([b7f2686](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/b7f26869a0891304e6e85ee217da66003cb55265))

### Bug Fixes

-   automatically naviagte to asset page once asset upload completes ([05d7bfe](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/05d7bfed1236499cb3d834caccbd8449094eca72))
-   cdk nag suppressions for python 3.9 and nodejs14.x ([#78](https://github.com/awslabs/visual-asset-management-system/issues/78)) ([926d159](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/926d159985b86541bcb5190167706cd64fea9e55))
-   ci.yml formatting ([46fd622](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/46fd62287f7af66c9dfa6bad631927099454f619))
-   congitoUsername --> cognitoUsername, added dependency to ([b2ca84f](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/b2ca84fab210ee9d1852f169fe9fc7c37d14fec4))
-   Hitting Execute Workflow button from the assets page doesn't work ([758902b](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/758902be9b78276bce30ba6ff54bd1c007cee10f))
-   **infra:** eslint fixes ([7c824c8](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/7c824c87b8859197b0b46b3fc9c97c80afafa92a))
-   renaming userpool causes failures in existing stack ([a798dec](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a798decd0c2fbeeda50933ba146b8890e0ae6abd))
-   resolve to fast-xml-parser 4.2.4 ([#89](https://github.com/awslabs/visual-asset-management-system/issues/89)) ([08a761c](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/08a761cfa39f5fb35f218cad00bbe11f269401a8))
-   resolves issue [#68](https://github.com/awslabs/visual-asset-management-system/issues/68), workflow editor added extra pipelines ([c390fe8](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/c390fe842577da65d253b884aefa35b9b66e850a))
-   saml callback url trailing slash variants ([51fe433](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/51fe433faa88e3c490a2315b828281a636bf5e6f))
-   Updated cdk-nag suppression ([46370a7](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/46370a779d9d10f06fa6c87334e8c5c7216b99e8))
-   updated the workflow editor ([#80](https://github.com/awslabs/visual-asset-management-system/issues/80)) ([78916ce](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/78916ced8bdae7e8a32bb44985347b6da9b6187e))
-   **web:** aligned grid definition with provided elements ([4ceb49b](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/4ceb49b3dd30cc369f73f7e7684d2233e2226268))
-   **web:** eslint eqeqeq ([d426baa](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/d426baa9ae75e523e60462aca1701a2bb1d7f626))
-   **web:** eslint fixes and exclusions ([d875f7e](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/d875f7e14c33ded5d7672f4326bda607193a8bef))
-   **web:** Fixed an event listener leak and Carousel radio buttons refactored to controlled components to reduce warnings. ([7ad8738](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/7ad8738ae288d3b8cd4cc7cbd51bcc472b55b9a6))
-   **web:** fixed event listener leak ([482bb48](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/482bb481525d7faffc6b7e07e6b4d34569c77a9f))
-   **web:** Handled undefined prop type with more grace. ([315abc9](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/315abc9d1074b67e8e194f0913a1d434132e6cf4))
-   **web:** Refactored input control to use refs. ([f91b8d7](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/f91b8d7f7f32fbc474fdb1c37c92dc48e979dbe0))
-   **web:** removed unused variables and imports ([6c3edd1](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/6c3edd10a2bdf3c40ee0b843ba063d6da054610d))
-   **web:** removed unused variables and updated useEffect dependencies. ([056a088](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/056a088eaca881a46320421f3fe303b80f4376aa))
-   **web:** Resolved a large stack trace logged to the console on the view asset screen. ([9e7fd81](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/9e7fd81a0ba1d62ee3e839807761603fa77c3475))
-   **web:** Suspense fallback requires a component rather than a function. ([a74a77c](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a74a77cf442b84998498e3f8a2d87d780867fadd))

### Chores

-   add lazy load for visualizers to view asset page ([5d3d8e2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/5d3d8e25d4fc1b51480c5ec46d6ce348108de031))
-   code split app, workflow editor, plotter ([03497f2](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/03497f20194963c8e1207a3761bf31695f370af8))
-   **deps:** bump requests from 2.30.0 to 2.31.0 in /backend ([#82](https://github.com/awslabs/visual-asset-management-system/issues/82)) ([8347563](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/8347563e2b4ec6ec9a6759797c05f2978ee4d977))
-   made corrections to links in changelog ([bb7cec9](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/bb7cec9c411b6673c8090ac0b9aa79a13e6a377c))
-   prettier check added in github actions ([7337bf6](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/7337bf6169cbba65b72daa99a61382bf932f62ad))
-   prettier configuration and reformatting ([70971a9](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/70971a97272235f13f56c2379d2da41108171404))
-   prettier formatting ([a5947cb](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/a5947cb7d98f73033ec6f5983ad31f538ddd8822))
-   testing ci build ([940882d](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/940882d706ad3861a8e33727f40d17a0abc168f7))
-   update yarn lock ([dc0e5fd](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/dc0e5fd238e561b45cd7eda817469dc49f350a39))
-   **web:** prettier formatting ([51f67b6](https://us-east-1.console.aws.amazon.com/codesuite/codecommit/repositories/vams/commits/51f67b6823bc9fcb2c46927f0b48430e4083f2ac))

## 1.2.0 (2023-03-14)

### Features

-   Added uploadAssetWorkflow lambda function ([810bab7](https://github.com/awslabs/visual-asset-management-system/commit/810bab79e201f390bd990e195bee9ef69126d029))
-   Asset metadata feature ([7818b67](https://github.com/awslabs/visual-asset-management-system/commit/7818b67eda1e0a97f39baf13a137a92838480040))
-   updates to UploadAssetWorkflow stepFunction ([10d6955](https://github.com/awslabs/visual-asset-management-system/commit/10d6955934106c956f7a36d35b29d57b74a46103))
-   uploadAssetWorkflow stepfunction orchestration ([a4cfb25](https://github.com/awslabs/visual-asset-management-system/commit/a4cfb2579c71de366d34dd0405e308af898f55d4))
-   **web:** awsui css replaced with cloudscape css ([c67b06f](https://github.com/awslabs/visual-asset-management-system/commit/c67b06fde30cde0789f8a1788296f192d45e2b8c))
-   **web:** call uploadAssetWorkflow ([1a58383](https://github.com/awslabs/visual-asset-management-system/commit/1a58383aa86c897eaee5b6d763cdfe28570f893e))
-   **web:** metadata editing on the asset screen ([2dbdc8c](https://github.com/awslabs/visual-asset-management-system/commit/2dbdc8cf5f3c172e720d0db6a438623c41f389b9))
-   **web:** wizard ux for upload ([ff1b92e](https://github.com/awslabs/visual-asset-management-system/commit/ff1b92efb5aec551b94107a5bf53d5241773bc0f))

### Bug Fixes

-   added common aws security rules for WAF ([23155e9](https://github.com/awslabs/visual-asset-management-system/commit/23155e933f56c58204d7722548200548ce7b161f))
-   **backend:** return 404 when no metadata records exist ([199e422](https://github.com/awslabs/visual-asset-management-system/commit/199e4226bb3d9a3100dfe2eb87b1800667c96fa0))
-   **backend:** tests missing assetName ([5deca7c](https://github.com/awslabs/visual-asset-management-system/commit/5deca7c4d352cefa453a68842938cca58c71583c))
-   **backend:** tests missing assetName ([900d85e](https://github.com/awslabs/visual-asset-management-system/commit/900d85e0b9d76727b193458e5d85d63ea4b36886))
-   change all buckets to S3_MANAGED encryption ([97f0ac4](https://github.com/awslabs/visual-asset-management-system/commit/97f0ac45f403aadfad95ffa08ce00186fe0bbfd5))
-   change log s3 bucket encryption type to S3_MANAGED ([28f1bb9](https://github.com/awslabs/visual-asset-management-system/commit/28f1bb9e44f1b17b8ef8af792a266c351ff0316e))
-   display generated assets and assetName ([fda1767](https://github.com/awslabs/visual-asset-management-system/commit/fda176746f8a3d81679657484e944dc8e7440e2b))
-   downgrading default notebook platform ([8477e0d](https://github.com/awslabs/visual-asset-management-system/commit/8477e0d4d7bbe8b45c0520202b028606a49201e1))
-   **examples:** Example lambda pipeline defect repaired ([89c4f71](https://github.com/awslabs/visual-asset-management-system/commit/89c4f71450e1ad2a594a22c7999aa4ae2d1fce92))
-   fixing loader-utils security vulnerability ([2f2d02f](https://github.com/awslabs/visual-asset-management-system/commit/2f2d02f9639e8125963a0b713dc13355bc9eb590))
-   s3 copy_object calls include owner acct ids ([#32](https://github.com/awslabs/visual-asset-management-system/issues/32)) ([71f55d8](https://github.com/awslabs/visual-asset-management-system/commit/71f55d8a7a00d94eb162df36d019553b979ed7f6))
-   set arch to linux/amd64 for apple m1/m2 users ([d70d1b8](https://github.com/awslabs/visual-asset-management-system/commit/d70d1b85f3522965384cf0acd9cb300cf0667405))
-   staging bucket env variable name ([0d228c6](https://github.com/awslabs/visual-asset-management-system/commit/0d228c62900f045988adda855f638cd1bfb3301a))
-   statemachine execution fix ([75887dc](https://github.com/awslabs/visual-asset-management-system/commit/75887dc585da67233832d24e7cc1e892648b80e9))
-   updated the ssm-parameter-reader custom resource's lamdba runtime to nodejs18.x for cdk-nag: AwsSolutions-L1 ([8d3d90b](https://github.com/awslabs/visual-asset-management-system/commit/8d3d90ba57e5e0b6492d47e5a4eecbf61d9b23a5))
-   updating certifi version for critical vulnerability ([ad573b6](https://github.com/awslabs/visual-asset-management-system/commit/ad573b6d9365491635f0a4004913e87e6faa8c8c))
-   updating ci.yml ([24c541f](https://github.com/awslabs/visual-asset-management-system/commit/24c541ff8b54ca012ba3a6a2dd22a51f98f52bdf))
-   use provided preview image when the generated image fails to load ([3404dd0](https://github.com/awslabs/visual-asset-management-system/commit/3404dd05839ff56f32c94d6bb0362090935cd958))
-   using cdk 2.62.1 with croRegionReferences set to true to resolve cfn-nag ([94b4874](https://github.com/awslabs/visual-asset-management-system/commit/94b4874443e00c0d403fc4106b876c9e571239ca))
-   **web:** hamburger menu overlapping other elements ([e6cb8f4](https://github.com/awslabs/visual-asset-management-system/commit/e6cb8f491258e6283808beae4a0e15ff180a867e))
-   **web:** prevent word wrapping in the visualizer ([0e966e8](https://github.com/awslabs/visual-asset-management-system/commit/0e966e87841ae6e72ff064ec9819c325e4f45744))
-   **web:** update create asset buttons ([87bba93](https://github.com/awslabs/visual-asset-management-system/commit/87bba93d60c77596084598e6df6742171da21c52))

### Chores

-   adding fbx file formats for pipelines ([#35](https://github.com/awslabs/visual-asset-management-system/issues/35)) ([e4aad1f](https://github.com/awslabs/visual-asset-management-system/commit/e4aad1f27fd908f96201f36c73559bda81b3a7f8))
-   adding suppressions on notebook for ash ([9a8b96e](https://github.com/awslabs/visual-asset-management-system/commit/9a8b96e73029f92641d5aabd006a019301e63017))
-   cleaned up some code in infra-stack.ts ([2aa53e2](https://github.com/awslabs/visual-asset-management-system/commit/2aa53e2bc867c72b64069e52bb70e5dc09d15537))
-   **deps:** bump axios from 0.21.1 to 0.26.0 in /web ([1635f86](https://github.com/awslabs/visual-asset-management-system/commit/1635f8619b4cd814627b013847c099e4c373982e))
-   **deps:** bump certifi from 2022.9.24 to 2022.12.7 in /backend ([c0d8b3e](https://github.com/awslabs/visual-asset-management-system/commit/c0d8b3e4db34c038b663e97cb6f6b07004f46654))
-   **deps:** bump werkzeug from 2.2.2 to 2.2.3 in /backend ([#34](https://github.com/awslabs/visual-asset-management-system/issues/34)) ([74d547f](https://github.com/awslabs/visual-asset-management-system/commit/74d547fd5839c604312b107fcb03bdead32ad3a0))
-   fixes after running automated security helper ([ee48599](https://github.com/awslabs/visual-asset-management-system/commit/ee485999edc378eb7ddeb0192b8a83a14ed9dbcf))
-   prettier configuration ([1cef984](https://github.com/awslabs/visual-asset-management-system/commit/1cef984630bf325b9477daa3358e85dc07b5b286))
-   **release:** 1.0.0 ([ae61d15](https://github.com/awslabs/visual-asset-management-system/commit/ae61d152ba9ea84dba58d12a682f66db895d0b08))
-   **release:** 1.0.1 ([#21](https://github.com/awslabs/visual-asset-management-system/issues/21)) ([ec85772](https://github.com/awslabs/visual-asset-management-system/commit/ec85772f9dc7e1a13538ef0bd070d1be1bfa18ca))
-   remove unused resources ([#31](https://github.com/awslabs/visual-asset-management-system/issues/31)) ([0138bf1](https://github.com/awslabs/visual-asset-management-system/commit/0138bf104d3b5a4dd6c35c5983c55ee2596bb561))
-   removing unused files ([4d86f9b](https://github.com/awslabs/visual-asset-management-system/commit/4d86f9bea713625f71c8d662c6fef3c665394dd9))
-   Repair copyright headers ([#30](https://github.com/awslabs/visual-asset-management-system/issues/30)) ([dff7d76](https://github.com/awslabs/visual-asset-management-system/commit/dff7d768a4faa28829e215c559dde2c59285f018))
-   update broken links on DeveloperGuide ([0cccd0e](https://github.com/awslabs/visual-asset-management-system/commit/0cccd0ec1ceb3efc88918dfe95acac58afaefdbb))
-   update to list_objects_v2 ([#33](https://github.com/awslabs/visual-asset-management-system/issues/33)) ([a62a788](https://github.com/awslabs/visual-asset-management-system/commit/a62a7883ea97d9be85cbf4cf0c934651dcbe2b26))
-   **web:** copyright headers ([16b4f84](https://github.com/awslabs/visual-asset-management-system/commit/16b4f844f86a7c7d72b345f3d0647b5729f77ea2))
-   **web:** update to cloudscape from awsui ([450bffe](https://github.com/awslabs/visual-asset-management-system/commit/450bffe543464f0f01faa29debf0b28ed85e5c73))

### 1.0.1 (2023-02-10)

### Bug Fixes

-   change all buckets to S3_MANAGED encryption ([97f0ac4](https://github.com/awslabs/visual-asset-management-system/commit/97f0ac45f403aadfad95ffa08ce00186fe0bbfd5))
-   change log s3 bucket encryption type to S3_MANAGED ([28f1bb9](https://github.com/awslabs/visual-asset-management-system/commit/28f1bb9e44f1b17b8ef8af792a266c351ff0316e))
-   set arch to linux/amd64 for apple m1/m2 users ([d70d1b8](https://github.com/awslabs/visual-asset-management-system/commit/d70d1b85f3522965384cf0acd9cb300cf0667405))

### Chores

-   **release:** 1.0.0 ([ae61d15](https://github.com/awslabs/visual-asset-management-system/commit/ae61d152ba9ea84dba58d12a682f66db895d0b08))

## 1.0.0 (2022-11-09)

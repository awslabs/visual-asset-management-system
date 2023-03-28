# Changelog

All notable changes to this project will be documented in this file. See [standard-version](https://github.com/conventional-changelog/standard-version) for commit guidelines.

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

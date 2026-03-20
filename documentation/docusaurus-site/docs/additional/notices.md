# Notices

This page contains important legal notices, disclaimers, and licensing information for the Visual Asset Management System (VAMS).

---

## Non-Production Disclaimer

:::warning[Non-Production Grade]
Visual Asset Management System (VAMS) is a solution that is near-production-grade at its default configuration. Consult with your organizational security team prior to production use. You are responsible for testing, securing, and optimizing the solution as appropriate for production-grade use based on your specific quality control practices and standards.
:::


Deploying VAMS may incur AWS charges for creating or using AWS chargeable resources, such as running Amazon EC2 instances, using Amazon S3 storage, Amazon DynamoDB tables, Amazon OpenSearch domains, and other services. You are responsible for the cost of AWS services used while running this solution.

---

## Content Security Legal Disclaimer

The sample code, software libraries, command line tools, proofs of concept, templates, or other related technology (including any of the foregoing that are provided by our personnel) is provided to you as AWS Content, as defined in the [Online Customer Agreement](https://aws.amazon.com/agreement/), or the relevant written agreement between you and AWS (whichever applies). You should not use this AWS Content in your production accounts, or on production or other critical data.

You are responsible for testing, securing, and optimizing the AWS Content, such as sample code, as appropriate for production-grade use based on your specific quality control practices and standards.

---

## Third-Party Software Notice

VAMS includes third-party software subject to their respective licenses. This software is provided under the terms and conditions set forth by the third-party developers.

### Third-Party Software Warning

:::info[Third-Party Software]
This solution allows you to interact with third-party software libraries and generative AI (GAI) models from third-party providers. Your use of the software libraries and third-party GAI models is governed by the terms provided to you by the third-party software library and GAI model providers when you acquired your license to use them (for example, their terms of service, license agreement, acceptable use policy, and privacy policy).
:::


You are responsible for ensuring that your use of the third-party software libraries and GAI models comply with the terms governing them, and any laws, rules, regulations, policies, or standards that apply to you.

You are also responsible for making your own independent assessment of the third-party software libraries and GAI models that you use, including their outputs and how third-party software library GAI model providers use any data that might be transmitted to them based on your deployment configuration. AWS does not make any representations, warranties, or guarantees regarding the third-party software libraries and GAI models, which are "Third-Party Content" under your agreement with AWS.

### Key Third-Party Dependencies

The complete list of third-party dependencies and their licenses is maintained in the project's `NOTICE.md` file. Key categories include:

| Category | Notable Libraries | Licenses |
|---|---|---|
| Web application | React, Vite, Cloudscape Design System, Amplify | MIT, Apache-2.0 |
| 3D visualization | Three.js, BabylonJS, CesiumJS, Potree, PlayCanvas, Needle Engine | MIT, Apache-2.0, BSD-2-Clause |
| Backend | Pydantic, Casbin, boto3, aws-lambda-powertools | MIT, Apache-2.0, MIT-0 |
| Infrastructure | AWS CDK, cdk-nag, constructs | Apache-2.0 |
| CLI | Click, requests, boto3, pycognito | BSD-3-Clause, Apache-2.0 |

### Optional LGPL-Licensed Components

Certain optional components use LGPL-2.1 licensed libraries:

- **OpenCascade.js** -- Dynamically loaded for CAD format support in the Three.js Viewer (STEP, IGES, BREP). Disabled by default and loaded on-demand from a CDN only when explicitly enabled.
- **CadQuery** -- Used in the CAD/Mesh Metadata Extraction pipeline and the 3D Preview Thumbnail pipeline for STEP/STP file tessellation.

:::note
LGPL-2.1 license terms apply only when these optional features are enabled. Review the license requirements with your legal team before enabling CAD-related features.
:::


### Commercial Licensed Components

Some viewer plugins and processing pipelines require separate commercial licenses:

| Component | Vendor | License |
|---|---|---|
| VNTANA 3D Model Viewer | [VNTANA](https://www.vntana.com/) | Commercial |
| VNTANA 3D Optimization Engine | VNTANA (AWS Marketplace) | Commercial EULA |
| Veerum 3D Viewer | [Veerum](https://veerum.com/) | Commercial |
| RapidPipeline 3D Processor | [RapidPipeline](https://rapidpipeline.com/) (AWS Marketplace) | Commercial |

### NVIDIA License Notice

The Isaac Lab Training pipeline uses NVIDIA Isaac Sim container images, which are subject to the [NVIDIA Software License Agreement](https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license). Users must accept the NVIDIA EULA by setting `acceptNvidiaEula: true` in the deployment configuration to use this pipeline.

---

## Operational Metrics Collection

To measure the performance of this solution and to help improve and develop AWS Content, AWS may collect and use anonymous operational metrics related to your use of this AWS Content. AWS will not access your content, as defined in the [Online Customer Agreement](https://aws.amazon.com/agreement/). Data collection is subject to the [AWS Privacy Policy](https://aws.amazon.com/privacy/).

You may opt out of operational metrics collection by removing the tag(s) starting with `uksb-` or `SO` from the description(s) in any AWS CloudFormation templates or CDK TemplateOptions.

---

## License Information

VAMS is licensed under the Apache License, Version 2.0. You may obtain a copy of the License at:

[https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

---

## Contributing

Contributions to VAMS are welcome. See the project's `CONTRIBUTING.md` file for guidelines on how to contribute, including code style requirements, testing expectations, and the pull request process.

---

## Security

### Shared Responsibility Model

When you build systems on AWS infrastructure, security responsibilities are shared between you and AWS. This [shared responsibility](https://aws.amazon.com/compliance/shared-responsibility-model/) model reduces your operational burden because AWS operates, manages, and controls the components including the host operating system, virtualization layer, and physical security of the facilities in which the services operate.

VAMS is provided under this shared responsibility model. Any customization for customer use must go through a security review to confirm that modifications do not introduce new vulnerabilities. Any team implementing VAMS takes on the responsibility of ensuring their implementation has gone through a proper security review.

### Security Recommendations

1. Run `npm audit` in the `web/` directory prior to deploying the frontend to ensure all packages are up to date.
2. When deploying to an AWS account, create an AWS IAM role for deployment that limits access to the least privilege necessary.
3. Run AWS CDK bootstrap with the least-privileged AWS IAM role needed to deploy CDK and VAMS environment components.
4. Review authentication token timeouts (defaulting to 1 hour) with your organization's security team.
5. Consider configuring IP range restrictions using `authorizerOptions.allowedIpRanges` in the deployment configuration.

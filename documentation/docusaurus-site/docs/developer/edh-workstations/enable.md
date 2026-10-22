---
sidebar_label: Enable
---

# Enable the EDH Workstations Add-on

The EDH Workstations add-on connects VAMS to an Engineering Development Hub (EDH) cluster. VAMS users launch, connect to, stop and delete GPU workstations on the cluster from the VAMS web interface and `vamscli`, and administrators register the images users launch, review sessions and set the workstation lease policy. VAMS does not install EDH: the add-on calls the REST API of an EDH cluster that you deploy first, and each workstation opens in the user's browser as an Amazon DCV session served by that cluster. This page walks through deploying an EDH cluster for VAMS, the EDH settings the add-on depends on, and enabling the add-on in VAMS.

![EDH Workstations add-on overview](/img/addon_edhWorkstations_overview.png)

The diagram shows the add-on as a whole and separates what VAMS already provides, what the add-on deploys and the EDH cluster you deploy and operate. The add-on's AWS Lambda functions in the VAMS VPC call the EDH REST API, EDH deploys each desktop as an Amazon EC2 instance in its own AWS CloudFormation stack, and the browser reaches the desktop through the cluster's Amazon DCV entry point. The [EDH Workstations Add-on](overview.md#how-vams-integrates-with-edh) developer guide describes each part in detail.

[Choose a network layout](#choose-a-network-layout) for VAMS and EDH before Step 1. With a stock EDH cluster, enabling the add-on then takes the following steps:

1. [Get EDH](#step-1-get-edh) at the supported revision.
2. [Deploy the EDH cluster](#step-2-deploy-the-edh-cluster) with the installer settings VAMS depends on.
3. [Configure EDH for VAMS](#step-3-configure-edh-for-vams): store the root key and the CA bundle in secrets. In the shared-VPC layout, the other settings need no action on a stock cluster.
4. [Connect the networks](#step-4-connect-the-networks). For a VAMS deployment you are about to make, first complete Steps 1 to 6 of [Deploy the solution](../../deployment/deploy-the-solution.md), which clone the repository, build the frontend, install and bootstrap the AWS CDK and create `infra/config/config.json`.
5. [Configure VAMS](#step-5-configure-vams) with the add-on's fields.
6. [Deploy VAMS](#step-6-deploy-vams).
7. [Grant access](#step-7-grant-access) with the EDH roles.
8. [Verify the add-on](#step-8-verify-the-add-on).

## Before you begin

You need the following:

-   A VAMS deployment, or one you are about to make, that meets the [VAMS prerequisites](../../deployment/prerequisites.md), in the `aws` or `aws-us-gov` partition. The add-on is not available in the AWS European Sovereign Cloud or in any other partition: configuration validation rejects it there, so keep `app.addons.useEdhWorkstations.enabled` set to `false` (see [Restricted-partition constraints](../../deployment/configuration-reference.md#restricted-partition-constraints)).
-   A VAMS VPC, with `app.useGlobalVpc.enabled` set to `true`. Configuration validation lists `addons.useEdhWorkstations` among the features that require the VPC when it is off. The shared-VPC layout of [Choose a network layout](#choose-a-network-layout), which needs the least EDH configuration, has VAMS import the EDH cluster's VPC.
-   An EDH cluster at the [supported revision](#supported-edh-version), in the same AWS account as VAMS and preferably the same AWS Region, whose management Application Load Balancer the VAMS VPC can reach. [Step 2](#step-2-deploy-the-edh-cluster) deploys one and [Step 4](#step-4-connect-the-networks) connects the networks.
-   A network path from your users' browsers to the cluster's Amazon DCV entry point. VAMS never proxies desktop traffic: the browser opens the Amazon DCV connection URL that EDH returns, which points at the cluster (see [Browser access to Amazon DCV](#browser-access-to-amazon-dcv)).
-   A Linux or macOS host with Python 3.13 to run the EDH installer on (see [Installer host](#installer-host)).
-   An Amazon S3 bucket for the installer's `--bucket` option, and an Amazon EC2 key pair in the cluster's Region for its `--ssh-keypair` option.
-   Permissions to install EDH (the [EDH installation guide](https://awslabs.github.io/engineering-development-hub-documentation/documentation/01-install-edh-cluster/) describes the scoped installer policies in `installer/policy_permissions/`), to deploy VAMS (see [IAM permissions](../../deployment/prerequisites.md#iam-permissions)), and to use AWS Systems Manager Run Command, AWS Secrets Manager and AWS Certificate Manager for [Step 3](#step-3-configure-edh-for-vams).
-   Amazon EC2 On-Demand Instance vCPU quotas in the cluster's Region for the desktop instance types. The default quota for **Running On-Demand G and VT instances** is 0 vCPUs, so request an increase in Service Quotas before users launch GPU desktops.

:::note
The shared-VPC layout of [Choose a network layout](#choose-a-network-layout), session bootstrap, containers and AMI recipes need the cluster in the VAMS Region. Whatever the cluster's Region, the root-key and CA bundle secrets of [Step 3](#step-3-configure-edh-for-vams) are created in the Region VAMS deploys to, where the add-on reads them.
:::

The EDH cluster's own infrastructure, such as its controller, Amazon DCV Connection Gateway and Session Manager broker instances, load balancers, NAT gateway, Amazon EFS file systems and Amazon OpenSearch Service domain, bills while the cluster is deployed, whether or not any desktop runs. [EDH Workstations Add-on Costs](resources.md#edh-workstations-add-on-costs) lists the charges the add-on adds.

## Choose a network layout

The add-on's AWS Lambda functions call the EDH Application Load Balancer from the VAMS VPC, so the network layout decides installer settings in [Step 2](#step-2-deploy-the-edh-cluster) as well as the VPC settings of [Step 4](#step-4-connect-the-networks). Choose it before you install EDH:

| Layout        | Fits                                                                                                      | Settings it implies                                                                                                                                                                                                                                                                                                                             |
| ------------- | --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Shared VPC    | A VAMS deployment you are about to make, or a running one without a VPC; it needs the least configuration | VAMS imports the EDH VPC and its subnets ([Step 4](#step-4-connect-the-networks)). Either `--deployment-mode` works.                                                                                                                                                                                                                            |
| Connected VPC | A VAMS deployment that keeps its own VPC, such as one that already runs in a VPC that VAMS created        | The VAMS VPC is connected to the EDH VPC over VPC peering or AWS Transit Gateway, with routes between the two, and the EDH `--vpc-cidr` does not overlap the VAMS VPC (`app.useGlobalVpc.vpcCidrRange` for a VPC that VAMS creates). The load balancer needs the ingress rule of [Load balancer security group](#load-balancer-security-group). |

In the connected-VPC layout, an internet-facing load balancer is reachable only when the VAMS isolated subnets route through a NAT gateway. The isolated subnets of a VPC that VAMS creates have no NAT route, so with such a VPC only an internal load balancer (`--deployment-mode private`) is reachable, which makes the Amazon DCV entry point internal as well (see [Browser access to Amazon DCV](#browser-access-to-amazon-dcv)).

## Step 1: Get EDH

EDH is open source under the Apache-2.0 license. Its source is in the [EDH repository](https://github.com/awslabs/engineering-development-hub) on GitHub, and its documentation is at [https://awslabs.github.io/engineering-development-hub-documentation/](https://awslabs.github.io/engineering-development-hub-documentation/). The [EDH installation guide](https://awslabs.github.io/engineering-development-hub-documentation/documentation/01-install-edh-cluster/) covers the installer's prerequisites, its IAM policies and the interactive installation; `./installer/edh_installer.sh --help` lists every installer option, and `--override` sets any key that `installer/default_config.yml` defines.

### Supported EDH version

The add-on is verified against **EDH 26.8.0 at commit [`e89c6537`](https://github.com/awslabs/engineering-development-hub/commit/e89c653772373be7dfe3ef82549f2032dc3ecb1b)** (2026-09-08) of the [EDH repository](https://github.com/awslabs/engineering-development-hub). Every EDH route the add-on calls, the record shapes it reads (software stacks, profiles, sessions, directory users, container catalog) and the installer settings on this page are those of that revision. A newer EDH release is not verified: EDH's REST API carries no version negotiation, so a renamed route or a changed field surfaces only at runtime. Install the cluster from that commit rather than from the repository head, and repeat [Step 8](#step-8-verify-the-add-on) before moving a cluster to a newer EDH:

```bash
git clone https://github.com/awslabs/engineering-development-hub.git
cd engineering-development-hub && git checkout e89c653772373be7dfe3ef82549f2032dc3ecb1b
```

## Step 2: Deploy the EDH cluster

Install the cluster with the EDH installer, `installer/edh_installer.sh` in the repository you checked out. The settings below are the ones a VAMS deployment depends on; everything else follows the EDH installation guide. Run the installer and the AWS CLI commands on this page with credentials for the AWS account VAMS deploys to, and with the cluster's Region set:

```bash
export AWS_PROFILE=<profile>
export AWS_DEFAULT_REGION=<cluster Region>
```

The installer's `--profile` option applies to its own API calls only, not to the AWS CDK deployment it runs, so set `AWS_PROFILE` instead.

### Installer host

The installer wrapper runs on Linux or macOS only (it rejects other platforms by `uname`) and needs Python 3.13. It uses the `python3` on the `PATH`, or the interpreter `SOCA_PYTHON` names, and with another version offers Python 3.13 through PyEnv, in prompts that no installer option answers. When the host has no `python3-venv` package, pre-activate a `virtualenv` built from Python 3.13 and set `SOCA_PYTHON_SKIP_VENV=true` so the wrapper reuses it; with an AWS CLI on the `PATH`, the wrapper skips its interactive `aws configure`. The [EDH installation guide](https://awslabs.github.io/engineering-development-hub-documentation/documentation/01-install-edh-cluster/) lists the other host requirements and describes AWS CloudShell as an installer host.

The wrapper's terminal UI needs a TTY even in silent mode. From a machine without a terminal, run it under `script -qefc "<command>" /dev/null` on Linux or `script -q /dev/null <command>` on macOS, either of which returns the installer's exit status.

### Installer settings for VAMS

| Flag or override                                                                                                                            | Value used with VAMS                  | Why                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--deployment-mode`                                                                                                                         | `public` (EDH's recommendation)       | Places the Application Load Balancer that serves the EDH web interface and REST API, and the Amazon DCV entry point, in public subnets as internet-facing load balancers. With `private` both are internal, and users reach the cluster over a VPN or AWS Direct Connect. [Choose a network layout](#choose-a-network-layout) describes how the add-on reaches the load balancer in each mode.                                                                                                                                                                                                                                                                                 |
| `--tls-certificate`                                                                                                                         | `auto`, or an ACM certificate ARN     | With `auto`, EDH reuses its default certificate in the Region or imports a self-signed one into AWS Certificate Manager (ACM), and describes it as not for production. That certificate's only name is `EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE`, so the load balancer host can never match it; [TLS trust](#tls-trust) sets VAMS up to verify it. Certificate verification in VAMS is always on: without that setup every EDH call fails verification, logged as `SSLError` in the Lambda logs (`self-signed certificate`, or `Hostname mismatch` when only the CA bundle is set), and the admin health probe reports `unreachable`.                                          |
| `--override Config.services.resources_mirroring.method,str,cloud-in-vpc-nat`                                                                | `cloud-in-vpc-nat`                    | The default `ask` opens a terminal question that has no flag; a silent install presets `cloud-in-vpc-nat`, `cloud-no-vpc` or `install-host`, or turns mirroring off with `--override Config.services.resources_mirroring.enabled,bool,false`, under which EDH nodes download their installation resources from the internet.                                                                                                                                                                                                                                                                                                                                                   |
| `--override Config.directoryservice.provider,str,openldap`                                                                                  | `openldap`                            | Optional; EDH's default directory is AWS Directory Service for Microsoft Active Directory. OpenLDAP on the controller installs faster and serves both the Linux and the Windows desktops VAMS launches; a Windows desktop then signs in with a local user whose password VAMS shows. `autoProvisionUsers` creates the VAMS users in either directory. With Active Directory, a Windows desktop signs in with the user's directory account, and `autoProvisionUsers` generates and discards that account's password, so an EDH administrator resets it (**Users Management** → **Reset user password** in the EDH web interface) before the user signs in to a Windows desktop. |
| `--client-ip`                                                                                                                               | The address ranges users connect from | One CIDR range per flag, repeated as needed, and at most 16. EDH builds a managed prefix list from them, through which the load balancer and the Amazon DCV entry point admit browsers; see [Browser access to Amazon DCV](#browser-access-to-amazon-dcv). Include the address the installer host connects from and those of the EDH administrators: once the stack is created, the installer requests the EDH web interface from the installer host, and exits with the error `Unable to connect to the EDH endpoint URL` when it cannot reach it, although the stack is complete by then.                                                                                    |
| `Config.network.use_vpc_endpoints`                                                                                                          | Left at the default `false`           | With `true`, EDH creates interface endpoints with private DNS and an Amazon S3 gateway endpoint for services that VAMS also creates endpoints for in the shared-VPC layout, and the VAMS deployment then fails with a conflicting private DNS name or an existing Amazon S3 route. Where EDH's endpoints are required, set `app.useGlobalVpc.addVpcEndpoints` to `false` and add the endpoints VAMS needs that EDH does not create (see [VPC Interface Endpoints](../../deployment/configuration-reference.md#vpc-interface-endpoints) and [Common deployment errors](../../deployment/deploy-the-solution.md#common-deployment-errors)).                                      |
| `--skip-config-message`                                                                                                                     | Set                                   | Skips the question that asks to confirm the settings in `installer/default_config.yml`, which a silent install cannot answer.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `--vpc-cidr`, `--name`, `--region`, `--bucket`, `--ssh-keypair`, `--base-os`, `--fs-apps-provider`, `--fs-data-provider`, `--email-address` | deployment specific                   | The cluster name is `edh-` followed by letters and digits only, at most 15 characters in total; the installer adds the `edh-` prefix when it is missing. [Choose a network layout](#choose-a-network-layout) gives the rule for `--vpc-cidr`.                                                                                                                                                                                                                                                                                                                                                                                                                                  |

An installation with these settings, run from the root of the checked-out repository, looks like the following; the values are examples:

```bash
./installer/edh_installer.sh \
    --region us-east-1 \
    --name edh-demo \
    --bucket amzn-s3-demo-bucket \
    --ssh-keypair my-key-pair \
    --vpc-cidr 10.0.0.0/16 \
    --base-os amazonlinux2023 \
    --fs-apps-provider efs \
    --fs-data-provider efs \
    --client-ip 203.0.113.0/24 \
    --email-address admin@example.com \
    --deployment-mode public \
    --tls-certificate auto \
    --override Config.services.resources_mirroring.method,str,cloud-in-vpc-nat \
    --override Config.directoryservice.provider,str,openldap \
    --skip-config-message
```

Once the AWS CloudFormation stack reaches `CREATE_COMPLETE`, the controller keeps configuring EDH and starting its web interface for up to 30 minutes; the installer waits for that and ends by reporting that the EDH environment is ready. The installer exits with a non-zero status when one of its checks or that wait fails, but with `0` when the AWS CloudFormation deployment itself fails, so an unattended install also checks that the stack status reads `CREATE_COMPLETE`:

```bash
CLUSTER=edh-demo
aws cloudformation describe-stacks --stack-name "${CLUSTER}" \
    --query "Stacks[0].StackStatus" --output text
```

With `--deployment-mode private`, an installer host outside the cluster VPC cannot reach the internal load balancer: once the stack is created, the installer exits with `Unable to connect to the EDH endpoint URL` and does not wait for the controller, and the cluster does not need to be removed. Confirm that the command above prints `CREATE_COMPLETE`, then check the web interface from inside the cluster VPC: with Run Command on the controller, as in [Root key](#root-key), run `curl -sk -o /dev/null -w '%{http_code}' <WebUserInterface URL>` until it prints `200`.

The installer refuses a cluster name that an AWS CloudFormation stack in the Region already has, including a failed one. Before you retry under the same name, remove the failed cluster with `installer/uninstall_edh.sh --cluster-name <cluster name> --region <Region> --s3-bucket <bucket>`, which also lifts the termination protection EDH enables by default.

EDH installs its high-scale Amazon DCV layout by default (`Config.dcv.high_scale` is `true` in the installer's `default_config.yml`). In that layout a Network Load Balancer in front of the Amazon DCV Connection Gateway is the DCV entry point, and the EDH controller creates each desktop's session through the Amazon DCV Session Manager broker, which issues the connection token; in the standard layout the Application Load Balancer is the DCV entry point. VAMS works with either layout: it reads the connection URL from EDH and hands it to the browser unchanged. [How EDH deploys a desktop](overview.md#how-edh-deploys-a-desktop) lists the services the cluster uses for each desktop.

### What the cluster provides

The cluster's AWS CloudFormation stack, named after the cluster, and its AWS Systems Manager Parameter Store entries under `/edh/<cluster name>/configuration/` hold the values the rest of this page uses:

| Value                 | Where EDH publishes it                                                                                                                                            | Used for                                                                                         |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Management endpoint   | The stack output `WebUserInterface`, the Application Load Balancer URL                                                                                            | `edhApiEndpoint`, as its scheme and host (port 443); the output's trailing `/` may stay          |
| Cluster name          | The stack name, including the `edh-` prefix                                                                                                                       | `edhClusterId`                                                                                   |
| Project and profile   | EDH seeds virtual desktop profile `1` ("default") and project `default` with `allowed_users: "*"` and every default software stack linked                         | `vdi.defaultProject` and `vdi.virtualDesktopProfileId`, without further EDH configuration        |
| Controller instance   | The `ControllerInstanceId` parameter                                                                                                                              | Reading the root key ([Root key](#root-key))                                                     |
| VPC and subnets       | The `VpcId`, `PrivateSubnets` and `PublicSubnets` parameters; the subnet parameters hold a bracketed list of quoted subnet ids                                    | Sharing the EDH VPC ([Step 4](#step-4-connect-the-networks))                                     |
| Desktop instance role | The `VdiNodeIAMRoleArn` parameter: the stack's `VdiNodeRole`, or the compute-node role on a cluster installed with existing IAM roles, which has no `VdiNodeRole` | `sessionBootstrap.workstationInstanceRoleArns` ([Desktop instance role](#desktop-instance-role)) |

```bash
CLUSTER=edh-demo
aws cloudformation describe-stacks --stack-name "${CLUSTER}" \
    --query "Stacks[0].Outputs[?OutputKey=='WebUserInterface'].OutputValue" --output text
aws ssm get-parameter --name "/edh/${CLUSTER}/configuration/VdiNodeIAMRoleArn" \
    --query Parameter.Value --output text
```

Git Bash on Windows rewrites an argument that begins with `/`, such as a Parameter Store name, into a Windows path; prefix such commands with `MSYS_NO_PATHCONV=1` (see [File Commands](../../cli/commands/files.md)).

The `edhadmin` password is printed by the installer and kept in the AWS Secrets Manager secret `/edh/<cluster name>/EDHAdminUser`; treat the installer log as sensitive.

## Step 3: Configure EDH for VAMS

The add-on depends on the following EDH settings. Several of them are satisfied by EDH's defaults:

| Setting                                                                     | EDH default                                                                | Action for VAMS                                                                                    |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| [Root key](#root-key)                                                       | Regenerated on each start of the EDH web interface, kept on the controller | Copy it into a secret, and again after each web interface restart or controller reboot             |
| [TLS trust](#tls-trust)                                                     | The certificate `--tls-certificate auto` imports                           | Store the certificate as a CA bundle and set the TLS server name, or use a certificate of your own |
| [Project and virtual desktop profile](#project-and-virtual-desktop-profile) | Project `default` and profile `1`                                          | None                                                                                               |
| [Feature-flag allow lists](#feature-flag-allow-lists)                       | Empty, which admits every caller                                           | None while they stay empty                                                                         |
| [Desktop schedule updates](#desktop-schedule-updates)                       | `DCV_ALLOW_DEFAULT_SCHEDULE_UPDATE` is `True`                              | None; keep the default                                                                             |
| [Load balancer security group](#load-balancer-security-group)               | Admits the cluster VPC and the addresses of EDH's NAT gateways             | None in the shared-VPC layout; one ingress rule when VAMS runs in a connected VPC                  |
| [Browser access to Amazon DCV](#browser-access-to-amazon-dcv)               | The `--client-ip` ranges                                                   | Admit every range your users connect from                                                          |
| [Desktop instance role](#desktop-instance-role)                             | `VdiNodeRole`, which holds `ec2:DescribeTags` in the high-scale layout     | Session bootstrap only: name the role in VAMS, and add `ec2:DescribeTags` where the role lacks it  |
| [AWS Batch queue and image pulls](#aws-batch-queue-and-image-pulls)         | An AWS Fargate queue                                                       | Containers only: an Amazon EC2 job queue tagged for the cluster, and image pull access             |
| [GPU desktops and the NVIDIA driver](#gpu-desktops-and-the-nvidia-driver)   | GRID driver pin `grid-19.4` (driver 580)                                   | Isaac Sim desktops only: pin an R595 GRID driver that the cluster's driver mirror holds            |

### Root key

The add-on authenticates to EDH with the cluster root key, which can act as any EDH user. EDH regenerates the key each time its web interface starts, including after every controller reboot, and writes it to `/opt/edh/<cluster name>/cluster_manager/web_interface/keys/admin_api_key.txt` on the controller. Once the installer reports that the EDH environment is ready, or the `WebUserInterface` URL answers HTTP 200 (from inside the cluster VPC for an internal load balancer; see [Step 2](#step-2-deploy-the-edh-cluster)), read the key with AWS Systems Manager Run Command (`AWS-RunShellScript`) and store it in an operator-managed AWS Secrets Manager secret as `{"rootKey": "..."}`:

```bash
CLUSTER=edh-demo
CONTROLLER_ID=$(aws ssm get-parameter \
    --name "/edh/${CLUSTER}/configuration/ControllerInstanceId" \
    --query Parameter.Value --output text)
COMMAND_ID=$(aws ssm send-command \
    --instance-ids "${CONTROLLER_ID}" \
    --document-name AWS-RunShellScript \
    --parameters "commands=[\"cat /opt/edh/${CLUSTER}/cluster_manager/web_interface/keys/admin_api_key.txt\"]" \
    --query Command.CommandId --output text)
aws ssm wait command-executed --command-id "${COMMAND_ID}" --instance-id "${CONTROLLER_ID}"
ROOT_KEY=$(aws ssm get-command-invocation \
    --command-id "${COMMAND_ID}" --instance-id "${CONTROLLER_ID}" \
    --query StandardOutputContent --output text | tr -d '[:space:]')
aws secretsmanager create-secret \
    --name edh-root-key \
    --secret-string "{\"rootKey\":\"${ROOT_KEY}\"}"
unset ROOT_KEY
```

Run Command keeps the output with the command invocation, so treat read access to the account's Run Command history like read access to the secret. Note the complete ARN the last command returns, including the 6-character suffix, for `edhAdminApiKeySecretArn`. When the cluster runs in another Region than VAMS, add `--region <VAMS Region>` to each `aws secretsmanager` command in this step. Encrypt the secret with the AWS managed key `aws/secretsmanager`, which `create-secret` uses by default, or with a customer managed key whose key policy grants `kms:Decrypt` to the add-on's Lambda execution roles; see [EDH root key](overview.md#edh-root-key).

EDH does not publish a regenerated key, so each time the key changes, write the current key into the secret again: repeat the commands above with `aws secretsmanager put-secret-value --secret-id edh-root-key` in place of `aws secretsmanager create-secret --name edh-root-key`. Until then every EDH call from VAMS fails and the admin health probe reports `credentialRejected`; see [Root key rotation](overview.md#root-key-rotation).

### TLS trust

The add-on's Lambda functions verify the certificate the EDH Application Load Balancer presents, and there is no setting that turns the verification off. For the certificate `--tls-certificate auto` imports, export it from ACM, store the PEM in a second secret, and set `edhApiTlsServerName` to `EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE`:

```bash
CERT_ARN=$(aws acm list-certificates \
    --query "CertificateSummaryList[?DomainName=='EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE'].CertificateArn" \
    --output text)
aws acm get-certificate --certificate-arn "${CERT_ARN}" \
    --query Certificate --output text > edh-alb-certificate.pem
aws secretsmanager create-secret \
    --name edh-alb-ca-bundle \
    --secret-string file://edh-alb-certificate.pem
```

Note the complete ARN the last command returns for `edhApiCaBundleSecretArn`.

With a certificate of your own (`--tls-certificate` with an ACM certificate ARN), set `edhApiEndpoint` to a name the certificate carries that resolves to the load balancer, and leave `edhApiTlsServerName` empty. EDH creates no DNS record for that name, so create one the add-on's functions can resolve, such as an Amazon Route 53 alias record to the load balancer in a public hosted zone, or in a private hosted zone associated with the VPC the functions run in (the EDH VPC in the shared-VPC layout, the VAMS VPC in the connected-VPC layout). Such a certificate needs no CA bundle when it chains to a public root; one issued by a private CA needs its CA bundle in `edhApiCaBundleSecretArn`. See [TLS trust](overview.md#tls-trust).

### Project and virtual desktop profile

Every desktop VAMS launches is created in the EDH project named by `vdi.defaultProject`. `vdi.virtualDesktopProfileId` names the virtual desktop profile whose allowed instance types the launch wizard offers; each desktop launches under the profile its image (EDH software stack) is bound to. The project `default` and the profile `1` that EDH seeds satisfy both, so a stock cluster needs no change here.

A different project works when it exists, is linked to every desktop software stack users may launch — VAMS attaches the stacks it registers to that project itself, so the link is one to make for a stack created outside VAMS — and admits the EDH uids VAMS derives for its users (see [Identity mapping](overview.md#identity-mapping)). EDH creates a desktop only inside those rules. A launch outside them ends `ORPHANED` with `IMAGE_UNAVAILABLE`, because EDH answers that the software stack does not exist or is inactive, or that the user lacks permission for it, and the Workstations page reports it as "The chosen image is no longer available in EDH."

### Feature-flag allow lists

EDH evaluates its feature-flag allow lists against the `X-EDH-USER` header of every call except the desktop session-state read, the directory user probe and the project listing and update, which carry no feature flag. The add-on's calls fall under the `VIRTUAL_DESKTOPS` flag (desktops and their software stacks), `TARGET_NODES` (simulation images), `USERS_GROUPS_MANAGEMENT` (directory user creation) and, with containers, `CONTAINERS_MANAGEMENT_BATCH` and `CONTAINERS_MANAGEMENT_EKS`. Each of these flags is enabled by default with empty `allowed_users` and `allowed_groups` lists, and a feature whose `allowed_users` and `allowed_groups` are both empty admits every caller its deny lists do not name, so EDH's defaults need no entries for VAMS.

When you set an allow list on one of these flags, include in it the uids of the VAMS users, the uids of the VAMS administrators — the **Sessions** and **Capacity** views list desktops under the administrator's own uid, and image registration, deletion and project attachment are sent under it — and the service actor `vamsservice`, under which the add-on sends its own calls, such as the reconciler's listing and the catalog reads (see [Identity mapping](overview.md#identity-mapping)). Keep the flags enabled, and keep those uids off their deny lists. EDH reads these overrides from the Parameter Store parameters `/edh/<cluster name>/configuration/FeatureFlags/<flag>/AllowedUsers`, `AllowedGroups`, `DeniedUsers` and `DeniedGroups`, each a JSON array such as `["vamsservice"]`, and `.../Enabled`. The `FeatureFlags/VirtualDesktops/...`, `FeatureFlags/TargetNodes/...` and similar parameters a cluster already holds under that path are other EDH settings.

### Desktop schedule updates

VAMS sets every desktop it launches to a schedule under which EDH never starts it (see [Desktop schedule](overview.md#desktop-schedule)), sending EDH's schedule update under the owner's uid. EDH accepts that update only while its `DCV_ALLOW_DEFAULT_SCHEDULE_UPDATE` setting keeps its default, `True`. With the setting `False`, EDH refuses every update and the desktops keep EDH's default schedule, under which EDH starts a stopped desktop again on weekdays between 08:00 and 19:00 in EDH's configured time zone (its `TIMEZONE` setting, UTC by default). Both settings are constants in `/opt/edh/<cluster name>/cluster_manager/web_interface/config.py` on the controller, not Parameter Store entries.

### Load balancer security group

The add-on's Lambda functions run in the VPC's isolated subnets behind their own security group, which permits outbound TCP 443 — plus the port `edhApiEndpoint` names when it is not 443 — and nothing else (see [EDH add-on Lambda security group](resources.md#edh-add-on-lambda-security-group)). EDH's load balancer security group admits all traffic from the cluster VPC and HTTPS from the addresses of EDH's NAT gateways. In the shared-VPC layout of [Choose a network layout](#choose-a-network-layout) it therefore needs no new rule: the functions run in EDH's private subnets, so an internal load balancer receives their calls from inside its VPC, and their calls to an internet-facing one leave through EDH's NAT gateway.

When VAMS runs in a connected VPC, the load balancer's security group needs one ingress rule:

-   For an internal load balancer, admit the add-on's Lambda security group as a source across a VPC peering connection in the same Region, or across a transit gateway with security group referencing turned on for the gateway and its VPC attachments. Otherwise, such as across Regions, admit the address ranges of the VAMS isolated subnets, with `--cidr <range>` in place of `--source-group` in the command below. The first VAMS deployment, in [Step 6](#step-6-deploy-vams), creates the group and publishes its id as the `EdhAddonLambdaSecurityGroupId` output of the add-on's nested stack (see [Add-on stack outputs](resources.md#edh-workstations-add-on-stack-outputs)) and in Parameter Store, from which the command below reads it.
-   For an internet-facing load balancer, admit the address of the NAT gateway that the VAMS isolated subnets route through (see [Choose a network layout](#choose-a-network-layout)).

The following commands look up the security group of the EDH load balancer, which EDH names `<cluster name>-viewer`, and add the rule for the Lambda security group. In the parameter name, `<name>` and `<baseStackName>` are the `name` and `app.baseStackName` values of `config.json` and `<Region>` is the VAMS Region; `<port>` is 443, or the port `edhApiEndpoint` names:

```bash
EDH_ALB_SG=$(aws elbv2 describe-load-balancers --region <cluster Region> \
    --names "<cluster name>-viewer" \
    --query "LoadBalancers[0].SecurityGroups[0]" --output text)
LAMBDA_SG=$(aws ssm get-parameter --region <Region> \
    --name "/<name>-<baseStackName>-<Region>/resourceNames/securityGroups/edhAddonLambda" \
    --query Parameter.Value --output text)
aws ec2 authorize-security-group-ingress --region <cluster Region> \
    --group-id "${EDH_ALB_SG}" \
    --protocol tcp --port <port> \
    --source-group "${LAMBDA_SG}"
```

A rule in the same VPC that names the Lambda security group blocks the deletion of that group, and one across a peering connection is left stale when the group is deleted, so revoke it before removing the add-on (see [Disable or remove the add-on](#disable-or-remove-the-add-on)).

### Browser access to Amazon DCV

Users' browsers open the Amazon DCV connection URL that EDH returns. It points at the cluster's DCV entry point: the Network Load Balancer in front of the Amazon DCV Connection Gateway in the high-scale layout, or the Application Load Balancer in the standard layout. Both are internet-facing or internal according to `--deployment-mode`, and an internal entry point is reachable only from networks connected to the cluster VPC, such as over a VPN or AWS Direct Connect.

By default, EDH builds the managed prefix list `<cluster name>-Clients-IP_V4` from the `--client-ip` ranges. The load balancer's security group admits that list on TCP 80 and 443, and in the high-scale layout the DCV entry point's security group admits it on TCP and UDP 443. To admit more ranges after the install, add them to that prefix list, which holds at most 16 entries unless you raise its maximum; a prefix list of your own, named with `--prefix-list-id` at install, is admitted on the same ports.

In the high-scale layout the Network Load Balancer passes TLS through to the Amazon DCV Connection Gateway, which presents a self-signed certificate that EDH generates for the DCV entry point host when it sets up the gateway instance, whatever `--tls-certificate` names. On the first connection the browser therefore warns that the connection is not private, and the desktop opens once that certificate is accepted; a browser whose policy does not allow accepting it does not open the desktop. A gateway instance that EDH replaces generates its own certificate, so the warning appears again. In the standard layout the Application Load Balancer presents the `--tls-certificate` certificate, and the certificate that `auto` imports causes a warning as well.

### Desktop instance role

This setting applies only with `sessionBootstrap.enabled`. The resource policy VAMS puts on a session's secret allows the desktop instance role to read it from the Amazon EC2 instance of that session's desktop, and the desktop's sign-in, which an XDG autostart entry starts when the user's desktop session starts, reads the desktop's own `edh:JobName` tag, so the role needs `ec2:DescribeTags`. Leave `secretsmanager:GetSecretValue` on the session secrets out of the role's identity policy, which would let every desktop with the role read every session's secret (see [Limits](overview.md#limits)). In EDH's default high-scale layout, EDH attaches its `<cluster name>-DCVCloudWatchAgent` managed policy, which grants `ec2:DescribeTags`, to the `VdiNodeRole` it creates. In the standard layout, or on a cluster installed with existing IAM roles, add the permission to the role yourself. Name the role's ARN, the `VdiNodeIAMRoleArn` parameter, in `sessionBootstrap.workstationInstanceRoleArns`; see [Session credentials](overview.md#session-credentials).

### AWS Batch queue and image pulls

This setting applies only with `containers.enabled`. EDH lists the AWS Batch job queues and job definitions that carry the tag `edh:visibility:<cluster name>` with the value `true`. EDH's default AWS Batch environment is an AWS Fargate compute environment behind the queue `<cluster name>-default-queue`, while VAMS registers only Amazon EC2 job definitions, so container jobs need a job queue backed by an Amazon EC2 compute environment, tagged `edh:visibility:<cluster name>` with the value `true` and named with the `edh-` prefix, such as `<cluster name>-ec2-queue`. EDH submits jobs under its controller role, whose policy allows `batch:SubmitJob` only on job queues and job definitions whose names begin with `edh-`, so each `containers.jobDefinitions[].name` begins with `edh-` as well, and CDK synthesis rejects a name that does not. A job queue named otherwise still appears on the **Simulations** tab, but EDH refuses every submission to it.

### GPU desktops and the NVIDIA driver

This setting applies to desktops on NVIDIA GPU instance types, and is required for the shipped `isaacsim` recipe. VAMS images carry no GPU driver: EDH installs it at each desktop's first boot. For a Linux desktop on an instance family that supports both driver kinds, which covers every type the `isaacsim` recipe offers, EDH installs the NVIDIA GRID driver from the folder its `grid_drivers/version` setting names, under the `grid_drivers/s3_bucket_path` location. Both are Parameter Store parameters:

-   `/edh/<cluster name>/system/gpu/gpu_settings/nvidia/linux/grid_drivers/version`: a folder name such as `grid-19.4`, or `latest`. EDH's default is `grid-19.4`, which holds driver 580.
-   `/edh/<cluster name>/system/gpu/gpu_settings/nvidia/linux/grid_drivers/s3_bucket_path`: AWS's public `s3://ec2-linux-nvidia-drivers` bucket, or, when EDH's resource mirroring is on (its default), the cluster's own copy under `s3://<cluster bucket>/<cluster name>/resources_mirroring/ec2-linux-nvidia-drivers`.

Isaac Sim 6.2, which the `isaacsim` recipe installs, needs an R595 driver: NVIDIA tests Isaac Sim 6.0.1 to 6.2.0 on driver 595, and the recipe's launcher refuses an older one (`nvidiaDriverMinMajor` is `595`). In AWS's bucket the R595 GRID drivers are `grid-20.0` (595.58.03), `grid-20.1` (595.71.05) and `grid-20.2` (595.91.07). A mirrored cluster holds only the folders it copied at install, typically the default pin, `grid-17.1` for Amazon Linux 2 and `latest`, where `latest` is AWS's newest driver on the day the cluster was installed. List what the cluster holds before you pin:

```bash
CLUSTER=edh-demo
DRIVERS=$(aws ssm get-parameter \
    --name "/edh/${CLUSTER}/system/gpu/gpu_settings/nvidia/linux/grid_drivers/s3_bucket_path" \
    --query Parameter.Value --output text)
aws s3 ls "${DRIVERS}/" --recursive
```

Then set the pin to a folder that holds an R595 driver (`NVIDIA-Linux-x86_64-595.*-grid-aws.run`), either `latest` when it holds one, or a `grid-20.*` folder. A mirrored cluster has no `grid-20.*` folder until you copy one in, for example `aws s3 cp --recursive s3://ec2-linux-nvidia-drivers/grid-20.2/ "${DRIVERS}/grid-20.2/"`. A named folder keeps the driver fixed, while `latest` changes whenever the mirror is refreshed.

```bash
aws ssm put-parameter --overwrite \
    --name "/edh/${CLUSTER}/system/gpu/gpu_settings/nvidia/linux/grid_drivers/version" \
    --value grid-20.2
```

EDH's configuration cache picks up a change under `/edh/<cluster name>/` within seconds while its SSM-to-cache sync is on, which EDH's default installation enables; no restart is needed. The pin applies to desktops created afterwards: a desktop keeps the driver its first boot installed. To return to the default, set the parameter back to `grid-19.4`. EDH does not fall back to another folder when the pinned driver fails to install, unless `grid_drivers/allow_latest_fallback` is `true`, so a desktop whose driver did not install shows no NVIDIA driver until the pin is fixed and a new desktop is launched.

Two account settings decide whether a GPU desktop can launch at all:

-   **Service quota.** G instances count against the Amazon EC2 quota **Running On-Demand G and VT instances** (`L-DB2E81BA`), in vCPUs, in the cluster's Region. A `g5.16xlarge` desktop alone uses 64. Check it, and request an increase, in the Service Quotas console. A launch over the quota ends `ORPHANED` with `QUOTA_EXCEEDED`.
-   **Capacity.** EDH reserves the desktop's instance in one subnet when it places the desktop, and does not retry in another subnet or Availability Zone. When AWS has no capacity for that type there, the launch ends `ORPHANED` with `CAPACITY_UNAVAILABLE` and the AWS error EDH recorded (`InsufficientInstanceCapacity`). Launching again with the same type usually fails the same way, so choose another type; see [Launch failure reasons](overview.md#launch-failure-reasons).

The job definitions VAMS registers name no execution role, so on an Amazon EC2 compute environment the Amazon ECS container instance role pulls the image. A container instance role in the same account whose IAM policy grants Amazon ECR read access, such as one with the AWS managed policy `AmazonEC2ContainerServiceforEC2Role`, pulls from the VAMS-owned repositories without further setup. A role whose IAM policy grants `ecr:GetAuthorizationToken` but not the pull actions on these repositories is named in `containers.imagePullRoleArns`, and the repository policy then grants it `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` and `ecr:BatchCheckLayerAvailability`; a repository policy cannot grant `ecr:GetAuthorizationToken`. See [Containers](overview.md#containers).

## Step 4: Connect the networks

![EDH Workstations add-on network topology](/img/addon_edhWorkstations_network.png)

The diagram shows the route from the add-on's functions to the EDH Application Load Balancer in the same or a connected VPC, and the browser's Amazon DCV path, which passes through no VAMS function, endpoint or load balancer; [Network](resources.md#network) describes each resource and path it draws.

In the shared-VPC layout of [Choose a network layout](#choose-a-network-layout), VAMS imports the EDH VPC. Set `useGlobalVpc.optionalExternalVpcId` to the EDH VPC (the `VpcId` parameter), pass EDH's private subnets (the `PrivateSubnets` parameter) as both `optionalExternalIsolatedSubnetIds` and `optionalExternalPrivateSubnetIds`, and pass its public subnets (the `PublicSubnets` parameter) as `optionalExternalPublicSubnetIds`. EDH's public subnets do not set `MapPublicIpOnLaunch`, so identify them by the parameter or by their `Name` tag rather than by that attribute. Each subnet field is a comma-delimited string with no spaces after the commas, the form in which the commands below print the subnet parameters:

```bash
CLUSTER=edh-demo
aws ssm get-parameter --name "/edh/${CLUSTER}/configuration/VpcId" \
    --query Parameter.Value --output text
aws ssm get-parameter --name "/edh/${CLUSTER}/configuration/PrivateSubnets" \
    --query Parameter.Value --output text | tr -d "[]' "
aws ssm get-parameter --name "/edh/${CLUSTER}/configuration/PublicSubnets" \
    --query Parameter.Value --output text | tr -d "[]' "
```

The values go into `app.useGlobalVpc` in `infra/config/config.json`:

```json
{
    "app": {
        "useGlobalVpc": {
            "enabled": true,
            "optionalExternalVpcId": "vpc-0123456789abcdef0",
            "optionalExternalIsolatedSubnetIds": "subnet-0aaaaaaaaaaaaaaa1,subnet-0aaaaaaaaaaaaaaa2,subnet-0aaaaaaaaaaaaaaa3",
            "optionalExternalPrivateSubnetIds": "subnet-0aaaaaaaaaaaaaaa1,subnet-0aaaaaaaaaaaaaaa2,subnet-0aaaaaaaaaaaaaaa3",
            "optionalExternalPublicSubnetIds": "subnet-0bbbbbbbbbbbbbbb1,subnet-0bbbbbbbbbbbbbbb2,subnet-0bbbbbbbbbbbbbbb3"
        }
    }
}
```

In this layout the add-on's functions run in EDH's private subnets and reach the EDH load balancer with no new rule, and the default AMI build subnet, the first VAMS private subnet, is an EDH private subnet that routes through EDH's NAT gateway. An imported VPC is deployed in two phases ([Step 6](#step-6-deploy-vams)).

:::warning[A deployment that is already running]
For a running VAMS deployment, populate the VPC context with `cdk synth --context loadContextIgnoreVPCStacks=true` from the `infra` directory in place of the first-phase deploy of [Step 6](#step-6-deploy-vams), then run its second phase. A deploy with that flag removes the API, search, pipeline, add-on and web nested stacks of a running deployment until the second phase recreates them, which also replaces the REST API and the web distribution. A running deployment without a VPC moves into the EDH VPC this way. A deployment that runs in a VPC that VAMS created keeps that VPC and uses the connected-VPC layout: moving it would move every VPC-bound resource, and the update fails for an Amazon OpenSearch Service domain, for the Amazon OpenSearch Serverless VPC endpoint that VAMS creates when `app.openSearch.useServerless.nextGen` is `false`, and for the Amazon EFS file systems of the NVIDIA Cosmos, GR00T and Isaac Lab pipelines.
:::

In the connected-VPC layout, VAMS keeps its own VPC. Connect it to the EDH VPC over VPC peering or AWS Transit Gateway with routes between the VAMS isolated subnets and the EDH load balancer, and add the ingress rule of [Load balancer security group](#load-balancer-security-group); a VPC that VAMS creates, and the Lambda security group the rule names, exist only after the first deployment of [Step 6](#step-6-deploy-vams). [Choose a network layout](#choose-a-network-layout) lists the installer settings this layout implies.

## Step 5: Configure VAMS

In `infra/config/config.json`, set the following fields of `app.addons.useEdhWorkstations`; the fields left out take their defaults. The values are examples:

```json
{
    "app": {
        "addons": {
            "useEdhWorkstations": {
                "enabled": true,
                "edhApiEndpoint": "https://edh-demo-viewer-<id>.us-east-1.elb.amazonaws.com/",
                "edhAdminApiKeySecretArn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:edh-root-key-AbCdEf",
                "edhApiCaBundleSecretArn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:edh-alb-ca-bundle-GhIjKl",
                "edhApiTlsServerName": "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE",
                "edhClusterId": "edh-demo",
                "vdi": {
                    "defaultProject": "default",
                    "virtualDesktopProfileId": 1
                }
            }
        }
    }
}
```

| Field                         | Value                                                                                                                                                                                        |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enabled`                     | `true`                                                                                                                                                                                       |
| `edhApiEndpoint`              | The `WebUserInterface` stack output, `https://<load balancer host>/`, with no other path ([What the cluster provides](#what-the-cluster-provides)), or the name your own certificate carries |
| `edhAdminApiKeySecretArn`     | The complete ARN of the root-key secret ([Root key](#root-key))                                                                                                                              |
| `edhApiCaBundleSecretArn`     | The complete ARN of the CA bundle secret ([TLS trust](#tls-trust)); empty for a certificate that chains to a public root                                                                     |
| `edhApiTlsServerName`         | `EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE` for the certificate `--tls-certificate auto` imports; empty when the certificate carries the endpoint host                                         |
| `edhClusterId`                | The cluster name, including the `edh-` prefix                                                                                                                                                |
| `vdi.defaultProject`          | `default`, or the project of [Project and virtual desktop profile](#project-and-virtual-desktop-profile)                                                                                     |
| `vdi.virtualDesktopProfileId` | `1`, or another EDH virtual desktop profile id; the value `0` the configuration templates ship fails validation                                                                              |

Every other field, with its meaning, is in the [Configuration](overview.md#configuration) table of the developer guide. Defaults, types and the validation applied to each field are in the [configuration reference](../../deployment/configuration-reference.md#edh-workstations-appaddonsuseedhworkstations), and the interactive [configuration builder](../../deployment/config-builder.mdx) carries the scalar and string-list fields.

## Step 6: Deploy VAMS

Continue [Deploy the solution](../../deployment/deploy-the-solution.md) from [Step 7: Import an external VPC](../../deployment/deploy-the-solution.md#step-7-import-an-external-vpc-conditional). With the EDH VPC imported, deploy in its two phases, from the `infra` directory:

```bash
cdk deploy --all --require-approval never --context loadContextIgnoreVPCStacks=true
cdk deploy --all --require-approval never
```

For a deployment that is already running, replace the first command with the `cdk synth` of [Step 4](#step-4-connect-the-networks).

Every deployment runs the deploy-time catalog registration, which registers the configured images in EDH and, with containers enabled, the configured job definitions with AWS Batch. A registration problem does not fail the deployment: it is reported in the `EdhCatalogRegistrationWarnings` output of the add-on's nested stack, which reads `none` when every entry is registered, and a redeploy retries the entry (see [Deploy-time catalog registration](overview.md#deploy-time-catalog-registration)). `cdk deploy` does not print nested stack outputs, so read it with the following command, where `<core stack name>` is the name `cdk deploy` reports for the VAMS core stack:

```bash
aws cloudformation describe-stacks --region <VAMS Region> \
    --query "Stacks[?starts_with(StackName,'<core stack name>') && contains(StackName,'EdhWorkstationsBuilderNestedStack')].Outputs[] | [?OutputKey=='EdhCatalogRegistrationWarnings'].OutputValue" \
    --output text
```

When VAMS runs in a connected VPC, connect the networks and add the ingress rule as [Step 4](#step-4-connect-the-networks) describes once the first deployment has created the Lambda security group and any VPC that VAMS creates, then redeploy to retry the registration.

## Step 7: Grant access

Run the commands in this step from the root of the VAMS repository, signed in to `vamscli` as the initial administrator (`app.adminUserId`), because `apply_template.py` runs the `vamscli` executable for each change. Install `vamscli` ([Installation and Profile Management](../../cli/installation.md)), then point it at the deployment's [application URL](../../deployment/deploy-the-solution.md#find-your-application-url) and sign in ([Setup and Authentication Commands](../../cli/commands/setup-and-auth.md)):

```bash
vamscli setup <application URL>
vamscli auth login -u <app.adminUserId>
```

The initial administrator's seeded `admin` role already holds every route and the three EDH object types the add-on checks. Other users need an account to sign in with, and `vamscli role user create` does not check that one exists: in an Amazon Cognito deployment, create each user with `vamscli user cognito create -u <user id> -e <email>` or under **Admin - Auth** → **User Management**, and Amazon Cognito emails the user a temporary password.

For those users, create an EDH user role and an EDH administrator role from the two permission templates and assign the roles. Leave `--mfa-required` off the `edh-admin.json` command unless every EDH administrator signs in with multi-factor authentication (MFA), as the paragraphs after the commands describe:

```bash
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/edh-user.json --role-name edh-user
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/edh-admin.json --role-name edh-admin --mfa-required
vamscli role user create -u user@example.com --role-name edh-user
vamscli role user create -u admin@example.com --role-name edh-admin
```

A role that runs container jobs also needs the container routes and an `edhContainer` constraint, which neither template grants.

With `--mfa-required`, the `edh-admin` constraints apply only in a session signed in with MFA. The Amazon Cognito user pool offers MFA as optional, and VAMS has no page or command that registers a factor, so each EDH administrator needs a factor registered in the user pool (see [TOTP software token MFA](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-mfa-totp.html) in the Amazon Cognito Developer Guide). The VAMS sign-in page and `vamscli auth login` then ask for the code; until then, a user whose only roles require MFA is denied.

Some deployments, sign-ins and API-key requests carry no MFA state, so an MFA-required role's constraints never apply to them; [Roles](overview.md#roles) lists the cases. For an `edh-admin` role already created with the flag, re-running the template command does not change it; `vamscli role update -r edh-admin --no-mfa-required` removes the requirement. See [Permissions](overview.md#permissions) in the developer guide for the roles, the templates and the object types, and [Permissions](../../user-guide/permissions.md) in the user guide for [assigning roles](../../user-guide/permissions.md#assigning-roles-to-users) and [MFA-required roles](../../user-guide/permissions.md#mfa-required-roles).

## Step 8: Verify the add-on

1. In the profile that [Step 7](#step-7-grant-access) signs in as the initial administrator, confirm that the add-on is enabled. The command reads the feature switches the profile stores at sign-in, so when the profile was signed in before [Step 6](#step-6-deploy-vams), first sign in again with `vamscli auth login -u <app.adminUserId>`:

    ```bash
    vamscli features check EDH_WORKSTATIONS
    ```

2. Check that VAMS reaches EDH with its credentials. As an administrator, open **Admin - Workstations** → **Workstation Admin**; the page header reads `EDH <cluster id>: reachable (<n> ms)` or names the reason it is not. The same probe from the CLI returns `status: ok`, `edhClusterId` and `latencyMs`. A failed probe exits with status 1 and prints a JSON object whose `error_type` is `EdhUnavailableError` and whose `error` text ends in `[reason: credentialRejected]`, `[reason: unreachable]` or `[reason: refused]`:

    ```bash
    vamscli edh admin health --json-output
    ```

3. Check the image catalog. On a stock cluster it lists the desktop software stacks EDH seeds, each in the **Baseline Image** category. With `--image-id` set to the `IMAGE` value of one of them, the second command lists the instance types that run that image's architecture:

    ```bash
    vamscli edh catalog images
    vamscli edh catalog instance-types --image-id <ID>
    ```

4. Launch one workstation from the Workstations page (**Virtual Workstations** → **My Workstations** in the navigation, then **Launch Workstation**), wait until its status is **Active**, choose **Connect** and confirm that the desktop opens in the browser after you accept the certificate warning that [Browser access to Amazon DCV](#browser-access-to-amazon-dcv) describes, then delete the workstation. For this check, a non-GPU instance type that the catalog lists avoids the GPU instance quota of [Before you begin](#before-you-begin).

    The same steps from the CLI follow. Take `<ID>` and `<OS>` from the `IMAGE` and `OS` columns of the image list, `<TYPE>` from the instance types listed for that image, and a `<GIB>` of at least the image's `ROOT GIB`. The launch runs asynchronously: repeat `vamscli edh workstation list` until the workstation's `State` reads `ACTIVE`, which takes several minutes, and pass the value in its `Identifier` column as `<SESSION_ID>`. `edh workstation connect` prints the DCV host, and returns the connection URL, which is a credential, only with `--json-output`:

    ```bash
    vamscli edh workstation create --image-id <ID> --instance-type <TYPE> --name verify --root-size-gb <GIB> --os-family <OS>
    vamscli edh workstation list
    vamscli edh workstation connect <SESSION_ID>
    vamscli edh workstation delete <SESSION_ID> --confirm
    ```

`credentialRejected` means EDH refused the key the secret holds, the secret could not be read or holds no `rootKey` field ([Root key](#root-key)), or EDH answered with a redirect, as a cluster not at the [supported revision](#supported-edh-version) does for a renamed route. `unreachable` points at the network path, the load balancer rule or TLS trust. `refused` means EDH refused the probe, which lists the virtual desktop profiles as the service actor `vamsservice`: the `VIRTUAL_DESKTOPS` flag is disabled, an allow or deny list on it does not admit `vamsservice` ([Feature-flag allow lists](#feature-flag-allow-lists)), or the cluster is at another revision. A launch that ends `ORPHANED` carries its reason in `lastError`; on a cluster at another revision that is `LAUNCH_ERROR` for a renamed route or `EDH_REFUSED` for a changed request field, and a changed record field leaves the catalog empty. See [Troubleshooting](overview.md#troubleshooting) and [EDH Workstations CLI Commands](cli-commands.md).

## Optional features

### Session bootstrap

With `sessionBootstrap.enabled`, each Linux workstation receives its own short-lived VAMS API key, so the desktop's `vamscli` is signed in as the launching user. It needs the role of [Desktop instance role](#desktop-instance-role) in `sessionBootstrap.workstationInstanceRoleArns` and a desktop image that carries `vamscli` and the sign-in, with an XDG autostart entry that starts it when the user's desktop session starts, such as one built from the shipped `al2023-desktop` or `isaacsim` recipe. When `app.authProvider.authorizerOptions.allowedIpRanges` is set, every call from the desktop's `vamscli` is denied unless the list includes the addresses the desktops reach the VAMS API from, such as those of EDH's NAT gateways. A user receives a key only with a role assignment of their own. See [Session bootstrap](overview.md#session-bootstrap) and its [Limits](overview.md#limits).

### AMI recipes

With `amiBuilds.enabled`, administrators build versioned desktop AMIs with EC2 Image Builder from the recipes shipped under `backendAddons/edh/amiRecipes/` or defined on the **AMI Recipes** tab, and VAMS registers each finished build in EDH as a software stack, which the launch wizard lists under **Custom AMI Recipe** while the build is available. A build launches in `amiBuilds.buildSubnetId`, or by default in the first VAMS private subnet, falling back to the first isolated subnet. The shipped recipes install packages from the internet, so their build subnet needs a route to a NAT gateway, and they register against virtual desktop profile `1`. A VPC that VAMS creates has private subnets only when another enabled feature adds them; in the connected-VPC layout without them, add a subnet with NAT egress to that VPC and set `amiBuilds.buildSubnetId` to it (see the [configuration reference](../../deployment/configuration-reference.md#edh-workstations-appaddonsuseedhworkstations)).

To register a shipped recipe against another profile, or change it otherwise, duplicate it. The duplicate form on the **AMI Recipes** tab sets the profile id and the copy's other fields; from the CLI, `vamscli edh admin ami-recipe duplicate` makes the copy and `vamscli edh admin ami-recipe update --file` changes it. The copy keeps the shipped recipe's bundled files, which cannot be changed. With `app.useWaf` enabled, the deployment's regional web ACL lets recipe bodies that read the instance metadata service through on the recipe routes only; every other managed rule still applies to them, and synthesis warns when the rule policy enforces one a recipe body can match (see [AWS WAF exemption for AMI recipes](resources.md#aws-waf-exemption-for-ami-recipes) and [WAF rule policy](../../deployment/configuration-reference.md#waf-rule-policy-configpolicywafpolicyconfigjson)). See [AMI recipes](overview.md#ami-recipes).

Before users launch desktops from the shipped `isaacsim` recipe (Isaac Sim 6.2 on Ubuntu 24.04), check that:

-   The cluster's GRID driver pin names an R595 driver the cluster holds, as [GPU desktops and the NVIDIA driver](#gpu-desktops-and-the-nvidia-driver) describes.
-   Virtual desktop profile `1`, and `edhAllowedInstanceTypes` when it is set, allow at least one of the recipe's instance types (the RTX `g5`, `g6`, `gr6`, `g6e` and `g7e` sizes listed in [Isaac Sim recipe](overview.md#isaac-sim-recipe)), and a root volume of at least 150 GB.
-   The account's G-instance quota covers the sizes users pick.
-   Users' address ranges are on the cluster's client prefix list (see [Browser access to Amazon DCV](#browser-access-to-amazon-dcv)).
-   The build subnet reaches `downloads.isaacsim.nvidia.com` for the 13 GB Isaac Sim archive, as well as the Ubuntu archive, `awscli.amazonaws.com` and PyPI.

A desktop's first boot installs the driver and reboots, and Isaac Sim starts after the user signs in to the desktop; the first start warms shaders for five to ten minutes. When Isaac Sim does not start, `/var/log/vams-isaacsim-preflight.log` on the desktop says why; see the troubleshooting table in [Isaac Sim recipe](overview.md#isaac-sim-recipe).

### Containers

With `containers.enabled`, VAMS builds container images into Amazon ECR, registers them as AWS Batch job definitions tagged for the cluster, and users run them from the **Simulations** tab. It needs the job queue and pull access of [AWS Batch queue and image pulls](#aws-batch-queue-and-image-pulls). See [Containers](overview.md#containers).

### Leases

Leases are off by default. A lease time-boxes a workstation: VAMS stops the desktop once its lease runs out and deletes it after a grace window. While the lease policy enables leases, a launch may ask for one, and while it both enables and forces them, every launch carries one; `forced` has no effect while leases are disabled. `vdi.lease` seeds the policy, including its `enabled` and `forced` switches, at the first deployment, and administrators change it on the **Settings** tab of the Workstation Admin page; leases need no EDH setting. See [Leases](overview.md#leases).

## Disable or remove the add-on

Setting `app.addons.useEdhWorkstations.enabled` and `amiBuilds.enabled` to `false` and redeploying removes the add-on's nested stack; configuration validation rejects AMI builds without the add-on, and turns `containers.enabled` off with a warning. `cdk destroy` removes the nested stack with the rest of VAMS. Before either, while the add-on is still deployed, complete the [pre-uninstall steps](uninstall.md#pre-uninstall-steps): delete the desktops, retire the AMI builds and delete the administrator recipes, delete the registered images, and revoke any load balancer ingress rule that names the add-on's Lambda security group.

The EDH cluster stays as it is. What the add-on created there and was not removed beforehand stays too: software stacks, desktops, and the directory users the add-on created, which VAMS never deletes. The job definitions VAMS registered stay in AWS Batch. See [Objects left in EDH](uninstall.md#objects-left-in-edh). With AMI builds, the AMIs, snapshots and EC2 Image Builder resources that retirement does not remove stay in the account as well; see [Remove EDH AMIs](uninstall.md#remove-edh-amis). The AWS CodeBuild log groups of the container image builds and the EC2 Image Builder log groups of the AMI builds also remain (see [Delete the EDH log groups](uninstall.md#delete-the-edh-log-groups)), and with session bootstrap a desktop left running leaves its session secret (see [Remove EDH session secrets](uninstall.md#remove-edh-session-secrets)). The root-key and CA bundle secrets are operator-managed, and VAMS does not delete them.

In the shared-VPC layout VAMS runs in the EDH VPC, so remove VAMS before you uninstall the EDH cluster with `installer/uninstall_edh.sh`, which deletes the VPC's NAT gateways and the cluster stack that owns the VPC.

## Related resources

-   [EDH Workstations Add-on](overview.md)
-   [EDH Workstations User Guide](user-guide.md)
-   [EDH Workstations CLI Commands](cli-commands.md)
-   [Configuration reference](../../deployment/configuration-reference.md#edh-workstations-appaddonsuseedhworkstations)
-   [EDH Workstations Add-on Resources](resources.md)
-   [Uninstall the EDH Workstations Add-on](uninstall.md)
-   [Uninstall the solution](../../deployment/uninstall.md)
-   [EDH documentation](https://awslabs.github.io/engineering-development-hub-documentation/)

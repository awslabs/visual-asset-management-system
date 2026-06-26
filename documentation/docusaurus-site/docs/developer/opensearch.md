# OpenSearch

VAMS uses Amazon OpenSearch to power asset and file search. Search is optional: enable at most one of Amazon OpenSearch Serverless (`app.openSearch.useServerless`) or a provisioned Amazon OpenSearch Service domain (`app.openSearch.useProvisioned`), or disable both to deploy without search. This page describes what developers and operators need to know for each mode: setup, how indexes and reindexing work, network access, and limits.

For the full list of configuration options, see the [Configuration reference](../deployment/configuration-reference.md#amazon-opensearch-service-appopensearch). For network topology, see [Network architecture](../architecture/networking.md#opensearch-serverless-interface-endpoint).

## Dual-index architecture

Both modes use the same two indexes: an **asset index** and a **file index** (`vams-assets-v3` and `vams-files-v3`). The index mappings use `flat_object` for metadata (`MD_`) and file attribute (`AB_`) fields to prevent field explosion, and a derived `geo_MD_location` field of type `geo_shape` for geospatial search.

### How indexes are created

The index mappings are created by a CloudFormation custom resource (the **schema-deploy** function) that runs during deployment. It connects to the collection or domain endpoint and creates each index with the correct mappings, checking `indices.exists` first so it is **idempotent** — a redeploy never recreates or overwrites an existing index.

:::important
The schema-deploy custom resource is the **only** component that creates the index mappings. The indexers and the reindex utility only write documents; they assume the index and its mappings already exist. If documents are written to a missing index, OpenSearch would auto-create it with incorrect dynamic mappings. Always let the schema-deploy resource create the indexes before reindexing.
:::

### How indexing and reindexing work

At runtime, asset and file changes flow through Amazon DynamoDB streams to the **asset indexer** and **file indexer** Lambda functions, which write documents into the indexes. The indexers read the endpoint and index names from AWS Systems Manager (SSM) parameters written at deploy time, and sign requests with SigV4.

To (re)populate the indexes from the source data in DynamoDB and Amazon S3 — for example after an index-name change, a schema change, or a fresh collection — use the [Reindex utility](utilities/reindex.md), or set `app.openSearch.reindexOnCdkDeploy = true` for a single deployment (then set it back to `false`). Reindexing requires the index mappings to already exist (see above).

## Serverless

Amazon OpenSearch Serverless auto-scales compute and removes cluster management. Enable it with `app.openSearch.useServerless.enabled = true`.

### Generations

A Serverless collection is created inside a collection group whose **generation** is set by `app.openSearch.useServerless.nextGen`:

-   **Next-generation (`nextGen = true`, default for commercial):** supports scale-to-zero (a minimum OCU of `0`), faster autoscaling, and per-collection-group OCU capacity limits. Requires standby replicas (`enableStandbyReplicas = true`). Not available in AWS GovCloud or the AWS European Sovereign Cloud.
-   **Classic (`nextGen = false`, required for GovCloud/EU):** the prior-generation Serverless behavior.

OCU capacity is set by `minIndexingOcu` / `maxIndexingOcu` / `minSearchOcu` / `maxSearchOcu` (each must be `0`, `2`, `4`, `8`, `16`, or any multiple of `16`). A minimum of `0` (scale-to-zero) requires `nextGen = true`.

### Collection endpoints and network access

The two generations expose different collection endpoint hostnames, which require different VPC endpoint types for private access:

| Generation                       | Collection endpoint hostname                      | Data-plane VPC endpoint                                                                                     |
| -------------------------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Next-generation (`nextGen=true`) | `\{collection-id\}.aoss.\{region\}.on.aws`        | Standard AWS PrivateLink interface endpoint (Amazon EC2 service `com.amazonaws.\{region\}.aoss-data`)       |
| Classic (`nextGen=false`)        | `\{collection-id\}.\{region\}.aoss.amazonaws.com` | Amazon OpenSearch Serverless-managed endpoint, which provisions its own Amazon Route 53 private hosted zone |

When `allowPublic = true`, the collection is reachable over the public internet (subject to data-access policies) and no VPC endpoint is needed. When `allowPublic = false` (recommended for production), the collection is reachable only through a VPC endpoint and requires:

1. **A VPC endpoint** into the data plane (type per the table above).
2. **A network access policy** with `AllowFromPublic = false` and the VPC endpoint id in `SourceVPCEs`.
3. **A data access policy** granting the consuming IAM principals access to the collection and indexes.

The OpenSearch-facing Lambda functions (search and the indexers) run inside the VPC and connect over private DNS on port 443, signing with SigV4 service name `aoss`. VAMS wires up the data access policy and the security-group access automatically. A private collection requires `app.useGlobalVpc.enabled = true` (only the OpenSearch-facing Lambdas are placed in the VPC; `useForAllLambdas` is not required).

### When VAMS creates the VPC endpoint

The next-generation data-plane endpoint is a standard Amazon EC2 interface endpoint, so it follows the global `app.useGlobalVpc.addVpcEndpoints` setting like every other interface endpoint VAMS creates:

| Generation | `allowPublic` | `addVpcEndpoints` | VAMS creates the endpoint + network policy?                                 |
| ---------- | ------------- | ----------------- | --------------------------------------------------------------------------- |
| NextGen    | `false`       | `true`            | Yes — standard `aoss-data` interface endpoint + network policy              |
| NextGen    | `false`       | `false`           | **No** — deferred to manual creation (see below)                            |
| Classic    | `false`       | `true` or `false` | Yes — managed endpoint + network policy (not governed by `addVpcEndpoints`) |
| Any        | `true`        | any               | No endpoint needed — collection is public                                   |

The classic managed endpoint is an Amazon OpenSearch Serverless resource (not an Amazon EC2 interface endpoint), so it is always created for a private classic collection regardless of `addVpcEndpoints`.

:::warning[Private next-gen with `addVpcEndpoints=false` is a deferred setup]
When you deploy a private next-generation collection (`allowPublic=false`, `nextGen=true`) with `app.useGlobalVpc.addVpcEndpoints=false`, VAMS **does not** create the `aoss-data` interface endpoint or the collection's VPC network access policy. The deployment still succeeds: the schema-deploy custom resource writes the OpenSearch SSM parameters and **skips index creation** (the collection is not reachable yet). You must create the endpoint and network policy manually, then run a follow-up deployment to create the index mappings, and finally reindex. Until then, search and indexing are non-functional. See [Deferred next-gen setup](#deferred-next-gen-setup-manual-vpc-endpoint).
:::

### Deferred next-gen setup (manual VPC endpoint)

Complete these steps after a deployment that deferred the VPC setup (private next-gen with `addVpcEndpoints=false`).

#### 1. Find the collection and VPC details

```bash
# Collection id and endpoint (NextGen endpoint is on *.aoss.<region>.on.aws)
aws opensearchserverless list-collections \
    --query "collectionSummaries[?contains(name, '<STACK_NAME>')]"

# The VPC and isolated subnets VAMS created (one subnet per Availability Zone)
aws ec2 describe-vpcs --query "Vpcs[?Tags[?Value=='<STACK_NAME>']].VpcId"
aws ec2 describe-subnets --filters "Name=vpc-id,Values=<VPC_ID>" \
    --query "Subnets[].{Id:SubnetId,AZ:AvailabilityZone}"
```

#### 2. Create a security group for the endpoint

Allow inbound HTTPS (port 443) from the VPC CIDR so the in-VPC Lambda functions can reach the endpoint.

```bash
aws ec2 create-security-group \
    --group-name vams-aoss-data-endpoint \
    --description "VAMS OpenSearch Serverless aoss-data endpoint" \
    --vpc-id <VPC_ID>

aws ec2 authorize-security-group-ingress \
    --group-id <SG_ID> \
    --protocol tcp --port 443 --cidr <VPC_CIDR>
```

#### 3. Create the standard PrivateLink interface endpoint

Use the next-generation data-plane service name `com.amazonaws.<region>.aoss-data`, place it in the isolated subnets across at least two Availability Zones, and enable private DNS so the `*.aoss.<region>.on.aws` hostnames resolve inside the VPC.

```bash
aws ec2 create-vpc-endpoint \
    --vpc-id <VPC_ID> \
    --vpc-endpoint-type Interface \
    --service-name com.amazonaws.<region>.aoss-data \
    --subnet-ids <SUBNET_ID_AZ1> <SUBNET_ID_AZ2> \
    --security-group-ids <SG_ID> \
    --private-dns-enabled
```

Note the returned endpoint id (format `vpce-...`).

#### 4. Create the network access policy tied to the endpoint

Create a `network` access policy that denies public access and allows the collection through the endpoint id from the previous step. Replace `<COLLECTION_NAME>` with the VAMS collection name and `<VPCE_ID>` with the endpoint id.

```bash
aws opensearchserverless create-security-policy \
    --name vams-aoss-network \
    --type network \
    --policy '[
        {
            "Rules": [
                { "ResourceType": "collection", "Resource": ["collection/<COLLECTION_NAME>"] },
                { "ResourceType": "dashboard", "Resource": ["collection/<COLLECTION_NAME>"] }
            ],
            "AllowFromPublic": false,
            "SourceVPCEs": ["<VPCE_ID>"]
        }
    ]'
```

The data access policy and the IAM permissions for the VAMS Lambda roles are already created by the deployment, so only the VPC endpoint and the network access policy need manual creation. The endpoint id in `SourceVPCEs` is the only link between the endpoint and the collection.

#### 5. Deploy the deferred index schema

The index mappings were not created during the initial deployment because the collection was unreachable. Now that the endpoint and network policy exist, set `app.openSearch.useServerless.deployDeferredIndexSchema = true` and deploy once. This runs the schema-deploy custom resource inside the VPC, against the operator-created endpoint, and creates the index mappings (it is idempotent and skips any index that already exists).

```bash
# In infra/config/config.json, set:
#   app.openSearch.useServerless.deployDeferredIndexSchema = true
npx cdk deploy --all --require-approval never
# (or pass it as CDK context: npx cdk deploy --all -c deployDeferredIndexSchema=true ...)
```

After the deployment succeeds, set `deployDeferredIndexSchema` back to `false`.

:::note
`deployDeferredIndexSchema` only controls whether the schema-deploy resource attempts index creation; it never causes VAMS to create the VPC endpoint or network policy (those remain manual). It is ignored when `app.useGlobalVpc.addVpcEndpoints = true`, because in that case the endpoint and schema are created normally and there is nothing deferred.
:::

#### 6. Reindex to populate the indexes

With the index mappings created, populate them from the source data — run the [Reindex utility](utilities/reindex.md) in `lambda` mode (the deployed reindexer runs inside the VPC), or set `app.openSearch.reindexOnCdkDeploy = true` for one deployment. After reindexing completes, search and indexing are fully functional.

To avoid the deferred flow entirely, set `app.useGlobalVpc.addVpcEndpoints = true` for a private next-generation deployment so VAMS creates the endpoint, network policy, and indexes for you. This is the recommended configuration unless your environment manages VPC endpoints centrally outside the VAMS stack.

### Serverless limits and considerations

-   **Scale-to-zero cold start:** on next-generation with a minimum OCU of `0`, the first search or indexing request after about 10 minutes of inactivity incurs roughly 10–30 seconds of added latency while capacity is restored.
-   **OCU values are constrained:** each OCU bound must be `0`, `2`, `4`, `8`, `16`, or a multiple of `16`.
-   **NextGen requires standby replicas** (`enableStandbyReplicas = true`) and is unavailable in GovCloud / EU Sovereign Cloud (use `nextGen = false` there).
-   **Reconfiguration is a re-deployment:** changing generation, `allowPublic`, or the collection group reshapes the collection. Disable Serverless and deploy, then re-enable with the new settings and deploy, then reindex.

## Provisioned

A provisioned Amazon OpenSearch Service domain offers dedicated capacity and custom instance sizing. Enable it with `app.openSearch.useProvisioned.enabled = true`. A provisioned domain always runs inside the VPC, so it requires `app.useGlobalVpc.enabled = true`.

### Setup and availability zones

The domain runs the engine version pinned in `config.ts`. The version is selected by partition: most partitions (commercial AWS, AWS GovCloud) use `OPENSEARCH_VERSION` (OpenSearch 3.x), while the **AWS European Sovereign Cloud** (partition `aws-eusc`, Region `eusc-de-east-1`) uses `OPENSEARCH_VERSION_EUSOVEREIGN` (OpenSearch 2.x) because OpenSearch 3.x is not yet supported there. The selection is automatic and requires no configuration.

The Availability Zone count is set by `app.openSearch.useProvisioned.availabilityZoneCount` (`2` or `3`, default `2`), with one data node per zone:

-   At **2 AZs**, the domain runs zone-aware **without** Standby (two data nodes, a single copy of each index).
-   At **3 AZs**, the domain runs as **Multi-AZ with Standby** (three data nodes, and the indexes are created with two replicas so each has three copies, which Standby requires).

Set `availabilityZoneCount` to `2` for Regions or partitions that expose only two Availability Zones (such as the AWS European Sovereign Cloud Region `eusc-de-east-1`).

The schema-deploy custom resource creates the index mappings against the domain endpoint (inside the VPC) during deployment, the same way as for Serverless.

### Indexes, shards, and reindexing

The primary shard count per index is set by `app.openSearch.useProvisioned.numberOfShards` (default `1`). As a sizing guideline, an index expected to exceed roughly 60 GB — about 3 million asset or file records — should use more than one shard.

The shard count and the replica count are **fixed at index creation**. Changing either requires re-creating the index: disable and re-enable OpenSearch (or otherwise recreate the domain), then reindex. Existing indexes are not re-sharded in place.

### Provisioned limits and considerations

-   **VPC required:** a provisioned domain runs in the VPC; `app.useGlobalVpc.enabled` must be `true`.
-   **3-AZ Standby must be created fresh:** switching an existing 2-AZ domain to `availabilityZoneCount: 3` in place is rejected by the service. To move to 3-AZ Standby, deploy with OpenSearch disabled to remove the domain, then re-enable with `availabilityZoneCount: 3`, then reindex.
-   **Fragile in-place updates:** domain configuration changes (instance type, EBS size, engine version) trigger blue/green updates that can take 30+ minutes and occasionally exceed the CloudFormation custom-resource timeout. A major engine-version upgrade may require deploying with OpenSearch disabled, then re-enabling.
-   **Service-linked role:** a provisioned domain in a VPC requires the `AWSServiceRoleForAmazonOpenSearchService` service-linked role. VAMS creates it idempotently during deployment (created if missing, left unchanged if present), so the _"you must enable a service-linked role"_ error should no longer require a manual retry. The role is account-wide and is not removed on stack teardown.

## Disabling OpenSearch

Set both `useServerless.enabled` and `useProvisioned.enabled` to `false` to deploy without search. The `NOOPENSEARCH` feature flag is set, and search features are unavailable in the UI; asset and file browsing and management remain functional.

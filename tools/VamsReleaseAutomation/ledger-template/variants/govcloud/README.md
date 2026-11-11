# GovCloud variant

The default envs in this template use the commercial VAMS template (`templateBase: commercial`,
CloudFront). For AWS GovCloud (US), copy `variants/govcloud/envs/` (dev, test, prod) over `envs/`, and
make the same edits in any other env. Every env of a ledger that promotes into a GovCloud env must be in
GovCloud too: bundles, history and state live in one partition, so `vra ledger validate` refuses a
`promotesFrom` that crosses partitions (unless the env sets `allowCrossPartitionPromotion: true`):

| Field                 | Commercial                             | GovCloud                                                                                                                                                   |
| --------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `region`              | `us-east-1`                            | `us-gov-west-1` (or `us-gov-east-1`)                                                                                                                       |
| `partition`           | `aws`                                  | `aws-us-gov`                                                                                                                                               |
| `templateBase`        | `commercial`                           | `govcloud`                                                                                                                                                 |
| role ARNs             | `arn:aws:iam::...`                     | `arn:aws-us-gov:iam::...`                                                                                                                                  |
| overlay `config.json` | from `config.template.commercial.json` | from `config.template.govcloud.json` (ALB + `useAlb.domainHost` / `certificateArn`, `useFips`, `useGlobalVpc`, provisioned OpenSearch, `govCloud.enabled`) |
| `liveTest.webUrl`     | resolved from stack outputs            | `https://<useAlb.domainHost>`                                                                                                                              |

`vra.yaml`: set `stateStore.bucket` / `region` and `notifications.region` to the GovCloud tools
account values, and prefer `githost.type: codecommit` (or any git host reachable from the GovCloud
tools account).

Regenerate an overlay from the current upstream template instead of copying: run
`vra ledger import --env prod --from <your existing config.json>`, then
`vra ledger upgrade-config --env prod --to-ref <vams tag>` to see new and removed keys.

EU Sovereign works the same way with `templateBase: eusovereign`, `partition: aws-eusc` and
`config.template.eusovereign.json`.

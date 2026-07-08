/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
export type SERVICE =
    | "A4B"
    | "ACCESS_ANALYZER"
    | "ACCOUNT"
    | "ACM"
    | "ACM_PCA"
    | "AGREEMENT_MARKETPLACE"
    | "AIRFLOW"
    | "AMPLIFY"
    | "AMPLIFYBACKEND"
    | "AMPLIFYUIBUILDER"
    | "AOSS"
    | "APIGATEWAY"
    | "API_DETECTIVE"
    | "API_ECR"
    | "API_ECR_PUBLIC"
    | "API_ELASTIC_INFERENCE"
    | "API_FLEETHUB_IOT"
    | "API_IOTDEVICEADVISOR"
    | "API_IOTWIRELESS"
    | "API_MEDIATAILOR"
    | "API_PRICING"
    | "API_SAGEMAKER"
    | "API_TUNNELING_IOT"
    | "APPCONFIG"
    | "APPCONFIGDATA"
    | "APPFLOW"
    | "APPLICATIONINSIGHTS"
    | "APPLICATION_AUTOSCALING"
    | "APPMESH"
    | "APPRUNNER"
    | "APPSTREAM2"
    | "APPSYNC"
    | "APP_INTEGRATIONS"
    | "APS"
    | "ARC_ZONAL_SHIFT"
    | "ATHENA"
    | "AUDITMANAGER"
    | "AUTOSCALING"
    | "AUTOSCALING_PLANS"
    | "BACKUP"
    | "BACKUPSTORAGE"
    | "BACKUP_GATEWAY"
    | "BATCH"
    | "BEDROCK"
    | "BILLINGCONDUCTOR"
    | "BRAKET"
    | "BUDGETS"
    | "CASES"
    | "CASSANDRA"
    | "CATALOG_MARKETPLACE"
    | "CE"
    | "CHIME"
    | "CLEANROOMS"
    | "CLOUD9"
    | "CLOUDCONTROLAPI"
    | "CLOUDDIRECTORY"
    | "CLOUDFORMATION"
    | "CLOUDFRONT"
    | "CLOUDHSM"
    | "CLOUDHSMV2"
    | "CLOUDSEARCH"
    | "CLOUDTRAIL"
    | "CLOUDTRAIL_DATA"
    | "CODEARTIFACT"
    | "CODEBUILD"
    | "CODECATALYST"
    | "CODECOMMIT"
    | "CODEDEPLOY"
    | "CODEGURU_PROFILER"
    | "CODEGURU_REVIEWER"
    | "CODEPIPELINE"
    | "CODESTAR"
    | "CODESTAR_CONNECTIONS"
    | "CODESTAR_NOTIFICATIONS"
    | "COGNITO_HOSTED_UI"
    | "COGNITO_IDENTITY"
    | "COGNITO_IDP"
    | "COGNITO_SYNC"
    | "COMPREHEND"
    | "COMPREHENDMEDICAL"
    | "COMPUTE_OPTIMIZER"
    | "CONFIG"
    | "CONNECT"
    | "CONNECT_CAMPAIGNS"
    | "CONTACT_LENS"
    | "CONTROLTOWER"
    | "COST_OPTIMIZATION_HUB"
    | "CUR"
    | "DATABREW"
    | "DATAEXCHANGE"
    | "DATAPIPELINE"
    | "DATASYNC"
    | "DATAZONE"
    | "DATA_ATS_IOT"
    | "DATA_IOT"
    | "DATA_JOBS_IOT"
    | "DATA_MEDIASTORE"
    | "DAX"
    | "DEVICEFARM"
    | "DEVOPS_GURU"
    | "DIRECTCONNECT"
    | "DISCOVERY"
    | "DLM"
    | "DMS"
    | "DOCDB"
    | "DRS"
    | "DS"
    | "DYNAMODB"
    | "EBS"
    | "EC2"
    | "ECR_DKR"
    | "ECS"
    | "ECS_TASKS"
    | "EDGE_SAGEMAKER"
    | "EKS"
    | "EKS_AUTH"
    | "ELASTICACHE"
    | "ELASTICBEANSTALK"
    | "ELASTICFILESYSTEM"
    | "ELASTICLOADBALANCING"
    | "ELASTICMAPREDUCE"
    | "ELASTICTRANSCODER"
    | "EMAIL"
    | "EMR_CONTAINERS"
    | "EMR_SERVERLESS"
    | "ENTITLEMENT_MARKETPLACE"
    | "ES"
    | "EVENTS"
    | "EVIDENTLY"
    | "EXECUTE_API"
    | "FINSPACE"
    | "FINSPACE_API"
    | "FIREHOSE"
    | "FMS"
    | "FORECAST"
    | "FORECASTQUERY"
    | "FRAUDDETECTOR"
    | "FSX"
    | "GAMELIFT"
    | "GAMELIFTSTREAMS"
    | "GAMESPARKS"
    | "GEO"
    | "GLACIER"
    | "GLOBALACCELERATOR"
    | "GLUE"
    | "GRAFANA"
    | "GREENGRASS"
    | "GROUNDSTATION"
    | "GUARDDUTY"
    | "HEALTH"
    | "HEALTHLAKE"
    | "HONEYCODE"
    | "IAM"
    | "IDENTITYSTORE"
    | "IDENTITY_CHIME"
    | "IMPORTEXPORT"
    | "INGEST_TIMESTREAM"
    | "INSPECTOR"
    | "INSPECTOR2"
    | "INTERNETMONITOR"
    | "IOT"
    | "IOTANALYTICS"
    | "IOTEVENTS"
    | "IOTEVENTSDATA"
    | "IOTFLEETWISE"
    | "IOTROBORUNNER"
    | "IOTSECUREDTUNNELING"
    | "IOTSITEWISE"
    | "IOTTHINGSGRAPH"
    | "IOTTWINMAKER"
    | "IOTWIRELESS"
    | "IVS"
    | "IVSCHAT"
    | "IVSREALTIME"
    | "KAFKA"
    | "KAFKACONNECT"
    | "KENDRA"
    | "KENDRA_RANKING"
    | "KINESIS"
    | "KINESISANALYTICS"
    | "KINESISVIDEO"
    | "KMS"
    | "LAKEFORMATION"
    | "LAMBDA"
    | "LICENSE_MANAGER"
    | "LICENSE_MANAGER_LINUX_SUBSCRIPTIONS"
    | "LICENSE_MANAGER_USER_SUBSCRIPTIONS"
    | "LIGHTSAIL"
    | "LOGS"
    | "LOOKOUTEQUIPMENT"
    | "LOOKOUTMETRICS"
    | "LOOKOUTVISION"
    | "M2"
    | "MACHINELEARNING"
    | "MACIE"
    | "MACIE2"
    | "MANAGEDBLOCKCHAIN"
    | "MANAGEDBLOCKCHAIN_QUERY"
    | "MARKETPLACECOMMERCEANALYTICS"
    | "MEDIACONNECT"
    | "MEDIACONVERT"
    | "MEDIALIVE"
    | "MEDIAPACKAGE"
    | "MEDIAPACKAGEV2"
    | "MEDIAPACKAGE_VOD"
    | "MEDIASTORE"
    | "MEDIA_PIPELINES_CHIME"
    | "MEETINGS_CHIME"
    | "MEMORY_DB"
    | "MESSAGING_CHIME"
    | "METERING_MARKETPLACE"
    | "METRICS_SAGEMAKER"
    | "MGH"
    | "MGN"
    | "MIGRATIONHUB_ORCHESTRATOR"
    | "MIGRATIONHUB_STRATEGY"
    | "MOBILEANALYTICS"
    | "MODELS_LEX"
    | "MODELS_V2_LEX"
    | "MONITORING"
    | "MQ"
    | "MTURK_REQUESTER"
    | "NEPTUNE"
    | "NETWORKMANAGER"
    | "NETWORK_FIREWALL"
    | "NIMBLE"
    | "NOTIFICATIONS"
    | "NOTIFICATIONS_CONTACTS"
    | "NOVA_ACT"
    | "OAM"
    | "OIDC"
    | "OMICS"
    | "OPSWORKS"
    | "OPSWORKS_CM"
    | "ORGANIZATIONS"
    | "OSIS"
    | "OUTPOSTS"
    | "PARTICIPANT_CONNECT"
    | "PARTNERCENTRAL_CHANNEL"
    | "PERSONALIZE"
    | "PI"
    | "PINPOINT"
    | "PIPES"
    | "POLLY"
    | "PORTAL_SSO"
    | "PROFILE"
    | "PROJECTS_IOT1CLICK"
    | "PROTON"
    | "QBUSINESS"
    | "QLDB"
    | "QUERY_TIMESTREAM"
    | "QUICKSIGHT"
    | "RAM"
    | "RBIN"
    | "RDS"
    | "RDS_DATA"
    | "REDSHIFT"
    | "REDSHIFT_SERVERLESS"
    | "REKOGNITION"
    | "RESILIENCEHUB"
    | "RESOURCE_EXPLORER_2"
    | "RESOURCE_GROUPS"
    | "ROBOMAKER"
    | "ROLESANYWHERE"
    | "ROUTE53"
    | "ROUTE53DOMAINS"
    | "ROUTE53PROFILES"
    | "ROUTE53RESOLVER"
    | "ROUTE53_RECOVERY_CONTROL_CONFIG"
    | "RUM"
    | "RUNTIME_LEX"
    | "RUNTIME_SAGEMAKER"
    | "RUNTIME_V2_LEX"
    | "S3"
    | "S3_CONTROL"
    | "S3_OUTPOSTS"
    | "SAGEMAKER"
    | "SAGEMAKER_GEOSPATIAL"
    | "SAVINGSPLANS"
    | "SCHEDULER"
    | "SCHEMAS"
    | "SDB"
    | "SECRETSMANAGER"
    | "SECURITYHUB"
    | "SECURITYLAKE"
    | "SERVERLESSREPO"
    | "SERVICECATALOG"
    | "SERVICECATALOG_APPREGISTRY"
    | "SERVICEDISCOVERY"
    | "SERVICEQUOTAS"
    | "SESSION_QLDB"
    | "SHIELD"
    | "SIGNER"
    | "SIMSPACEWEAVER"
    | "SMS"
    | "SMS_VOICE"
    | "SNOWBALL"
    | "SNS"
    | "SQS"
    | "SSM"
    | "SSM_CONTACTS"
    | "SSM_INCIDENTS"
    | "SSM_QUICKSETUP"
    | "SSM_SAP"
    | "SSO"
    | "STATES"
    | "STORAGEGATEWAY"
    | "STREAMS_DYNAMODB"
    | "STS"
    | "SUPPORT"
    | "SUPPORTAPP"
    | "SWF"
    | "SYNTHETICS"
    | "TAGGING"
    | "TAX"
    | "TEXTRACT"
    | "THINCLIENT"
    | "TNB"
    | "TRANSCRIBE"
    | "TRANSCRIBESTREAMING"
    | "TRANSFER"
    | "TRANSLATE"
    | "TRUSTEDADVISOR"
    | "VERIFIEDPERMISSIONS"
    | "VOICEID"
    | "VOICE_CHIME"
    | "VPC_LATTICE"
    | "WAF"
    | "WAFV2"
    | "WAF_REGIONAL"
    | "WELLARCHITECTED"
    | "WISDOM"
    | "WORKDOCS"
    | "WORKMAIL"
    | "WORKSPACES"
    | "WORKSPACES_WEB"
    | "XRAY";
export const TYPE_SERVICE_LOOKUP = {
    A4B: "a4b",
    ACCESS_ANALYZER: "access-analyzer",
    ACCOUNT: "account",
    ACM: "acm",
    ACM_PCA: "acm-pca",
    AGREEMENT_MARKETPLACE: "agreement-marketplace",
    AIRFLOW: "airflow",
    AMPLIFY: "amplify",
    AMPLIFYBACKEND: "amplifybackend",
    AMPLIFYUIBUILDER: "amplifyuibuilder",
    AOSS: "aoss",
    API_DETECTIVE: "api.detective",
    API_ECR: "api.ecr",
    API_ECR_PUBLIC: "api.ecr-public",
    API_ELASTIC_INFERENCE: "api.elastic-inference",
    API_FLEETHUB_IOT: "api.fleethub.iot",
    API_IOTDEVICEADVISOR: "api.iotdeviceadvisor",
    API_IOTWIRELESS: "api.iotwireless",
    API_MEDIATAILOR: "api.mediatailor",
    API_PRICING: "api.pricing",
    API_SAGEMAKER: "api.sagemaker",
    API_TUNNELING_IOT: "api.tunneling.iot",
    APIGATEWAY: "apigateway",
    APP_INTEGRATIONS: "app-integrations",
    APPCONFIG: "appconfig",
    APPCONFIGDATA: "appconfigdata",
    APPFLOW: "appflow",
    APPLICATION_AUTOSCALING: "application-autoscaling",
    APPLICATIONINSIGHTS: "applicationinsights",
    APPMESH: "appmesh",
    APPRUNNER: "apprunner",
    APPSTREAM2: "appstream2",
    APPSYNC: "appsync",
    APS: "aps",
    ARC_ZONAL_SHIFT: "arc-zonal-shift",
    ATHENA: "athena",
    AUDITMANAGER: "auditmanager",
    AUTOSCALING: "autoscaling",
    AUTOSCALING_PLANS: "autoscaling-plans",
    BACKUP: "backup",
    BACKUP_GATEWAY: "backup-gateway",
    BACKUPSTORAGE: "backupstorage",
    BATCH: "batch",
    BEDROCK: "bedrock",
    BILLINGCONDUCTOR: "billingconductor",
    BRAKET: "braket",
    BUDGETS: "budgets",
    CASES: "cases",
    CASSANDRA: "cassandra",
    CATALOG_MARKETPLACE: "catalog.marketplace",
    CE: "ce",
    CHIME: "chime",
    CLEANROOMS: "cleanrooms",
    CLOUD9: "cloud9",
    CLOUDCONTROLAPI: "cloudcontrolapi",
    CLOUDDIRECTORY: "clouddirectory",
    CLOUDFORMATION: "cloudformation",
    CLOUDFRONT: "cloudfront",
    CLOUDHSM: "cloudhsm",
    CLOUDHSMV2: "cloudhsmv2",
    CLOUDSEARCH: "cloudsearch",
    CLOUDTRAIL: "cloudtrail",
    CLOUDTRAIL_DATA: "cloudtrail-data",
    CODEARTIFACT: "codeartifact",
    CODEBUILD: "codebuild",
    CODECATALYST: "codecatalyst",
    CODECOMMIT: "codecommit",
    CODEDEPLOY: "codedeploy",
    CODEGURU_PROFILER: "codeguru-profiler",
    CODEGURU_REVIEWER: "codeguru-reviewer",
    CODEPIPELINE: "codepipeline",
    CODESTAR: "codestar",
    CODESTAR_CONNECTIONS: "codestar-connections",
    CODESTAR_NOTIFICATIONS: "codestar-notifications",
    COGNITO_HOSTED_UI: "cognito-hosted-ui",
    COGNITO_IDENTITY: "cognito-identity",
    COGNITO_IDP: "cognito-idp",
    COGNITO_SYNC: "cognito-sync",
    COMPREHEND: "comprehend",
    COMPREHENDMEDICAL: "comprehendmedical",
    COMPUTE_OPTIMIZER: "compute-optimizer",
    CONFIG: "config",
    CONNECT: "connect",
    CONNECT_CAMPAIGNS: "connect-campaigns",
    CONTACT_LENS: "contact-lens",
    CONTROLTOWER: "controltower",
    COST_OPTIMIZATION_HUB: "cost-optimization-hub",
    CUR: "cur",
    DATA_ATS_IOT: "data-ats.iot",
    DATA_IOT: "data.iot",
    DATA_JOBS_IOT: "data.jobs.iot",
    DATA_MEDIASTORE: "data.mediastore",
    DATABREW: "databrew",
    DATAEXCHANGE: "dataexchange",
    DATAPIPELINE: "datapipeline",
    DATASYNC: "datasync",
    DATAZONE: "datazone",
    DAX: "dax",
    DEVICEFARM: "devicefarm",
    DEVOPS_GURU: "devops-guru",
    DIRECTCONNECT: "directconnect",
    DISCOVERY: "discovery",
    DLM: "dlm",
    DMS: "dms",
    DOCDB: "docdb",
    DRS: "drs",
    DS: "ds",
    DYNAMODB: "dynamodb",
    EBS: "ebs",
    EC2: "ec2",
    ECR_DKR: "ecr-dkr",
    ECS: "ecs",
    ECS_TASKS: "ecs-tasks",
    EDGE_SAGEMAKER: "edge.sagemaker",
    EKS: "eks",
    EKS_AUTH: "eks-auth",
    ELASTICACHE: "elasticache",
    ELASTICBEANSTALK: "elasticbeanstalk",
    ELASTICFILESYSTEM: "elasticfilesystem",
    ELASTICLOADBALANCING: "elasticloadbalancing",
    ELASTICMAPREDUCE: "elasticmapreduce",
    ELASTICTRANSCODER: "elastictranscoder",
    EMAIL: "email",
    EMR_CONTAINERS: "emr-containers",
    EMR_SERVERLESS: "emr-serverless",
    ENTITLEMENT_MARKETPLACE: "entitlement.marketplace",
    ES: "es",
    EVENTS: "events",
    EVIDENTLY: "evidently",
    EXECUTE_API: "execute-api",
    FINSPACE: "finspace",
    FINSPACE_API: "finspace-api",
    FIREHOSE: "firehose",
    FMS: "fms",
    FORECAST: "forecast",
    FORECASTQUERY: "forecastquery",
    FRAUDDETECTOR: "frauddetector",
    FSX: "fsx",
    GAMELIFT: "gamelift",
    GAMELIFTSTREAMS: "gameliftstreams",
    GAMESPARKS: "gamesparks",
    GEO: "geo",
    GLACIER: "glacier",
    GLOBALACCELERATOR: "globalaccelerator",
    GLUE: "glue",
    GRAFANA: "grafana",
    GREENGRASS: "greengrass",
    GROUNDSTATION: "groundstation",
    GUARDDUTY: "guardduty",
    HEALTH: "health",
    HEALTHLAKE: "healthlake",
    HONEYCODE: "honeycode",
    IAM: "iam",
    IDENTITY_CHIME: "identity-chime",
    IDENTITYSTORE: "identitystore",
    IMPORTEXPORT: "importexport",
    INGEST_TIMESTREAM: "ingest.timestream",
    INSPECTOR: "inspector",
    INSPECTOR2: "inspector2",
    INTERNETMONITOR: "internetmonitor",
    IOT: "iot",
    IOTANALYTICS: "iotanalytics",
    IOTEVENTS: "iotevents",
    IOTEVENTSDATA: "ioteventsdata",
    IOTFLEETWISE: "iotfleetwise",
    IOTROBORUNNER: "iotroborunner",
    IOTSECUREDTUNNELING: "iotsecuredtunneling",
    IOTSITEWISE: "iotsitewise",
    IOTTHINGSGRAPH: "iotthingsgraph",
    IOTTWINMAKER: "iottwinmaker",
    IOTWIRELESS: "iotwireless",
    IVS: "ivs",
    IVSCHAT: "ivschat",
    IVSREALTIME: "ivsrealtime",
    KAFKA: "kafka",
    KAFKACONNECT: "kafkaconnect",
    KENDRA: "kendra",
    KENDRA_RANKING: "kendra-ranking",
    KINESIS: "kinesis",
    KINESISANALYTICS: "kinesisanalytics",
    KINESISVIDEO: "kinesisvideo",
    KMS: "kms",
    LAKEFORMATION: "lakeformation",
    LAMBDA: "lambda",
    LICENSE_MANAGER: "license-manager",
    LICENSE_MANAGER_LINUX_SUBSCRIPTIONS: "license-manager-linux-subscriptions",
    LICENSE_MANAGER_USER_SUBSCRIPTIONS: "license-manager-user-subscriptions",
    LIGHTSAIL: "lightsail",
    LOGS: "logs",
    LOOKOUTEQUIPMENT: "lookoutequipment",
    LOOKOUTMETRICS: "lookoutmetrics",
    LOOKOUTVISION: "lookoutvision",
    M2: "m2",
    MACHINELEARNING: "machinelearning",
    MACIE: "macie",
    MACIE2: "macie2",
    MANAGEDBLOCKCHAIN: "managedblockchain",
    MANAGEDBLOCKCHAIN_QUERY: "managedblockchain-query",
    MARKETPLACECOMMERCEANALYTICS: "marketplacecommerceanalytics",
    MEDIA_PIPELINES_CHIME: "media-pipelines-chime",
    MEDIACONNECT: "mediaconnect",
    MEDIACONVERT: "mediaconvert",
    MEDIALIVE: "medialive",
    MEDIAPACKAGE: "mediapackage",
    MEDIAPACKAGE_VOD: "mediapackage-vod",
    MEDIAPACKAGEV2: "mediapackagev2",
    MEDIASTORE: "mediastore",
    MEETINGS_CHIME: "meetings-chime",
    MEMORY_DB: "memory-db",
    MESSAGING_CHIME: "messaging-chime",
    METERING_MARKETPLACE: "metering.marketplace",
    METRICS_SAGEMAKER: "metrics.sagemaker",
    MGH: "mgh",
    MGN: "mgn",
    MIGRATIONHUB_ORCHESTRATOR: "migrationhub-orchestrator",
    MIGRATIONHUB_STRATEGY: "migrationhub-strategy",
    MOBILEANALYTICS: "mobileanalytics",
    MODELS_V2_LEX: "models-v2-lex",
    MODELS_LEX: "models.lex",
    MONITORING: "monitoring",
    MQ: "mq",
    MTURK_REQUESTER: "mturk-requester",
    NEPTUNE: "neptune",
    NETWORK_FIREWALL: "network-firewall",
    NETWORKMANAGER: "networkmanager",
    NIMBLE: "nimble",
    NOTIFICATIONS: "notifications",
    NOTIFICATIONS_CONTACTS: "notifications-contacts",
    NOVA_ACT: "nova-act",
    OAM: "oam",
    OIDC: "oidc",
    OMICS: "omics",
    OPSWORKS: "opsworks",
    OPSWORKS_CM: "opsworks-cm",
    ORGANIZATIONS: "organizations",
    OSIS: "osis",
    OUTPOSTS: "outposts",
    PARTICIPANT_CONNECT: "participant.connect",
    PARTNERCENTRAL_CHANNEL: "partnercentral-channel",
    PERSONALIZE: "personalize",
    PI: "pi",
    PINPOINT: "pinpoint",
    PIPES: "pipes",
    POLLY: "polly",
    PORTAL_SSO: "portal.sso",
    PROFILE: "profile",
    PROJECTS_IOT1CLICK: "projects.iot1click",
    PROTON: "proton",
    QBUSINESS: "qbusiness",
    QLDB: "qldb",
    QUERY_TIMESTREAM: "query.timestream",
    QUICKSIGHT: "quicksight",
    RAM: "ram",
    RBIN: "rbin",
    RDS: "rds",
    RDS_DATA: "rds-data",
    REDSHIFT: "redshift",
    REDSHIFT_SERVERLESS: "redshift-serverless",
    REKOGNITION: "rekognition",
    RESILIENCEHUB: "resiliencehub",
    RESOURCE_EXPLORER_2: "resource-explorer-2",
    RESOURCE_GROUPS: "resource-groups",
    ROBOMAKER: "robomaker",
    ROLESANYWHERE: "rolesanywhere",
    ROUTE53: "route53",
    ROUTE53_RECOVERY_CONTROL_CONFIG: "route53-recovery-control-config",
    ROUTE53DOMAINS: "route53domains",
    ROUTE53PROFILES: "route53profiles",
    ROUTE53RESOLVER: "route53resolver",
    RUM: "rum",
    RUNTIME_V2_LEX: "runtime-v2-lex",
    RUNTIME_LEX: "runtime.lex",
    RUNTIME_SAGEMAKER: "runtime.sagemaker",
    S3: "s3",
    S3_CONTROL: "s3-control",
    S3_OUTPOSTS: "s3-outposts",
    SAGEMAKER: "sagemaker",
    SAGEMAKER_GEOSPATIAL: "sagemaker-geospatial",
    SAVINGSPLANS: "savingsplans",
    SCHEDULER: "scheduler",
    SCHEMAS: "schemas",
    SDB: "sdb",
    SECRETSMANAGER: "secretsmanager",
    SECURITYHUB: "securityhub",
    SECURITYLAKE: "securitylake",
    SERVERLESSREPO: "serverlessrepo",
    SERVICECATALOG: "servicecatalog",
    SERVICECATALOG_APPREGISTRY: "servicecatalog-appregistry",
    SERVICEDISCOVERY: "servicediscovery",
    SERVICEQUOTAS: "servicequotas",
    SESSION_QLDB: "session.qldb",
    SHIELD: "shield",
    SIGNER: "signer",
    SIMSPACEWEAVER: "simspaceweaver",
    SMS: "sms",
    SMS_VOICE: "sms-voice",
    SNOWBALL: "snowball",
    SNS: "sns",
    SQS: "sqs",
    SSM: "ssm",
    SSM_CONTACTS: "ssm-contacts",
    SSM_INCIDENTS: "ssm-incidents",
    SSM_QUICKSETUP: "ssm-quicksetup",
    SSM_SAP: "ssm-sap",
    SSO: "sso",
    STATES: "states",
    STORAGEGATEWAY: "storagegateway",
    STREAMS_DYNAMODB: "streams.dynamodb",
    STS: "sts",
    SUPPORT: "support",
    SUPPORTAPP: "supportapp",
    SWF: "swf",
    SYNTHETICS: "synthetics",
    TAGGING: "tagging",
    TAX: "tax",
    TEXTRACT: "textract",
    THINCLIENT: "thinclient",
    TNB: "tnb",
    TRANSCRIBE: "transcribe",
    TRANSCRIBESTREAMING: "transcribestreaming",
    TRANSFER: "transfer",
    TRANSLATE: "translate",
    TRUSTEDADVISOR: "trustedadvisor",
    VERIFIEDPERMISSIONS: "verifiedpermissions",
    VOICE_CHIME: "voice-chime",
    VOICEID: "voiceid",
    VPC_LATTICE: "vpc-lattice",
    WAF: "waf",
    WAF_REGIONAL: "waf-regional",
    WAFV2: "wafv2",
    WELLARCHITECTED: "wellarchitected",
    WISDOM: "wisdom",
    WORKDOCS: "workdocs",
    WORKMAIL: "workmail",
    WORKSPACES: "workspaces",
    WORKSPACES_WEB: "workspaces-web",
    XRAY: "xray",
};
export interface IServiceInfo {
    arn: string;
    principal: string;
    hostname: string;
    fipsHostname: string;
}
export const SERVICE_LOOKUP: { [key: string]: { [partition: string]: IServiceInfo } } = {
    a4b: {
        aws: {
            arn: "arn:aws:a4b:{region}:{account-id}:{resource-id}",
            principal: "a4b.amazonaws.com",
            hostname: "a4b.{region}.amazonaws.com",
            fipsHostname: "a4b-fips.{region}.amazonaws.com",
        },
    },
    "access-analyzer": {
        aws: {
            arn: "arn:aws:access-analyzer:{region}:{account-id}:{resource-id}",
            principal: "access-analyzer.amazonaws.com",
            hostname: "access-analyzer.{region}.amazonaws.com",
            fipsHostname: "access-analyzer-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:access-analyzer:{region}:{account-id}:{resource-id}",
            principal: "access-analyzer.amazonaws.com.cn",
            hostname: "access-analyzer.{region}.amazonaws.com.cn",
            fipsHostname: "access-analyzer-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:access-analyzer:{region}:{account-id}:{resource-id}",
            principal: "access-analyzer.amazonaws.com",
            hostname: "access-analyzer.{region}.amazonaws.com",
            fipsHostname: "access-analyzer-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:access-analyzer:{region}:{account-id}:{resource-id}",
            principal: "access-analyzer.cloud.adc-e.uk",
            hostname: "access-analyzer.{region}.cloud.adc-e.uk",
            fipsHostname: "access-analyzer-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:access-analyzer:{region}:{account-id}:{resource-id}",
            principal: "access-analyzer.csp.hci.ic.gov",
            hostname: "access-analyzer.{region}.csp.hci.ic.gov",
            fipsHostname: "access-analyzer-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:access-analyzer:{region}:{account-id}:{resource-id}",
            principal: "access-analyzer.amazonaws.com",
            hostname: "access-analyzer.{region}.amazonaws.eu",
            fipsHostname: "access-analyzer-fips.{region}.amazonaws.eu",
        },
    },
    account: {
        aws: {
            arn: "arn:aws:account:{region}:{account-id}:{resource-id}",
            principal: "account.amazonaws.com",
            hostname: "account.{region}.amazonaws.com",
            fipsHostname: "account-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:account:{region}:{account-id}:{resource-id}",
            principal: "account.amazonaws.com.cn",
            hostname: "account.{region}.amazonaws.com.cn",
            fipsHostname: "account-fips.{region}.amazonaws.com.cn",
        },
    },
    acm: {
        aws: {
            arn: "arn:aws:acm:{region}:{account-id}:{resource-id}",
            principal: "acm.amazonaws.com",
            hostname: "acm.{region}.amazonaws.com",
            fipsHostname: "acm-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:acm:{region}:{account-id}:{resource-id}",
            principal: "acm.amazonaws.com.cn",
            hostname: "acm.{region}.amazonaws.com.cn",
            fipsHostname: "acm-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:acm:{region}:{account-id}:{resource-id}",
            principal: "acm.amazonaws.com",
            hostname: "acm.{region}.amazonaws.com",
            fipsHostname: "acm-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:acm:{region}:{account-id}:{resource-id}",
            principal: "acm.c2s.ic.gov",
            hostname: "acm.{region}.c2s.ic.gov",
            fipsHostname: "acm-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:acm:{region}:{account-id}:{resource-id}",
            principal: "acm.sc2s.sgov.gov",
            hostname: "acm.{region}.sc2s.sgov.gov",
            fipsHostname: "acm-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:acm:{region}:{account-id}:{resource-id}",
            principal: "acm.cloud.adc-e.uk",
            hostname: "acm.{region}.cloud.adc-e.uk",
            fipsHostname: "acm-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:acm:{region}:{account-id}:{resource-id}",
            principal: "acm.csp.hci.ic.gov",
            hostname: "acm.{region}.csp.hci.ic.gov",
            fipsHostname: "acm-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:acm:{region}:{account-id}:{resource-id}",
            principal: "acm.amazonaws.com",
            hostname: "acm.{region}.amazonaws.eu",
            fipsHostname: "acm-fips.{region}.amazonaws.eu",
        },
    },
    "acm-pca": {
        aws: {
            arn: "arn:aws:acm-pca:{region}:{account-id}:{resource-id}",
            principal: "acm-pca.amazonaws.com",
            hostname: "acm-pca.{region}.amazonaws.com",
            fipsHostname: "acm-pca-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:acm-pca:{region}:{account-id}:{resource-id}",
            principal: "acm-pca.amazonaws.com",
            hostname: "acm-pca.{region}.amazonaws.com",
            fipsHostname: "acm-pca-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:acm-pca:{region}:{account-id}:{resource-id}",
            principal: "acm-pca.amazonaws.com.cn",
            hostname: "acm-pca.{region}.amazonaws.com.cn",
            fipsHostname: "acm-pca-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso": {
            arn: "arn:aws-iso:acm-pca:{region}:{account-id}:{resource-id}",
            principal: "acm-pca.c2s.ic.gov",
            hostname: "acm-pca.{region}.c2s.ic.gov",
            fipsHostname: "acm-pca-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:acm-pca:{region}:{account-id}:{resource-id}",
            principal: "acm-pca.cloud.adc-e.uk",
            hostname: "acm-pca.{region}.cloud.adc-e.uk",
            fipsHostname: "acm-pca-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:acm-pca:{region}:{account-id}:{resource-id}",
            principal: "acm-pca.csp.hci.ic.gov",
            hostname: "acm-pca.{region}.csp.hci.ic.gov",
            fipsHostname: "acm-pca-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:acm-pca:{region}:{account-id}:{resource-id}",
            principal: "acm-pca.amazonaws.com",
            hostname: "acm-pca.{region}.amazonaws.eu",
            fipsHostname: "acm-pca-fips.{region}.amazonaws.eu",
        },
    },
    "agreement-marketplace": {
        aws: {
            arn: "arn:aws:agreement-marketplace:{region}:{account-id}:{resource-id}",
            principal: "agreement-marketplace.amazonaws.com",
            hostname: "agreement-marketplace.{region}.amazonaws.com",
            fipsHostname: "agreement-marketplace-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:agreement-marketplace:{region}:{account-id}:{resource-id}",
            principal: "agreement-marketplace.c2s.ic.gov",
            hostname: "agreement-marketplace.{region}.c2s.ic.gov",
            fipsHostname: "agreement-marketplace-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:agreement-marketplace:{region}:{account-id}:{resource-id}",
            principal: "agreement-marketplace.sc2s.sgov.gov",
            hostname: "agreement-marketplace.{region}.sc2s.sgov.gov",
            fipsHostname: "agreement-marketplace-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:agreement-marketplace:{region}:{account-id}:{resource-id}",
            principal: "agreement-marketplace.csp.hci.ic.gov",
            hostname: "agreement-marketplace.{region}.csp.hci.ic.gov",
            fipsHostname: "agreement-marketplace-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:agreement-marketplace:{region}:{account-id}:{resource-id}",
            principal: "agreement-marketplace.amazonaws.com",
            hostname: "agreement-marketplace.{region}.amazonaws.eu",
            fipsHostname: "agreement-marketplace-fips.{region}.amazonaws.eu",
        },
    },
    airflow: {
        aws: {
            arn: "arn:aws:airflow:{region}:{account-id}:{resource-id}",
            principal: "airflow.amazonaws.com",
            hostname: "airflow.{region}.amazonaws.com",
            fipsHostname: "airflow-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:airflow:{region}:{account-id}:{resource-id}",
            principal: "airflow.amazonaws.com.cn",
            hostname: "airflow.{region}.amazonaws.com.cn",
            fipsHostname: "airflow-fips.{region}.amazonaws.com.cn",
        },
    },
    amplify: {
        aws: {
            arn: "arn:aws:amplify:{region}:{account-id}:{resource-id}",
            principal: "amplify.amazonaws.com",
            hostname: "amplify.{region}.amazonaws.com",
            fipsHostname: "amplify-fips.{region}.amazonaws.com",
        },
    },
    amplifybackend: {
        aws: {
            arn: "arn:aws:amplifybackend:{region}:{account-id}:{resource-id}",
            principal: "amplifybackend.amazonaws.com",
            hostname: "amplifybackend.{region}.amazonaws.com",
            fipsHostname: "amplifybackend-fips.{region}.amazonaws.com",
        },
    },
    amplifyuibuilder: {
        aws: {
            arn: "arn:aws:amplifyuibuilder:{region}:{account-id}:{resource-id}",
            principal: "amplifyuibuilder.amazonaws.com",
            hostname: "amplifyuibuilder.{region}.amazonaws.com",
            fipsHostname: "amplifyuibuilder-fips.{region}.amazonaws.com",
        },
    },
    aoss: {
        aws: {
            arn: "arn:aws:aoss:{region}:{account-id}:{resource-id}",
            principal: "aoss.amazonaws.com",
            hostname: "aoss.{region}.amazonaws.com",
            fipsHostname: "aoss-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:aoss:{region}:{account-id}:{resource-id}",
            principal: "aoss.amazonaws.com",
            hostname: "aoss.{region}.amazonaws.com",
            fipsHostname: "aoss-fips.{region}.amazonaws.com",
        },
    },
    "api.detective": {
        aws: {
            arn: "arn:aws:api.detective:{region}:{account-id}:{resource-id}",
            principal: "api.detective.amazonaws.com",
            hostname: "api.detective.{region}.amazonaws.com",
            fipsHostname: "api.detective-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:api.detective:{region}:{account-id}:{resource-id}",
            principal: "api.detective.amazonaws.com",
            hostname: "api.detective.{region}.amazonaws.com",
            fipsHostname: "api.detective-fips.{region}.amazonaws.com",
        },
    },
    "api.ecr": {
        aws: {
            arn: "arn:aws:api.ecr:{region}:{account-id}:{resource-id}",
            principal: "api.ecr.amazonaws.com",
            hostname: "api.ecr.{region}.amazonaws.com",
            fipsHostname: "api.ecr-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:api.ecr:{region}:{account-id}:{resource-id}",
            principal: "api.ecr.amazonaws.com.cn",
            hostname: "api.ecr.{region}.amazonaws.com.cn",
            fipsHostname: "api.ecr-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:api.ecr:{region}:{account-id}:{resource-id}",
            principal: "api.ecr.amazonaws.com",
            hostname: "api.ecr.{region}.amazonaws.com",
            fipsHostname: "api.ecr-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:api.ecr:{region}:{account-id}:{resource-id}",
            principal: "api.ecr.c2s.ic.gov",
            hostname: "api.ecr.{region}.c2s.ic.gov",
            fipsHostname: "api.ecr-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:api.ecr:{region}:{account-id}:{resource-id}",
            principal: "api.ecr.sc2s.sgov.gov",
            hostname: "api.ecr.{region}.sc2s.sgov.gov",
            fipsHostname: "api.ecr-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:api.ecr:{region}:{account-id}:{resource-id}",
            principal: "api.ecr.cloud.adc-e.uk",
            hostname: "api.ecr.{region}.cloud.adc-e.uk",
            fipsHostname: "api.ecr-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:api.ecr:{region}:{account-id}:{resource-id}",
            principal: "api.ecr.csp.hci.ic.gov",
            hostname: "api.ecr.{region}.csp.hci.ic.gov",
            fipsHostname: "api.ecr-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:api.ecr:{region}:{account-id}:{resource-id}",
            principal: "api.ecr.amazonaws.com",
            hostname: "api.ecr.{region}.amazonaws.eu",
            fipsHostname: "api.ecr-fips.{region}.amazonaws.eu",
        },
    },
    "api.ecr-public": {
        aws: {
            arn: "arn:aws:api.ecr-public:{region}:{account-id}:{resource-id}",
            principal: "api.ecr-public.amazonaws.com",
            hostname: "api.ecr-public.{region}.amazonaws.com",
            fipsHostname: "api.ecr-public-fips.{region}.amazonaws.com",
        },
    },
    "api.elastic-inference": {
        aws: {
            arn: "arn:aws:api.elastic-inference:{region}:{account-id}:{resource-id}",
            principal: "api.elastic-inference.amazonaws.com",
            hostname: "api.elastic-inference.{region}.amazonaws.com",
            fipsHostname: "api.elastic-inference-fips.{region}.amazonaws.com",
        },
    },
    "api.fleethub.iot": {
        aws: {
            arn: "arn:aws:api.fleethub.iot:{region}:{account-id}:{resource-id}",
            principal: "api.fleethub.iot.amazonaws.com",
            hostname: "api.fleethub.iot.{region}.amazonaws.com",
            fipsHostname: "api.fleethub.iot-fips.{region}.amazonaws.com",
        },
    },
    "api.iotdeviceadvisor": {
        aws: {
            arn: "arn:aws:api.iotdeviceadvisor:{region}:{account-id}:{resource-id}",
            principal: "api.iotdeviceadvisor.amazonaws.com",
            hostname: "api.iotdeviceadvisor.{region}.amazonaws.com",
            fipsHostname: "api.iotdeviceadvisor-fips.{region}.amazonaws.com",
        },
    },
    "api.iotwireless": {
        aws: {
            arn: "arn:aws:api.iotwireless:{region}:{account-id}:{resource-id}",
            principal: "api.iotwireless.amazonaws.com",
            hostname: "api.iotwireless.{region}.amazonaws.com",
            fipsHostname: "api.iotwireless-fips.{region}.amazonaws.com",
        },
    },
    "api.mediatailor": {
        aws: {
            arn: "arn:aws:api.mediatailor:{region}:{account-id}:{resource-id}",
            principal: "api.mediatailor.amazonaws.com",
            hostname: "api.mediatailor.{region}.amazonaws.com",
            fipsHostname: "api.mediatailor-fips.{region}.amazonaws.com",
        },
    },
    "api.pricing": {
        aws: {
            arn: "arn:aws:api.pricing:{region}:{account-id}:{resource-id}",
            principal: "api.pricing.amazonaws.com",
            hostname: "api.pricing.{region}.amazonaws.com",
            fipsHostname: "api.pricing-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:api.pricing:{region}:{account-id}:{resource-id}",
            principal: "api.pricing.amazonaws.com.cn",
            hostname: "api.pricing.{region}.amazonaws.com.cn",
            fipsHostname: "api.pricing-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso": {
            arn: "arn:aws-iso:api.pricing:{region}:{account-id}:{resource-id}",
            principal: "api.pricing.c2s.ic.gov",
            hostname: "api.pricing.{region}.c2s.ic.gov",
            fipsHostname: "api.pricing-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:api.pricing:{region}:{account-id}:{resource-id}",
            principal: "api.pricing.sc2s.sgov.gov",
            hostname: "api.pricing.{region}.sc2s.sgov.gov",
            fipsHostname: "api.pricing-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:api.pricing:{region}:{account-id}:{resource-id}",
            principal: "api.pricing.cloud.adc-e.uk",
            hostname: "api.pricing.{region}.cloud.adc-e.uk",
            fipsHostname: "api.pricing-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:api.pricing:{region}:{account-id}:{resource-id}",
            principal: "api.pricing.csp.hci.ic.gov",
            hostname: "api.pricing.{region}.csp.hci.ic.gov",
            fipsHostname: "api.pricing-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:api.pricing:{region}:{account-id}:{resource-id}",
            principal: "api.pricing.amazonaws.com",
            hostname: "api.pricing.{region}.amazonaws.eu",
            fipsHostname: "api.pricing-fips.{region}.amazonaws.eu",
        },
    },
    "api.sagemaker": {
        aws: {
            arn: "arn:aws:api.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "api.sagemaker.amazonaws.com",
            hostname: "api.sagemaker.{region}.amazonaws.com",
            fipsHostname: "api.sagemaker-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:api.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "api.sagemaker.amazonaws.com.cn",
            hostname: "api.sagemaker.{region}.amazonaws.com.cn",
            fipsHostname: "api.sagemaker-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:api.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "api.sagemaker.amazonaws.com",
            hostname: "api.sagemaker.{region}.amazonaws.com",
            fipsHostname: "api.sagemaker-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:api.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "api.sagemaker.c2s.ic.gov",
            hostname: "api.sagemaker.{region}.c2s.ic.gov",
            fipsHostname: "api.sagemaker-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:api.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "api.sagemaker.sc2s.sgov.gov",
            hostname: "api.sagemaker.{region}.sc2s.sgov.gov",
            fipsHostname: "api.sagemaker-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:api.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "api.sagemaker.cloud.adc-e.uk",
            hostname: "api.sagemaker.{region}.cloud.adc-e.uk",
            fipsHostname: "api.sagemaker-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:api.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "api.sagemaker.csp.hci.ic.gov",
            hostname: "api.sagemaker.{region}.csp.hci.ic.gov",
            fipsHostname: "api.sagemaker-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:api.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "api.sagemaker.amazonaws.com",
            hostname: "api.sagemaker.{region}.amazonaws.eu",
            fipsHostname: "api.sagemaker-fips.{region}.amazonaws.eu",
        },
    },
    "api.tunneling.iot": {
        aws: {
            arn: "arn:aws:api.tunneling.iot:{region}:{account-id}:{resource-id}",
            principal: "api.tunneling.iot.amazonaws.com",
            hostname: "api.tunneling.iot.{region}.amazonaws.com",
            fipsHostname: "api.tunneling.iot-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:api.tunneling.iot:{region}:{account-id}:{resource-id}",
            principal: "api.tunneling.iot.amazonaws.com.cn",
            hostname: "api.tunneling.iot.{region}.amazonaws.com.cn",
            fipsHostname: "api.tunneling.iot-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:api.tunneling.iot:{region}:{account-id}:{resource-id}",
            principal: "api.tunneling.iot.amazonaws.com",
            hostname: "api.tunneling.iot.{region}.amazonaws.com",
            fipsHostname: "api.tunneling.iot-fips.{region}.amazonaws.com",
        },
    },
    apigateway: {
        aws: {
            arn: "arn:aws:apigateway:{region}:{account-id}:{resource-id}",
            principal: "apigateway.amazonaws.com",
            hostname: "apigateway.{region}.amazonaws.com",
            fipsHostname: "apigateway-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:apigateway:{region}:{account-id}:{resource-id}",
            principal: "apigateway.amazonaws.com.cn",
            hostname: "apigateway.{region}.amazonaws.com.cn",
            fipsHostname: "apigateway-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:apigateway:{region}:{account-id}:{resource-id}",
            principal: "apigateway.amazonaws.com",
            hostname: "apigateway.{region}.amazonaws.com",
            fipsHostname: "apigateway-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:apigateway:{region}:{account-id}:{resource-id}",
            principal: "apigateway.c2s.ic.gov",
            hostname: "apigateway.{region}.c2s.ic.gov",
            fipsHostname: "apigateway-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:apigateway:{region}:{account-id}:{resource-id}",
            principal: "apigateway.sc2s.sgov.gov",
            hostname: "apigateway.{region}.sc2s.sgov.gov",
            fipsHostname: "apigateway-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:apigateway:{region}:{account-id}:{resource-id}",
            principal: "apigateway.cloud.adc-e.uk",
            hostname: "apigateway.{region}.cloud.adc-e.uk",
            fipsHostname: "apigateway-fips.{region}.cloud.adc-e.uk",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:apigateway:{region}:{account-id}:{resource-id}",
            principal: "apigateway.amazonaws.com",
            hostname: "apigateway.{region}.amazonaws.eu",
            fipsHostname: "apigateway-fips.{region}.amazonaws.eu",
        },
    },
    "app-integrations": {
        aws: {
            arn: "arn:aws:app-integrations:{region}:{account-id}:{resource-id}",
            principal: "app-integrations.amazonaws.com",
            hostname: "app-integrations.{region}.amazonaws.com",
            fipsHostname: "app-integrations-fips.{region}.amazonaws.com",
        },
    },
    appconfig: {
        aws: {
            arn: "arn:aws:appconfig:{region}:{account-id}:{resource-id}",
            principal: "appconfig.amazonaws.com",
            hostname: "appconfig.{region}.amazonaws.com",
            fipsHostname: "appconfig-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:appconfig:{region}:{account-id}:{resource-id}",
            principal: "appconfig.amazonaws.com.cn",
            hostname: "appconfig.{region}.amazonaws.com.cn",
            fipsHostname: "appconfig-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:appconfig:{region}:{account-id}:{resource-id}",
            principal: "appconfig.amazonaws.com",
            hostname: "appconfig.{region}.amazonaws.com",
            fipsHostname: "appconfig-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:appconfig:{region}:{account-id}:{resource-id}",
            principal: "appconfig.c2s.ic.gov",
            hostname: "appconfig.{region}.c2s.ic.gov",
            fipsHostname: "appconfig-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:appconfig:{region}:{account-id}:{resource-id}",
            principal: "appconfig.sc2s.sgov.gov",
            hostname: "appconfig.{region}.sc2s.sgov.gov",
            fipsHostname: "appconfig-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:appconfig:{region}:{account-id}:{resource-id}",
            principal: "appconfig.cloud.adc-e.uk",
            hostname: "appconfig.{region}.cloud.adc-e.uk",
            fipsHostname: "appconfig-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:appconfig:{region}:{account-id}:{resource-id}",
            principal: "appconfig.csp.hci.ic.gov",
            hostname: "appconfig.{region}.csp.hci.ic.gov",
            fipsHostname: "appconfig-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:appconfig:{region}:{account-id}:{resource-id}",
            principal: "appconfig.amazonaws.com",
            hostname: "appconfig.{region}.amazonaws.eu",
            fipsHostname: "appconfig-fips.{region}.amazonaws.eu",
        },
    },
    appconfigdata: {
        aws: {
            arn: "arn:aws:appconfigdata:{region}:{account-id}:{resource-id}",
            principal: "appconfigdata.amazonaws.com",
            hostname: "appconfigdata.{region}.amazonaws.com",
            fipsHostname: "appconfigdata-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:appconfigdata:{region}:{account-id}:{resource-id}",
            principal: "appconfigdata.amazonaws.com.cn",
            hostname: "appconfigdata.{region}.amazonaws.com.cn",
            fipsHostname: "appconfigdata-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:appconfigdata:{region}:{account-id}:{resource-id}",
            principal: "appconfigdata.amazonaws.com",
            hostname: "appconfigdata.{region}.amazonaws.com",
            fipsHostname: "appconfigdata-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:appconfigdata:{region}:{account-id}:{resource-id}",
            principal: "appconfigdata.c2s.ic.gov",
            hostname: "appconfigdata.{region}.c2s.ic.gov",
            fipsHostname: "appconfigdata-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:appconfigdata:{region}:{account-id}:{resource-id}",
            principal: "appconfigdata.sc2s.sgov.gov",
            hostname: "appconfigdata.{region}.sc2s.sgov.gov",
            fipsHostname: "appconfigdata-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:appconfigdata:{region}:{account-id}:{resource-id}",
            principal: "appconfigdata.cloud.adc-e.uk",
            hostname: "appconfigdata.{region}.cloud.adc-e.uk",
            fipsHostname: "appconfigdata-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:appconfigdata:{region}:{account-id}:{resource-id}",
            principal: "appconfigdata.csp.hci.ic.gov",
            hostname: "appconfigdata.{region}.csp.hci.ic.gov",
            fipsHostname: "appconfigdata-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:appconfigdata:{region}:{account-id}:{resource-id}",
            principal: "appconfigdata.amazonaws.com",
            hostname: "appconfigdata.{region}.amazonaws.eu",
            fipsHostname: "appconfigdata-fips.{region}.amazonaws.eu",
        },
    },
    appflow: {
        aws: {
            arn: "arn:aws:appflow:{region}:{account-id}:{resource-id}",
            principal: "appflow.amazonaws.com",
            hostname: "appflow.{region}.amazonaws.com",
            fipsHostname: "appflow-fips.{region}.amazonaws.com",
        },
    },
    "application-autoscaling": {
        aws: {
            arn: "arn:aws:application-autoscaling:{region}:{account-id}:{resource-id}",
            principal: "application-autoscaling.amazonaws.com",
            hostname: "application-autoscaling.{region}.amazonaws.com",
            fipsHostname: "application-autoscaling-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:application-autoscaling:{region}:{account-id}:{resource-id}",
            principal: "application-autoscaling.amazonaws.com.cn",
            hostname: "application-autoscaling.{region}.amazonaws.com.cn",
            fipsHostname: "application-autoscaling-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:application-autoscaling:{region}:{account-id}:{resource-id}",
            principal: "application-autoscaling.amazonaws.com",
            hostname: "application-autoscaling.{region}.amazonaws.com",
            fipsHostname: "application-autoscaling-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:application-autoscaling:{region}:{account-id}:{resource-id}",
            principal: "application-autoscaling.c2s.ic.gov",
            hostname: "application-autoscaling.{region}.c2s.ic.gov",
            fipsHostname: "application-autoscaling-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:application-autoscaling:{region}:{account-id}:{resource-id}",
            principal: "application-autoscaling.sc2s.sgov.gov",
            hostname: "application-autoscaling.{region}.sc2s.sgov.gov",
            fipsHostname: "application-autoscaling-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:application-autoscaling:{region}:{account-id}:{resource-id}",
            principal: "application-autoscaling.cloud.adc-e.uk",
            hostname: "application-autoscaling.{region}.cloud.adc-e.uk",
            fipsHostname: "application-autoscaling-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:application-autoscaling:{region}:{account-id}:{resource-id}",
            principal: "application-autoscaling.csp.hci.ic.gov",
            hostname: "application-autoscaling.{region}.csp.hci.ic.gov",
            fipsHostname: "application-autoscaling-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:application-autoscaling:{region}:{account-id}:{resource-id}",
            principal: "application-autoscaling.amazonaws.com",
            hostname: "application-autoscaling.{region}.amazonaws.eu",
            fipsHostname: "application-autoscaling-fips.{region}.amazonaws.eu",
        },
    },
    applicationinsights: {
        aws: {
            arn: "arn:aws:applicationinsights:{region}:{account-id}:{resource-id}",
            principal: "applicationinsights.amazonaws.com",
            hostname: "applicationinsights.{region}.amazonaws.com",
            fipsHostname: "applicationinsights-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:applicationinsights:{region}:{account-id}:{resource-id}",
            principal: "applicationinsights.amazonaws.com.cn",
            hostname: "applicationinsights.{region}.amazonaws.com.cn",
            fipsHostname: "applicationinsights-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:applicationinsights:{region}:{account-id}:{resource-id}",
            principal: "applicationinsights.amazonaws.com",
            hostname: "applicationinsights.{region}.amazonaws.com",
            fipsHostname: "applicationinsights-fips.{region}.amazonaws.com",
        },
    },
    appmesh: {
        aws: {
            arn: "arn:aws:appmesh:{region}:{account-id}:{resource-id}",
            principal: "appmesh.amazonaws.com",
            hostname: "appmesh.{region}.amazonaws.com",
            fipsHostname: "appmesh-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:appmesh:{region}:{account-id}:{resource-id}",
            principal: "appmesh.amazonaws.com.cn",
            hostname: "appmesh.{region}.amazonaws.com.cn",
            fipsHostname: "appmesh-fips.{region}.amazonaws.com.cn",
        },
    },
    apprunner: {
        aws: {
            arn: "arn:aws:apprunner:{region}:{account-id}:{resource-id}",
            principal: "apprunner.amazonaws.com",
            hostname: "apprunner.{region}.amazonaws.com",
            fipsHostname: "apprunner-fips.{region}.amazonaws.com",
        },
    },
    appstream2: {
        aws: {
            arn: "arn:aws:appstream2:{region}:{account-id}:{resource-id}",
            principal: "appstream2.amazonaws.com",
            hostname: "appstream2.{region}.amazonaws.com",
            fipsHostname: "appstream2-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:appstream2:{region}:{account-id}:{resource-id}",
            principal: "appstream2.amazonaws.com",
            hostname: "appstream2.{region}.amazonaws.com",
            fipsHostname: "appstream2-fips.{region}.amazonaws.com",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:appstream2:{region}:{account-id}:{resource-id}",
            principal: "appstream2.sc2s.sgov.gov",
            hostname: "appstream2.{region}.sc2s.sgov.gov",
            fipsHostname: "appstream2-fips.{region}.sc2s.sgov.gov",
        },
    },
    appsync: {
        aws: {
            arn: "arn:aws:appsync:{region}:{account-id}:{resource-id}",
            principal: "appsync.amazonaws.com",
            hostname: "appsync.{region}.amazonaws.com",
            fipsHostname: "appsync-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:appsync:{region}:{account-id}:{resource-id}",
            principal: "appsync.amazonaws.com.cn",
            hostname: "appsync.{region}.amazonaws.com.cn",
            fipsHostname: "appsync-fips.{region}.amazonaws.com.cn",
        },
    },
    aps: {
        aws: {
            arn: "arn:aws:aps:{region}:{account-id}:{resource-id}",
            principal: "aps.amazonaws.com",
            hostname: "aps.{region}.amazonaws.com",
            fipsHostname: "aps-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:aps:{region}:{account-id}:{resource-id}",
            principal: "aps.amazonaws.com",
            hostname: "aps.{region}.amazonaws.com",
            fipsHostname: "aps-fips.{region}.amazonaws.com",
        },
    },
    "arc-zonal-shift": {
        aws: {
            arn: "arn:aws:arc-zonal-shift:{region}:{account-id}:{resource-id}",
            principal: "arc-zonal-shift.amazonaws.com",
            hostname: "arc-zonal-shift.{region}.amazonaws.com",
            fipsHostname: "arc-zonal-shift-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:arc-zonal-shift:{region}:{account-id}:{resource-id}",
            principal: "arc-zonal-shift.amazonaws.com.cn",
            hostname: "arc-zonal-shift.{region}.amazonaws.com.cn",
            fipsHostname: "arc-zonal-shift-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:arc-zonal-shift:{region}:{account-id}:{resource-id}",
            principal: "arc-zonal-shift.amazonaws.com",
            hostname: "arc-zonal-shift.{region}.amazonaws.com",
            fipsHostname: "arc-zonal-shift-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:arc-zonal-shift:{region}:{account-id}:{resource-id}",
            principal: "arc-zonal-shift.c2s.ic.gov",
            hostname: "arc-zonal-shift.{region}.c2s.ic.gov",
            fipsHostname: "arc-zonal-shift-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:arc-zonal-shift:{region}:{account-id}:{resource-id}",
            principal: "arc-zonal-shift.sc2s.sgov.gov",
            hostname: "arc-zonal-shift.{region}.sc2s.sgov.gov",
            fipsHostname: "arc-zonal-shift-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:arc-zonal-shift:{region}:{account-id}:{resource-id}",
            principal: "arc-zonal-shift.cloud.adc-e.uk",
            hostname: "arc-zonal-shift.{region}.cloud.adc-e.uk",
            fipsHostname: "arc-zonal-shift-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:arc-zonal-shift:{region}:{account-id}:{resource-id}",
            principal: "arc-zonal-shift.csp.hci.ic.gov",
            hostname: "arc-zonal-shift.{region}.csp.hci.ic.gov",
            fipsHostname: "arc-zonal-shift-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:arc-zonal-shift:{region}:{account-id}:{resource-id}",
            principal: "arc-zonal-shift.amazonaws.com",
            hostname: "arc-zonal-shift.{region}.amazonaws.eu",
            fipsHostname: "arc-zonal-shift-fips.{region}.amazonaws.eu",
        },
    },
    athena: {
        aws: {
            arn: "arn:aws:athena:{region}:{account-id}:{resource-id}",
            principal: "athena.amazonaws.com",
            hostname: "athena.{region}.amazonaws.com",
            fipsHostname: "athena-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:athena:{region}:{account-id}:{resource-id}",
            principal: "athena.amazonaws.com.cn",
            hostname: "athena.{region}.amazonaws.com.cn",
            fipsHostname: "athena-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:athena:{region}:{account-id}:{resource-id}",
            principal: "athena.amazonaws.com",
            hostname: "athena.{region}.amazonaws.com",
            fipsHostname: "athena-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:athena:{region}:{account-id}:{resource-id}",
            principal: "athena.c2s.ic.gov",
            hostname: "athena.{region}.c2s.ic.gov",
            fipsHostname: "athena-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:athena:{region}:{account-id}:{resource-id}",
            principal: "athena.sc2s.sgov.gov",
            hostname: "athena.{region}.sc2s.sgov.gov",
            fipsHostname: "athena-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:athena:{region}:{account-id}:{resource-id}",
            principal: "athena.cloud.adc-e.uk",
            hostname: "athena.{region}.cloud.adc-e.uk",
            fipsHostname: "athena-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:athena:{region}:{account-id}:{resource-id}",
            principal: "athena.csp.hci.ic.gov",
            hostname: "athena.{region}.csp.hci.ic.gov",
            fipsHostname: "athena-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:athena:{region}:{account-id}:{resource-id}",
            principal: "athena.amazonaws.com",
            hostname: "athena.{region}.amazonaws.eu",
            fipsHostname: "athena-fips.{region}.amazonaws.eu",
        },
    },
    auditmanager: {
        aws: {
            arn: "arn:aws:auditmanager:{region}:{account-id}:{resource-id}",
            principal: "auditmanager.amazonaws.com",
            hostname: "auditmanager.{region}.amazonaws.com",
            fipsHostname: "auditmanager-fips.{region}.amazonaws.com",
        },
    },
    autoscaling: {
        aws: {
            arn: "arn:aws:autoscaling:{region}:{account-id}:{resource-id}",
            principal: "autoscaling.amazonaws.com",
            hostname: "autoscaling.{region}.amazonaws.com",
            fipsHostname: "autoscaling-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:autoscaling:{region}:{account-id}:{resource-id}",
            principal: "autoscaling.amazonaws.com.cn",
            hostname: "autoscaling.{region}.amazonaws.com.cn",
            fipsHostname: "autoscaling-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:autoscaling:{region}:{account-id}:{resource-id}",
            principal: "autoscaling.amazonaws.com",
            hostname: "autoscaling.{region}.amazonaws.com",
            fipsHostname: "autoscaling-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:autoscaling:{region}:{account-id}:{resource-id}",
            principal: "autoscaling.c2s.ic.gov",
            hostname: "autoscaling.{region}.c2s.ic.gov",
            fipsHostname: "autoscaling-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:autoscaling:{region}:{account-id}:{resource-id}",
            principal: "autoscaling.sc2s.sgov.gov",
            hostname: "autoscaling.{region}.sc2s.sgov.gov",
            fipsHostname: "autoscaling-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:autoscaling:{region}:{account-id}:{resource-id}",
            principal: "autoscaling.cloud.adc-e.uk",
            hostname: "autoscaling.{region}.cloud.adc-e.uk",
            fipsHostname: "autoscaling-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:autoscaling:{region}:{account-id}:{resource-id}",
            principal: "autoscaling.csp.hci.ic.gov",
            hostname: "autoscaling.{region}.csp.hci.ic.gov",
            fipsHostname: "autoscaling-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:autoscaling:{region}:{account-id}:{resource-id}",
            principal: "autoscaling.amazonaws.com",
            hostname: "autoscaling.{region}.amazonaws.eu",
            fipsHostname: "autoscaling-fips.{region}.amazonaws.eu",
        },
    },
    "autoscaling-plans": {
        aws: {
            arn: "arn:aws:autoscaling-plans:{region}:{account-id}:{resource-id}",
            principal: "autoscaling-plans.amazonaws.com",
            hostname: "autoscaling-plans.{region}.amazonaws.com",
            fipsHostname: "autoscaling-plans-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:autoscaling-plans:{region}:{account-id}:{resource-id}",
            principal: "autoscaling-plans.amazonaws.com.cn",
            hostname: "autoscaling-plans.{region}.amazonaws.com.cn",
            fipsHostname: "autoscaling-plans-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:autoscaling-plans:{region}:{account-id}:{resource-id}",
            principal: "autoscaling-plans.amazonaws.com",
            hostname: "autoscaling-plans.{region}.amazonaws.com",
            fipsHostname: "autoscaling-plans-fips.{region}.amazonaws.com",
        },
    },
    backup: {
        aws: {
            arn: "arn:aws:backup:{region}:{account-id}:{resource-id}",
            principal: "backup.amazonaws.com",
            hostname: "backup.{region}.amazonaws.com",
            fipsHostname: "backup-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:backup:{region}:{account-id}:{resource-id}",
            principal: "backup.amazonaws.com.cn",
            hostname: "backup.{region}.amazonaws.com.cn",
            fipsHostname: "backup-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:backup:{region}:{account-id}:{resource-id}",
            principal: "backup.amazonaws.com",
            hostname: "backup.{region}.amazonaws.com",
            fipsHostname: "backup-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:backup:{region}:{account-id}:{resource-id}",
            principal: "backup.c2s.ic.gov",
            hostname: "backup.{region}.c2s.ic.gov",
            fipsHostname: "backup-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:backup:{region}:{account-id}:{resource-id}",
            principal: "backup.sc2s.sgov.gov",
            hostname: "backup.{region}.sc2s.sgov.gov",
            fipsHostname: "backup-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:backup:{region}:{account-id}:{resource-id}",
            principal: "backup.csp.hci.ic.gov",
            hostname: "backup.{region}.csp.hci.ic.gov",
            fipsHostname: "backup-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:backup:{region}:{account-id}:{resource-id}",
            principal: "backup.amazonaws.com",
            hostname: "backup.{region}.amazonaws.eu",
            fipsHostname: "backup-fips.{region}.amazonaws.eu",
        },
    },
    "backup-gateway": {
        aws: {
            arn: "arn:aws:backup-gateway:{region}:{account-id}:{resource-id}",
            principal: "backup-gateway.amazonaws.com",
            hostname: "backup-gateway.{region}.amazonaws.com",
            fipsHostname: "backup-gateway-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:backup-gateway:{region}:{account-id}:{resource-id}",
            principal: "backup-gateway.amazonaws.com",
            hostname: "backup-gateway.{region}.amazonaws.com",
            fipsHostname: "backup-gateway-fips.{region}.amazonaws.com",
        },
    },
    backupstorage: {
        aws: {
            arn: "arn:aws:backupstorage:{region}:{account-id}:{resource-id}",
            principal: "backupstorage.amazonaws.com",
            hostname: "backupstorage.{region}.amazonaws.com",
            fipsHostname: "backupstorage-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:backupstorage:{region}:{account-id}:{resource-id}",
            principal: "backupstorage.amazonaws.com.cn",
            hostname: "backupstorage.{region}.amazonaws.com.cn",
            fipsHostname: "backupstorage-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:backupstorage:{region}:{account-id}:{resource-id}",
            principal: "backupstorage.amazonaws.com",
            hostname: "backupstorage.{region}.amazonaws.com",
            fipsHostname: "backupstorage-fips.{region}.amazonaws.com",
        },
    },
    batch: {
        aws: {
            arn: "arn:aws:batch:{region}:{account-id}:{resource-id}",
            principal: "batch.amazonaws.com",
            hostname: "batch.{region}.amazonaws.com",
            fipsHostname: "batch-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:batch:{region}:{account-id}:{resource-id}",
            principal: "batch.amazonaws.com.cn",
            hostname: "batch.{region}.amazonaws.com.cn",
            fipsHostname: "batch-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:batch:{region}:{account-id}:{resource-id}",
            principal: "batch.amazonaws.com",
            hostname: "batch.{region}.amazonaws.com",
            fipsHostname: "batch-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:batch:{region}:{account-id}:{resource-id}",
            principal: "batch.c2s.ic.gov",
            hostname: "batch.{region}.c2s.ic.gov",
            fipsHostname: "batch-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:batch:{region}:{account-id}:{resource-id}",
            principal: "batch.sc2s.sgov.gov",
            hostname: "batch.{region}.sc2s.sgov.gov",
            fipsHostname: "batch-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:batch:{region}:{account-id}:{resource-id}",
            principal: "batch.cloud.adc-e.uk",
            hostname: "batch.{region}.cloud.adc-e.uk",
            fipsHostname: "batch-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:batch:{region}:{account-id}:{resource-id}",
            principal: "batch.csp.hci.ic.gov",
            hostname: "batch.{region}.csp.hci.ic.gov",
            fipsHostname: "batch-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:batch:{region}:{account-id}:{resource-id}",
            principal: "batch.amazonaws.com",
            hostname: "batch.{region}.amazonaws.eu",
            fipsHostname: "batch-fips.{region}.amazonaws.eu",
        },
    },
    bedrock: {
        aws: {
            arn: "arn:aws:bedrock:{region}:{account-id}:{resource-id}",
            principal: "bedrock.amazonaws.com",
            hostname: "bedrock.{region}.amazonaws.com",
            fipsHostname: "bedrock-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:bedrock:{region}:{account-id}:{resource-id}",
            principal: "bedrock.amazonaws.com",
            hostname: "bedrock.{region}.amazonaws.com",
            fipsHostname: "bedrock-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:bedrock:{region}:{account-id}:{resource-id}",
            principal: "bedrock.c2s.ic.gov",
            hostname: "bedrock.{region}.c2s.ic.gov",
            fipsHostname: "bedrock-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:bedrock:{region}:{account-id}:{resource-id}",
            principal: "bedrock.sc2s.sgov.gov",
            hostname: "bedrock.{region}.sc2s.sgov.gov",
            fipsHostname: "bedrock-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:bedrock:{region}:{account-id}:{resource-id}",
            principal: "bedrock.csp.hci.ic.gov",
            hostname: "bedrock.{region}.csp.hci.ic.gov",
            fipsHostname: "bedrock-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:bedrock:{region}:{account-id}:{resource-id}",
            principal: "bedrock.amazonaws.com",
            hostname: "bedrock.{region}.amazonaws.eu",
            fipsHostname: "bedrock-fips.{region}.amazonaws.eu",
        },
    },
    billingconductor: {
        aws: {
            arn: "arn:aws:billingconductor:{region}:{account-id}:{resource-id}",
            principal: "billingconductor.amazonaws.com",
            hostname: "billingconductor.{region}.amazonaws.com",
            fipsHostname: "billingconductor-fips.{region}.amazonaws.com",
        },
    },
    braket: {
        aws: {
            arn: "arn:aws:braket:{region}:{account-id}:{resource-id}",
            principal: "braket.amazonaws.com",
            hostname: "braket.{region}.amazonaws.com",
            fipsHostname: "braket-fips.{region}.amazonaws.com",
        },
    },
    budgets: {
        aws: {
            arn: "arn:aws:budgets:{region}:{account-id}:{resource-id}",
            principal: "budgets.amazonaws.com",
            hostname: "budgets.{region}.amazonaws.com",
            fipsHostname: "budgets-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:budgets:{region}:{account-id}:{resource-id}",
            principal: "budgets.amazonaws.com.cn",
            hostname: "budgets.{region}.amazonaws.com.cn",
            fipsHostname: "budgets-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso": {
            arn: "arn:aws-iso:budgets:{region}:{account-id}:{resource-id}",
            principal: "budgets.c2s.ic.gov",
            hostname: "budgets.{region}.c2s.ic.gov",
            fipsHostname: "budgets-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:budgets:{region}:{account-id}:{resource-id}",
            principal: "budgets.sc2s.sgov.gov",
            hostname: "budgets.{region}.sc2s.sgov.gov",
            fipsHostname: "budgets-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:budgets:{region}:{account-id}:{resource-id}",
            principal: "budgets.cloud.adc-e.uk",
            hostname: "budgets.{region}.cloud.adc-e.uk",
            fipsHostname: "budgets-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:budgets:{region}:{account-id}:{resource-id}",
            principal: "budgets.csp.hci.ic.gov",
            hostname: "budgets.{region}.csp.hci.ic.gov",
            fipsHostname: "budgets-fips.{region}.csp.hci.ic.gov",
        },
    },
    cases: {
        aws: {
            arn: "arn:aws:cases:{region}:{account-id}:{resource-id}",
            principal: "cases.amazonaws.com",
            hostname: "cases.{region}.amazonaws.com",
            fipsHostname: "cases-fips.{region}.amazonaws.com",
        },
    },
    cassandra: {
        aws: {
            arn: "arn:aws:cassandra:{region}:{account-id}:{resource-id}",
            principal: "cassandra.amazonaws.com",
            hostname: "cassandra.{region}.amazonaws.com",
            fipsHostname: "cassandra-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:cassandra:{region}:{account-id}:{resource-id}",
            principal: "cassandra.amazonaws.com.cn",
            hostname: "cassandra.{region}.amazonaws.com.cn",
            fipsHostname: "cassandra-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:cassandra:{region}:{account-id}:{resource-id}",
            principal: "cassandra.amazonaws.com",
            hostname: "cassandra.{region}.amazonaws.com",
            fipsHostname: "cassandra-fips.{region}.amazonaws.com",
        },
    },
    "catalog.marketplace": {
        aws: {
            arn: "arn:aws:catalog.marketplace:{region}:{account-id}:{resource-id}",
            principal: "catalog.marketplace.amazonaws.com",
            hostname: "catalog.marketplace.{region}.amazonaws.com",
            fipsHostname: "catalog.marketplace-fips.{region}.amazonaws.com",
        },
    },
    ce: {
        aws: {
            arn: "arn:aws:ce:{region}:{account-id}:{resource-id}",
            principal: "ce.amazonaws.com",
            hostname: "ce.{region}.amazonaws.com",
            fipsHostname: "ce-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ce:{region}:{account-id}:{resource-id}",
            principal: "ce.amazonaws.com.cn",
            hostname: "ce.{region}.amazonaws.com.cn",
            fipsHostname: "ce-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso": {
            arn: "arn:aws-iso:ce:{region}:{account-id}:{resource-id}",
            principal: "ce.c2s.ic.gov",
            hostname: "ce.{region}.c2s.ic.gov",
            fipsHostname: "ce-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:ce:{region}:{account-id}:{resource-id}",
            principal: "ce.sc2s.sgov.gov",
            hostname: "ce.{region}.sc2s.sgov.gov",
            fipsHostname: "ce-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ce:{region}:{account-id}:{resource-id}",
            principal: "ce.csp.hci.ic.gov",
            hostname: "ce.{region}.csp.hci.ic.gov",
            fipsHostname: "ce-fips.{region}.csp.hci.ic.gov",
        },
    },
    chime: {
        aws: {
            arn: "arn:aws:chime:{region}:{account-id}:{resource-id}",
            principal: "chime.amazonaws.com",
            hostname: "chime.{region}.amazonaws.com",
            fipsHostname: "chime-fips.{region}.amazonaws.com",
        },
    },
    cleanrooms: {
        aws: {
            arn: "arn:aws:cleanrooms:{region}:{account-id}:{resource-id}",
            principal: "cleanrooms.amazonaws.com",
            hostname: "cleanrooms.{region}.amazonaws.com",
            fipsHostname: "cleanrooms-fips.{region}.amazonaws.com",
        },
    },
    cloud9: {
        aws: {
            arn: "arn:aws:cloud9:{region}:{account-id}:{resource-id}",
            principal: "cloud9.amazonaws.com",
            hostname: "cloud9.{region}.amazonaws.com",
            fipsHostname: "cloud9-fips.{region}.amazonaws.com",
        },
    },
    cloudcontrolapi: {
        aws: {
            arn: "arn:aws:cloudcontrolapi:{region}:{account-id}:{resource-id}",
            principal: "cloudcontrolapi.amazonaws.com",
            hostname: "cloudcontrolapi.{region}.amazonaws.com",
            fipsHostname: "cloudcontrolapi-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:cloudcontrolapi:{region}:{account-id}:{resource-id}",
            principal: "cloudcontrolapi.amazonaws.com.cn",
            hostname: "cloudcontrolapi.{region}.amazonaws.com.cn",
            fipsHostname: "cloudcontrolapi-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:cloudcontrolapi:{region}:{account-id}:{resource-id}",
            principal: "cloudcontrolapi.amazonaws.com",
            hostname: "cloudcontrolapi.{region}.amazonaws.com",
            fipsHostname: "cloudcontrolapi-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:cloudcontrolapi:{region}:{account-id}:{resource-id}",
            principal: "cloudcontrolapi.c2s.ic.gov",
            hostname: "cloudcontrolapi.{region}.c2s.ic.gov",
            fipsHostname: "cloudcontrolapi-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:cloudcontrolapi:{region}:{account-id}:{resource-id}",
            principal: "cloudcontrolapi.sc2s.sgov.gov",
            hostname: "cloudcontrolapi.{region}.sc2s.sgov.gov",
            fipsHostname: "cloudcontrolapi-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:cloudcontrolapi:{region}:{account-id}:{resource-id}",
            principal: "cloudcontrolapi.cloud.adc-e.uk",
            hostname: "cloudcontrolapi.{region}.cloud.adc-e.uk",
            fipsHostname: "cloudcontrolapi-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:cloudcontrolapi:{region}:{account-id}:{resource-id}",
            principal: "cloudcontrolapi.csp.hci.ic.gov",
            hostname: "cloudcontrolapi.{region}.csp.hci.ic.gov",
            fipsHostname: "cloudcontrolapi-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:cloudcontrolapi:{region}:{account-id}:{resource-id}",
            principal: "cloudcontrolapi.amazonaws.com",
            hostname: "cloudcontrolapi.{region}.amazonaws.eu",
            fipsHostname: "cloudcontrolapi-fips.{region}.amazonaws.eu",
        },
    },
    clouddirectory: {
        aws: {
            arn: "arn:aws:clouddirectory:{region}:{account-id}:{resource-id}",
            principal: "clouddirectory.amazonaws.com",
            hostname: "clouddirectory.{region}.amazonaws.com",
            fipsHostname: "clouddirectory-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:clouddirectory:{region}:{account-id}:{resource-id}",
            principal: "clouddirectory.amazonaws.com",
            hostname: "clouddirectory.{region}.amazonaws.com",
            fipsHostname: "clouddirectory-fips.{region}.amazonaws.com",
        },
    },
    cloudformation: {
        aws: {
            arn: "arn:aws:cloudformation:{region}:{account-id}:{resource-id}",
            principal: "cloudformation.amazonaws.com",
            hostname: "cloudformation.{region}.amazonaws.com",
            fipsHostname: "cloudformation-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:cloudformation:{region}:{account-id}:{resource-id}",
            principal: "cloudformation.amazonaws.com.cn",
            hostname: "cloudformation.{region}.amazonaws.com.cn",
            fipsHostname: "cloudformation-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:cloudformation:{region}:{account-id}:{resource-id}",
            principal: "cloudformation.amazonaws.com",
            hostname: "cloudformation.{region}.amazonaws.com",
            fipsHostname: "cloudformation-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:cloudformation:{region}:{account-id}:{resource-id}",
            principal: "cloudformation.c2s.ic.gov",
            hostname: "cloudformation.{region}.c2s.ic.gov",
            fipsHostname: "cloudformation-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:cloudformation:{region}:{account-id}:{resource-id}",
            principal: "cloudformation.sc2s.sgov.gov",
            hostname: "cloudformation.{region}.sc2s.sgov.gov",
            fipsHostname: "cloudformation-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:cloudformation:{region}:{account-id}:{resource-id}",
            principal: "cloudformation.cloud.adc-e.uk",
            hostname: "cloudformation.{region}.cloud.adc-e.uk",
            fipsHostname: "cloudformation-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:cloudformation:{region}:{account-id}:{resource-id}",
            principal: "cloudformation.csp.hci.ic.gov",
            hostname: "cloudformation.{region}.csp.hci.ic.gov",
            fipsHostname: "cloudformation-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:cloudformation:{region}:{account-id}:{resource-id}",
            principal: "cloudformation.amazonaws.com",
            hostname: "cloudformation.{region}.amazonaws.eu",
            fipsHostname: "cloudformation-fips.{region}.amazonaws.eu",
        },
    },
    cloudfront: {
        aws: {
            arn: "arn:aws:cloudfront:{region}:{account-id}:{resource-id}",
            principal: "cloudfront.amazonaws.com",
            hostname: "cloudfront.{region}.amazonaws.com",
            fipsHostname: "cloudfront-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:cloudfront:{region}:{account-id}:{resource-id}",
            principal: "cloudfront.amazonaws.com.cn",
            hostname: "cloudfront.{region}.amazonaws.com.cn",
            fipsHostname: "cloudfront-fips.{region}.amazonaws.com.cn",
        },
    },
    cloudhsm: {
        aws: {
            arn: "arn:aws:cloudhsm:{region}:{account-id}:{resource-id}",
            principal: "cloudhsm.amazonaws.com",
            hostname: "cloudhsm.{region}.amazonaws.com",
            fipsHostname: "cloudhsm-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:cloudhsm:{region}:{account-id}:{resource-id}",
            principal: "cloudhsm.amazonaws.com",
            hostname: "cloudhsm.{region}.amazonaws.com",
            fipsHostname: "cloudhsm-fips.{region}.amazonaws.com",
        },
    },
    cloudhsmv2: {
        aws: {
            arn: "arn:aws:cloudhsmv2:{region}:{account-id}:{resource-id}",
            principal: "cloudhsmv2.amazonaws.com",
            hostname: "cloudhsmv2.{region}.amazonaws.com",
            fipsHostname: "cloudhsmv2-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:cloudhsmv2:{region}:{account-id}:{resource-id}",
            principal: "cloudhsmv2.amazonaws.com",
            hostname: "cloudhsmv2.{region}.amazonaws.com",
            fipsHostname: "cloudhsmv2-fips.{region}.amazonaws.com",
        },
    },
    cloudsearch: {
        aws: {
            arn: "arn:aws:cloudsearch:{region}:{account-id}:{resource-id}",
            principal: "cloudsearch.amazonaws.com",
            hostname: "cloudsearch.{region}.amazonaws.com",
            fipsHostname: "cloudsearch-fips.{region}.amazonaws.com",
        },
    },
    cloudtrail: {
        aws: {
            arn: "arn:aws:cloudtrail:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail.amazonaws.com",
            hostname: "cloudtrail.{region}.amazonaws.com",
            fipsHostname: "cloudtrail-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:cloudtrail:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail.amazonaws.com.cn",
            hostname: "cloudtrail.{region}.amazonaws.com.cn",
            fipsHostname: "cloudtrail-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:cloudtrail:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail.amazonaws.com",
            hostname: "cloudtrail.{region}.amazonaws.com",
            fipsHostname: "cloudtrail-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:cloudtrail:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail.c2s.ic.gov",
            hostname: "cloudtrail.{region}.c2s.ic.gov",
            fipsHostname: "cloudtrail-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:cloudtrail:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail.sc2s.sgov.gov",
            hostname: "cloudtrail.{region}.sc2s.sgov.gov",
            fipsHostname: "cloudtrail-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:cloudtrail:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail.cloud.adc-e.uk",
            hostname: "cloudtrail.{region}.cloud.adc-e.uk",
            fipsHostname: "cloudtrail-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:cloudtrail:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail.csp.hci.ic.gov",
            hostname: "cloudtrail.{region}.csp.hci.ic.gov",
            fipsHostname: "cloudtrail-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:cloudtrail:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail.amazonaws.com",
            hostname: "cloudtrail.{region}.amazonaws.eu",
            fipsHostname: "cloudtrail-fips.{region}.amazonaws.eu",
        },
    },
    "cloudtrail-data": {
        aws: {
            arn: "arn:aws:cloudtrail-data:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail-data.amazonaws.com",
            hostname: "cloudtrail-data.{region}.amazonaws.com",
            fipsHostname: "cloudtrail-data-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:cloudtrail-data:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail-data.cloud.adc-e.uk",
            hostname: "cloudtrail-data.{region}.cloud.adc-e.uk",
            fipsHostname: "cloudtrail-data-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:cloudtrail-data:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail-data.csp.hci.ic.gov",
            hostname: "cloudtrail-data.{region}.csp.hci.ic.gov",
            fipsHostname: "cloudtrail-data-fips.{region}.csp.hci.ic.gov",
        },
    },
    codeartifact: {
        aws: {
            arn: "arn:aws:codeartifact:{region}:{account-id}:{resource-id}",
            principal: "codeartifact.amazonaws.com",
            hostname: "codeartifact.{region}.amazonaws.com",
            fipsHostname: "codeartifact-fips.{region}.amazonaws.com",
        },
    },
    codebuild: {
        aws: {
            arn: "arn:aws:codebuild:{region}:{account-id}:{resource-id}",
            principal: "codebuild.amazonaws.com",
            hostname: "codebuild.{region}.amazonaws.com",
            fipsHostname: "codebuild-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:codebuild:{region}:{account-id}:{resource-id}",
            principal: "codebuild.amazonaws.com.cn",
            hostname: "codebuild.{region}.amazonaws.com.cn",
            fipsHostname: "codebuild-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:codebuild:{region}:{account-id}:{resource-id}",
            principal: "codebuild.amazonaws.com",
            hostname: "codebuild.{region}.amazonaws.com",
            fipsHostname: "codebuild-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:codebuild:{region}:{account-id}:{resource-id}",
            principal: "codebuild.c2s.ic.gov",
            hostname: "codebuild.{region}.c2s.ic.gov",
            fipsHostname: "codebuild-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:codebuild:{region}:{account-id}:{resource-id}",
            principal: "codebuild.sc2s.sgov.gov",
            hostname: "codebuild.{region}.sc2s.sgov.gov",
            fipsHostname: "codebuild-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:codebuild:{region}:{account-id}:{resource-id}",
            principal: "codebuild.csp.hci.ic.gov",
            hostname: "codebuild.{region}.csp.hci.ic.gov",
            fipsHostname: "codebuild-fips.{region}.csp.hci.ic.gov",
        },
    },
    codecatalyst: {
        aws: {
            arn: "arn:aws:codecatalyst:{region}:{account-id}:{resource-id}",
            principal: "codecatalyst.amazonaws.com",
            hostname: "codecatalyst.{region}.amazonaws.com",
            fipsHostname: "codecatalyst-fips.{region}.amazonaws.com",
        },
    },
    codecommit: {
        aws: {
            arn: "arn:aws:codecommit:{region}:{account-id}:{resource-id}",
            principal: "codecommit.amazonaws.com",
            hostname: "codecommit.{region}.amazonaws.com",
            fipsHostname: "codecommit-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:codecommit:{region}:{account-id}:{resource-id}",
            principal: "codecommit.amazonaws.com.cn",
            hostname: "codecommit.{region}.amazonaws.com.cn",
            fipsHostname: "codecommit-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:codecommit:{region}:{account-id}:{resource-id}",
            principal: "codecommit.amazonaws.com",
            hostname: "codecommit.{region}.amazonaws.com",
            fipsHostname: "codecommit-fips.{region}.amazonaws.com",
        },
    },
    codedeploy: {
        aws: {
            arn: "arn:aws:codedeploy:{region}:{account-id}:{resource-id}",
            principal: "codedeploy.amazonaws.com",
            hostname: "codedeploy.{region}.amazonaws.com",
            fipsHostname: "codedeploy-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:codedeploy:{region}:{account-id}:{resource-id}",
            principal: "codedeploy.amazonaws.com.cn",
            hostname: "codedeploy.{region}.amazonaws.com.cn",
            fipsHostname: "codedeploy-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:codedeploy:{region}:{account-id}:{resource-id}",
            principal: "codedeploy.amazonaws.com",
            hostname: "codedeploy.{region}.amazonaws.com",
            fipsHostname: "codedeploy-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:codedeploy:{region}:{account-id}:{resource-id}",
            principal: "codedeploy.c2s.ic.gov",
            hostname: "codedeploy.{region}.c2s.ic.gov",
            fipsHostname: "codedeploy-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:codedeploy:{region}:{account-id}:{resource-id}",
            principal: "codedeploy.sc2s.sgov.gov",
            hostname: "codedeploy.{region}.sc2s.sgov.gov",
            fipsHostname: "codedeploy-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:codedeploy:{region}:{account-id}:{resource-id}",
            principal: "codedeploy.cloud.adc-e.uk",
            hostname: "codedeploy.{region}.cloud.adc-e.uk",
            fipsHostname: "codedeploy-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:codedeploy:{region}:{account-id}:{resource-id}",
            principal: "codedeploy.csp.hci.ic.gov",
            hostname: "codedeploy.{region}.csp.hci.ic.gov",
            fipsHostname: "codedeploy-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:codedeploy:{region}:{account-id}:{resource-id}",
            principal: "codedeploy.amazonaws.com",
            hostname: "codedeploy.{region}.amazonaws.eu",
            fipsHostname: "codedeploy-fips.{region}.amazonaws.eu",
        },
    },
    "codeguru-profiler": {
        aws: {
            arn: "arn:aws:codeguru-profiler:{region}:{account-id}:{resource-id}",
            principal: "codeguru-profiler.amazonaws.com",
            hostname: "codeguru-profiler.{region}.amazonaws.com",
            fipsHostname: "codeguru-profiler-fips.{region}.amazonaws.com",
        },
    },
    "codeguru-reviewer": {
        aws: {
            arn: "arn:aws:codeguru-reviewer:{region}:{account-id}:{resource-id}",
            principal: "codeguru-reviewer.amazonaws.com",
            hostname: "codeguru-reviewer.{region}.amazonaws.com",
            fipsHostname: "codeguru-reviewer-fips.{region}.amazonaws.com",
        },
    },
    codepipeline: {
        aws: {
            arn: "arn:aws:codepipeline:{region}:{account-id}:{resource-id}",
            principal: "codepipeline.amazonaws.com",
            hostname: "codepipeline.{region}.amazonaws.com",
            fipsHostname: "codepipeline-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:codepipeline:{region}:{account-id}:{resource-id}",
            principal: "codepipeline.amazonaws.com.cn",
            hostname: "codepipeline.{region}.amazonaws.com.cn",
            fipsHostname: "codepipeline-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:codepipeline:{region}:{account-id}:{resource-id}",
            principal: "codepipeline.amazonaws.com",
            hostname: "codepipeline.{region}.amazonaws.com",
            fipsHostname: "codepipeline-fips.{region}.amazonaws.com",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:codepipeline:{region}:{account-id}:{resource-id}",
            principal: "codepipeline.csp.hci.ic.gov",
            hostname: "codepipeline.{region}.csp.hci.ic.gov",
            fipsHostname: "codepipeline-fips.{region}.csp.hci.ic.gov",
        },
    },
    codestar: {
        aws: {
            arn: "arn:aws:codestar:{region}:{account-id}:{resource-id}",
            principal: "codestar.amazonaws.com",
            hostname: "codestar.{region}.amazonaws.com",
            fipsHostname: "codestar-fips.{region}.amazonaws.com",
        },
    },
    "codestar-connections": {
        aws: {
            arn: "arn:aws:codestar-connections:{region}:{account-id}:{resource-id}",
            principal: "codestar-connections.amazonaws.com",
            hostname: "codestar-connections.{region}.amazonaws.com",
            fipsHostname: "codestar-connections-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:codestar-connections:{region}:{account-id}:{resource-id}",
            principal: "codestar-connections.amazonaws.com",
            hostname: "codestar-connections.{region}.amazonaws.com",
            fipsHostname: "codestar-connections-fips.{region}.amazonaws.com",
        },
    },
    "codestar-notifications": {
        aws: {
            arn: "arn:aws:codestar-notifications:{region}:{account-id}:{resource-id}",
            principal: "codestar-notifications.amazonaws.com",
            hostname: "codestar-notifications.{region}.amazonaws.com",
            fipsHostname: "codestar-notifications-fips.{region}.amazonaws.com",
        },
    },
    "cognito-identity": {
        aws: {
            arn: "arn:aws:cognito-identity:{region}:{account-id}:{resource-id}",
            principal: "cognito-identity.amazonaws.com",
            hostname: "cognito-identity.{region}.amazonaws.com",
            fipsHostname: "cognito-identity-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:cognito-identity:{region}:{account-id}:{resource-id}",
            principal: "cognito-identity.amazonaws.com.cn",
            hostname: "cognito-identity.{region}.amazonaws.com.cn",
            fipsHostname: "cognito-identity-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:cognito-identity:{region}:{account-id}:{resource-id}",
            principal: "cognito-identity-us-gov.amazonaws.com",
            hostname: "cognito-identity.{region}.amazonaws.com",
            fipsHostname: "cognito-identity-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:cognito-identity:{region}:{account-id}:{resource-id}",
            principal: "cognito-identity.amazonaws.com",
            hostname: "cognito-identity.{region}.amazonaws.eu",
            fipsHostname: "cognito-identity-fips.{region}.amazonaws.eu",
        },
    },
    "cognito-hosted-ui": {
        aws: {
            arn: "",
            principal: "",
            hostname: "auth.{region}.amazoncognito.com",
            fipsHostname: "auth.{region}.amazoncognito.com",
        },
    },
    "cognito-idp": {
        aws: {
            arn: "arn:aws:cognito-idp:{region}:{account-id}:{resource-id}",
            principal: "cognito-idp.amazonaws.com",
            hostname: "cognito-idp.{region}.amazonaws.com",
            fipsHostname: "cognito-idp-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:cognito-idp:{region}:{account-id}:{resource-id}",
            principal: "cognito-idp.amazonaws.com",
            hostname: "cognito-idp.{region}.amazonaws.com",
            fipsHostname: "cognito-idp-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:cognito-idp:{region}:{account-id}:{resource-id}",
            principal: "cognito-idp.amazonaws.com",
            hostname: "cognito-idp.{region}.amazonaws.eu",
            fipsHostname: "cognito-idp-fips.{region}.amazonaws.eu",
        },
    },
    "cognito-sync": {
        aws: {
            arn: "arn:aws:cognito-sync:{region}:{account-id}:{resource-id}",
            principal: "cognito-sync.amazonaws.com",
            hostname: "cognito-sync.{region}.amazonaws.com",
            fipsHostname: "cognito-sync-fips.{region}.amazonaws.com",
        },
    },
    comprehend: {
        aws: {
            arn: "arn:aws:comprehend:{region}:{account-id}:{resource-id}",
            principal: "comprehend.amazonaws.com",
            hostname: "comprehend.{region}.amazonaws.com",
            fipsHostname: "comprehend-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:comprehend:{region}:{account-id}:{resource-id}",
            principal: "comprehend.amazonaws.com",
            hostname: "comprehend.{region}.amazonaws.com",
            fipsHostname: "comprehend-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:comprehend:{region}:{account-id}:{resource-id}",
            principal: "comprehend.c2s.ic.gov",
            hostname: "comprehend.{region}.c2s.ic.gov",
            fipsHostname: "comprehend-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:comprehend:{region}:{account-id}:{resource-id}",
            principal: "comprehend.csp.hci.ic.gov",
            hostname: "comprehend.{region}.csp.hci.ic.gov",
            fipsHostname: "comprehend-fips.{region}.csp.hci.ic.gov",
        },
    },
    comprehendmedical: {
        aws: {
            arn: "arn:aws:comprehendmedical:{region}:{account-id}:{resource-id}",
            principal: "comprehendmedical.amazonaws.com",
            hostname: "comprehendmedical.{region}.amazonaws.com",
            fipsHostname: "comprehendmedical-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:comprehendmedical:{region}:{account-id}:{resource-id}",
            principal: "comprehendmedical.amazonaws.com",
            hostname: "comprehendmedical.{region}.amazonaws.com",
            fipsHostname: "comprehendmedical-fips.{region}.amazonaws.com",
        },
    },
    "compute-optimizer": {
        aws: {
            arn: "arn:aws:compute-optimizer:{region}:{account-id}:{resource-id}",
            principal: "compute-optimizer.amazonaws.com",
            hostname: "compute-optimizer.{region}.amazonaws.com",
            fipsHostname: "compute-optimizer-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:compute-optimizer:{region}:{account-id}:{resource-id}",
            principal: "compute-optimizer.amazonaws.com.cn",
            hostname: "compute-optimizer.{region}.amazonaws.com.cn",
            fipsHostname: "compute-optimizer-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:compute-optimizer:{region}:{account-id}:{resource-id}",
            principal: "compute-optimizer.amazonaws.com",
            hostname: "compute-optimizer.{region}.amazonaws.com",
            fipsHostname: "compute-optimizer-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:compute-optimizer:{region}:{account-id}:{resource-id}",
            principal: "compute-optimizer.cloud.adc-e.uk",
            hostname: "compute-optimizer.{region}.cloud.adc-e.uk",
            fipsHostname: "compute-optimizer-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:compute-optimizer:{region}:{account-id}:{resource-id}",
            principal: "compute-optimizer.csp.hci.ic.gov",
            hostname: "compute-optimizer.{region}.csp.hci.ic.gov",
            fipsHostname: "compute-optimizer-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:compute-optimizer:{region}:{account-id}:{resource-id}",
            principal: "compute-optimizer.amazonaws.com",
            hostname: "compute-optimizer.{region}.amazonaws.eu",
            fipsHostname: "compute-optimizer-fips.{region}.amazonaws.eu",
        },
    },
    config: {
        aws: {
            arn: "arn:aws:config:{region}:{account-id}:{resource-id}",
            principal: "config.amazonaws.com",
            hostname: "config.{region}.amazonaws.com",
            fipsHostname: "config-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:config:{region}:{account-id}:{resource-id}",
            principal: "config.amazonaws.com.cn",
            hostname: "config.{region}.amazonaws.com.cn",
            fipsHostname: "config-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:config:{region}:{account-id}:{resource-id}",
            principal: "config.amazonaws.com",
            hostname: "config.{region}.amazonaws.com",
            fipsHostname: "config-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:config:{region}:{account-id}:{resource-id}",
            principal: "config.c2s.ic.gov",
            hostname: "config.{region}.c2s.ic.gov",
            fipsHostname: "config-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:config:{region}:{account-id}:{resource-id}",
            principal: "config.sc2s.sgov.gov",
            hostname: "config.{region}.sc2s.sgov.gov",
            fipsHostname: "config-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:config:{region}:{account-id}:{resource-id}",
            principal: "config.cloud.adc-e.uk",
            hostname: "config.{region}.cloud.adc-e.uk",
            fipsHostname: "config-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:config:{region}:{account-id}:{resource-id}",
            principal: "config.csp.hci.ic.gov",
            hostname: "config.{region}.csp.hci.ic.gov",
            fipsHostname: "config-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:config:{region}:{account-id}:{resource-id}",
            principal: "config.amazonaws.com",
            hostname: "config.{region}.amazonaws.eu",
            fipsHostname: "config-fips.{region}.amazonaws.eu",
        },
    },
    connect: {
        aws: {
            arn: "arn:aws:connect:{region}:{account-id}:{resource-id}",
            principal: "connect.amazonaws.com",
            hostname: "connect.{region}.amazonaws.com",
            fipsHostname: "connect-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:connect:{region}:{account-id}:{resource-id}",
            principal: "connect.amazonaws.com",
            hostname: "connect.{region}.amazonaws.com",
            fipsHostname: "connect-fips.{region}.amazonaws.com",
        },
    },
    "connect-campaigns": {
        aws: {
            arn: "arn:aws:connect-campaigns:{region}:{account-id}:{resource-id}",
            principal: "connect-campaigns.amazonaws.com",
            hostname: "connect-campaigns.{region}.amazonaws.com",
            fipsHostname: "connect-campaigns-fips.{region}.amazonaws.com",
        },
    },
    "contact-lens": {
        aws: {
            arn: "arn:aws:contact-lens:{region}:{account-id}:{resource-id}",
            principal: "contact-lens.amazonaws.com",
            hostname: "contact-lens.{region}.amazonaws.com",
            fipsHostname: "contact-lens-fips.{region}.amazonaws.com",
        },
    },
    controltower: {
        aws: {
            arn: "arn:aws:controltower:{region}:{account-id}:{resource-id}",
            principal: "controltower.amazonaws.com",
            hostname: "controltower.{region}.amazonaws.com",
            fipsHostname: "controltower-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:controltower:{region}:{account-id}:{resource-id}",
            principal: "controltower.amazonaws.com",
            hostname: "controltower.{region}.amazonaws.com",
            fipsHostname: "controltower-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:controltower:{region}:{account-id}:{resource-id}",
            principal: "controltower.amazonaws.com",
            hostname: "controltower.{region}.amazonaws.eu",
            fipsHostname: "controltower-fips.{region}.amazonaws.eu",
        },
    },
    "cost-optimization-hub": {
        aws: {
            arn: "arn:aws:cost-optimization-hub:{region}:{account-id}:{resource-id}",
            principal: "cost-optimization-hub.amazonaws.com",
            hostname: "cost-optimization-hub.{region}.amazonaws.com",
            fipsHostname: "cost-optimization-hub-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:cost-optimization-hub:{region}:{account-id}:{resource-id}",
            principal: "cost-optimization-hub.cloud.adc-e.uk",
            hostname: "cost-optimization-hub.{region}.cloud.adc-e.uk",
            fipsHostname: "cost-optimization-hub-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:cost-optimization-hub:{region}:{account-id}:{resource-id}",
            principal: "cost-optimization-hub.csp.hci.ic.gov",
            hostname: "cost-optimization-hub.{region}.csp.hci.ic.gov",
            fipsHostname: "cost-optimization-hub-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:cost-optimization-hub:{region}:{account-id}:{resource-id}",
            principal: "cost-optimization-hub.amazonaws.com",
            hostname: "cost-optimization-hub.{region}.amazonaws.eu",
            fipsHostname: "cost-optimization-hub-fips.{region}.amazonaws.eu",
        },
    },
    cur: {
        aws: {
            arn: "arn:aws:cur:{region}:{account-id}:{resource-id}",
            principal: "cur.amazonaws.com",
            hostname: "cur.{region}.amazonaws.com",
            fipsHostname: "cur-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:cur:{region}:{account-id}:{resource-id}",
            principal: "cur.amazonaws.com.cn",
            hostname: "cur.{region}.amazonaws.com.cn",
            fipsHostname: "cur-fips.{region}.amazonaws.com.cn",
        },
    },
    "data-ats.iot": {
        aws: {
            arn: "arn:aws:data-ats.iot:{region}:{account-id}:{resource-id}",
            principal: "data-ats.iot.amazonaws.com",
            hostname: "data-ats.iot.{region}.amazonaws.com",
            fipsHostname: "data-ats.iot-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:data-ats.iot:{region}:{account-id}:{resource-id}",
            principal: "data-ats.iot.amazonaws.com.cn",
            hostname: "data-ats.iot.{region}.amazonaws.com.cn",
            fipsHostname: "data-ats.iot-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:data-ats.iot:{region}:{account-id}:{resource-id}",
            principal: "data-ats.iot.amazonaws.com",
            hostname: "data-ats.iot.{region}.amazonaws.com",
            fipsHostname: "data-ats.iot-fips.{region}.amazonaws.com",
        },
    },
    "data.iot": {
        aws: {
            arn: "arn:aws:data.iot:{region}:{account-id}:{resource-id}",
            principal: "data.iot.amazonaws.com",
            hostname: "data.iot.{region}.amazonaws.com",
            fipsHostname: "data.iot-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:data.iot:{region}:{account-id}:{resource-id}",
            principal: "data.iot.amazonaws.com.cn",
            hostname: "data.iot.{region}.amazonaws.com.cn",
            fipsHostname: "data.iot-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:data.iot:{region}:{account-id}:{resource-id}",
            principal: "data.iot.amazonaws.com",
            hostname: "data.iot.{region}.amazonaws.com",
            fipsHostname: "data.iot-fips.{region}.amazonaws.com",
        },
    },
    "data.jobs.iot": {
        aws: {
            arn: "arn:aws:data.jobs.iot:{region}:{account-id}:{resource-id}",
            principal: "data.jobs.iot.amazonaws.com",
            hostname: "data.jobs.iot.{region}.amazonaws.com",
            fipsHostname: "data.jobs.iot-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:data.jobs.iot:{region}:{account-id}:{resource-id}",
            principal: "data.jobs.iot.amazonaws.com.cn",
            hostname: "data.jobs.iot.{region}.amazonaws.com.cn",
            fipsHostname: "data.jobs.iot-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:data.jobs.iot:{region}:{account-id}:{resource-id}",
            principal: "data.jobs.iot.amazonaws.com",
            hostname: "data.jobs.iot.{region}.amazonaws.com",
            fipsHostname: "data.jobs.iot-fips.{region}.amazonaws.com",
        },
    },
    "data.mediastore": {
        aws: {
            arn: "arn:aws:data.mediastore:{region}:{account-id}:{resource-id}",
            principal: "data.mediastore.amazonaws.com",
            hostname: "data.mediastore.{region}.amazonaws.com",
            fipsHostname: "data.mediastore-fips.{region}.amazonaws.com",
        },
    },
    databrew: {
        aws: {
            arn: "arn:aws:databrew:{region}:{account-id}:{resource-id}",
            principal: "databrew.amazonaws.com",
            hostname: "databrew.{region}.amazonaws.com",
            fipsHostname: "databrew-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:databrew:{region}:{account-id}:{resource-id}",
            principal: "databrew.amazonaws.com.cn",
            hostname: "databrew.{region}.amazonaws.com.cn",
            fipsHostname: "databrew-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:databrew:{region}:{account-id}:{resource-id}",
            principal: "databrew.amazonaws.com",
            hostname: "databrew.{region}.amazonaws.com",
            fipsHostname: "databrew-fips.{region}.amazonaws.com",
        },
    },
    dataexchange: {
        aws: {
            arn: "arn:aws:dataexchange:{region}:{account-id}:{resource-id}",
            principal: "dataexchange.amazonaws.com",
            hostname: "dataexchange.{region}.amazonaws.com",
            fipsHostname: "dataexchange-fips.{region}.amazonaws.com",
        },
    },
    datapipeline: {
        aws: {
            arn: "arn:aws:datapipeline:{region}:{account-id}:{resource-id}",
            principal: "datapipeline.amazonaws.com",
            hostname: "datapipeline.{region}.amazonaws.com",
            fipsHostname: "datapipeline-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:datapipeline:{region}:{account-id}:{resource-id}",
            principal: "datapipeline.c2s.ic.gov",
            hostname: "datapipeline.{region}.c2s.ic.gov",
            fipsHostname: "datapipeline-fips.{region}.c2s.ic.gov",
        },
    },
    datasync: {
        aws: {
            arn: "arn:aws:datasync:{region}:{account-id}:{resource-id}",
            principal: "datasync.amazonaws.com",
            hostname: "datasync.{region}.amazonaws.com",
            fipsHostname: "datasync-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:datasync:{region}:{account-id}:{resource-id}",
            principal: "datasync.amazonaws.com.cn",
            hostname: "datasync.{region}.amazonaws.com.cn",
            fipsHostname: "datasync-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:datasync:{region}:{account-id}:{resource-id}",
            principal: "datasync.amazonaws.com",
            hostname: "datasync.{region}.amazonaws.com",
            fipsHostname: "datasync-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:datasync:{region}:{account-id}:{resource-id}",
            principal: "datasync.c2s.ic.gov",
            hostname: "datasync.{region}.c2s.ic.gov",
            fipsHostname: "datasync-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:datasync:{region}:{account-id}:{resource-id}",
            principal: "datasync.sc2s.sgov.gov",
            hostname: "datasync.{region}.sc2s.sgov.gov",
            fipsHostname: "datasync-fips.{region}.sc2s.sgov.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:datasync:{region}:{account-id}:{resource-id}",
            principal: "datasync.amazonaws.com",
            hostname: "datasync.{region}.amazonaws.eu",
            fipsHostname: "datasync-fips.{region}.amazonaws.eu",
        },
    },
    datazone: {
        aws: {
            arn: "arn:aws:datazone:{region}:{account-id}:{resource-id}",
            principal: "datazone.amazonaws.com",
            hostname: "datazone.{region}.amazonaws.com",
            fipsHostname: "datazone-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:datazone:{region}:{account-id}:{resource-id}",
            principal: "datazone.amazonaws.com.cn",
            hostname: "datazone.{region}.amazonaws.com.cn",
            fipsHostname: "datazone-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:datazone:{region}:{account-id}:{resource-id}",
            principal: "datazone.amazonaws.com",
            hostname: "datazone.{region}.amazonaws.com",
            fipsHostname: "datazone-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:datazone:{region}:{account-id}:{resource-id}",
            principal: "datazone.amazonaws.com",
            hostname: "datazone.{region}.amazonaws.eu",
            fipsHostname: "datazone-fips.{region}.amazonaws.eu",
        },
    },
    dax: {
        aws: {
            arn: "arn:aws:dax:{region}:{account-id}:{resource-id}",
            principal: "dax.amazonaws.com",
            hostname: "dax.{region}.amazonaws.com",
            fipsHostname: "dax-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:dax:{region}:{account-id}:{resource-id}",
            principal: "dax.amazonaws.com.cn",
            hostname: "dax.{region}.amazonaws.com.cn",
            fipsHostname: "dax-fips.{region}.amazonaws.com.cn",
        },
    },
    devicefarm: {
        aws: {
            arn: "arn:aws:devicefarm:{region}:{account-id}:{resource-id}",
            principal: "devicefarm.amazonaws.com",
            hostname: "devicefarm.{region}.amazonaws.com",
            fipsHostname: "devicefarm-fips.{region}.amazonaws.com",
        },
    },
    "devops-guru": {
        aws: {
            arn: "arn:aws:devops-guru:{region}:{account-id}:{resource-id}",
            principal: "devops-guru.amazonaws.com",
            hostname: "devops-guru.{region}.amazonaws.com",
            fipsHostname: "devops-guru-fips.{region}.amazonaws.com",
        },
    },
    directconnect: {
        aws: {
            arn: "arn:aws:directconnect:{region}:{account-id}:{resource-id}",
            principal: "directconnect.amazonaws.com",
            hostname: "directconnect.{region}.amazonaws.com",
            fipsHostname: "directconnect-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:directconnect:{region}:{account-id}:{resource-id}",
            principal: "directconnect.amazonaws.com.cn",
            hostname: "directconnect.{region}.amazonaws.com.cn",
            fipsHostname: "directconnect-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:directconnect:{region}:{account-id}:{resource-id}",
            principal: "directconnect.amazonaws.com",
            hostname: "directconnect.{region}.amazonaws.com",
            fipsHostname: "directconnect-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:directconnect:{region}:{account-id}:{resource-id}",
            principal: "directconnect.c2s.ic.gov",
            hostname: "directconnect.{region}.c2s.ic.gov",
            fipsHostname: "directconnect-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:directconnect:{region}:{account-id}:{resource-id}",
            principal: "directconnect.sc2s.sgov.gov",
            hostname: "directconnect.{region}.sc2s.sgov.gov",
            fipsHostname: "directconnect-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:directconnect:{region}:{account-id}:{resource-id}",
            principal: "directconnect.cloud.adc-e.uk",
            hostname: "directconnect.{region}.cloud.adc-e.uk",
            fipsHostname: "directconnect-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:directconnect:{region}:{account-id}:{resource-id}",
            principal: "directconnect.csp.hci.ic.gov",
            hostname: "directconnect.{region}.csp.hci.ic.gov",
            fipsHostname: "directconnect-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:directconnect:{region}:{account-id}:{resource-id}",
            principal: "directconnect.amazonaws.com",
            hostname: "directconnect.{region}.amazonaws.eu",
            fipsHostname: "directconnect-fips.{region}.amazonaws.eu",
        },
    },
    discovery: {
        aws: {
            arn: "arn:aws:discovery:{region}:{account-id}:{resource-id}",
            principal: "discovery.amazonaws.com",
            hostname: "discovery.{region}.amazonaws.com",
            fipsHostname: "discovery-fips.{region}.amazonaws.com",
        },
    },
    dlm: {
        aws: {
            arn: "arn:aws:dlm:{region}:{account-id}:{resource-id}",
            principal: "dlm.amazonaws.com",
            hostname: "dlm.{region}.amazonaws.com",
            fipsHostname: "dlm-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:dlm:{region}:{account-id}:{resource-id}",
            principal: "dlm.amazonaws.com.cn",
            hostname: "dlm.{region}.amazonaws.com.cn",
            fipsHostname: "dlm-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:dlm:{region}:{account-id}:{resource-id}",
            principal: "dlm.amazonaws.com",
            hostname: "dlm.{region}.amazonaws.com",
            fipsHostname: "dlm-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:dlm:{region}:{account-id}:{resource-id}",
            principal: "dlm.c2s.ic.gov",
            hostname: "dlm.{region}.c2s.ic.gov",
            fipsHostname: "dlm-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:dlm:{region}:{account-id}:{resource-id}",
            principal: "dlm.sc2s.sgov.gov",
            hostname: "dlm.{region}.sc2s.sgov.gov",
            fipsHostname: "dlm-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:dlm:{region}:{account-id}:{resource-id}",
            principal: "dlm.cloud.adc-e.uk",
            hostname: "dlm.{region}.cloud.adc-e.uk",
            fipsHostname: "dlm-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:dlm:{region}:{account-id}:{resource-id}",
            principal: "dlm.csp.hci.ic.gov",
            hostname: "dlm.{region}.csp.hci.ic.gov",
            fipsHostname: "dlm-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:dlm:{region}:{account-id}:{resource-id}",
            principal: "dlm.amazonaws.com",
            hostname: "dlm.{region}.amazonaws.eu",
            fipsHostname: "dlm-fips.{region}.amazonaws.eu",
        },
    },
    dms: {
        aws: {
            arn: "arn:aws:dms:{region}:{account-id}:{resource-id}",
            principal: "dms.amazonaws.com",
            hostname: "dms.{region}.amazonaws.com",
            fipsHostname: "dms-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:dms:{region}:{account-id}:{resource-id}",
            principal: "dms.amazonaws.com.cn",
            hostname: "dms.{region}.amazonaws.com.cn",
            fipsHostname: "dms-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:dms:{region}:{account-id}:{resource-id}",
            principal: "dms.amazonaws.com",
            hostname: "dms.{region}.amazonaws.com",
            fipsHostname: "dms-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:dms:{region}:{account-id}:{resource-id}",
            principal: "dms.c2s.ic.gov",
            hostname: "dms.{region}.c2s.ic.gov",
            fipsHostname: "dms-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:dms:{region}:{account-id}:{resource-id}",
            principal: "dms.sc2s.sgov.gov",
            hostname: "dms.{region}.sc2s.sgov.gov",
            fipsHostname: "dms-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:dms:{region}:{account-id}:{resource-id}",
            principal: "dms.cloud.adc-e.uk",
            hostname: "dms.{region}.cloud.adc-e.uk",
            fipsHostname: "dms-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:dms:{region}:{account-id}:{resource-id}",
            principal: "dms.csp.hci.ic.gov",
            hostname: "dms.{region}.csp.hci.ic.gov",
            fipsHostname: "dms-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:dms:{region}:{account-id}:{resource-id}",
            principal: "dms.amazonaws.com",
            hostname: "dms.{region}.amazonaws.eu",
            fipsHostname: "dms-fips.{region}.amazonaws.eu",
        },
    },
    docdb: {
        aws: {
            arn: "arn:aws:docdb:{region}:{account-id}:{resource-id}",
            principal: "docdb.amazonaws.com",
            hostname: "docdb.{region}.amazonaws.com",
            fipsHostname: "docdb-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:docdb:{region}:{account-id}:{resource-id}",
            principal: "docdb.amazonaws.com.cn",
            hostname: "docdb.{region}.amazonaws.com.cn",
            fipsHostname: "docdb-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:docdb:{region}:{account-id}:{resource-id}",
            principal: "docdb.amazonaws.com",
            hostname: "docdb.{region}.amazonaws.com",
            fipsHostname: "docdb-fips.{region}.amazonaws.com",
        },
    },
    drs: {
        aws: {
            arn: "arn:aws:drs:{region}:{account-id}:{resource-id}",
            principal: "drs.amazonaws.com",
            hostname: "drs.{region}.amazonaws.com",
            fipsHostname: "drs-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:drs:{region}:{account-id}:{resource-id}",
            principal: "drs.amazonaws.com",
            hostname: "drs.{region}.amazonaws.com",
            fipsHostname: "drs-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:drs:{region}:{account-id}:{resource-id}",
            principal: "drs.amazonaws.com",
            hostname: "drs.{region}.amazonaws.eu",
            fipsHostname: "drs-fips.{region}.amazonaws.eu",
        },
    },
    ds: {
        aws: {
            arn: "arn:aws:ds:{region}:{account-id}:{resource-id}",
            principal: "ds.amazonaws.com",
            hostname: "ds.{region}.amazonaws.com",
            fipsHostname: "ds-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ds:{region}:{account-id}:{resource-id}",
            principal: "ds.amazonaws.com.cn",
            hostname: "ds.{region}.amazonaws.com.cn",
            fipsHostname: "ds-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ds:{region}:{account-id}:{resource-id}",
            principal: "ds.amazonaws.com",
            hostname: "ds.{region}.amazonaws.com",
            fipsHostname: "ds-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:ds:{region}:{account-id}:{resource-id}",
            principal: "ds.c2s.ic.gov",
            hostname: "ds.{region}.c2s.ic.gov",
            fipsHostname: "ds-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:ds:{region}:{account-id}:{resource-id}",
            principal: "ds.sc2s.sgov.gov",
            hostname: "ds.{region}.sc2s.sgov.gov",
            fipsHostname: "ds-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:ds:{region}:{account-id}:{resource-id}",
            principal: "ds.cloud.adc-e.uk",
            hostname: "ds.{region}.cloud.adc-e.uk",
            fipsHostname: "ds-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ds:{region}:{account-id}:{resource-id}",
            principal: "ds.csp.hci.ic.gov",
            hostname: "ds.{region}.csp.hci.ic.gov",
            fipsHostname: "ds-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:ds:{region}:{account-id}:{resource-id}",
            principal: "ds.amazonaws.com",
            hostname: "ds.{region}.amazonaws.eu",
            fipsHostname: "ds-fips.{region}.amazonaws.eu",
        },
    },
    dynamodb: {
        aws: {
            arn: "arn:aws:dynamodb:{region}:{account-id}:{resource-id}",
            principal: "dynamodb.amazonaws.com",
            hostname: "dynamodb.{region}.amazonaws.com",
            fipsHostname: "dynamodb-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:dynamodb:{region}:{account-id}:{resource-id}",
            principal: "dynamodb.amazonaws.com.cn",
            hostname: "dynamodb.{region}.amazonaws.com.cn",
            fipsHostname: "dynamodb-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:dynamodb:{region}:{account-id}:{resource-id}",
            principal: "dynamodb.amazonaws.com",
            hostname: "dynamodb.{region}.amazonaws.com",
            fipsHostname: "dynamodb-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:dynamodb:{region}:{account-id}:{resource-id}",
            principal: "dynamodb.c2s.ic.gov",
            hostname: "dynamodb.{region}.c2s.ic.gov",
            fipsHostname: "dynamodb-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:dynamodb:{region}:{account-id}:{resource-id}",
            principal: "dynamodb.sc2s.sgov.gov",
            hostname: "dynamodb.{region}.sc2s.sgov.gov",
            fipsHostname: "dynamodb-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:dynamodb:{region}:{account-id}:{resource-id}",
            principal: "dynamodb.cloud.adc-e.uk",
            hostname: "dynamodb.{region}.cloud.adc-e.uk",
            fipsHostname: "dynamodb-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:dynamodb:{region}:{account-id}:{resource-id}",
            principal: "dynamodb.csp.hci.ic.gov",
            hostname: "dynamodb.{region}.csp.hci.ic.gov",
            fipsHostname: "dynamodb-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:dynamodb:{region}:{account-id}:{resource-id}",
            principal: "dynamodb.amazonaws.com",
            hostname: "dynamodb.{region}.amazonaws.eu",
            fipsHostname: "dynamodb-fips.{region}.amazonaws.eu",
        },
    },
    ebs: {
        aws: {
            arn: "arn:aws:ebs:{region}:{account-id}:{resource-id}",
            principal: "ebs.amazonaws.com",
            hostname: "ebs.{region}.amazonaws.com",
            fipsHostname: "ebs-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ebs:{region}:{account-id}:{resource-id}",
            principal: "ebs.amazonaws.com.cn",
            hostname: "ebs.{region}.amazonaws.com.cn",
            fipsHostname: "ebs-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ebs:{region}:{account-id}:{resource-id}",
            principal: "ebs.amazonaws.com",
            hostname: "ebs.{region}.amazonaws.com",
            fipsHostname: "ebs-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:ebs:{region}:{account-id}:{resource-id}",
            principal: "ebs.c2s.ic.gov",
            hostname: "ebs.{region}.c2s.ic.gov",
            fipsHostname: "ebs-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:ebs:{region}:{account-id}:{resource-id}",
            principal: "ebs.sc2s.sgov.gov",
            hostname: "ebs.{region}.sc2s.sgov.gov",
            fipsHostname: "ebs-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:ebs:{region}:{account-id}:{resource-id}",
            principal: "ebs.cloud.adc-e.uk",
            hostname: "ebs.{region}.cloud.adc-e.uk",
            fipsHostname: "ebs-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ebs:{region}:{account-id}:{resource-id}",
            principal: "ebs.csp.hci.ic.gov",
            hostname: "ebs.{region}.csp.hci.ic.gov",
            fipsHostname: "ebs-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:ebs:{region}:{account-id}:{resource-id}",
            principal: "ebs.amazonaws.com",
            hostname: "ebs.{region}.amazonaws.eu",
            fipsHostname: "ebs-fips.{region}.amazonaws.eu",
        },
    },
    ec2: {
        aws: {
            arn: "arn:aws:ec2:{region}:{account-id}:{resource-id}",
            principal: "ec2.amazonaws.com",
            hostname: "ec2.{region}.amazonaws.com",
            fipsHostname: "ec2-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ec2:{region}:{account-id}:{resource-id}",
            principal: "ec2.amazonaws.com.cn",
            hostname: "ec2.{region}.amazonaws.com.cn",
            fipsHostname: "ec2-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ec2:{region}:{account-id}:{resource-id}",
            principal: "ec2.amazonaws.com",
            hostname: "ec2.{region}.amazonaws.com",
            fipsHostname: "ec2-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:ec2:{region}:{account-id}:{resource-id}",
            principal: "ec2.c2s.ic.gov",
            hostname: "ec2.{region}.c2s.ic.gov",
            fipsHostname: "ec2-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:ec2:{region}:{account-id}:{resource-id}",
            principal: "ec2.sc2s.sgov.gov",
            hostname: "ec2.{region}.sc2s.sgov.gov",
            fipsHostname: "ec2-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:ec2:{region}:{account-id}:{resource-id}",
            principal: "ec2.cloud.adc-e.uk",
            hostname: "ec2.{region}.cloud.adc-e.uk",
            fipsHostname: "ec2-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ec2:{region}:{account-id}:{resource-id}",
            principal: "ec2.csp.hci.ic.gov",
            hostname: "ec2.{region}.csp.hci.ic.gov",
            fipsHostname: "ec2-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:ec2:{region}:{account-id}:{resource-id}",
            principal: "ec2.amazonaws.com",
            hostname: "ec2.{region}.amazonaws.eu",
            fipsHostname: "ec2-fips.{region}.amazonaws.eu",
        },
    },
    "ecr-dkr": {
        aws: {
            arn: "XXX",
            principal: "XXX",
            hostname: "dkr.ecr.{region}.amazonaws.com",
            fipsHostname: "dkr.ecr-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "XXX",
            principal: "XXX",
            hostname: "dkr.ecr.{region}.amazonaws.com.cn",
            fipsHostname: "dkr.ecr-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "XXX",
            principal: "XXX",
            hostname: "dkr.ecr.{region}.amazonaws.com",
            fipsHostname: "dkr.ecr-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "XXX",
            principal: "XXX",
            hostname: "dkr.ecr.{region}.c2s.ic.gov",
            fipsHostname: "dkr.ecr-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "XXX",
            principal: "XXX",
            hostname: "dkr.ecr.{region}.sc2s.sgov.gov",
            fipsHostname: "dkr.ecr-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:ecr-dkr:{region}:{account-id}:{resource-id}",
            principal: "ecr-dkr.cloud.adc-e.uk",
            hostname: "ecr-dkr.{region}.cloud.adc-e.uk",
            fipsHostname: "ecr-dkr-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ecr-dkr:{region}:{account-id}:{resource-id}",
            principal: "ecr-dkr.csp.hci.ic.gov",
            hostname: "ecr-dkr.{region}.csp.hci.ic.gov",
            fipsHostname: "ecr-dkr-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:ecr-dkr:{region}:{account-id}:{resource-id}",
            principal: "ecr-dkr.amazonaws.com",
            hostname: "ecr-dkr.{region}.amazonaws.eu",
            fipsHostname: "ecr-dkr-fips.{region}.amazonaws.eu",
        },
    },
    ecs: {
        aws: {
            arn: "arn:aws:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs.amazonaws.com",
            hostname: "ecs.{region}.amazonaws.com",
            fipsHostname: "ecs-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs.amazonaws.com.cn",
            hostname: "ecs.{region}.amazonaws.com.cn",
            fipsHostname: "ecs-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs.amazonaws.com",
            hostname: "ecs.{region}.amazonaws.com",
            fipsHostname: "ecs-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs.c2s.ic.gov",
            hostname: "ecs.{region}.c2s.ic.gov",
            fipsHostname: "ecs-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs.sc2s.sgov.gov",
            hostname: "ecs.{region}.sc2s.sgov.gov",
            fipsHostname: "ecs-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs.cloud.adc-e.uk",
            hostname: "ecs.{region}.cloud.adc-e.uk",
            fipsHostname: "ecs-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs.csp.hci.ic.gov",
            hostname: "ecs.{region}.csp.hci.ic.gov",
            fipsHostname: "ecs-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs.amazonaws.com",
            hostname: "ecs.{region}.amazonaws.eu",
            fipsHostname: "ecs-fips.{region}.amazonaws.eu",
        },
    },
    "ecs-tasks": {
        aws: {
            arn: "arn:aws:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs-tasks.amazonaws.com",
            hostname: "ecs.{region}.amazonaws.com",
            fipsHostname: "ecs-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs-tasks.amazonaws.com.cn",
            hostname: "ecs.{region}.amazonaws.com.cn",
            fipsHostname: "ecs-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs-tasks.amazonaws.com",
            hostname: "ecs.{region}.amazonaws.com",
            fipsHostname: "ecs-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs-tasks.c2s.ic.gov",
            hostname: "ecs.{region}.c2s.ic.gov",
            fipsHostname: "ecs-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:ecs:{region}:{account-id}:{resource-id}",
            principal: "ecs-tasks.sc2s.sgov.gov",
            hostname: "ecs.{region}.sc2s.sgov.gov",
            fipsHostname: "ecs-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:ecs-tasks:{region}:{account-id}:{resource-id}",
            principal: "ecs-tasks.cloud.adc-e.uk",
            hostname: "ecs-tasks.{region}.cloud.adc-e.uk",
            fipsHostname: "ecs-tasks-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ecs-tasks:{region}:{account-id}:{resource-id}",
            principal: "ecs-tasks.csp.hci.ic.gov",
            hostname: "ecs-tasks.{region}.csp.hci.ic.gov",
            fipsHostname: "ecs-tasks-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:ecs-tasks:{region}:{account-id}:{resource-id}",
            principal: "ecs-tasks.amazonaws.com",
            hostname: "ecs-tasks.{region}.amazonaws.eu",
            fipsHostname: "ecs-tasks-fips.{region}.amazonaws.eu",
        },
    },
    "edge.sagemaker": {
        aws: {
            arn: "arn:aws:edge.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "edge.sagemaker.amazonaws.com",
            hostname: "edge.sagemaker.{region}.amazonaws.com",
            fipsHostname: "edge.sagemaker-fips.{region}.amazonaws.com",
        },
    },
    eks: {
        aws: {
            arn: "arn:aws:eks:{region}:{account-id}:{resource-id}",
            principal: "eks.amazonaws.com",
            hostname: "eks.{region}.amazonaws.com",
            fipsHostname: "eks-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:eks:{region}:{account-id}:{resource-id}",
            principal: "eks.amazonaws.com.cn",
            hostname: "eks.{region}.amazonaws.com.cn",
            fipsHostname: "eks-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:eks:{region}:{account-id}:{resource-id}",
            principal: "eks.amazonaws.com",
            hostname: "eks.{region}.amazonaws.com",
            fipsHostname: "eks-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:eks:{region}:{account-id}:{resource-id}",
            principal: "eks.c2s.ic.gov",
            hostname: "eks.{region}.c2s.ic.gov",
            fipsHostname: "eks-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:eks:{region}:{account-id}:{resource-id}",
            principal: "eks.sc2s.sgov.gov",
            hostname: "eks.{region}.sc2s.sgov.gov",
            fipsHostname: "eks-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:eks:{region}:{account-id}:{resource-id}",
            principal: "eks.cloud.adc-e.uk",
            hostname: "eks.{region}.cloud.adc-e.uk",
            fipsHostname: "eks-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:eks:{region}:{account-id}:{resource-id}",
            principal: "eks.csp.hci.ic.gov",
            hostname: "eks.{region}.csp.hci.ic.gov",
            fipsHostname: "eks-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:eks:{region}:{account-id}:{resource-id}",
            principal: "eks.amazonaws.com",
            hostname: "eks.{region}.amazonaws.eu",
            fipsHostname: "eks-fips.{region}.amazonaws.eu",
        },
    },
    "eks-auth": {
        aws: {
            arn: "arn:aws:eks-auth:{region}:{account-id}:{resource-id}",
            principal: "eks-auth.amazonaws.com",
            hostname: "eks-auth.{region}.amazonaws.com",
            fipsHostname: "eks-auth-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:eks-auth:{region}:{account-id}:{resource-id}",
            principal: "eks-auth.amazonaws.com.cn",
            hostname: "eks-auth.{region}.amazonaws.com.cn",
            fipsHostname: "eks-auth-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:eks-auth:{region}:{account-id}:{resource-id}",
            principal: "eks-auth.amazonaws.com",
            hostname: "eks-auth.{region}.amazonaws.com",
            fipsHostname: "eks-auth-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:eks-auth:{region}:{account-id}:{resource-id}",
            principal: "eks-auth.amazonaws.com",
            hostname: "eks-auth.{region}.amazonaws.eu",
            fipsHostname: "eks-auth-fips.{region}.amazonaws.eu",
        },
    },
    elasticache: {
        aws: {
            arn: "arn:aws:elasticache:{region}:{account-id}:{resource-id}",
            principal: "elasticache.amazonaws.com",
            hostname: "elasticache.{region}.amazonaws.com",
            fipsHostname: "elasticache-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:elasticache:{region}:{account-id}:{resource-id}",
            principal: "elasticache.amazonaws.com.cn",
            hostname: "elasticache.{region}.amazonaws.com.cn",
            fipsHostname: "elasticache-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:elasticache:{region}:{account-id}:{resource-id}",
            principal: "elasticache.amazonaws.com",
            hostname: "elasticache.{region}.amazonaws.com",
            fipsHostname: "elasticache-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:elasticache:{region}:{account-id}:{resource-id}",
            principal: "elasticache.c2s.ic.gov",
            hostname: "elasticache.{region}.c2s.ic.gov",
            fipsHostname: "elasticache-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:elasticache:{region}:{account-id}:{resource-id}",
            principal: "elasticache.sc2s.sgov.gov",
            hostname: "elasticache.{region}.sc2s.sgov.gov",
            fipsHostname: "elasticache-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:elasticache:{region}:{account-id}:{resource-id}",
            principal: "elasticache.cloud.adc-e.uk",
            hostname: "elasticache.{region}.cloud.adc-e.uk",
            fipsHostname: "elasticache-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:elasticache:{region}:{account-id}:{resource-id}",
            principal: "elasticache.csp.hci.ic.gov",
            hostname: "elasticache.{region}.csp.hci.ic.gov",
            fipsHostname: "elasticache-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:elasticache:{region}:{account-id}:{resource-id}",
            principal: "elasticache.amazonaws.com",
            hostname: "elasticache.{region}.amazonaws.eu",
            fipsHostname: "elasticache-fips.{region}.amazonaws.eu",
        },
    },
    elasticbeanstalk: {
        aws: {
            arn: "arn:aws:elasticbeanstalk:{region}:{account-id}:{resource-id}",
            principal: "elasticbeanstalk.amazonaws.com",
            hostname: "elasticbeanstalk.{region}.amazonaws.com",
            fipsHostname: "elasticbeanstalk-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:elasticbeanstalk:{region}:{account-id}:{resource-id}",
            principal: "elasticbeanstalk.amazonaws.com.cn",
            hostname: "elasticbeanstalk.{region}.amazonaws.com.cn",
            fipsHostname: "elasticbeanstalk-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:elasticbeanstalk:{region}:{account-id}:{resource-id}",
            principal: "elasticbeanstalk.amazonaws.com",
            hostname: "elasticbeanstalk.{region}.amazonaws.com",
            fipsHostname: "elasticbeanstalk-fips.{region}.amazonaws.com",
        },
    },
    elasticfilesystem: {
        aws: {
            arn: "arn:aws:elasticfilesystem:{region}:{account-id}:{resource-id}",
            principal: "elasticfilesystem.amazonaws.com",
            hostname: "elasticfilesystem.{region}.amazonaws.com",
            fipsHostname: "elasticfilesystem-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:elasticfilesystem:{region}:{account-id}:{resource-id}",
            principal: "elasticfilesystem.amazonaws.com.cn",
            hostname: "elasticfilesystem.{region}.amazonaws.com.cn",
            fipsHostname: "elasticfilesystem-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:elasticfilesystem:{region}:{account-id}:{resource-id}",
            principal: "elasticfilesystem.amazonaws.com",
            hostname: "elasticfilesystem.{region}.amazonaws.com",
            fipsHostname: "elasticfilesystem-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:elasticfilesystem:{region}:{account-id}:{resource-id}",
            principal: "elasticfilesystem.c2s.ic.gov",
            hostname: "elasticfilesystem.{region}.c2s.ic.gov",
            fipsHostname: "elasticfilesystem-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:elasticfilesystem:{region}:{account-id}:{resource-id}",
            principal: "elasticfilesystem.sc2s.sgov.gov",
            hostname: "elasticfilesystem.{region}.sc2s.sgov.gov",
            fipsHostname: "elasticfilesystem-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:elasticfilesystem:{region}:{account-id}:{resource-id}",
            principal: "elasticfilesystem.cloud.adc-e.uk",
            hostname: "elasticfilesystem.{region}.cloud.adc-e.uk",
            fipsHostname: "elasticfilesystem-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:elasticfilesystem:{region}:{account-id}:{resource-id}",
            principal: "elasticfilesystem.csp.hci.ic.gov",
            hostname: "elasticfilesystem.{region}.csp.hci.ic.gov",
            fipsHostname: "elasticfilesystem-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:elasticfilesystem:{region}:{account-id}:{resource-id}",
            principal: "elasticfilesystem.amazonaws.com",
            hostname: "elasticfilesystem.{region}.amazonaws.eu",
            fipsHostname: "elasticfilesystem-fips.{region}.amazonaws.eu",
        },
    },
    elasticloadbalancing: {
        aws: {
            arn: "arn:aws:elasticloadbalancing:{region}:{account-id}:{resource-id}",
            principal: "elasticloadbalancing.amazonaws.com",
            hostname: "elasticloadbalancing.{region}.amazonaws.com",
            fipsHostname: "elasticloadbalancing-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:elasticloadbalancing:{region}:{account-id}:{resource-id}",
            principal: "elasticloadbalancing.amazonaws.com.cn",
            hostname: "elasticloadbalancing.{region}.amazonaws.com.cn",
            fipsHostname: "elasticloadbalancing-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:elasticloadbalancing:{region}:{account-id}:{resource-id}",
            principal: "elasticloadbalancing.amazonaws.com",
            hostname: "elasticloadbalancing.{region}.amazonaws.com",
            fipsHostname: "elasticloadbalancing-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:elasticloadbalancing:{region}:{account-id}:{resource-id}",
            principal: "elasticloadbalancing.c2s.ic.gov",
            hostname: "elasticloadbalancing.{region}.c2s.ic.gov",
            fipsHostname: "elasticloadbalancing-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:elasticloadbalancing:{region}:{account-id}:{resource-id}",
            principal: "elasticloadbalancing.sc2s.sgov.gov",
            hostname: "elasticloadbalancing.{region}.sc2s.sgov.gov",
            fipsHostname: "elasticloadbalancing-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:elasticloadbalancing:{region}:{account-id}:{resource-id}",
            principal: "elasticloadbalancing.cloud.adc-e.uk",
            hostname: "elasticloadbalancing.{region}.cloud.adc-e.uk",
            fipsHostname: "elasticloadbalancing-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:elasticloadbalancing:{region}:{account-id}:{resource-id}",
            principal: "elasticloadbalancing.csp.hci.ic.gov",
            hostname: "elasticloadbalancing.{region}.csp.hci.ic.gov",
            fipsHostname: "elasticloadbalancing-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:elasticloadbalancing:{region}:{account-id}:{resource-id}",
            principal: "elasticloadbalancing.amazonaws.com",
            hostname: "elasticloadbalancing.{region}.amazonaws.eu",
            fipsHostname: "elasticloadbalancing-fips.{region}.amazonaws.eu",
        },
    },
    elasticmapreduce: {
        aws: {
            arn: "arn:aws:elasticmapreduce:{region}:{account-id}:{resource-id}",
            principal: "elasticmapreduce.amazonaws.com",
            hostname: "elasticmapreduce.{region}.amazonaws.com",
            fipsHostname: "elasticmapreduce-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:elasticmapreduce:{region}:{account-id}:{resource-id}",
            principal: "elasticmapreduce.amazonaws.com.cn",
            hostname: "elasticmapreduce.{region}.amazonaws.com.cn",
            fipsHostname: "elasticmapreduce-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:elasticmapreduce:{region}:{account-id}:{resource-id}",
            principal: "elasticmapreduce.amazonaws.com",
            hostname: "elasticmapreduce.{region}.amazonaws.com",
            fipsHostname: "elasticmapreduce-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:elasticmapreduce:{region}:{account-id}:{resource-id}",
            principal: "elasticmapreduce.c2s.ic.gov",
            hostname: "elasticmapreduce.{region}.c2s.ic.gov",
            fipsHostname: "elasticmapreduce-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:elasticmapreduce:{region}:{account-id}:{resource-id}",
            principal: "elasticmapreduce.sc2s.sgov.gov",
            hostname: "elasticmapreduce.{region}.sc2s.sgov.gov",
            fipsHostname: "elasticmapreduce-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:elasticmapreduce:{region}:{account-id}:{resource-id}",
            principal: "elasticmapreduce.cloud.adc-e.uk",
            hostname: "elasticmapreduce.{region}.cloud.adc-e.uk",
            fipsHostname: "elasticmapreduce-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:elasticmapreduce:{region}:{account-id}:{resource-id}",
            principal: "elasticmapreduce.csp.hci.ic.gov",
            hostname: "elasticmapreduce.{region}.csp.hci.ic.gov",
            fipsHostname: "elasticmapreduce-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:elasticmapreduce:{region}:{account-id}:{resource-id}",
            principal: "elasticmapreduce.amazonaws.com",
            hostname: "elasticmapreduce.{region}.amazonaws.eu",
            fipsHostname: "elasticmapreduce-fips.{region}.amazonaws.eu",
        },
    },
    elastictranscoder: {
        aws: {
            arn: "arn:aws:elastictranscoder:{region}:{account-id}:{resource-id}",
            principal: "elastictranscoder.amazonaws.com",
            hostname: "elastictranscoder.{region}.amazonaws.com",
            fipsHostname: "elastictranscoder-fips.{region}.amazonaws.com",
        },
    },
    email: {
        aws: {
            arn: "arn:aws:email:{region}:{account-id}:{resource-id}",
            principal: "email.amazonaws.com",
            hostname: "email.{region}.amazonaws.com",
            fipsHostname: "email-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:email:{region}:{account-id}:{resource-id}",
            principal: "email.amazonaws.com",
            hostname: "email.{region}.amazonaws.com",
            fipsHostname: "email-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:email:{region}:{account-id}:{resource-id}",
            principal: "email.amazonaws.com",
            hostname: "email.{region}.amazonaws.eu",
            fipsHostname: "email-fips.{region}.amazonaws.eu",
        },
    },
    "emr-containers": {
        aws: {
            arn: "arn:aws:emr-containers:{region}:{account-id}:{resource-id}",
            principal: "emr-containers.amazonaws.com",
            hostname: "emr-containers.{region}.amazonaws.com",
            fipsHostname: "emr-containers-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:emr-containers:{region}:{account-id}:{resource-id}",
            principal: "emr-containers.amazonaws.com.cn",
            hostname: "emr-containers.{region}.amazonaws.com.cn",
            fipsHostname: "emr-containers-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:emr-containers:{region}:{account-id}:{resource-id}",
            principal: "emr-containers.amazonaws.com",
            hostname: "emr-containers.{region}.amazonaws.com",
            fipsHostname: "emr-containers-fips.{region}.amazonaws.com",
        },
    },
    "emr-serverless": {
        aws: {
            arn: "arn:aws:emr-serverless:{region}:{account-id}:{resource-id}",
            principal: "emr-serverless.amazonaws.com",
            hostname: "emr-serverless.{region}.amazonaws.com",
            fipsHostname: "emr-serverless-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:emr-serverless:{region}:{account-id}:{resource-id}",
            principal: "emr-serverless.amazonaws.com.cn",
            hostname: "emr-serverless.{region}.amazonaws.com.cn",
            fipsHostname: "emr-serverless-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:emr-serverless:{region}:{account-id}:{resource-id}",
            principal: "emr-serverless.amazonaws.com",
            hostname: "emr-serverless.{region}.amazonaws.com",
            fipsHostname: "emr-serverless-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:emr-serverless:{region}:{account-id}:{resource-id}",
            principal: "emr-serverless.cloud.adc-e.uk",
            hostname: "emr-serverless.{region}.cloud.adc-e.uk",
            fipsHostname: "emr-serverless-fips.{region}.cloud.adc-e.uk",
        },
    },
    "entitlement.marketplace": {
        aws: {
            arn: "arn:aws:entitlement.marketplace:{region}:{account-id}:{resource-id}",
            principal: "entitlement.marketplace.amazonaws.com",
            hostname: "entitlement.marketplace.{region}.amazonaws.com",
            fipsHostname: "entitlement.marketplace-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:entitlement.marketplace:{region}:{account-id}:{resource-id}",
            principal: "entitlement.marketplace.amazonaws.com.cn",
            hostname: "entitlement.marketplace.{region}.amazonaws.com.cn",
            fipsHostname: "entitlement.marketplace-fips.{region}.amazonaws.com.cn",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:entitlement.marketplace:{region}:{account-id}:{resource-id}",
            principal: "entitlement.marketplace.amazonaws.com",
            hostname: "entitlement.marketplace.{region}.amazonaws.eu",
            fipsHostname: "entitlement.marketplace-fips.{region}.amazonaws.eu",
        },
    },
    es: {
        aws: {
            arn: "arn:aws:es:{region}:{account-id}:{resource-id}",
            principal: "opensearchservice.amazonaws.com",
            hostname: "opensearchservice.{region}.amazonaws.com",
            fipsHostname: "opensearchservice-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:es:{region}:{account-id}:{resource-id}",
            principal: "opensearchservice.amazonaws.com.cn",
            hostname: "opensearchservice.{region}.amazonaws.com.cn",
            fipsHostname: "opensearchservice-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:es:{region}:{account-id}:{resource-id}",
            principal: "opensearchservice.amazonaws.com",
            hostname: "opensearchservice.{region}.amazonaws.com",
            fipsHostname: "opensearchservice-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:es:{region}:{account-id}:{resource-id}",
            principal: "opensearchservice.c2s.ic.gov",
            hostname: "opensearchservice.{region}.c2s.ic.gov",
            fipsHostname: "opensearchservice-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:es:{region}:{account-id}:{resource-id}",
            principal: "opensearchservice.sc2s.sgov.gov",
            hostname: "opensearchservice.{region}.sc2s.sgov.gov",
            fipsHostname: "opensearchservice-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:es:{region}:{account-id}:{resource-id}",
            principal: "opensearchservice.cloud.adc-e.uk",
            hostname: "opensearchservice.{region}.cloud.adc-e.uk",
            fipsHostname: "opensearchservice-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:es:{region}:{account-id}:{resource-id}",
            principal: "opensearchservice.csp.hci.ic.gov",
            hostname: "opensearchservice.{region}.csp.hci.ic.gov",
            fipsHostname: "opensearchservice-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:es:{region}:{account-id}:{resource-id}",
            principal: "opensearchservice.amazonaws.com",
            hostname: "opensearchservice.{region}.amazonaws.eu",
            fipsHostname: "opensearchservice-fips.{region}.amazonaws.eu",
        },
    },
    events: {
        aws: {
            arn: "arn:aws:events:{region}:{account-id}:{resource-id}",
            principal: "events.amazonaws.com",
            hostname: "events.{region}.amazonaws.com",
            fipsHostname: "events-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:events:{region}:{account-id}:{resource-id}",
            principal: "events.amazonaws.com.cn",
            hostname: "events.{region}.amazonaws.com.cn",
            fipsHostname: "events-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:events:{region}:{account-id}:{resource-id}",
            principal: "events.amazonaws.com",
            hostname: "events.{region}.amazonaws.com",
            fipsHostname: "events-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:events:{region}:{account-id}:{resource-id}",
            principal: "events.c2s.ic.gov",
            hostname: "events.{region}.c2s.ic.gov",
            fipsHostname: "events-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:events:{region}:{account-id}:{resource-id}",
            principal: "events.sc2s.sgov.gov",
            hostname: "events.{region}.sc2s.sgov.gov",
            fipsHostname: "events-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:events:{region}:{account-id}:{resource-id}",
            principal: "events.cloud.adc-e.uk",
            hostname: "events.{region}.cloud.adc-e.uk",
            fipsHostname: "events-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:events:{region}:{account-id}:{resource-id}",
            principal: "events.csp.hci.ic.gov",
            hostname: "events.{region}.csp.hci.ic.gov",
            fipsHostname: "events-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:events:{region}:{account-id}:{resource-id}",
            principal: "events.amazonaws.com",
            hostname: "events.{region}.amazonaws.eu",
            fipsHostname: "events-fips.{region}.amazonaws.eu",
        },
    },
    evidently: {
        aws: {
            arn: "arn:aws:evidently:{region}:{account-id}:{resource-id}",
            principal: "evidently.amazonaws.com",
            hostname: "evidently.{region}.amazonaws.com",
            fipsHostname: "evidently-fips.{region}.amazonaws.com",
        },
    },
    "execute-api": {
        aws: {
            arn: "arn:aws:execute-api:{region}:{account-id}:{resource-id}",
            principal: "execute-api.amazonaws.com",
            hostname: "execute-api.{region}.amazonaws.com",
            fipsHostname: "execute-api-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:execute-api:{region}:{account-id}:{resource-id}",
            principal: "execute-api.amazonaws.com.cn",
            hostname: "execute-api.{region}.amazonaws.com.cn",
            fipsHostname: "execute-api-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:execute-api:{region}:{account-id}:{resource-id}",
            principal: "execute-api.amazonaws.com",
            hostname: "execute-api.{region}.amazonaws.com",
            fipsHostname: "execute-api-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:execute-api:{region}:{account-id}:{resource-id}",
            principal: "execute-api.c2s.ic.gov",
            hostname: "execute-api.{region}.c2s.ic.gov",
            fipsHostname: "execute-api-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:execute-api:{region}:{account-id}:{resource-id}",
            principal: "execute-api.sc2s.sgov.gov",
            hostname: "execute-api.{region}.sc2s.sgov.gov",
            fipsHostname: "execute-api-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:execute-api:{region}:{account-id}:{resource-id}",
            principal: "execute-api.cloud.adc-e.uk",
            hostname: "execute-api.{region}.cloud.adc-e.uk",
            fipsHostname: "execute-api-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:execute-api:{region}:{account-id}:{resource-id}",
            principal: "execute-api.csp.hci.ic.gov",
            hostname: "execute-api.{region}.csp.hci.ic.gov",
            fipsHostname: "execute-api-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:execute-api:{region}:{account-id}:{resource-id}",
            principal: "execute-api.amazonaws.com",
            hostname: "execute-api.{region}.amazonaws.eu",
            fipsHostname: "execute-api-fips.{region}.amazonaws.eu",
        },
    },
    finspace: {
        aws: {
            arn: "arn:aws:finspace:{region}:{account-id}:{resource-id}",
            principal: "finspace.amazonaws.com",
            hostname: "finspace.{region}.amazonaws.com",
            fipsHostname: "finspace-fips.{region}.amazonaws.com",
        },
    },
    "finspace-api": {
        aws: {
            arn: "arn:aws:finspace-api:{region}:{account-id}:{resource-id}",
            principal: "finspace-api.amazonaws.com",
            hostname: "finspace-api.{region}.amazonaws.com",
            fipsHostname: "finspace-api-fips.{region}.amazonaws.com",
        },
    },
    firehose: {
        aws: {
            arn: "arn:aws:firehose:{region}:{account-id}:{resource-id}",
            principal: "firehose.amazonaws.com",
            hostname: "firehose.{region}.amazonaws.com",
            fipsHostname: "firehose-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:firehose:{region}:{account-id}:{resource-id}",
            principal: "firehose.amazonaws.com.cn",
            hostname: "firehose.{region}.amazonaws.com.cn",
            fipsHostname: "firehose-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:firehose:{region}:{account-id}:{resource-id}",
            principal: "firehose.amazonaws.com",
            hostname: "firehose.{region}.amazonaws.com",
            fipsHostname: "firehose-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:firehose:{region}:{account-id}:{resource-id}",
            principal: "firehose.c2s.ic.gov",
            hostname: "firehose.{region}.c2s.ic.gov",
            fipsHostname: "firehose-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:firehose:{region}:{account-id}:{resource-id}",
            principal: "firehose.sc2s.sgov.gov",
            hostname: "firehose.{region}.sc2s.sgov.gov",
            fipsHostname: "firehose-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:firehose:{region}:{account-id}:{resource-id}",
            principal: "firehose.cloud.adc-e.uk",
            hostname: "firehose.{region}.cloud.adc-e.uk",
            fipsHostname: "firehose-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:firehose:{region}:{account-id}:{resource-id}",
            principal: "firehose.csp.hci.ic.gov",
            hostname: "firehose.{region}.csp.hci.ic.gov",
            fipsHostname: "firehose-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:firehose:{region}:{account-id}:{resource-id}",
            principal: "firehose.amazonaws.com",
            hostname: "firehose.{region}.amazonaws.eu",
            fipsHostname: "firehose-fips.{region}.amazonaws.eu",
        },
    },
    fms: {
        aws: {
            arn: "arn:aws:fms:{region}:{account-id}:{resource-id}",
            principal: "fms.amazonaws.com",
            hostname: "fms.{region}.amazonaws.com",
            fipsHostname: "fms-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:fms:{region}:{account-id}:{resource-id}",
            principal: "fms.amazonaws.com.cn",
            hostname: "fms.{region}.amazonaws.com.cn",
            fipsHostname: "fms-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:fms:{region}:{account-id}:{resource-id}",
            principal: "fms.amazonaws.com",
            hostname: "fms.{region}.amazonaws.com",
            fipsHostname: "fms-fips.{region}.amazonaws.com",
        },
    },
    forecast: {
        aws: {
            arn: "arn:aws:forecast:{region}:{account-id}:{resource-id}",
            principal: "forecast.amazonaws.com",
            hostname: "forecast.{region}.amazonaws.com",
            fipsHostname: "forecast-fips.{region}.amazonaws.com",
        },
    },
    forecastquery: {
        aws: {
            arn: "arn:aws:forecastquery:{region}:{account-id}:{resource-id}",
            principal: "forecastquery.amazonaws.com",
            hostname: "forecastquery.{region}.amazonaws.com",
            fipsHostname: "forecastquery-fips.{region}.amazonaws.com",
        },
    },
    frauddetector: {
        aws: {
            arn: "arn:aws:frauddetector:{region}:{account-id}:{resource-id}",
            principal: "frauddetector.amazonaws.com",
            hostname: "frauddetector.{region}.amazonaws.com",
            fipsHostname: "frauddetector-fips.{region}.amazonaws.com",
        },
    },
    fsx: {
        aws: {
            arn: "arn:aws:fsx:{region}:{account-id}:{resource-id}",
            principal: "fsx.amazonaws.com",
            hostname: "fsx.{region}.amazonaws.com",
            fipsHostname: "fsx-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:fsx:{region}:{account-id}:{resource-id}",
            principal: "fsx.amazonaws.com.cn",
            hostname: "fsx.{region}.amazonaws.com.cn",
            fipsHostname: "fsx-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:fsx:{region}:{account-id}:{resource-id}",
            principal: "fsx.amazonaws.com",
            hostname: "fsx.{region}.amazonaws.com",
            fipsHostname: "fsx-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:fsx:{region}:{account-id}:{resource-id}",
            principal: "fsx.c2s.ic.gov",
            hostname: "fsx.{region}.c2s.ic.gov",
            fipsHostname: "fsx-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:fsx:{region}:{account-id}:{resource-id}",
            principal: "fsx.sc2s.sgov.gov",
            hostname: "fsx.{region}.sc2s.sgov.gov",
            fipsHostname: "fsx-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:fsx:{region}:{account-id}:{resource-id}",
            principal: "fsx.csp.hci.ic.gov",
            hostname: "fsx.{region}.csp.hci.ic.gov",
            fipsHostname: "fsx-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:fsx:{region}:{account-id}:{resource-id}",
            principal: "fsx.amazonaws.com",
            hostname: "fsx.{region}.amazonaws.eu",
            fipsHostname: "fsx-fips.{region}.amazonaws.eu",
        },
    },
    gamelift: {
        aws: {
            arn: "arn:aws:gamelift:{region}:{account-id}:{resource-id}",
            principal: "gamelift.amazonaws.com",
            hostname: "gamelift.{region}.amazonaws.com",
            fipsHostname: "gamelift-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:gamelift:{region}:{account-id}:{resource-id}",
            principal: "gamelift.amazonaws.com.cn",
            hostname: "gamelift.{region}.amazonaws.com.cn",
            fipsHostname: "gamelift-fips.{region}.amazonaws.com.cn",
        },
    },
    gameliftstreams: {
        aws: {
            arn: "arn:aws:gameliftstreams:{region}:{account-id}:{resource-id}",
            principal: "gameliftstreams.amazonaws.com",
            hostname: "gameliftstreams.{region}.amazonaws.com",
            fipsHostname: "gameliftstreams-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:gameliftstreams:{region}:{account-id}:{resource-id}",
            principal: "gameliftstreams.amazonaws.com.cn",
            hostname: "gameliftstreams.{region}.amazonaws.com.cn",
            fipsHostname: "gameliftstreams-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:gameliftstreams:{region}:{account-id}:{resource-id}",
            principal: "gameliftstreams.amazonaws.com",
            hostname: "gameliftstreams.{region}.amazonaws.com",
            fipsHostname: "gameliftstreams-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:gameliftstreams:{region}:{account-id}:{resource-id}",
            principal: "gameliftstreams.amazonaws.com",
            hostname: "gameliftstreams.{region}.amazonaws.eu",
            fipsHostname: "gameliftstreams-fips.{region}.amazonaws.eu",
        },
    },
    gamesparks: {
        aws: {
            arn: "arn:aws:gamesparks:{region}:{account-id}:{resource-id}",
            principal: "gamesparks.amazonaws.com",
            hostname: "gamesparks.{region}.amazonaws.com",
            fipsHostname: "gamesparks-fips.{region}.amazonaws.com",
        },
    },
    geo: {
        aws: {
            arn: "arn:aws:geo:{region}:{account-id}:{resource-id}",
            principal: "geo.amazonaws.com",
            hostname: "geo.{region}.amazonaws.com",
            fipsHostname: "geo-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:geo:{region}:{account-id}:{resource-id}",
            principal: "geo.amazonaws.com",
            hostname: "geo.{region}.amazonaws.com",
            fipsHostname: "geo-fips.{region}.amazonaws.com",
        },
    },
    glacier: {
        aws: {
            arn: "arn:aws:glacier:{region}:{account-id}:{resource-id}",
            principal: "glacier.amazonaws.com",
            hostname: "glacier.{region}.amazonaws.com",
            fipsHostname: "glacier-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:glacier:{region}:{account-id}:{resource-id}",
            principal: "glacier.amazonaws.com.cn",
            hostname: "glacier.{region}.amazonaws.com.cn",
            fipsHostname: "glacier-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:glacier:{region}:{account-id}:{resource-id}",
            principal: "glacier.amazonaws.com",
            hostname: "glacier.{region}.amazonaws.com",
            fipsHostname: "glacier-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:glacier:{region}:{account-id}:{resource-id}",
            principal: "glacier.c2s.ic.gov",
            hostname: "glacier.{region}.c2s.ic.gov",
            fipsHostname: "glacier-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:glacier:{region}:{account-id}:{resource-id}",
            principal: "glacier.sc2s.sgov.gov",
            hostname: "glacier.{region}.sc2s.sgov.gov",
            fipsHostname: "glacier-fips.{region}.sc2s.sgov.gov",
        },
    },
    globalaccelerator: {
        aws: {
            arn: "arn:aws:globalaccelerator:{region}:{account-id}:{resource-id}",
            principal: "globalaccelerator.amazonaws.com",
            hostname: "globalaccelerator.{region}.amazonaws.com",
            fipsHostname: "globalaccelerator-fips.{region}.amazonaws.com",
        },
    },
    glue: {
        aws: {
            arn: "arn:aws:glue:{region}:{account-id}:{resource-id}",
            principal: "glue.amazonaws.com",
            hostname: "glue.{region}.amazonaws.com",
            fipsHostname: "glue-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:glue:{region}:{account-id}:{resource-id}",
            principal: "glue.amazonaws.com.cn",
            hostname: "glue.{region}.amazonaws.com.cn",
            fipsHostname: "glue-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:glue:{region}:{account-id}:{resource-id}",
            principal: "glue.amazonaws.com",
            hostname: "glue.{region}.amazonaws.com",
            fipsHostname: "glue-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:glue:{region}:{account-id}:{resource-id}",
            principal: "glue.c2s.ic.gov",
            hostname: "glue.{region}.c2s.ic.gov",
            fipsHostname: "glue-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:glue:{region}:{account-id}:{resource-id}",
            principal: "glue.sc2s.sgov.gov",
            hostname: "glue.{region}.sc2s.sgov.gov",
            fipsHostname: "glue-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:glue:{region}:{account-id}:{resource-id}",
            principal: "glue.cloud.adc-e.uk",
            hostname: "glue.{region}.cloud.adc-e.uk",
            fipsHostname: "glue-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:glue:{region}:{account-id}:{resource-id}",
            principal: "glue.csp.hci.ic.gov",
            hostname: "glue.{region}.csp.hci.ic.gov",
            fipsHostname: "glue-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:glue:{region}:{account-id}:{resource-id}",
            principal: "glue.amazonaws.com",
            hostname: "glue.{region}.amazonaws.eu",
            fipsHostname: "glue-fips.{region}.amazonaws.eu",
        },
    },
    grafana: {
        aws: {
            arn: "arn:aws:grafana:{region}:{account-id}:{resource-id}",
            principal: "grafana.amazonaws.com",
            hostname: "grafana.{region}.amazonaws.com",
            fipsHostname: "grafana-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:grafana:{region}:{account-id}:{resource-id}",
            principal: "grafana.amazonaws.com",
            hostname: "grafana.{region}.amazonaws.com",
            fipsHostname: "grafana-fips.{region}.amazonaws.com",
        },
    },
    greengrass: {
        aws: {
            arn: "arn:aws:greengrass:{region}:{account-id}:{resource-id}",
            principal: "greengrass.amazonaws.com",
            hostname: "greengrass.{region}.amazonaws.com",
            fipsHostname: "greengrass-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:greengrass:{region}:{account-id}:{resource-id}",
            principal: "greengrass.amazonaws.com.cn",
            hostname: "greengrass.{region}.amazonaws.com.cn",
            fipsHostname: "greengrass-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:greengrass:{region}:{account-id}:{resource-id}",
            principal: "greengrass.amazonaws.com",
            hostname: "greengrass.{region}.amazonaws.com",
            fipsHostname: "greengrass-fips.{region}.amazonaws.com",
        },
    },
    groundstation: {
        aws: {
            arn: "arn:aws:groundstation:{region}:{account-id}:{resource-id}",
            principal: "groundstation.amazonaws.com",
            hostname: "groundstation.{region}.amazonaws.com",
            fipsHostname: "groundstation-fips.{region}.amazonaws.com",
        },
    },
    guardduty: {
        aws: {
            arn: "arn:aws:guardduty:{region}:{account-id}:{resource-id}",
            principal: "guardduty.amazonaws.com",
            hostname: "guardduty.{region}.amazonaws.com",
            fipsHostname: "guardduty-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:guardduty:{region}:{account-id}:{resource-id}",
            principal: "guardduty.amazonaws.com.cn",
            hostname: "guardduty.{region}.amazonaws.com.cn",
            fipsHostname: "guardduty-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:guardduty:{region}:{account-id}:{resource-id}",
            principal: "guardduty.amazonaws.com",
            hostname: "guardduty.{region}.amazonaws.com",
            fipsHostname: "guardduty-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:guardduty:{region}:{account-id}:{resource-id}",
            principal: "guardduty.c2s.ic.gov",
            hostname: "guardduty.{region}.c2s.ic.gov",
            fipsHostname: "guardduty-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:guardduty:{region}:{account-id}:{resource-id}",
            principal: "guardduty.sc2s.sgov.gov",
            hostname: "guardduty.{region}.sc2s.sgov.gov",
            fipsHostname: "guardduty-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:guardduty:{region}:{account-id}:{resource-id}",
            principal: "guardduty.csp.hci.ic.gov",
            hostname: "guardduty.{region}.csp.hci.ic.gov",
            fipsHostname: "guardduty-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:guardduty:{region}:{account-id}:{resource-id}",
            principal: "guardduty.amazonaws.com",
            hostname: "guardduty.{region}.amazonaws.eu",
            fipsHostname: "guardduty-fips.{region}.amazonaws.eu",
        },
    },
    health: {
        aws: {
            arn: "arn:aws:health:{region}:{account-id}:{resource-id}",
            principal: "health.amazonaws.com",
            hostname: "health.{region}.amazonaws.com",
            fipsHostname: "health-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:health:{region}:{account-id}:{resource-id}",
            principal: "health.amazonaws.com.cn",
            hostname: "health.{region}.amazonaws.com.cn",
            fipsHostname: "health-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:health:{region}:{account-id}:{resource-id}",
            principal: "health.amazonaws.com",
            hostname: "health.{region}.amazonaws.com",
            fipsHostname: "health-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:health:{region}:{account-id}:{resource-id}",
            principal: "health.c2s.ic.gov",
            hostname: "health.{region}.c2s.ic.gov",
            fipsHostname: "health-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:health:{region}:{account-id}:{resource-id}",
            principal: "health.sc2s.sgov.gov",
            hostname: "health.{region}.sc2s.sgov.gov",
            fipsHostname: "health-fips.{region}.sc2s.sgov.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:health:{region}:{account-id}:{resource-id}",
            principal: "health.amazonaws.com",
            hostname: "health.{region}.amazonaws.eu",
            fipsHostname: "health-fips.{region}.amazonaws.eu",
        },
    },
    healthlake: {
        aws: {
            arn: "arn:aws:healthlake:{region}:{account-id}:{resource-id}",
            principal: "healthlake.amazonaws.com",
            hostname: "healthlake.{region}.amazonaws.com",
            fipsHostname: "healthlake-fips.{region}.amazonaws.com",
        },
    },
    honeycode: {
        aws: {
            arn: "arn:aws:honeycode:{region}:{account-id}:{resource-id}",
            principal: "honeycode.amazonaws.com",
            hostname: "honeycode.{region}.amazonaws.com",
            fipsHostname: "honeycode-fips.{region}.amazonaws.com",
        },
    },
    iam: {
        aws: {
            arn: "arn:aws:iam:{region}:{account-id}:{resource-id}",
            principal: "iam.amazonaws.com",
            hostname: "iam.{region}.amazonaws.com",
            fipsHostname: "iam-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:iam:{region}:{account-id}:{resource-id}",
            principal: "iam.amazonaws.com.cn",
            hostname: "iam.{region}.amazonaws.com.cn",
            fipsHostname: "iam-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:iam:{region}:{account-id}:{resource-id}",
            principal: "iam.amazonaws.com",
            hostname: "iam.{region}.amazonaws.com",
            fipsHostname: "iam-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:iam:{region}:{account-id}:{resource-id}",
            principal: "iam.c2s.ic.gov",
            hostname: "iam.{region}.c2s.ic.gov",
            fipsHostname: "iam-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:iam:{region}:{account-id}:{resource-id}",
            principal: "iam.sc2s.sgov.gov",
            hostname: "iam.{region}.sc2s.sgov.gov",
            fipsHostname: "iam-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:iam:{region}:{account-id}:{resource-id}",
            principal: "iam.csp.hci.ic.gov",
            hostname: "iam.{region}.csp.hci.ic.gov",
            fipsHostname: "iam-fips.{region}.csp.hci.ic.gov",
        },
    },
    "identity-chime": {
        aws: {
            arn: "arn:aws:identity-chime:{region}:{account-id}:{resource-id}",
            principal: "identity-chime.amazonaws.com",
            hostname: "identity-chime.{region}.amazonaws.com",
            fipsHostname: "identity-chime-fips.{region}.amazonaws.com",
        },
    },
    identitystore: {
        aws: {
            arn: "arn:aws:identitystore:{region}:{account-id}:{resource-id}",
            principal: "identitystore.amazonaws.com",
            hostname: "identitystore.{region}.amazonaws.com",
            fipsHostname: "identitystore-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:identitystore:{region}:{account-id}:{resource-id}",
            principal: "identitystore.amazonaws.com",
            hostname: "identitystore.{region}.amazonaws.com",
            fipsHostname: "identitystore-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:identitystore:{region}:{account-id}:{resource-id}",
            principal: "identitystore.amazonaws.com.cn",
            hostname: "identitystore.{region}.amazonaws.com.cn",
            fipsHostname: "identitystore-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:identitystore:{region}:{account-id}:{resource-id}",
            principal: "identitystore.csp.hci.ic.gov",
            hostname: "identitystore.{region}.csp.hci.ic.gov",
            fipsHostname: "identitystore-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:identitystore:{region}:{account-id}:{resource-id}",
            principal: "identitystore.amazonaws.com",
            hostname: "identitystore.{region}.amazonaws.eu",
            fipsHostname: "identitystore-fips.{region}.amazonaws.eu",
        },
    },
    importexport: {
        aws: {
            arn: "arn:aws:importexport:{region}:{account-id}:{resource-id}",
            principal: "importexport.amazonaws.com",
            hostname: "importexport.{region}.amazonaws.com",
            fipsHostname: "importexport-fips.{region}.amazonaws.com",
        },
    },
    "ingest.timestream": {
        aws: {
            arn: "arn:aws:ingest.timestream:{region}:{account-id}:{resource-id}",
            principal: "ingest.timestream.amazonaws.com",
            hostname: "ingest.timestream.{region}.amazonaws.com",
            fipsHostname: "ingest.timestream-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ingest.timestream:{region}:{account-id}:{resource-id}",
            principal: "ingest.timestream.amazonaws.com",
            hostname: "ingest.timestream.{region}.amazonaws.com",
            fipsHostname: "ingest.timestream-fips.{region}.amazonaws.com",
        },
    },
    inspector: {
        aws: {
            arn: "arn:aws:inspector:{region}:{account-id}:{resource-id}",
            principal: "inspector.amazonaws.com",
            hostname: "inspector.{region}.amazonaws.com",
            fipsHostname: "inspector-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:inspector:{region}:{account-id}:{resource-id}",
            principal: "inspector.amazonaws.com",
            hostname: "inspector.{region}.amazonaws.com",
            fipsHostname: "inspector-fips.{region}.amazonaws.com",
        },
    },
    inspector2: {
        aws: {
            arn: "arn:aws:inspector2:{region}:{account-id}:{resource-id}",
            principal: "inspector2.amazonaws.com",
            hostname: "inspector2.{region}.amazonaws.com",
            fipsHostname: "inspector2-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:inspector2:{region}:{account-id}:{resource-id}",
            principal: "inspector2.amazonaws.com",
            hostname: "inspector2.{region}.amazonaws.com",
            fipsHostname: "inspector2-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:inspector2:{region}:{account-id}:{resource-id}",
            principal: "inspector2.amazonaws.com.cn",
            hostname: "inspector2.{region}.amazonaws.com.cn",
            fipsHostname: "inspector2-fips.{region}.amazonaws.com.cn",
        },
    },
    internetmonitor: {
        aws: {
            arn: "arn:aws:internetmonitor:{region}:{account-id}:{resource-id}",
            principal: "internetmonitor.amazonaws.com",
            hostname: "internetmonitor.{region}.amazonaws.com",
            fipsHostname: "internetmonitor-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:internetmonitor:{region}:{account-id}:{resource-id}",
            principal: "internetmonitor.amazonaws.com.cn",
            hostname: "internetmonitor.{region}.amazonaws.com.cn",
            fipsHostname: "internetmonitor-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:internetmonitor:{region}:{account-id}:{resource-id}",
            principal: "internetmonitor.amazonaws.com",
            hostname: "internetmonitor.{region}.amazonaws.com",
            fipsHostname: "internetmonitor-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:internetmonitor:{region}:{account-id}:{resource-id}",
            principal: "internetmonitor.amazonaws.com",
            hostname: "internetmonitor.{region}.amazonaws.eu",
            fipsHostname: "internetmonitor-fips.{region}.amazonaws.eu",
        },
    },
    iot: {
        aws: {
            arn: "arn:aws:iot:{region}:{account-id}:{resource-id}",
            principal: "iot.amazonaws.com",
            hostname: "iot.{region}.amazonaws.com",
            fipsHostname: "iot-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:iot:{region}:{account-id}:{resource-id}",
            principal: "iot.amazonaws.com.cn",
            hostname: "iot.{region}.amazonaws.com.cn",
            fipsHostname: "iot-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:iot:{region}:{account-id}:{resource-id}",
            principal: "iot.amazonaws.com",
            hostname: "iot.{region}.amazonaws.com",
            fipsHostname: "iot-fips.{region}.amazonaws.com",
        },
    },
    iotanalytics: {
        aws: {
            arn: "arn:aws:iotanalytics:{region}:{account-id}:{resource-id}",
            principal: "iotanalytics.amazonaws.com",
            hostname: "iotanalytics.{region}.amazonaws.com",
            fipsHostname: "iotanalytics-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:iotanalytics:{region}:{account-id}:{resource-id}",
            principal: "iotanalytics.amazonaws.com.cn",
            hostname: "iotanalytics.{region}.amazonaws.com.cn",
            fipsHostname: "iotanalytics-fips.{region}.amazonaws.com.cn",
        },
    },
    iotevents: {
        aws: {
            arn: "arn:aws:iotevents:{region}:{account-id}:{resource-id}",
            principal: "iotevents.amazonaws.com",
            hostname: "iotevents.{region}.amazonaws.com",
            fipsHostname: "iotevents-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:iotevents:{region}:{account-id}:{resource-id}",
            principal: "iotevents.amazonaws.com.cn",
            hostname: "iotevents.{region}.amazonaws.com.cn",
            fipsHostname: "iotevents-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:iotevents:{region}:{account-id}:{resource-id}",
            principal: "iotevents.amazonaws.com",
            hostname: "iotevents.{region}.amazonaws.com",
            fipsHostname: "iotevents-fips.{region}.amazonaws.com",
        },
    },
    ioteventsdata: {
        aws: {
            arn: "arn:aws:ioteventsdata:{region}:{account-id}:{resource-id}",
            principal: "ioteventsdata.amazonaws.com",
            hostname: "ioteventsdata.{region}.amazonaws.com",
            fipsHostname: "ioteventsdata-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ioteventsdata:{region}:{account-id}:{resource-id}",
            principal: "ioteventsdata.amazonaws.com.cn",
            hostname: "ioteventsdata.{region}.amazonaws.com.cn",
            fipsHostname: "ioteventsdata-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ioteventsdata:{region}:{account-id}:{resource-id}",
            principal: "ioteventsdata.amazonaws.com",
            hostname: "ioteventsdata.{region}.amazonaws.com",
            fipsHostname: "ioteventsdata-fips.{region}.amazonaws.com",
        },
    },
    iotfleetwise: {
        aws: {
            arn: "arn:aws:iotfleetwise:{region}:{account-id}:{resource-id}",
            principal: "iotfleetwise.amazonaws.com",
            hostname: "iotfleetwise.{region}.amazonaws.com",
            fipsHostname: "iotfleetwise-fips.{region}.amazonaws.com",
        },
    },
    iotroborunner: {
        aws: {
            arn: "arn:aws:iotroborunner:{region}:{account-id}:{resource-id}",
            principal: "iotroborunner.amazonaws.com",
            hostname: "iotroborunner.{region}.amazonaws.com",
            fipsHostname: "iotroborunner-fips.{region}.amazonaws.com",
        },
    },
    iotsecuredtunneling: {
        aws: {
            arn: "arn:aws:iotsecuredtunneling:{region}:{account-id}:{resource-id}",
            principal: "iotsecuredtunneling.amazonaws.com",
            hostname: "iotsecuredtunneling.{region}.amazonaws.com",
            fipsHostname: "iotsecuredtunneling-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:iotsecuredtunneling:{region}:{account-id}:{resource-id}",
            principal: "iotsecuredtunneling.amazonaws.com.cn",
            hostname: "iotsecuredtunneling.{region}.amazonaws.com.cn",
            fipsHostname: "iotsecuredtunneling-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:iotsecuredtunneling:{region}:{account-id}:{resource-id}",
            principal: "iotsecuredtunneling.amazonaws.com",
            hostname: "iotsecuredtunneling.{region}.amazonaws.com",
            fipsHostname: "iotsecuredtunneling-fips.{region}.amazonaws.com",
        },
    },
    iotsitewise: {
        aws: {
            arn: "arn:aws:iotsitewise:{region}:{account-id}:{resource-id}",
            principal: "iotsitewise.amazonaws.com",
            hostname: "iotsitewise.{region}.amazonaws.com",
            fipsHostname: "iotsitewise-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:iotsitewise:{region}:{account-id}:{resource-id}",
            principal: "iotsitewise.amazonaws.com.cn",
            hostname: "iotsitewise.{region}.amazonaws.com.cn",
            fipsHostname: "iotsitewise-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:iotsitewise:{region}:{account-id}:{resource-id}",
            principal: "iotsitewise.amazonaws.com",
            hostname: "iotsitewise.{region}.amazonaws.com",
            fipsHostname: "iotsitewise-fips.{region}.amazonaws.com",
        },
    },
    iotthingsgraph: {
        aws: {
            arn: "arn:aws:iotthingsgraph:{region}:{account-id}:{resource-id}",
            principal: "iotthingsgraph.amazonaws.com",
            hostname: "iotthingsgraph.{region}.amazonaws.com",
            fipsHostname: "iotthingsgraph-fips.{region}.amazonaws.com",
        },
    },
    iottwinmaker: {
        aws: {
            arn: "arn:aws:iottwinmaker:{region}:{account-id}:{resource-id}",
            principal: "iottwinmaker.amazonaws.com",
            hostname: "iottwinmaker.{region}.amazonaws.com",
            fipsHostname: "iottwinmaker-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:iottwinmaker:{region}:{account-id}:{resource-id}",
            principal: "iottwinmaker.amazonaws.com",
            hostname: "iottwinmaker.{region}.amazonaws.com",
            fipsHostname: "iottwinmaker-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:iottwinmaker:{region}:{account-id}:{resource-id}",
            principal: "iottwinmaker.amazonaws.com.cn",
            hostname: "iottwinmaker.{region}.amazonaws.com.cn",
            fipsHostname: "iottwinmaker-fips.{region}.amazonaws.com.cn",
        },
    },
    iotwireless: {
        aws: {
            arn: "arn:aws:iotwireless:{region}:{account-id}:{resource-id}",
            principal: "iotwireless.amazonaws.com",
            hostname: "iotwireless.{region}.amazonaws.com",
            fipsHostname: "iotwireless-fips.{region}.amazonaws.com",
        },
    },
    ivs: {
        aws: {
            arn: "arn:aws:ivs:{region}:{account-id}:{resource-id}",
            principal: "ivs.amazonaws.com",
            hostname: "ivs.{region}.amazonaws.com",
            fipsHostname: "ivs-fips.{region}.amazonaws.com",
        },
    },
    ivschat: {
        aws: {
            arn: "arn:aws:ivschat:{region}:{account-id}:{resource-id}",
            principal: "ivschat.amazonaws.com",
            hostname: "ivschat.{region}.amazonaws.com",
            fipsHostname: "ivschat-fips.{region}.amazonaws.com",
        },
    },
    ivsrealtime: {
        aws: {
            arn: "arn:aws:ivsrealtime:{region}:{account-id}:{resource-id}",
            principal: "ivsrealtime.amazonaws.com",
            hostname: "ivsrealtime.{region}.amazonaws.com",
            fipsHostname: "ivsrealtime-fips.{region}.amazonaws.com",
        },
    },
    kafka: {
        aws: {
            arn: "arn:aws:kafka:{region}:{account-id}:{resource-id}",
            principal: "kafka.amazonaws.com",
            hostname: "kafka.{region}.amazonaws.com",
            fipsHostname: "kafka-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:kafka:{region}:{account-id}:{resource-id}",
            principal: "kafka.amazonaws.com.cn",
            hostname: "kafka.{region}.amazonaws.com.cn",
            fipsHostname: "kafka-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:kafka:{region}:{account-id}:{resource-id}",
            principal: "kafka.amazonaws.com",
            hostname: "kafka.{region}.amazonaws.com",
            fipsHostname: "kafka-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:kafka:{region}:{account-id}:{resource-id}",
            principal: "kafka.amazonaws.com",
            hostname: "kafka.{region}.amazonaws.eu",
            fipsHostname: "kafka-fips.{region}.amazonaws.eu",
        },
    },
    kafkaconnect: {
        aws: {
            arn: "arn:aws:kafkaconnect:{region}:{account-id}:{resource-id}",
            principal: "kafkaconnect.amazonaws.com",
            hostname: "kafkaconnect.{region}.amazonaws.com",
            fipsHostname: "kafkaconnect-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:kafkaconnect:{region}:{account-id}:{resource-id}",
            principal: "kafkaconnect.amazonaws.com.cn",
            hostname: "kafkaconnect.{region}.amazonaws.com.cn",
            fipsHostname: "kafkaconnect-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:kafkaconnect:{region}:{account-id}:{resource-id}",
            principal: "kafkaconnect.amazonaws.com",
            hostname: "kafkaconnect.{region}.amazonaws.com",
            fipsHostname: "kafkaconnect-fips.{region}.amazonaws.com",
        },
    },
    kendra: {
        aws: {
            arn: "arn:aws:kendra:{region}:{account-id}:{resource-id}",
            principal: "kendra.amazonaws.com",
            hostname: "kendra.{region}.amazonaws.com",
            fipsHostname: "kendra-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:kendra:{region}:{account-id}:{resource-id}",
            principal: "kendra.amazonaws.com",
            hostname: "kendra.{region}.amazonaws.com",
            fipsHostname: "kendra-fips.{region}.amazonaws.com",
        },
    },
    "kendra-ranking": {
        aws: {
            arn: "arn:aws:kendra-ranking:{region}:{account-id}:{resource-id}",
            principal: "kendra-ranking.amazonaws.com",
            hostname: "kendra-ranking.{region}.amazonaws.com",
            fipsHostname: "kendra-ranking-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:kendra-ranking:{region}:{account-id}:{resource-id}",
            principal: "kendra-ranking.amazonaws.com.cn",
            hostname: "kendra-ranking.{region}.amazonaws.com.cn",
            fipsHostname: "kendra-ranking-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:kendra-ranking:{region}:{account-id}:{resource-id}",
            principal: "kendra-ranking.amazonaws.com",
            hostname: "kendra-ranking.{region}.amazonaws.com",
            fipsHostname: "kendra-ranking-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:kendra-ranking:{region}:{account-id}:{resource-id}",
            principal: "kendra-ranking.amazonaws.com",
            hostname: "kendra-ranking.{region}.amazonaws.eu",
            fipsHostname: "kendra-ranking-fips.{region}.amazonaws.eu",
        },
    },
    kinesis: {
        aws: {
            arn: "arn:aws:kinesis:{region}:{account-id}:{resource-id}",
            principal: "kinesis.amazonaws.com",
            hostname: "kinesis.{region}.amazonaws.com",
            fipsHostname: "kinesis-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:kinesis:{region}:{account-id}:{resource-id}",
            principal: "kinesis.amazonaws.com.cn",
            hostname: "kinesis.{region}.amazonaws.com.cn",
            fipsHostname: "kinesis-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:kinesis:{region}:{account-id}:{resource-id}",
            principal: "kinesis.amazonaws.com",
            hostname: "kinesis.{region}.amazonaws.com",
            fipsHostname: "kinesis-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:kinesis:{region}:{account-id}:{resource-id}",
            principal: "kinesis.c2s.ic.gov",
            hostname: "kinesis.{region}.c2s.ic.gov",
            fipsHostname: "kinesis-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:kinesis:{region}:{account-id}:{resource-id}",
            principal: "kinesis.sc2s.sgov.gov",
            hostname: "kinesis.{region}.sc2s.sgov.gov",
            fipsHostname: "kinesis-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:kinesis:{region}:{account-id}:{resource-id}",
            principal: "kinesis.cloud.adc-e.uk",
            hostname: "kinesis.{region}.cloud.adc-e.uk",
            fipsHostname: "kinesis-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:kinesis:{region}:{account-id}:{resource-id}",
            principal: "kinesis.csp.hci.ic.gov",
            hostname: "kinesis.{region}.csp.hci.ic.gov",
            fipsHostname: "kinesis-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:kinesis:{region}:{account-id}:{resource-id}",
            principal: "kinesis.amazonaws.com",
            hostname: "kinesis.{region}.amazonaws.eu",
            fipsHostname: "kinesis-fips.{region}.amazonaws.eu",
        },
    },
    kinesisanalytics: {
        aws: {
            arn: "arn:aws:kinesisanalytics:{region}:{account-id}:{resource-id}",
            principal: "kinesisanalytics.amazonaws.com",
            hostname: "kinesisanalytics.{region}.amazonaws.com",
            fipsHostname: "kinesisanalytics-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:kinesisanalytics:{region}:{account-id}:{resource-id}",
            principal: "kinesisanalytics.amazonaws.com.cn",
            hostname: "kinesisanalytics.{region}.amazonaws.com.cn",
            fipsHostname: "kinesisanalytics-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:kinesisanalytics:{region}:{account-id}:{resource-id}",
            principal: "kinesisanalytics.amazonaws.com",
            hostname: "kinesisanalytics.{region}.amazonaws.com",
            fipsHostname: "kinesisanalytics-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:kinesisanalytics:{region}:{account-id}:{resource-id}",
            principal: "kinesisanalytics.c2s.ic.gov",
            hostname: "kinesisanalytics.{region}.c2s.ic.gov",
            fipsHostname: "kinesisanalytics-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:kinesisanalytics:{region}:{account-id}:{resource-id}",
            principal: "kinesisanalytics.sc2s.sgov.gov",
            hostname: "kinesisanalytics.{region}.sc2s.sgov.gov",
            fipsHostname: "kinesisanalytics-fips.{region}.sc2s.sgov.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:kinesisanalytics:{region}:{account-id}:{resource-id}",
            principal: "kinesisanalytics.amazonaws.com",
            hostname: "kinesisanalytics.{region}.amazonaws.eu",
            fipsHostname: "kinesisanalytics-fips.{region}.amazonaws.eu",
        },
    },
    kinesisvideo: {
        aws: {
            arn: "arn:aws:kinesisvideo:{region}:{account-id}:{resource-id}",
            principal: "kinesisvideo.amazonaws.com",
            hostname: "kinesisvideo.{region}.amazonaws.com",
            fipsHostname: "kinesisvideo-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:kinesisvideo:{region}:{account-id}:{resource-id}",
            principal: "kinesisvideo.amazonaws.com.cn",
            hostname: "kinesisvideo.{region}.amazonaws.com.cn",
            fipsHostname: "kinesisvideo-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:kinesisvideo:{region}:{account-id}:{resource-id}",
            principal: "kinesisvideo.amazonaws.com",
            hostname: "kinesisvideo.{region}.amazonaws.com",
            fipsHostname: "kinesisvideo-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:kinesisvideo:{region}:{account-id}:{resource-id}",
            principal: "kinesisvideo.c2s.ic.gov",
            hostname: "kinesisvideo.{region}.c2s.ic.gov",
            fipsHostname: "kinesisvideo-fips.{region}.c2s.ic.gov",
        },
    },
    kms: {
        aws: {
            arn: "arn:aws:kms:{region}:{account-id}:{resource-id}",
            principal: "kms.amazonaws.com",
            hostname: "kms.{region}.amazonaws.com",
            fipsHostname: "kms-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:kms:{region}:{account-id}:{resource-id}",
            principal: "kms.amazonaws.com.cn",
            hostname: "kms.{region}.amazonaws.com.cn",
            fipsHostname: "kms-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:kms:{region}:{account-id}:{resource-id}",
            principal: "kms.amazonaws.com",
            hostname: "kms.{region}.amazonaws.com",
            fipsHostname: "kms-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:kms:{region}:{account-id}:{resource-id}",
            principal: "kms.c2s.ic.gov",
            hostname: "kms.{region}.c2s.ic.gov",
            fipsHostname: "kms-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:kms:{region}:{account-id}:{resource-id}",
            principal: "kms.sc2s.sgov.gov",
            hostname: "kms.{region}.sc2s.sgov.gov",
            fipsHostname: "kms-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:kms:{region}:{account-id}:{resource-id}",
            principal: "kms.cloud.adc-e.uk",
            hostname: "kms.{region}.cloud.adc-e.uk",
            fipsHostname: "kms-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:kms:{region}:{account-id}:{resource-id}",
            principal: "kms.csp.hci.ic.gov",
            hostname: "kms.{region}.csp.hci.ic.gov",
            fipsHostname: "kms-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:kms:{region}:{account-id}:{resource-id}",
            principal: "kms.amazonaws.com",
            hostname: "kms.{region}.amazonaws.eu",
            fipsHostname: "kms-fips.{region}.amazonaws.eu",
        },
    },
    lakeformation: {
        aws: {
            arn: "arn:aws:lakeformation:{region}:{account-id}:{resource-id}",
            principal: "lakeformation.amazonaws.com",
            hostname: "lakeformation.{region}.amazonaws.com",
            fipsHostname: "lakeformation-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:lakeformation:{region}:{account-id}:{resource-id}",
            principal: "lakeformation.amazonaws.com.cn",
            hostname: "lakeformation.{region}.amazonaws.com.cn",
            fipsHostname: "lakeformation-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:lakeformation:{region}:{account-id}:{resource-id}",
            principal: "lakeformation.amazonaws.com",
            hostname: "lakeformation.{region}.amazonaws.com",
            fipsHostname: "lakeformation-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:lakeformation:{region}:{account-id}:{resource-id}",
            principal: "lakeformation.c2s.ic.gov",
            hostname: "lakeformation.{region}.c2s.ic.gov",
            fipsHostname: "lakeformation-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:lakeformation:{region}:{account-id}:{resource-id}",
            principal: "lakeformation.sc2s.sgov.gov",
            hostname: "lakeformation.{region}.sc2s.sgov.gov",
            fipsHostname: "lakeformation-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:lakeformation:{region}:{account-id}:{resource-id}",
            principal: "lakeformation.cloud.adc-e.uk",
            hostname: "lakeformation.{region}.cloud.adc-e.uk",
            fipsHostname: "lakeformation-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:lakeformation:{region}:{account-id}:{resource-id}",
            principal: "lakeformation.csp.hci.ic.gov",
            hostname: "lakeformation.{region}.csp.hci.ic.gov",
            fipsHostname: "lakeformation-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:lakeformation:{region}:{account-id}:{resource-id}",
            principal: "lakeformation.amazonaws.com",
            hostname: "lakeformation.{region}.amazonaws.eu",
            fipsHostname: "lakeformation-fips.{region}.amazonaws.eu",
        },
    },
    lambda: {
        aws: {
            arn: "arn:aws:lambda:{region}:{account-id}:{resource-id}",
            principal: "lambda.amazonaws.com",
            hostname: "lambda.{region}.amazonaws.com",
            fipsHostname: "lambda-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:lambda:{region}:{account-id}:{resource-id}",
            principal: "lambda.amazonaws.com.cn",
            hostname: "lambda.{region}.amazonaws.com.cn",
            fipsHostname: "lambda-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:lambda:{region}:{account-id}:{resource-id}",
            principal: "lambda.amazonaws.com",
            hostname: "lambda.{region}.amazonaws.com",
            fipsHostname: "lambda-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:lambda:{region}:{account-id}:{resource-id}",
            principal: "lambda.c2s.ic.gov",
            hostname: "lambda.{region}.c2s.ic.gov",
            fipsHostname: "lambda-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:lambda:{region}:{account-id}:{resource-id}",
            principal: "lambda.sc2s.sgov.gov",
            hostname: "lambda.{region}.sc2s.sgov.gov",
            fipsHostname: "lambda-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:lambda:{region}:{account-id}:{resource-id}",
            principal: "lambda.cloud.adc-e.uk",
            hostname: "lambda.{region}.cloud.adc-e.uk",
            fipsHostname: "lambda-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:lambda:{region}:{account-id}:{resource-id}",
            principal: "lambda.csp.hci.ic.gov",
            hostname: "lambda.{region}.csp.hci.ic.gov",
            fipsHostname: "lambda-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:lambda:{region}:{account-id}:{resource-id}",
            principal: "lambda.amazonaws.com",
            hostname: "lambda.{region}.amazonaws.eu",
            fipsHostname: "lambda-fips.{region}.amazonaws.eu",
        },
    },
    "license-manager": {
        aws: {
            arn: "arn:aws:license-manager:{region}:{account-id}:{resource-id}",
            principal: "license-manager.amazonaws.com",
            hostname: "license-manager.{region}.amazonaws.com",
            fipsHostname: "license-manager-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:license-manager:{region}:{account-id}:{resource-id}",
            principal: "license-manager.amazonaws.com.cn",
            hostname: "license-manager.{region}.amazonaws.com.cn",
            fipsHostname: "license-manager-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:license-manager:{region}:{account-id}:{resource-id}",
            principal: "license-manager.amazonaws.com",
            hostname: "license-manager.{region}.amazonaws.com",
            fipsHostname: "license-manager-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:license-manager:{region}:{account-id}:{resource-id}",
            principal: "license-manager.c2s.ic.gov",
            hostname: "license-manager.{region}.c2s.ic.gov",
            fipsHostname: "license-manager-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:license-manager:{region}:{account-id}:{resource-id}",
            principal: "license-manager.sc2s.sgov.gov",
            hostname: "license-manager.{region}.sc2s.sgov.gov",
            fipsHostname: "license-manager-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:license-manager:{region}:{account-id}:{resource-id}",
            principal: "license-manager.cloud.adc-e.uk",
            hostname: "license-manager.{region}.cloud.adc-e.uk",
            fipsHostname: "license-manager-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:license-manager:{region}:{account-id}:{resource-id}",
            principal: "license-manager.csp.hci.ic.gov",
            hostname: "license-manager.{region}.csp.hci.ic.gov",
            fipsHostname: "license-manager-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:license-manager:{region}:{account-id}:{resource-id}",
            principal: "license-manager.amazonaws.com",
            hostname: "license-manager.{region}.amazonaws.eu",
            fipsHostname: "license-manager-fips.{region}.amazonaws.eu",
        },
    },
    "license-manager-linux-subscriptions": {
        aws: {
            arn: "arn:aws:license-manager-linux-subscriptions:{region}:{account-id}:{resource-id}",
            principal: "license-manager-linux-subscriptions.amazonaws.com",
            hostname: "license-manager-linux-subscriptions.{region}.amazonaws.com",
            fipsHostname: "license-manager-linux-subscriptions-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:license-manager-linux-subscriptions:{region}:{account-id}:{resource-id}",
            principal: "license-manager-linux-subscriptions.amazonaws.com.cn",
            hostname: "license-manager-linux-subscriptions.{region}.amazonaws.com.cn",
            fipsHostname: "license-manager-linux-subscriptions-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:license-manager-linux-subscriptions:{region}:{account-id}:{resource-id}",
            principal: "license-manager-linux-subscriptions.amazonaws.com",
            hostname: "license-manager-linux-subscriptions.{region}.amazonaws.com",
            fipsHostname: "license-manager-linux-subscriptions-fips.{region}.amazonaws.com",
        },
    },
    "license-manager-user-subscriptions": {
        aws: {
            arn: "arn:aws:license-manager-user-subscriptions:{region}:{account-id}:{resource-id}",
            principal: "license-manager-user-subscriptions.amazonaws.com",
            hostname: "license-manager-user-subscriptions.{region}.amazonaws.com",
            fipsHostname: "license-manager-user-subscriptions-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:license-manager-user-subscriptions:{region}:{account-id}:{resource-id}",
            principal: "license-manager-user-subscriptions.amazonaws.com",
            hostname: "license-manager-user-subscriptions.{region}.amazonaws.com",
            fipsHostname: "license-manager-user-subscriptions-fips.{region}.amazonaws.com",
        },
    },
    lightsail: {
        aws: {
            arn: "arn:aws:lightsail:{region}:{account-id}:{resource-id}",
            principal: "lightsail.amazonaws.com",
            hostname: "lightsail.{region}.amazonaws.com",
            fipsHostname: "lightsail-fips.{region}.amazonaws.com",
        },
    },
    logs: {
        aws: {
            arn: "arn:aws:logs:{region}:{account-id}:{resource-id}",
            principal: "logs.amazonaws.com",
            hostname: "logs.{region}.amazonaws.com",
            fipsHostname: "logs-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:logs:{region}:{account-id}:{resource-id}",
            principal: "logs.amazonaws.com.cn",
            hostname: "logs.{region}.amazonaws.com.cn",
            fipsHostname: "logs-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:logs:{region}:{account-id}:{resource-id}",
            principal: "logs.amazonaws.com",
            hostname: "logs.{region}.amazonaws.com",
            fipsHostname: "logs-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:logs:{region}:{account-id}:{resource-id}",
            principal: "logs.c2s.ic.gov",
            hostname: "logs.{region}.c2s.ic.gov",
            fipsHostname: "logs-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:logs:{region}:{account-id}:{resource-id}",
            principal: "logs.sc2s.sgov.gov",
            hostname: "logs.{region}.sc2s.sgov.gov",
            fipsHostname: "logs-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:logs:{region}:{account-id}:{resource-id}",
            principal: "logs.cloud.adc-e.uk",
            hostname: "logs.{region}.cloud.adc-e.uk",
            fipsHostname: "logs-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:logs:{region}:{account-id}:{resource-id}",
            principal: "logs.csp.hci.ic.gov",
            hostname: "logs.{region}.csp.hci.ic.gov",
            fipsHostname: "logs-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:logs:{region}:{account-id}:{resource-id}",
            principal: "logs.amazonaws.com",
            hostname: "logs.{region}.amazonaws.eu",
            fipsHostname: "logs-fips.{region}.amazonaws.eu",
        },
    },
    lookoutequipment: {
        aws: {
            arn: "arn:aws:lookoutequipment:{region}:{account-id}:{resource-id}",
            principal: "lookoutequipment.amazonaws.com",
            hostname: "lookoutequipment.{region}.amazonaws.com",
            fipsHostname: "lookoutequipment-fips.{region}.amazonaws.com",
        },
    },
    lookoutmetrics: {
        aws: {
            arn: "arn:aws:lookoutmetrics:{region}:{account-id}:{resource-id}",
            principal: "lookoutmetrics.amazonaws.com",
            hostname: "lookoutmetrics.{region}.amazonaws.com",
            fipsHostname: "lookoutmetrics-fips.{region}.amazonaws.com",
        },
    },
    lookoutvision: {
        aws: {
            arn: "arn:aws:lookoutvision:{region}:{account-id}:{resource-id}",
            principal: "lookoutvision.amazonaws.com",
            hostname: "lookoutvision.{region}.amazonaws.com",
            fipsHostname: "lookoutvision-fips.{region}.amazonaws.com",
        },
    },
    m2: {
        aws: {
            arn: "arn:aws:m2:{region}:{account-id}:{resource-id}",
            principal: "m2.amazonaws.com",
            hostname: "m2.{region}.amazonaws.com",
            fipsHostname: "m2-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:m2:{region}:{account-id}:{resource-id}",
            principal: "m2.amazonaws.com",
            hostname: "m2.{region}.amazonaws.com",
            fipsHostname: "m2-fips.{region}.amazonaws.com",
        },
    },
    machinelearning: {
        aws: {
            arn: "arn:aws:machinelearning:{region}:{account-id}:{resource-id}",
            principal: "machinelearning.amazonaws.com",
            hostname: "machinelearning.{region}.amazonaws.com",
            fipsHostname: "machinelearning-fips.{region}.amazonaws.com",
        },
    },
    macie: {
        aws: {
            arn: "arn:aws:macie:{region}:{account-id}:{resource-id}",
            principal: "macie.amazonaws.com",
            hostname: "macie.{region}.amazonaws.com",
            fipsHostname: "macie-fips.{region}.amazonaws.com",
        },
    },
    macie2: {
        aws: {
            arn: "arn:aws:macie2:{region}:{account-id}:{resource-id}",
            principal: "macie2.amazonaws.com",
            hostname: "macie2.{region}.amazonaws.com",
            fipsHostname: "macie2-fips.{region}.amazonaws.com",
        },
    },
    managedblockchain: {
        aws: {
            arn: "arn:aws:managedblockchain:{region}:{account-id}:{resource-id}",
            principal: "managedblockchain.amazonaws.com",
            hostname: "managedblockchain.{region}.amazonaws.com",
            fipsHostname: "managedblockchain-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:managedblockchain:{region}:{account-id}:{resource-id}",
            principal: "managedblockchain.amazonaws.com",
            hostname: "managedblockchain.{region}.amazonaws.com",
            fipsHostname: "managedblockchain-fips.{region}.amazonaws.com",
        },
    },
    "managedblockchain-query": {
        aws: {
            arn: "arn:aws:managedblockchain-query:{region}:{account-id}:{resource-id}",
            principal: "managedblockchain-query.amazonaws.com",
            hostname: "managedblockchain-query.{region}.amazonaws.com",
            fipsHostname: "managedblockchain-query-fips.{region}.amazonaws.com",
        },
    },
    marketplacecommerceanalytics: {
        aws: {
            arn: "arn:aws:marketplacecommerceanalytics:{region}:{account-id}:{resource-id}",
            principal: "marketplacecommerceanalytics.amazonaws.com",
            hostname: "marketplacecommerceanalytics.{region}.amazonaws.com",
            fipsHostname: "marketplacecommerceanalytics-fips.{region}.amazonaws.com",
        },
    },
    "media-pipelines-chime": {
        aws: {
            arn: "arn:aws:media-pipelines-chime:{region}:{account-id}:{resource-id}",
            principal: "media-pipelines-chime.amazonaws.com",
            hostname: "media-pipelines-chime.{region}.amazonaws.com",
            fipsHostname: "media-pipelines-chime-fips.{region}.amazonaws.com",
        },
    },
    mediaconnect: {
        aws: {
            arn: "arn:aws:mediaconnect:{region}:{account-id}:{resource-id}",
            principal: "mediaconnect.amazonaws.com",
            hostname: "mediaconnect.{region}.amazonaws.com",
            fipsHostname: "mediaconnect-fips.{region}.amazonaws.com",
        },
    },
    mediaconvert: {
        aws: {
            arn: "arn:aws:mediaconvert:{region}:{account-id}:{resource-id}",
            principal: "mediaconvert.amazonaws.com",
            hostname: "mediaconvert.{region}.amazonaws.com",
            fipsHostname: "mediaconvert-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:mediaconvert:{region}:{account-id}:{resource-id}",
            principal: "mediaconvert.amazonaws.com.cn",
            hostname: "mediaconvert.{region}.amazonaws.com.cn",
            fipsHostname: "mediaconvert-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:mediaconvert:{region}:{account-id}:{resource-id}",
            principal: "mediaconvert.amazonaws.com",
            hostname: "mediaconvert.{region}.amazonaws.com",
            fipsHostname: "mediaconvert-fips.{region}.amazonaws.com",
        },
    },
    medialive: {
        aws: {
            arn: "arn:aws:medialive:{region}:{account-id}:{resource-id}",
            principal: "medialive.amazonaws.com",
            hostname: "medialive.{region}.amazonaws.com",
            fipsHostname: "medialive-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:medialive:{region}:{account-id}:{resource-id}",
            principal: "medialive.c2s.ic.gov",
            hostname: "medialive.{region}.c2s.ic.gov",
            fipsHostname: "medialive-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:medialive:{region}:{account-id}:{resource-id}",
            principal: "medialive.sc2s.sgov.gov",
            hostname: "medialive.{region}.sc2s.sgov.gov",
            fipsHostname: "medialive-fips.{region}.sc2s.sgov.gov",
        },
    },
    mediapackage: {
        aws: {
            arn: "arn:aws:mediapackage:{region}:{account-id}:{resource-id}",
            principal: "mediapackage.amazonaws.com",
            hostname: "mediapackage.{region}.amazonaws.com",
            fipsHostname: "mediapackage-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:mediapackage:{region}:{account-id}:{resource-id}",
            principal: "mediapackage.c2s.ic.gov",
            hostname: "mediapackage.{region}.c2s.ic.gov",
            fipsHostname: "mediapackage-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:mediapackage:{region}:{account-id}:{resource-id}",
            principal: "mediapackage.sc2s.sgov.gov",
            hostname: "mediapackage.{region}.sc2s.sgov.gov",
            fipsHostname: "mediapackage-fips.{region}.sc2s.sgov.gov",
        },
    },
    "mediapackage-vod": {
        aws: {
            arn: "arn:aws:mediapackage-vod:{region}:{account-id}:{resource-id}",
            principal: "mediapackage-vod.amazonaws.com",
            hostname: "mediapackage-vod.{region}.amazonaws.com",
            fipsHostname: "mediapackage-vod-fips.{region}.amazonaws.com",
        },
    },
    mediapackagev2: {
        aws: {
            arn: "arn:aws:mediapackagev2:{region}:{account-id}:{resource-id}",
            principal: "mediapackagev2.amazonaws.com",
            hostname: "mediapackagev2.{region}.amazonaws.com",
            fipsHostname: "mediapackagev2-fips.{region}.amazonaws.com",
        },
    },
    mediastore: {
        aws: {
            arn: "arn:aws:mediastore:{region}:{account-id}:{resource-id}",
            principal: "mediastore.amazonaws.com",
            hostname: "mediastore.{region}.amazonaws.com",
            fipsHostname: "mediastore-fips.{region}.amazonaws.com",
        },
    },
    "meetings-chime": {
        aws: {
            arn: "arn:aws:meetings-chime:{region}:{account-id}:{resource-id}",
            principal: "meetings-chime.amazonaws.com",
            hostname: "meetings-chime.{region}.amazonaws.com",
            fipsHostname: "meetings-chime-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:meetings-chime:{region}:{account-id}:{resource-id}",
            principal: "meetings-chime.amazonaws.com",
            hostname: "meetings-chime.{region}.amazonaws.com",
            fipsHostname: "meetings-chime-fips.{region}.amazonaws.com",
        },
    },
    "memory-db": {
        aws: {
            arn: "arn:aws:memory-db:{region}:{account-id}:{resource-id}",
            principal: "memory-db.amazonaws.com",
            hostname: "memory-db.{region}.amazonaws.com",
            fipsHostname: "memory-db-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:memory-db:{region}:{account-id}:{resource-id}",
            principal: "memory-db.amazonaws.com.cn",
            hostname: "memory-db.{region}.amazonaws.com.cn",
            fipsHostname: "memory-db-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:memory-db:{region}:{account-id}:{resource-id}",
            principal: "memory-db.amazonaws.com",
            hostname: "memory-db.{region}.amazonaws.com",
            fipsHostname: "memory-db-fips.{region}.amazonaws.com",
        },
    },
    "messaging-chime": {
        aws: {
            arn: "arn:aws:messaging-chime:{region}:{account-id}:{resource-id}",
            principal: "messaging-chime.amazonaws.com",
            hostname: "messaging-chime.{region}.amazonaws.com",
            fipsHostname: "messaging-chime-fips.{region}.amazonaws.com",
        },
    },
    "metering.marketplace": {
        aws: {
            arn: "arn:aws:metering.marketplace:{region}:{account-id}:{resource-id}",
            principal: "metering.marketplace.amazonaws.com",
            hostname: "metering.marketplace.{region}.amazonaws.com",
            fipsHostname: "metering.marketplace-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:metering.marketplace:{region}:{account-id}:{resource-id}",
            principal: "metering.marketplace.amazonaws.com",
            hostname: "metering.marketplace.{region}.amazonaws.com",
            fipsHostname: "metering.marketplace-fips.{region}.amazonaws.com",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:metering.marketplace:{region}:{account-id}:{resource-id}",
            principal: "metering.marketplace.sc2s.sgov.gov",
            hostname: "metering.marketplace.{region}.sc2s.sgov.gov",
            fipsHostname: "metering.marketplace-fips.{region}.sc2s.sgov.gov",
        },
        "aws-cn": {
            arn: "arn:aws-cn:metering.marketplace:{region}:{account-id}:{resource-id}",
            principal: "metering.marketplace.amazonaws.com.cn",
            hostname: "metering.marketplace.{region}.amazonaws.com.cn",
            fipsHostname: "metering.marketplace-fips.{region}.amazonaws.com.cn",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:metering.marketplace:{region}:{account-id}:{resource-id}",
            principal: "metering.marketplace.amazonaws.com",
            hostname: "metering.marketplace.{region}.amazonaws.eu",
            fipsHostname: "metering.marketplace-fips.{region}.amazonaws.eu",
        },
    },
    "metrics.sagemaker": {
        aws: {
            arn: "arn:aws:metrics.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "metrics.sagemaker.amazonaws.com",
            hostname: "metrics.sagemaker.{region}.amazonaws.com",
            fipsHostname: "metrics.sagemaker-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:metrics.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "metrics.sagemaker.amazonaws.com.cn",
            hostname: "metrics.sagemaker.{region}.amazonaws.com.cn",
            fipsHostname: "metrics.sagemaker-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:metrics.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "metrics.sagemaker.amazonaws.com",
            hostname: "metrics.sagemaker.{region}.amazonaws.com",
            fipsHostname: "metrics.sagemaker-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:metrics.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "metrics.sagemaker.c2s.ic.gov",
            hostname: "metrics.sagemaker.{region}.c2s.ic.gov",
            fipsHostname: "metrics.sagemaker-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:metrics.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "metrics.sagemaker.sc2s.sgov.gov",
            hostname: "metrics.sagemaker.{region}.sc2s.sgov.gov",
            fipsHostname: "metrics.sagemaker-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:metrics.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "metrics.sagemaker.cloud.adc-e.uk",
            hostname: "metrics.sagemaker.{region}.cloud.adc-e.uk",
            fipsHostname: "metrics.sagemaker-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:metrics.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "metrics.sagemaker.csp.hci.ic.gov",
            hostname: "metrics.sagemaker.{region}.csp.hci.ic.gov",
            fipsHostname: "metrics.sagemaker-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:metrics.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "metrics.sagemaker.amazonaws.com",
            hostname: "metrics.sagemaker.{region}.amazonaws.eu",
            fipsHostname: "metrics.sagemaker-fips.{region}.amazonaws.eu",
        },
    },
    mgh: {
        aws: {
            arn: "arn:aws:mgh:{region}:{account-id}:{resource-id}",
            principal: "mgh.amazonaws.com",
            hostname: "mgh.{region}.amazonaws.com",
            fipsHostname: "mgh-fips.{region}.amazonaws.com",
        },
    },
    mgn: {
        aws: {
            arn: "arn:aws:mgn:{region}:{account-id}:{resource-id}",
            principal: "mgn.amazonaws.com",
            hostname: "mgn.{region}.amazonaws.com",
            fipsHostname: "mgn-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:mgn:{region}:{account-id}:{resource-id}",
            principal: "mgn.amazonaws.com",
            hostname: "mgn.{region}.amazonaws.com",
            fipsHostname: "mgn-fips.{region}.amazonaws.com",
        },
    },
    "migrationhub-orchestrator": {
        aws: {
            arn: "arn:aws:migrationhub-orchestrator:{region}:{account-id}:{resource-id}",
            principal: "migrationhub-orchestrator.amazonaws.com",
            hostname: "migrationhub-orchestrator.{region}.amazonaws.com",
            fipsHostname: "migrationhub-orchestrator-fips.{region}.amazonaws.com",
        },
    },
    "migrationhub-strategy": {
        aws: {
            arn: "arn:aws:migrationhub-strategy:{region}:{account-id}:{resource-id}",
            principal: "migrationhub-strategy.amazonaws.com",
            hostname: "migrationhub-strategy.{region}.amazonaws.com",
            fipsHostname: "migrationhub-strategy-fips.{region}.amazonaws.com",
        },
    },
    mobileanalytics: {
        aws: {
            arn: "arn:aws:mobileanalytics:{region}:{account-id}:{resource-id}",
            principal: "mobileanalytics.amazonaws.com",
            hostname: "mobileanalytics.{region}.amazonaws.com",
            fipsHostname: "mobileanalytics-fips.{region}.amazonaws.com",
        },
    },
    "models-v2-lex": {
        aws: {
            arn: "arn:aws:models-v2-lex:{region}:{account-id}:{resource-id}",
            principal: "models-v2-lex.amazonaws.com",
            hostname: "models-v2-lex.{region}.amazonaws.com",
            fipsHostname: "models-v2-lex-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:models-v2-lex:{region}:{account-id}:{resource-id}",
            principal: "models-v2-lex.amazonaws.com",
            hostname: "models-v2-lex.{region}.amazonaws.com",
            fipsHostname: "models-v2-lex-fips.{region}.amazonaws.com",
        },
    },
    "models.lex": {
        aws: {
            arn: "arn:aws:models.lex:{region}:{account-id}:{resource-id}",
            principal: "models.lex.amazonaws.com",
            hostname: "models.lex.{region}.amazonaws.com",
            fipsHostname: "models.lex-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:models.lex:{region}:{account-id}:{resource-id}",
            principal: "models.lex.amazonaws.com",
            hostname: "models.lex.{region}.amazonaws.com",
            fipsHostname: "models.lex-fips.{region}.amazonaws.com",
        },
    },
    monitoring: {
        aws: {
            arn: "arn:aws:monitoring:{region}:{account-id}:{resource-id}",
            principal: "monitoring.amazonaws.com",
            hostname: "monitoring.{region}.amazonaws.com",
            fipsHostname: "monitoring-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:monitoring:{region}:{account-id}:{resource-id}",
            principal: "monitoring.amazonaws.com.cn",
            hostname: "monitoring.{region}.amazonaws.com.cn",
            fipsHostname: "monitoring-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:monitoring:{region}:{account-id}:{resource-id}",
            principal: "monitoring.amazonaws.com",
            hostname: "monitoring.{region}.amazonaws.com",
            fipsHostname: "monitoring-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:monitoring:{region}:{account-id}:{resource-id}",
            principal: "monitoring.c2s.ic.gov",
            hostname: "monitoring.{region}.c2s.ic.gov",
            fipsHostname: "monitoring-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:monitoring:{region}:{account-id}:{resource-id}",
            principal: "monitoring.sc2s.sgov.gov",
            hostname: "monitoring.{region}.sc2s.sgov.gov",
            fipsHostname: "monitoring-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:monitoring:{region}:{account-id}:{resource-id}",
            principal: "monitoring.cloud.adc-e.uk",
            hostname: "monitoring.{region}.cloud.adc-e.uk",
            fipsHostname: "monitoring-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:monitoring:{region}:{account-id}:{resource-id}",
            principal: "monitoring.csp.hci.ic.gov",
            hostname: "monitoring.{region}.csp.hci.ic.gov",
            fipsHostname: "monitoring-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:monitoring:{region}:{account-id}:{resource-id}",
            principal: "monitoring.amazonaws.com",
            hostname: "monitoring.{region}.amazonaws.eu",
            fipsHostname: "monitoring-fips.{region}.amazonaws.eu",
        },
    },
    mq: {
        aws: {
            arn: "arn:aws:mq:{region}:{account-id}:{resource-id}",
            principal: "mq.amazonaws.com",
            hostname: "mq.{region}.amazonaws.com",
            fipsHostname: "mq-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:mq:{region}:{account-id}:{resource-id}",
            principal: "mq.amazonaws.com.cn",
            hostname: "mq.{region}.amazonaws.com.cn",
            fipsHostname: "mq-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:mq:{region}:{account-id}:{resource-id}",
            principal: "mq.amazonaws.com",
            hostname: "mq.{region}.amazonaws.com",
            fipsHostname: "mq-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:mq:{region}:{account-id}:{resource-id}",
            principal: "mq.c2s.ic.gov",
            hostname: "mq.{region}.c2s.ic.gov",
            fipsHostname: "mq-fips.{region}.c2s.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:mq:{region}:{account-id}:{resource-id}",
            principal: "mq.amazonaws.com",
            hostname: "mq.{region}.amazonaws.eu",
            fipsHostname: "mq-fips.{region}.amazonaws.eu",
        },
    },
    "mturk-requester": {
        aws: {
            arn: "arn:aws:mturk-requester:{region}:{account-id}:{resource-id}",
            principal: "mturk-requester.amazonaws.com",
            hostname: "mturk-requester.{region}.amazonaws.com",
            fipsHostname: "mturk-requester-fips.{region}.amazonaws.com",
        },
    },
    neptune: {
        aws: {
            arn: "arn:aws:neptune:{region}:{account-id}:{resource-id}",
            principal: "neptune.amazonaws.com",
            hostname: "neptune.{region}.amazonaws.com",
            fipsHostname: "neptune-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:neptune:{region}:{account-id}:{resource-id}",
            principal: "neptune.amazonaws.com.cn",
            hostname: "neptune.{region}.amazonaws.com.cn",
            fipsHostname: "neptune-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:neptune:{region}:{account-id}:{resource-id}",
            principal: "neptune.amazonaws.com",
            hostname: "neptune.{region}.amazonaws.com",
            fipsHostname: "neptune-fips.{region}.amazonaws.com",
        },
    },
    "network-firewall": {
        aws: {
            arn: "arn:aws:network-firewall:{region}:{account-id}:{resource-id}",
            principal: "network-firewall.amazonaws.com",
            hostname: "network-firewall.{region}.amazonaws.com",
            fipsHostname: "network-firewall-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:network-firewall:{region}:{account-id}:{resource-id}",
            principal: "network-firewall.amazonaws.com",
            hostname: "network-firewall.{region}.amazonaws.com",
            fipsHostname: "network-firewall-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:network-firewall:{region}:{account-id}:{resource-id}",
            principal: "network-firewall.amazonaws.com.cn",
            hostname: "network-firewall.{region}.amazonaws.com.cn",
            fipsHostname: "network-firewall-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso": {
            arn: "arn:aws-iso:network-firewall:{region}:{account-id}:{resource-id}",
            principal: "network-firewall.c2s.ic.gov",
            hostname: "network-firewall.{region}.c2s.ic.gov",
            fipsHostname: "network-firewall-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:network-firewall:{region}:{account-id}:{resource-id}",
            principal: "network-firewall.sc2s.sgov.gov",
            hostname: "network-firewall.{region}.sc2s.sgov.gov",
            fipsHostname: "network-firewall-fips.{region}.sc2s.sgov.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:network-firewall:{region}:{account-id}:{resource-id}",
            principal: "network-firewall.amazonaws.com",
            hostname: "network-firewall.{region}.amazonaws.eu",
            fipsHostname: "network-firewall-fips.{region}.amazonaws.eu",
        },
    },
    networkmanager: {
        aws: {
            arn: "arn:aws:networkmanager:{region}:{account-id}:{resource-id}",
            principal: "networkmanager.amazonaws.com",
            hostname: "networkmanager.{region}.amazonaws.com",
            fipsHostname: "networkmanager-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:networkmanager:{region}:{account-id}:{resource-id}",
            principal: "networkmanager.amazonaws.com",
            hostname: "networkmanager.{region}.amazonaws.com",
            fipsHostname: "networkmanager-fips.{region}.amazonaws.com",
        },
    },
    nimble: {
        aws: {
            arn: "arn:aws:nimble:{region}:{account-id}:{resource-id}",
            principal: "nimble.amazonaws.com",
            hostname: "nimble.{region}.amazonaws.com",
            fipsHostname: "nimble-fips.{region}.amazonaws.com",
        },
    },
    notifications: {
        aws: {
            arn: "arn:aws:notifications:{region}:{account-id}:{resource-id}",
            principal: "notifications.amazonaws.com",
            hostname: "notifications.{region}.amazonaws.com",
            fipsHostname: "notifications-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:notifications:{region}:{account-id}:{resource-id}",
            principal: "notifications.amazonaws.com.cn",
            hostname: "notifications.{region}.amazonaws.com.cn",
            fipsHostname: "notifications-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:notifications:{region}:{account-id}:{resource-id}",
            principal: "notifications.amazonaws.com",
            hostname: "notifications.{region}.amazonaws.com",
            fipsHostname: "notifications-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:notifications:{region}:{account-id}:{resource-id}",
            principal: "notifications.amazonaws.com",
            hostname: "notifications.{region}.amazonaws.eu",
            fipsHostname: "notifications-fips.{region}.amazonaws.eu",
        },
    },
    "notifications-contacts": {
        aws: {
            arn: "arn:aws:notifications-contacts:{region}:{account-id}:{resource-id}",
            principal: "notifications-contacts.amazonaws.com",
            hostname: "notifications-contacts.{region}.amazonaws.com",
            fipsHostname: "notifications-contacts-fips.{region}.amazonaws.com",
        },
    },
    "nova-act": {
        aws: {
            arn: "arn:aws:nova-act:{region}:{account-id}:{resource-id}",
            principal: "nova-act.amazonaws.com",
            hostname: "nova-act.{region}.amazonaws.com",
            fipsHostname: "nova-act-fips.{region}.amazonaws.com",
        },
    },
    oam: {
        aws: {
            arn: "arn:aws:oam:{region}:{account-id}:{resource-id}",
            principal: "oam.amazonaws.com",
            hostname: "oam.{region}.amazonaws.com",
            fipsHostname: "oam-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:oam:{region}:{account-id}:{resource-id}",
            principal: "oam.amazonaws.com.cn",
            hostname: "oam.{region}.amazonaws.com.cn",
            fipsHostname: "oam-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:oam:{region}:{account-id}:{resource-id}",
            principal: "oam.amazonaws.com",
            hostname: "oam.{region}.amazonaws.com",
            fipsHostname: "oam-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:oam:{region}:{account-id}:{resource-id}",
            principal: "oam.c2s.ic.gov",
            hostname: "oam.{region}.c2s.ic.gov",
            fipsHostname: "oam-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:oam:{region}:{account-id}:{resource-id}",
            principal: "oam.sc2s.sgov.gov",
            hostname: "oam.{region}.sc2s.sgov.gov",
            fipsHostname: "oam-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:oam:{region}:{account-id}:{resource-id}",
            principal: "oam.cloud.adc-e.uk",
            hostname: "oam.{region}.cloud.adc-e.uk",
            fipsHostname: "oam-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:oam:{region}:{account-id}:{resource-id}",
            principal: "oam.csp.hci.ic.gov",
            hostname: "oam.{region}.csp.hci.ic.gov",
            fipsHostname: "oam-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:oam:{region}:{account-id}:{resource-id}",
            principal: "oam.amazonaws.com",
            hostname: "oam.{region}.amazonaws.eu",
            fipsHostname: "oam-fips.{region}.amazonaws.eu",
        },
    },
    oidc: {
        aws: {
            arn: "arn:aws:oidc:{region}:{account-id}:{resource-id}",
            principal: "oidc.amazonaws.com",
            hostname: "oidc.{region}.amazonaws.com",
            fipsHostname: "oidc-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:oidc:{region}:{account-id}:{resource-id}",
            principal: "oidc.amazonaws.com",
            hostname: "oidc.{region}.amazonaws.com",
            fipsHostname: "oidc-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:oidc:{region}:{account-id}:{resource-id}",
            principal: "oidc.amazonaws.com.cn",
            hostname: "oidc.{region}.amazonaws.com.cn",
            fipsHostname: "oidc-fips.{region}.amazonaws.com.cn",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:oidc:{region}:{account-id}:{resource-id}",
            principal: "oidc.amazonaws.com",
            hostname: "oidc.{region}.amazonaws.eu",
            fipsHostname: "oidc-fips.{region}.amazonaws.eu",
        },
    },
    omics: {
        aws: {
            arn: "arn:aws:omics:{region}:{account-id}:{resource-id}",
            principal: "omics.amazonaws.com",
            hostname: "omics.{region}.amazonaws.com",
            fipsHostname: "omics-fips.{region}.amazonaws.com",
        },
    },
    opsworks: {
        aws: {
            arn: "arn:aws:opsworks:{region}:{account-id}:{resource-id}",
            principal: "opsworks.amazonaws.com",
            hostname: "opsworks.{region}.amazonaws.com",
            fipsHostname: "opsworks-fips.{region}.amazonaws.com",
        },
    },
    "opsworks-cm": {
        aws: {
            arn: "arn:aws:opsworks-cm:{region}:{account-id}:{resource-id}",
            principal: "opsworks-cm.amazonaws.com",
            hostname: "opsworks-cm.{region}.amazonaws.com",
            fipsHostname: "opsworks-cm-fips.{region}.amazonaws.com",
        },
    },
    organizations: {
        aws: {
            arn: "arn:aws:organizations:{region}:{account-id}:{resource-id}",
            principal: "organizations.amazonaws.com",
            hostname: "organizations.{region}.amazonaws.com",
            fipsHostname: "organizations-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:organizations:{region}:{account-id}:{resource-id}",
            principal: "organizations.amazonaws.com.cn",
            hostname: "organizations.{region}.amazonaws.com.cn",
            fipsHostname: "organizations-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:organizations:{region}:{account-id}:{resource-id}",
            principal: "organizations.amazonaws.com",
            hostname: "organizations.{region}.amazonaws.com",
            fipsHostname: "organizations-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:organizations:{region}:{account-id}:{resource-id}",
            principal: "organizations.c2s.ic.gov",
            hostname: "organizations.{region}.c2s.ic.gov",
            fipsHostname: "organizations-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:organizations:{region}:{account-id}:{resource-id}",
            principal: "organizations.sc2s.sgov.gov",
            hostname: "organizations.{region}.sc2s.sgov.gov",
            fipsHostname: "organizations-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:organizations:{region}:{account-id}:{resource-id}",
            principal: "organizations.cloud.adc-e.uk",
            hostname: "organizations.{region}.cloud.adc-e.uk",
            fipsHostname: "organizations-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:organizations:{region}:{account-id}:{resource-id}",
            principal: "organizations.csp.hci.ic.gov",
            hostname: "organizations.{region}.csp.hci.ic.gov",
            fipsHostname: "organizations-fips.{region}.csp.hci.ic.gov",
        },
    },
    osis: {
        aws: {
            arn: "arn:aws:osis:{region}:{account-id}:{resource-id}",
            principal: "osis.amazonaws.com",
            hostname: "osis.{region}.amazonaws.com",
            fipsHostname: "osis-fips.{region}.amazonaws.com",
        },
    },
    outposts: {
        aws: {
            arn: "arn:aws:outposts:{region}:{account-id}:{resource-id}",
            principal: "outposts.amazonaws.com",
            hostname: "outposts.{region}.amazonaws.com",
            fipsHostname: "outposts-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:outposts:{region}:{account-id}:{resource-id}",
            principal: "outposts.amazonaws.com",
            hostname: "outposts.{region}.amazonaws.com",
            fipsHostname: "outposts-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:outposts:{region}:{account-id}:{resource-id}",
            principal: "outposts.c2s.ic.gov",
            hostname: "outposts.{region}.c2s.ic.gov",
            fipsHostname: "outposts-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:outposts:{region}:{account-id}:{resource-id}",
            principal: "outposts.sc2s.sgov.gov",
            hostname: "outposts.{region}.sc2s.sgov.gov",
            fipsHostname: "outposts-fips.{region}.sc2s.sgov.gov",
        },
    },
    "participant.connect": {
        aws: {
            arn: "arn:aws:participant.connect:{region}:{account-id}:{resource-id}",
            principal: "participant.connect.amazonaws.com",
            hostname: "participant.connect.{region}.amazonaws.com",
            fipsHostname: "participant.connect-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:participant.connect:{region}:{account-id}:{resource-id}",
            principal: "participant.connect.amazonaws.com",
            hostname: "participant.connect.{region}.amazonaws.com",
            fipsHostname: "participant.connect-fips.{region}.amazonaws.com",
        },
    },
    "partnercentral-channel": {
        aws: {
            arn: "arn:aws:partnercentral-channel:{region}:{account-id}:{resource-id}",
            principal: "partnercentral-channel.amazonaws.com",
            hostname: "partnercentral-channel.{region}.amazonaws.com",
            fipsHostname: "partnercentral-channel-fips.{region}.amazonaws.com",
        },
    },
    personalize: {
        aws: {
            arn: "arn:aws:personalize:{region}:{account-id}:{resource-id}",
            principal: "personalize.amazonaws.com",
            hostname: "personalize.{region}.amazonaws.com",
            fipsHostname: "personalize-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:personalize:{region}:{account-id}:{resource-id}",
            principal: "personalize.amazonaws.com.cn",
            hostname: "personalize.{region}.amazonaws.com.cn",
            fipsHostname: "personalize-fips.{region}.amazonaws.com.cn",
        },
    },
    pi: {
        aws: {
            arn: "arn:aws:pi:{region}:{account-id}:{resource-id}",
            principal: "pi.amazonaws.com",
            hostname: "pi.{region}.amazonaws.com",
            fipsHostname: "pi-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:pi:{region}:{account-id}:{resource-id}",
            principal: "pi.amazonaws.com.cn",
            hostname: "pi.{region}.amazonaws.com.cn",
            fipsHostname: "pi-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:pi:{region}:{account-id}:{resource-id}",
            principal: "pi.amazonaws.com",
            hostname: "pi.{region}.amazonaws.com",
            fipsHostname: "pi-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:pi:{region}:{account-id}:{resource-id}",
            principal: "pi.c2s.ic.gov",
            hostname: "pi.{region}.c2s.ic.gov",
            fipsHostname: "pi-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:pi:{region}:{account-id}:{resource-id}",
            principal: "pi.sc2s.sgov.gov",
            hostname: "pi.{region}.sc2s.sgov.gov",
            fipsHostname: "pi-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:pi:{region}:{account-id}:{resource-id}",
            principal: "pi.cloud.adc-e.uk",
            hostname: "pi.{region}.cloud.adc-e.uk",
            fipsHostname: "pi-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:pi:{region}:{account-id}:{resource-id}",
            principal: "pi.csp.hci.ic.gov",
            hostname: "pi.{region}.csp.hci.ic.gov",
            fipsHostname: "pi-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:pi:{region}:{account-id}:{resource-id}",
            principal: "pi.amazonaws.com",
            hostname: "pi.{region}.amazonaws.eu",
            fipsHostname: "pi-fips.{region}.amazonaws.eu",
        },
    },
    pinpoint: {
        aws: {
            arn: "arn:aws:pinpoint:{region}:{account-id}:{resource-id}",
            principal: "pinpoint.amazonaws.com",
            hostname: "pinpoint.{region}.amazonaws.com",
            fipsHostname: "pinpoint-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:pinpoint:{region}:{account-id}:{resource-id}",
            principal: "pinpoint.amazonaws.com",
            hostname: "pinpoint.{region}.amazonaws.com",
            fipsHostname: "pinpoint-fips.{region}.amazonaws.com",
        },
    },
    pipes: {
        aws: {
            arn: "arn:aws:pipes:{region}:{account-id}:{resource-id}",
            principal: "pipes.amazonaws.com",
            hostname: "pipes.{region}.amazonaws.com",
            fipsHostname: "pipes-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:pipes:{region}:{account-id}:{resource-id}",
            principal: "pipes.amazonaws.com.cn",
            hostname: "pipes.{region}.amazonaws.com.cn",
            fipsHostname: "pipes-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:pipes:{region}:{account-id}:{resource-id}",
            principal: "pipes.cloud.adc-e.uk",
            hostname: "pipes.{region}.cloud.adc-e.uk",
            fipsHostname: "pipes-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:pipes:{region}:{account-id}:{resource-id}",
            principal: "pipes.csp.hci.ic.gov",
            hostname: "pipes.{region}.csp.hci.ic.gov",
            fipsHostname: "pipes-fips.{region}.csp.hci.ic.gov",
        },
    },
    polly: {
        aws: {
            arn: "arn:aws:polly:{region}:{account-id}:{resource-id}",
            principal: "polly.amazonaws.com",
            hostname: "polly.{region}.amazonaws.com",
            fipsHostname: "polly-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:polly:{region}:{account-id}:{resource-id}",
            principal: "polly.amazonaws.com.cn",
            hostname: "polly.{region}.amazonaws.com.cn",
            fipsHostname: "polly-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:polly:{region}:{account-id}:{resource-id}",
            principal: "polly.amazonaws.com",
            hostname: "polly.{region}.amazonaws.com",
            fipsHostname: "polly-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:polly:{region}:{account-id}:{resource-id}",
            principal: "polly.amazonaws.com",
            hostname: "polly.{region}.amazonaws.eu",
            fipsHostname: "polly-fips.{region}.amazonaws.eu",
        },
    },
    "portal.sso": {
        aws: {
            arn: "arn:aws:portal.sso:{region}:{account-id}:{resource-id}",
            principal: "portal.sso.amazonaws.com",
            hostname: "portal.sso.{region}.amazonaws.com",
            fipsHostname: "portal.sso-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:portal.sso:{region}:{account-id}:{resource-id}",
            principal: "portal.sso.amazonaws.com",
            hostname: "portal.sso.{region}.amazonaws.com",
            fipsHostname: "portal.sso-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:portal.sso:{region}:{account-id}:{resource-id}",
            principal: "portal.sso.amazonaws.com.cn",
            hostname: "portal.sso.{region}.amazonaws.com.cn",
            fipsHostname: "portal.sso-fips.{region}.amazonaws.com.cn",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:portal.sso:{region}:{account-id}:{resource-id}",
            principal: "portal.sso.amazonaws.com",
            hostname: "portal.sso.{region}.amazonaws.eu",
            fipsHostname: "portal.sso-fips.{region}.amazonaws.eu",
        },
    },
    profile: {
        aws: {
            arn: "arn:aws:profile:{region}:{account-id}:{resource-id}",
            principal: "profile.amazonaws.com",
            hostname: "profile.{region}.amazonaws.com",
            fipsHostname: "profile-fips.{region}.amazonaws.com",
        },
    },
    "projects.iot1click": {
        aws: {
            arn: "arn:aws:projects.iot1click:{region}:{account-id}:{resource-id}",
            principal: "projects.iot1click.amazonaws.com",
            hostname: "projects.iot1click.{region}.amazonaws.com",
            fipsHostname: "projects.iot1click-fips.{region}.amazonaws.com",
        },
    },
    proton: {
        aws: {
            arn: "arn:aws:proton:{region}:{account-id}:{resource-id}",
            principal: "proton.amazonaws.com",
            hostname: "proton.{region}.amazonaws.com",
            fipsHostname: "proton-fips.{region}.amazonaws.com",
        },
    },
    qbusiness: {
        aws: {
            arn: "arn:aws:qbusiness:{region}:{account-id}:{resource-id}",
            principal: "qbusiness.amazonaws.com",
            hostname: "qbusiness.{region}.amazonaws.com",
            fipsHostname: "qbusiness-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:qbusiness:{region}:{account-id}:{resource-id}",
            principal: "qbusiness.amazonaws.com.cn",
            hostname: "qbusiness.{region}.amazonaws.com.cn",
            fipsHostname: "qbusiness-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:qbusiness:{region}:{account-id}:{resource-id}",
            principal: "qbusiness.amazonaws.com",
            hostname: "qbusiness.{region}.amazonaws.com",
            fipsHostname: "qbusiness-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:qbusiness:{region}:{account-id}:{resource-id}",
            principal: "qbusiness.amazonaws.com",
            hostname: "qbusiness.{region}.amazonaws.eu",
            fipsHostname: "qbusiness-fips.{region}.amazonaws.eu",
        },
    },
    qldb: {
        aws: {
            arn: "arn:aws:qldb:{region}:{account-id}:{resource-id}",
            principal: "qldb.amazonaws.com",
            hostname: "qldb.{region}.amazonaws.com",
            fipsHostname: "qldb-fips.{region}.amazonaws.com",
        },
    },
    "query.timestream": {
        aws: {
            arn: "arn:aws:query.timestream:{region}:{account-id}:{resource-id}",
            principal: "query.timestream.amazonaws.com",
            hostname: "query.timestream.{region}.amazonaws.com",
            fipsHostname: "query.timestream-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:query.timestream:{region}:{account-id}:{resource-id}",
            principal: "query.timestream.amazonaws.com",
            hostname: "query.timestream.{region}.amazonaws.com",
            fipsHostname: "query.timestream-fips.{region}.amazonaws.com",
        },
    },
    quicksight: {
        aws: {
            arn: "arn:aws:quicksight:{region}:{account-id}:{resource-id}",
            principal: "quicksight.amazonaws.com",
            hostname: "quicksight.{region}.amazonaws.com",
            fipsHostname: "quicksight-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:quicksight:{region}:{account-id}:{resource-id}",
            principal: "quicksight.amazonaws.com",
            hostname: "quicksight.{region}.amazonaws.com",
            fipsHostname: "quicksight-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:quicksight:{region}:{account-id}:{resource-id}",
            principal: "quicksight.amazonaws.com.cn",
            hostname: "quicksight.{region}.amazonaws.com.cn",
            fipsHostname: "quicksight-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:quicksight:{region}:{account-id}:{resource-id}",
            principal: "quicksight.csp.hci.ic.gov",
            hostname: "quicksight.{region}.csp.hci.ic.gov",
            fipsHostname: "quicksight-fips.{region}.csp.hci.ic.gov",
        },
    },
    ram: {
        aws: {
            arn: "arn:aws:ram:{region}:{account-id}:{resource-id}",
            principal: "ram.amazonaws.com",
            hostname: "ram.{region}.amazonaws.com",
            fipsHostname: "ram-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ram:{region}:{account-id}:{resource-id}",
            principal: "ram.amazonaws.com.cn",
            hostname: "ram.{region}.amazonaws.com.cn",
            fipsHostname: "ram-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ram:{region}:{account-id}:{resource-id}",
            principal: "ram.amazonaws.com",
            hostname: "ram.{region}.amazonaws.com",
            fipsHostname: "ram-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:ram:{region}:{account-id}:{resource-id}",
            principal: "ram.c2s.ic.gov",
            hostname: "ram.{region}.c2s.ic.gov",
            fipsHostname: "ram-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:ram:{region}:{account-id}:{resource-id}",
            principal: "ram.sc2s.sgov.gov",
            hostname: "ram.{region}.sc2s.sgov.gov",
            fipsHostname: "ram-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:ram:{region}:{account-id}:{resource-id}",
            principal: "ram.cloud.adc-e.uk",
            hostname: "ram.{region}.cloud.adc-e.uk",
            fipsHostname: "ram-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ram:{region}:{account-id}:{resource-id}",
            principal: "ram.csp.hci.ic.gov",
            hostname: "ram.{region}.csp.hci.ic.gov",
            fipsHostname: "ram-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:ram:{region}:{account-id}:{resource-id}",
            principal: "ram.amazonaws.com",
            hostname: "ram.{region}.amazonaws.eu",
            fipsHostname: "ram-fips.{region}.amazonaws.eu",
        },
    },
    rbin: {
        aws: {
            arn: "arn:aws:rbin:{region}:{account-id}:{resource-id}",
            principal: "rbin.amazonaws.com",
            hostname: "rbin.{region}.amazonaws.com",
            fipsHostname: "rbin-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:rbin:{region}:{account-id}:{resource-id}",
            principal: "rbin.amazonaws.com.cn",
            hostname: "rbin.{region}.amazonaws.com.cn",
            fipsHostname: "rbin-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:rbin:{region}:{account-id}:{resource-id}",
            principal: "rbin.amazonaws.com",
            hostname: "rbin.{region}.amazonaws.com",
            fipsHostname: "rbin-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:rbin:{region}:{account-id}:{resource-id}",
            principal: "rbin.c2s.ic.gov",
            hostname: "rbin.{region}.c2s.ic.gov",
            fipsHostname: "rbin-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:rbin:{region}:{account-id}:{resource-id}",
            principal: "rbin.sc2s.sgov.gov",
            hostname: "rbin.{region}.sc2s.sgov.gov",
            fipsHostname: "rbin-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:rbin:{region}:{account-id}:{resource-id}",
            principal: "rbin.cloud.adc-e.uk",
            hostname: "rbin.{region}.cloud.adc-e.uk",
            fipsHostname: "rbin-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:rbin:{region}:{account-id}:{resource-id}",
            principal: "rbin.csp.hci.ic.gov",
            hostname: "rbin.{region}.csp.hci.ic.gov",
            fipsHostname: "rbin-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:rbin:{region}:{account-id}:{resource-id}",
            principal: "rbin.amazonaws.com",
            hostname: "rbin.{region}.amazonaws.eu",
            fipsHostname: "rbin-fips.{region}.amazonaws.eu",
        },
    },
    rds: {
        aws: {
            arn: "arn:aws:rds:{region}:{account-id}:{resource-id}",
            principal: "rds.amazonaws.com",
            hostname: "rds.{region}.amazonaws.com",
            fipsHostname: "rds-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:rds:{region}:{account-id}:{resource-id}",
            principal: "rds.amazonaws.com.cn",
            hostname: "rds.{region}.amazonaws.com.cn",
            fipsHostname: "rds-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:rds:{region}:{account-id}:{resource-id}",
            principal: "rds.amazonaws.com",
            hostname: "rds.{region}.amazonaws.com",
            fipsHostname: "rds-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:rds:{region}:{account-id}:{resource-id}",
            principal: "rds.c2s.ic.gov",
            hostname: "rds.{region}.c2s.ic.gov",
            fipsHostname: "rds-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:rds:{region}:{account-id}:{resource-id}",
            principal: "rds.sc2s.sgov.gov",
            hostname: "rds.{region}.sc2s.sgov.gov",
            fipsHostname: "rds-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:rds:{region}:{account-id}:{resource-id}",
            principal: "rds.cloud.adc-e.uk",
            hostname: "rds.{region}.cloud.adc-e.uk",
            fipsHostname: "rds-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:rds:{region}:{account-id}:{resource-id}",
            principal: "rds.csp.hci.ic.gov",
            hostname: "rds.{region}.csp.hci.ic.gov",
            fipsHostname: "rds-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:rds:{region}:{account-id}:{resource-id}",
            principal: "rds.amazonaws.com",
            hostname: "rds.{region}.amazonaws.eu",
            fipsHostname: "rds-fips.{region}.amazonaws.eu",
        },
    },
    "rds-data": {
        aws: {
            arn: "arn:aws:rds-data:{region}:{account-id}:{resource-id}",
            principal: "rds-data.amazonaws.com",
            hostname: "rds-data.{region}.amazonaws.com",
            fipsHostname: "rds-data-fips.{region}.amazonaws.com",
        },
    },
    redshift: {
        aws: {
            arn: "arn:aws:redshift:{region}:{account-id}:{resource-id}",
            principal: "redshift.amazonaws.com",
            hostname: "redshift.{region}.amazonaws.com",
            fipsHostname: "redshift-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:redshift:{region}:{account-id}:{resource-id}",
            principal: "redshift.amazonaws.com.cn",
            hostname: "redshift.{region}.amazonaws.com.cn",
            fipsHostname: "redshift-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:redshift:{region}:{account-id}:{resource-id}",
            principal: "redshift.amazonaws.com",
            hostname: "redshift.{region}.amazonaws.com",
            fipsHostname: "redshift-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:redshift:{region}:{account-id}:{resource-id}",
            principal: "redshift.c2s.ic.gov",
            hostname: "redshift.{region}.c2s.ic.gov",
            fipsHostname: "redshift-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:redshift:{region}:{account-id}:{resource-id}",
            principal: "redshift.sc2s.sgov.gov",
            hostname: "redshift.{region}.sc2s.sgov.gov",
            fipsHostname: "redshift-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:redshift:{region}:{account-id}:{resource-id}",
            principal: "redshift.cloud.adc-e.uk",
            hostname: "redshift.{region}.cloud.adc-e.uk",
            fipsHostname: "redshift-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:redshift:{region}:{account-id}:{resource-id}",
            principal: "redshift.csp.hci.ic.gov",
            hostname: "redshift.{region}.csp.hci.ic.gov",
            fipsHostname: "redshift-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:redshift:{region}:{account-id}:{resource-id}",
            principal: "redshift.amazonaws.com",
            hostname: "redshift.{region}.amazonaws.eu",
            fipsHostname: "redshift-fips.{region}.amazonaws.eu",
        },
    },
    "redshift-serverless": {
        aws: {
            arn: "arn:aws:redshift-serverless:{region}:{account-id}:{resource-id}",
            principal: "redshift-serverless.amazonaws.com",
            hostname: "redshift-serverless.{region}.amazonaws.com",
            fipsHostname: "redshift-serverless-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:redshift-serverless:{region}:{account-id}:{resource-id}",
            principal: "redshift-serverless.amazonaws.com.cn",
            hostname: "redshift-serverless.{region}.amazonaws.com.cn",
            fipsHostname: "redshift-serverless-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:redshift-serverless:{region}:{account-id}:{resource-id}",
            principal: "redshift-serverless.amazonaws.com",
            hostname: "redshift-serverless.{region}.amazonaws.com",
            fipsHostname: "redshift-serverless-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:redshift-serverless:{region}:{account-id}:{resource-id}",
            principal: "redshift-serverless.cloud.adc-e.uk",
            hostname: "redshift-serverless.{region}.cloud.adc-e.uk",
            fipsHostname: "redshift-serverless-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:redshift-serverless:{region}:{account-id}:{resource-id}",
            principal: "redshift-serverless.csp.hci.ic.gov",
            hostname: "redshift-serverless.{region}.csp.hci.ic.gov",
            fipsHostname: "redshift-serverless-fips.{region}.csp.hci.ic.gov",
        },
    },
    rekognition: {
        aws: {
            arn: "arn:aws:rekognition:{region}:{account-id}:{resource-id}",
            principal: "rekognition.amazonaws.com",
            hostname: "rekognition.{region}.amazonaws.com",
            fipsHostname: "rekognition-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:rekognition:{region}:{account-id}:{resource-id}",
            principal: "rekognition.amazonaws.com",
            hostname: "rekognition.{region}.amazonaws.com",
            fipsHostname: "rekognition-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:rekognition:{region}:{account-id}:{resource-id}",
            principal: "rekognition.cloud.adc-e.uk",
            hostname: "rekognition.{region}.cloud.adc-e.uk",
            fipsHostname: "rekognition-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:rekognition:{region}:{account-id}:{resource-id}",
            principal: "rekognition.csp.hci.ic.gov",
            hostname: "rekognition.{region}.csp.hci.ic.gov",
            fipsHostname: "rekognition-fips.{region}.csp.hci.ic.gov",
        },
    },
    resiliencehub: {
        aws: {
            arn: "arn:aws:resiliencehub:{region}:{account-id}:{resource-id}",
            principal: "resiliencehub.amazonaws.com",
            hostname: "resiliencehub.{region}.amazonaws.com",
            fipsHostname: "resiliencehub-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:resiliencehub:{region}:{account-id}:{resource-id}",
            principal: "resiliencehub.amazonaws.com",
            hostname: "resiliencehub.{region}.amazonaws.com",
            fipsHostname: "resiliencehub-fips.{region}.amazonaws.com",
        },
    },
    "resource-explorer-2": {
        aws: {
            arn: "arn:aws:resource-explorer-2:{region}:{account-id}:{resource-id}",
            principal: "resource-explorer-2.amazonaws.com",
            hostname: "resource-explorer-2.{region}.amazonaws.com",
            fipsHostname: "resource-explorer-2-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:resource-explorer-2:{region}:{account-id}:{resource-id}",
            principal: "resource-explorer-2.amazonaws.com.cn",
            hostname: "resource-explorer-2.{region}.amazonaws.com.cn",
            fipsHostname: "resource-explorer-2-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:resource-explorer-2:{region}:{account-id}:{resource-id}",
            principal: "resource-explorer-2.amazonaws.com",
            hostname: "resource-explorer-2.{region}.amazonaws.com",
            fipsHostname: "resource-explorer-2-fips.{region}.amazonaws.com",
        },
    },
    "resource-groups": {
        aws: {
            arn: "arn:aws:resource-groups:{region}:{account-id}:{resource-id}",
            principal: "resource-groups.amazonaws.com",
            hostname: "resource-groups.{region}.amazonaws.com",
            fipsHostname: "resource-groups-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:resource-groups:{region}:{account-id}:{resource-id}",
            principal: "resource-groups.amazonaws.com.cn",
            hostname: "resource-groups.{region}.amazonaws.com.cn",
            fipsHostname: "resource-groups-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:resource-groups:{region}:{account-id}:{resource-id}",
            principal: "resource-groups.amazonaws.com",
            hostname: "resource-groups.{region}.amazonaws.com",
            fipsHostname: "resource-groups-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:resource-groups:{region}:{account-id}:{resource-id}",
            principal: "resource-groups.c2s.ic.gov",
            hostname: "resource-groups.{region}.c2s.ic.gov",
            fipsHostname: "resource-groups-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:resource-groups:{region}:{account-id}:{resource-id}",
            principal: "resource-groups.sc2s.sgov.gov",
            hostname: "resource-groups.{region}.sc2s.sgov.gov",
            fipsHostname: "resource-groups-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:resource-groups:{region}:{account-id}:{resource-id}",
            principal: "resource-groups.cloud.adc-e.uk",
            hostname: "resource-groups.{region}.cloud.adc-e.uk",
            fipsHostname: "resource-groups-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:resource-groups:{region}:{account-id}:{resource-id}",
            principal: "resource-groups.csp.hci.ic.gov",
            hostname: "resource-groups.{region}.csp.hci.ic.gov",
            fipsHostname: "resource-groups-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:resource-groups:{region}:{account-id}:{resource-id}",
            principal: "resource-groups.amazonaws.com",
            hostname: "resource-groups.{region}.amazonaws.eu",
            fipsHostname: "resource-groups-fips.{region}.amazonaws.eu",
        },
    },
    robomaker: {
        aws: {
            arn: "arn:aws:robomaker:{region}:{account-id}:{resource-id}",
            principal: "robomaker.amazonaws.com",
            hostname: "robomaker.{region}.amazonaws.com",
            fipsHostname: "robomaker-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:robomaker:{region}:{account-id}:{resource-id}",
            principal: "robomaker.amazonaws.com",
            hostname: "robomaker.{region}.amazonaws.com",
            fipsHostname: "robomaker-fips.{region}.amazonaws.com",
        },
    },
    rolesanywhere: {
        aws: {
            arn: "arn:aws:rolesanywhere:{region}:{account-id}:{resource-id}",
            principal: "rolesanywhere.amazonaws.com",
            hostname: "rolesanywhere.{region}.amazonaws.com",
            fipsHostname: "rolesanywhere-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:rolesanywhere:{region}:{account-id}:{resource-id}",
            principal: "rolesanywhere.amazonaws.com.cn",
            hostname: "rolesanywhere.{region}.amazonaws.com.cn",
            fipsHostname: "rolesanywhere-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:rolesanywhere:{region}:{account-id}:{resource-id}",
            principal: "rolesanywhere.amazonaws.com",
            hostname: "rolesanywhere.{region}.amazonaws.com",
            fipsHostname: "rolesanywhere-fips.{region}.amazonaws.com",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:rolesanywhere:{region}:{account-id}:{resource-id}",
            principal: "rolesanywhere.csp.hci.ic.gov",
            hostname: "rolesanywhere.{region}.csp.hci.ic.gov",
            fipsHostname: "rolesanywhere-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:rolesanywhere:{region}:{account-id}:{resource-id}",
            principal: "rolesanywhere.amazonaws.com",
            hostname: "rolesanywhere.{region}.amazonaws.eu",
            fipsHostname: "rolesanywhere-fips.{region}.amazonaws.eu",
        },
    },
    route53: {
        aws: {
            arn: "arn:aws:route53:{region}:{account-id}:{resource-id}",
            principal: "route53.amazonaws.com",
            hostname: "route53.{region}.amazonaws.com",
            fipsHostname: "route53-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:route53:{region}:{account-id}:{resource-id}",
            principal: "route53.amazonaws.com.cn",
            hostname: "route53.{region}.amazonaws.com.cn",
            fipsHostname: "route53-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:route53:{region}:{account-id}:{resource-id}",
            principal: "route53.amazonaws.com",
            hostname: "route53.{region}.amazonaws.com",
            fipsHostname: "route53-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:route53:{region}:{account-id}:{resource-id}",
            principal: "route53.c2s.ic.gov",
            hostname: "route53.{region}.c2s.ic.gov",
            fipsHostname: "route53-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:route53:{region}:{account-id}:{resource-id}",
            principal: "route53.sc2s.sgov.gov",
            hostname: "route53.{region}.sc2s.sgov.gov",
            fipsHostname: "route53-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:route53:{region}:{account-id}:{resource-id}",
            principal: "route53.cloud.adc-e.uk",
            hostname: "route53.{region}.cloud.adc-e.uk",
            fipsHostname: "route53-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:route53:{region}:{account-id}:{resource-id}",
            principal: "route53.csp.hci.ic.gov",
            hostname: "route53.{region}.csp.hci.ic.gov",
            fipsHostname: "route53-fips.{region}.csp.hci.ic.gov",
        },
    },
    "route53-recovery-control-config": {
        aws: {
            arn: "arn:aws:route53-recovery-control-config:{region}:{account-id}:{resource-id}",
            principal: "route53-recovery-control-config.amazonaws.com",
            hostname: "route53-recovery-control-config.{region}.amazonaws.com",
            fipsHostname: "route53-recovery-control-config-fips.{region}.amazonaws.com",
        },
    },
    route53domains: {
        aws: {
            arn: "arn:aws:route53domains:{region}:{account-id}:{resource-id}",
            principal: "route53domains.amazonaws.com",
            hostname: "route53domains.{region}.amazonaws.com",
            fipsHostname: "route53domains-fips.{region}.amazonaws.com",
        },
    },
    route53profiles: {
        aws: {
            arn: "arn:aws:route53profiles:{region}:{account-id}:{resource-id}",
            principal: "route53profiles.amazonaws.com",
            hostname: "route53profiles.{region}.amazonaws.com",
            fipsHostname: "route53profiles-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:route53profiles:{region}:{account-id}:{resource-id}",
            principal: "route53profiles.amazonaws.com.cn",
            hostname: "route53profiles.{region}.amazonaws.com.cn",
            fipsHostname: "route53profiles-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:route53profiles:{region}:{account-id}:{resource-id}",
            principal: "route53profiles.amazonaws.com",
            hostname: "route53profiles.{region}.amazonaws.com",
            fipsHostname: "route53profiles-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:route53profiles:{region}:{account-id}:{resource-id}",
            principal: "route53profiles.cloud.adc-e.uk",
            hostname: "route53profiles.{region}.cloud.adc-e.uk",
            fipsHostname: "route53profiles-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:route53profiles:{region}:{account-id}:{resource-id}",
            principal: "route53profiles.csp.hci.ic.gov",
            hostname: "route53profiles.{region}.csp.hci.ic.gov",
            fipsHostname: "route53profiles-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:route53profiles:{region}:{account-id}:{resource-id}",
            principal: "route53profiles.amazonaws.com",
            hostname: "route53profiles.{region}.amazonaws.eu",
            fipsHostname: "route53profiles-fips.{region}.amazonaws.eu",
        },
    },
    route53resolver: {
        aws: {
            arn: "arn:aws:route53resolver:{region}:{account-id}:{resource-id}",
            principal: "route53resolver.amazonaws.com",
            hostname: "route53resolver.{region}.amazonaws.com",
            fipsHostname: "route53resolver-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:route53resolver:{region}:{account-id}:{resource-id}",
            principal: "route53resolver.amazonaws.com.cn",
            hostname: "route53resolver.{region}.amazonaws.com.cn",
            fipsHostname: "route53resolver-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:route53resolver:{region}:{account-id}:{resource-id}",
            principal: "route53resolver.amazonaws.com",
            hostname: "route53resolver.{region}.amazonaws.com",
            fipsHostname: "route53resolver-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:route53resolver:{region}:{account-id}:{resource-id}",
            principal: "route53resolver.c2s.ic.gov",
            hostname: "route53resolver.{region}.c2s.ic.gov",
            fipsHostname: "route53resolver-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:route53resolver:{region}:{account-id}:{resource-id}",
            principal: "route53resolver.sc2s.sgov.gov",
            hostname: "route53resolver.{region}.sc2s.sgov.gov",
            fipsHostname: "route53resolver-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:route53resolver:{region}:{account-id}:{resource-id}",
            principal: "route53resolver.cloud.adc-e.uk",
            hostname: "route53resolver.{region}.cloud.adc-e.uk",
            fipsHostname: "route53resolver-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:route53resolver:{region}:{account-id}:{resource-id}",
            principal: "route53resolver.csp.hci.ic.gov",
            hostname: "route53resolver.{region}.csp.hci.ic.gov",
            fipsHostname: "route53resolver-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:route53resolver:{region}:{account-id}:{resource-id}",
            principal: "route53resolver.amazonaws.com",
            hostname: "route53resolver.{region}.amazonaws.eu",
            fipsHostname: "route53resolver-fips.{region}.amazonaws.eu",
        },
    },
    rum: {
        aws: {
            arn: "arn:aws:rum:{region}:{account-id}:{resource-id}",
            principal: "rum.amazonaws.com",
            hostname: "rum.{region}.amazonaws.com",
            fipsHostname: "rum-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:rum:{region}:{account-id}:{resource-id}",
            principal: "rum.amazonaws.com",
            hostname: "rum.{region}.amazonaws.com",
            fipsHostname: "rum-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:rum:{region}:{account-id}:{resource-id}",
            principal: "rum.amazonaws.com",
            hostname: "rum.{region}.amazonaws.eu",
            fipsHostname: "rum-fips.{region}.amazonaws.eu",
        },
    },
    "runtime-v2-lex": {
        aws: {
            arn: "arn:aws:runtime-v2-lex:{region}:{account-id}:{resource-id}",
            principal: "runtime-v2-lex.amazonaws.com",
            hostname: "runtime-v2-lex.{region}.amazonaws.com",
            fipsHostname: "runtime-v2-lex-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:runtime-v2-lex:{region}:{account-id}:{resource-id}",
            principal: "runtime-v2-lex.amazonaws.com",
            hostname: "runtime-v2-lex.{region}.amazonaws.com",
            fipsHostname: "runtime-v2-lex-fips.{region}.amazonaws.com",
        },
    },
    "runtime.lex": {
        aws: {
            arn: "arn:aws:runtime.lex:{region}:{account-id}:{resource-id}",
            principal: "runtime.lex.amazonaws.com",
            hostname: "runtime.lex.{region}.amazonaws.com",
            fipsHostname: "runtime.lex-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:runtime.lex:{region}:{account-id}:{resource-id}",
            principal: "runtime.lex.amazonaws.com",
            hostname: "runtime.lex.{region}.amazonaws.com",
            fipsHostname: "runtime.lex-fips.{region}.amazonaws.com",
        },
    },
    "runtime.sagemaker": {
        aws: {
            arn: "arn:aws:runtime.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "runtime.sagemaker.amazonaws.com",
            hostname: "runtime.sagemaker.{region}.amazonaws.com",
            fipsHostname: "runtime.sagemaker-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:runtime.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "runtime.sagemaker.amazonaws.com.cn",
            hostname: "runtime.sagemaker.{region}.amazonaws.com.cn",
            fipsHostname: "runtime.sagemaker-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:runtime.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "runtime.sagemaker.amazonaws.com",
            hostname: "runtime.sagemaker.{region}.amazonaws.com",
            fipsHostname: "runtime.sagemaker-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:runtime.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "runtime.sagemaker.c2s.ic.gov",
            hostname: "runtime.sagemaker.{region}.c2s.ic.gov",
            fipsHostname: "runtime.sagemaker-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:runtime.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "runtime.sagemaker.sc2s.sgov.gov",
            hostname: "runtime.sagemaker.{region}.sc2s.sgov.gov",
            fipsHostname: "runtime.sagemaker-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:runtime.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "runtime.sagemaker.csp.hci.ic.gov",
            hostname: "runtime.sagemaker.{region}.csp.hci.ic.gov",
            fipsHostname: "runtime.sagemaker-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:runtime.sagemaker:{region}:{account-id}:{resource-id}",
            principal: "runtime.sagemaker.amazonaws.com",
            hostname: "runtime.sagemaker.{region}.amazonaws.eu",
            fipsHostname: "runtime.sagemaker-fips.{region}.amazonaws.eu",
        },
    },
    s3: {
        aws: {
            arn: "arn:aws:s3:{region}:{account-id}:{resource-id}",
            principal: "s3.amazonaws.com",
            hostname: "s3.{region}.amazonaws.com",
            fipsHostname: "s3-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:s3:{region}:{account-id}:{resource-id}",
            principal: "s3.amazonaws.com.cn",
            hostname: "s3.{region}.amazonaws.com.cn",
            fipsHostname: "s3-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:s3:{region}:{account-id}:{resource-id}",
            principal: "s3.amazonaws.com",
            hostname: "s3.{region}.amazonaws.com",
            fipsHostname: "s3-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:s3:{region}:{account-id}:{resource-id}",
            principal: "s3.c2s.ic.gov",
            hostname: "s3.{region}.c2s.ic.gov",
            fipsHostname: "s3-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:s3:{region}:{account-id}:{resource-id}",
            principal: "s3.sc2s.sgov.gov",
            hostname: "s3.{region}.sc2s.sgov.gov",
            fipsHostname: "s3-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:s3:{region}:{account-id}:{resource-id}",
            principal: "s3.cloud.adc-e.uk",
            hostname: "s3.{region}.cloud.adc-e.uk",
            fipsHostname: "s3-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:s3:{region}:{account-id}:{resource-id}",
            principal: "s3.csp.hci.ic.gov",
            hostname: "s3.{region}.csp.hci.ic.gov",
            fipsHostname: "s3-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:s3:{region}:{account-id}:{resource-id}",
            principal: "s3.amazonaws.com",
            hostname: "s3.{region}.amazonaws.eu",
            fipsHostname: "s3-fips.{region}.amazonaws.eu",
        },
    },
    "s3-control": {
        aws: {
            arn: "arn:aws:s3-control:{region}:{account-id}:{resource-id}",
            principal: "s3-control.amazonaws.com",
            hostname: "s3-control.{region}.amazonaws.com",
            fipsHostname: "s3-control-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:s3-control:{region}:{account-id}:{resource-id}",
            principal: "s3-control.amazonaws.com.cn",
            hostname: "s3-control.{region}.amazonaws.com.cn",
            fipsHostname: "s3-control-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:s3-control:{region}:{account-id}:{resource-id}",
            principal: "s3-control.amazonaws.com",
            hostname: "s3-control.{region}.amazonaws.com",
            fipsHostname: "s3-control-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:s3-control:{region}:{account-id}:{resource-id}",
            principal: "s3-control.c2s.ic.gov",
            hostname: "s3-control.{region}.c2s.ic.gov",
            fipsHostname: "s3-control-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:s3-control:{region}:{account-id}:{resource-id}",
            principal: "s3-control.sc2s.sgov.gov",
            hostname: "s3-control.{region}.sc2s.sgov.gov",
            fipsHostname: "s3-control-fips.{region}.sc2s.sgov.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:s3-control:{region}:{account-id}:{resource-id}",
            principal: "s3-control.amazonaws.com",
            hostname: "s3-control.{region}.amazonaws.eu",
            fipsHostname: "s3-control-fips.{region}.amazonaws.eu",
        },
    },
    "s3-outposts": {
        aws: {
            arn: "arn:aws:s3-outposts:{region}:{account-id}:{resource-id}",
            principal: "s3-outposts.amazonaws.com",
            hostname: "s3-outposts.{region}.amazonaws.com",
            fipsHostname: "s3-outposts-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:s3-outposts:{region}:{account-id}:{resource-id}",
            principal: "s3-outposts.amazonaws.com",
            hostname: "s3-outposts.{region}.amazonaws.com",
            fipsHostname: "s3-outposts-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:s3-outposts:{region}:{account-id}:{resource-id}",
            principal: "s3-outposts.c2s.ic.gov",
            hostname: "s3-outposts.{region}.c2s.ic.gov",
            fipsHostname: "s3-outposts-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:s3-outposts:{region}:{account-id}:{resource-id}",
            principal: "s3-outposts.sc2s.sgov.gov",
            hostname: "s3-outposts.{region}.sc2s.sgov.gov",
            fipsHostname: "s3-outposts-fips.{region}.sc2s.sgov.gov",
        },
    },
    sagemaker: {
        aws: {
            arn: "arn:aws:sagemaker:{region}:{account-id}:{resource-id}",
            principal: "sagemaker.amazonaws.com",
            hostname: "sagemaker.{region}.amazonaws.com",
            fipsHostname: "sagemaker-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:sagemaker:{region}:{account-id}:{resource-id}",
            principal: "sagemaker.amazonaws.com.cn",
            hostname: "sagemaker.{region}.amazonaws.com.cn",
            fipsHostname: "sagemaker-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:sagemaker:{region}:{account-id}:{resource-id}",
            principal: "sagemaker.amazonaws.com",
            hostname: "sagemaker.{region}.amazonaws.com",
            fipsHostname: "sagemaker-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:sagemaker:{region}:{account-id}:{resource-id}",
            principal: "sagemaker.c2s.ic.gov",
            hostname: "sagemaker.{region}.c2s.ic.gov",
            fipsHostname: "sagemaker-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:sagemaker:{region}:{account-id}:{resource-id}",
            principal: "sagemaker.sc2s.sgov.gov",
            hostname: "sagemaker.{region}.sc2s.sgov.gov",
            fipsHostname: "sagemaker-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:sagemaker:{region}:{account-id}:{resource-id}",
            principal: "sagemaker.cloud.adc-e.uk",
            hostname: "sagemaker.{region}.cloud.adc-e.uk",
            fipsHostname: "sagemaker-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:sagemaker:{region}:{account-id}:{resource-id}",
            principal: "sagemaker.csp.hci.ic.gov",
            hostname: "sagemaker.{region}.csp.hci.ic.gov",
            fipsHostname: "sagemaker-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:sagemaker:{region}:{account-id}:{resource-id}",
            principal: "sagemaker.amazonaws.com",
            hostname: "sagemaker.{region}.amazonaws.eu",
            fipsHostname: "sagemaker-fips.{region}.amazonaws.eu",
        },
    },
    "sagemaker-geospatial": {
        aws: {
            arn: "arn:aws:sagemaker-geospatial:{region}:{account-id}:{resource-id}",
            principal: "sagemaker-geospatial.amazonaws.com",
            hostname: "sagemaker-geospatial.{region}.amazonaws.com",
            fipsHostname: "sagemaker-geospatial-fips.{region}.amazonaws.com",
        },
    },
    savingsplans: {
        aws: {
            arn: "arn:aws:savingsplans:{region}:{account-id}:{resource-id}",
            principal: "savingsplans.amazonaws.com",
            hostname: "savingsplans.{region}.amazonaws.com",
            fipsHostname: "savingsplans-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:savingsplans:{region}:{account-id}:{resource-id}",
            principal: "savingsplans.amazonaws.com.cn",
            hostname: "savingsplans.{region}.amazonaws.com.cn",
            fipsHostname: "savingsplans-fips.{region}.amazonaws.com.cn",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:savingsplans:{region}:{account-id}:{resource-id}",
            principal: "savingsplans.cloud.adc-e.uk",
            hostname: "savingsplans.{region}.cloud.adc-e.uk",
            fipsHostname: "savingsplans-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:savingsplans:{region}:{account-id}:{resource-id}",
            principal: "savingsplans.csp.hci.ic.gov",
            hostname: "savingsplans.{region}.csp.hci.ic.gov",
            fipsHostname: "savingsplans-fips.{region}.csp.hci.ic.gov",
        },
    },
    scheduler: {
        aws: {
            arn: "arn:aws:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.amazonaws.com",
            hostname: "scheduler.{region}.amazonaws.com",
            fipsHostname: "scheduler-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.amazonaws.com.cn",
            hostname: "scheduler.{region}.amazonaws.com.cn",
            fipsHostname: "scheduler-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.amazonaws.com",
            hostname: "scheduler.{region}.amazonaws.com",
            fipsHostname: "scheduler-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.c2s.ic.gov",
            hostname: "scheduler.{region}.c2s.ic.gov",
            fipsHostname: "scheduler-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.sc2s.sgov.gov",
            hostname: "scheduler.{region}.sc2s.sgov.gov",
            fipsHostname: "scheduler-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.cloud.adc-e.uk",
            hostname: "scheduler.{region}.cloud.adc-e.uk",
            fipsHostname: "scheduler-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.csp.hci.ic.gov",
            hostname: "scheduler.{region}.csp.hci.ic.gov",
            fipsHostname: "scheduler-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.amazonaws.com",
            hostname: "scheduler.{region}.amazonaws.eu",
            fipsHostname: "scheduler-fips.{region}.amazonaws.eu",
        },
    },
    schemas: {
        aws: {
            arn: "arn:aws:schemas:{region}:{account-id}:{resource-id}",
            principal: "schemas.amazonaws.com",
            hostname: "schemas.{region}.amazonaws.com",
            fipsHostname: "schemas-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:schemas:{region}:{account-id}:{resource-id}",
            principal: "schemas.amazonaws.com.cn",
            hostname: "schemas.{region}.amazonaws.com.cn",
            fipsHostname: "schemas-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:schemas:{region}:{account-id}:{resource-id}",
            principal: "schemas.amazonaws.com",
            hostname: "schemas.{region}.amazonaws.com",
            fipsHostname: "schemas-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:schemas:{region}:{account-id}:{resource-id}",
            principal: "schemas.cloud.adc-e.uk",
            hostname: "schemas.{region}.cloud.adc-e.uk",
            fipsHostname: "schemas-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:schemas:{region}:{account-id}:{resource-id}",
            principal: "schemas.csp.hci.ic.gov",
            hostname: "schemas.{region}.csp.hci.ic.gov",
            fipsHostname: "schemas-fips.{region}.csp.hci.ic.gov",
        },
    },
    sdb: {
        aws: {
            arn: "arn:aws:sdb:{region}:{account-id}:{resource-id}",
            principal: "sdb.amazonaws.com",
            hostname: "sdb.{region}.amazonaws.com",
            fipsHostname: "sdb-fips.{region}.amazonaws.com",
        },
    },
    secretsmanager: {
        aws: {
            arn: "arn:aws:secretsmanager:{region}:{account-id}:{resource-id}",
            principal: "secretsmanager.amazonaws.com",
            hostname: "secretsmanager.{region}.amazonaws.com",
            fipsHostname: "secretsmanager-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:secretsmanager:{region}:{account-id}:{resource-id}",
            principal: "secretsmanager.amazonaws.com.cn",
            hostname: "secretsmanager.{region}.amazonaws.com.cn",
            fipsHostname: "secretsmanager-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:secretsmanager:{region}:{account-id}:{resource-id}",
            principal: "secretsmanager.amazonaws.com",
            hostname: "secretsmanager.{region}.amazonaws.com",
            fipsHostname: "secretsmanager-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:secretsmanager:{region}:{account-id}:{resource-id}",
            principal: "secretsmanager.c2s.ic.gov",
            hostname: "secretsmanager.{region}.c2s.ic.gov",
            fipsHostname: "secretsmanager-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:secretsmanager:{region}:{account-id}:{resource-id}",
            principal: "secretsmanager.sc2s.sgov.gov",
            hostname: "secretsmanager.{region}.sc2s.sgov.gov",
            fipsHostname: "secretsmanager-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:secretsmanager:{region}:{account-id}:{resource-id}",
            principal: "secretsmanager.cloud.adc-e.uk",
            hostname: "secretsmanager.{region}.cloud.adc-e.uk",
            fipsHostname: "secretsmanager-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:secretsmanager:{region}:{account-id}:{resource-id}",
            principal: "secretsmanager.csp.hci.ic.gov",
            hostname: "secretsmanager.{region}.csp.hci.ic.gov",
            fipsHostname: "secretsmanager-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:secretsmanager:{region}:{account-id}:{resource-id}",
            principal: "secretsmanager.amazonaws.com",
            hostname: "secretsmanager.{region}.amazonaws.eu",
            fipsHostname: "secretsmanager-fips.{region}.amazonaws.eu",
        },
    },
    securityhub: {
        aws: {
            arn: "arn:aws:securityhub:{region}:{account-id}:{resource-id}",
            principal: "securityhub.amazonaws.com",
            hostname: "securityhub.{region}.amazonaws.com",
            fipsHostname: "securityhub-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:securityhub:{region}:{account-id}:{resource-id}",
            principal: "securityhub.amazonaws.com.cn",
            hostname: "securityhub.{region}.amazonaws.com.cn",
            fipsHostname: "securityhub-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:securityhub:{region}:{account-id}:{resource-id}",
            principal: "securityhub.amazonaws.com",
            hostname: "securityhub.{region}.amazonaws.com",
            fipsHostname: "securityhub-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:securityhub:{region}:{account-id}:{resource-id}",
            principal: "securityhub.c2s.ic.gov",
            hostname: "securityhub.{region}.c2s.ic.gov",
            fipsHostname: "securityhub-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:securityhub:{region}:{account-id}:{resource-id}",
            principal: "securityhub.sc2s.sgov.gov",
            hostname: "securityhub.{region}.sc2s.sgov.gov",
            fipsHostname: "securityhub-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:securityhub:{region}:{account-id}:{resource-id}",
            principal: "securityhub.csp.hci.ic.gov",
            hostname: "securityhub.{region}.csp.hci.ic.gov",
            fipsHostname: "securityhub-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:securityhub:{region}:{account-id}:{resource-id}",
            principal: "securityhub.amazonaws.com",
            hostname: "securityhub.{region}.amazonaws.eu",
            fipsHostname: "securityhub-fips.{region}.amazonaws.eu",
        },
    },
    securitylake: {
        aws: {
            arn: "arn:aws:securitylake:{region}:{account-id}:{resource-id}",
            principal: "securitylake.amazonaws.com",
            hostname: "securitylake.{region}.amazonaws.com",
            fipsHostname: "securitylake-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:securitylake:{region}:{account-id}:{resource-id}",
            principal: "securitylake.amazonaws.com",
            hostname: "securitylake.{region}.amazonaws.com",
            fipsHostname: "securitylake-fips.{region}.amazonaws.com",
        },
    },
    serverlessrepo: {
        aws: {
            arn: "arn:aws:serverlessrepo:{region}:{account-id}:{resource-id}",
            principal: "serverlessrepo.amazonaws.com",
            hostname: "serverlessrepo.{region}.amazonaws.com",
            fipsHostname: "serverlessrepo-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:serverlessrepo:{region}:{account-id}:{resource-id}",
            principal: "serverlessrepo.amazonaws.com.cn",
            hostname: "serverlessrepo.{region}.amazonaws.com.cn",
            fipsHostname: "serverlessrepo-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:serverlessrepo:{region}:{account-id}:{resource-id}",
            principal: "serverlessrepo.amazonaws.com",
            hostname: "serverlessrepo.{region}.amazonaws.com",
            fipsHostname: "serverlessrepo-fips.{region}.amazonaws.com",
        },
    },
    servicecatalog: {
        aws: {
            arn: "arn:aws:servicecatalog:{region}:{account-id}:{resource-id}",
            principal: "servicecatalog.amazonaws.com",
            hostname: "servicecatalog.{region}.amazonaws.com",
            fipsHostname: "servicecatalog-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:servicecatalog:{region}:{account-id}:{resource-id}",
            principal: "servicecatalog.amazonaws.com.cn",
            hostname: "servicecatalog.{region}.amazonaws.com.cn",
            fipsHostname: "servicecatalog-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:servicecatalog:{region}:{account-id}:{resource-id}",
            principal: "servicecatalog.amazonaws.com",
            hostname: "servicecatalog.{region}.amazonaws.com",
            fipsHostname: "servicecatalog-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:servicecatalog:{region}:{account-id}:{resource-id}",
            principal: "servicecatalog.cloud.adc-e.uk",
            hostname: "servicecatalog.{region}.cloud.adc-e.uk",
            fipsHostname: "servicecatalog-fips.{region}.cloud.adc-e.uk",
        },
    },
    "servicecatalog-appregistry": {
        aws: {
            arn: "arn:aws:servicecatalog-appregistry:{region}:{account-id}:{resource-id}",
            principal: "servicecatalog-appregistry.amazonaws.com",
            hostname: "servicecatalog-appregistry.{region}.amazonaws.com",
            fipsHostname: "servicecatalog-appregistry-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:servicecatalog-appregistry:{region}:{account-id}:{resource-id}",
            principal: "servicecatalog-appregistry.amazonaws.com",
            hostname: "servicecatalog-appregistry.{region}.amazonaws.com",
            fipsHostname: "servicecatalog-appregistry-fips.{region}.amazonaws.com",
        },
    },
    servicediscovery: {
        aws: {
            arn: "arn:aws:servicediscovery:{region}:{account-id}:{resource-id}",
            principal: "servicediscovery.amazonaws.com",
            hostname: "servicediscovery.{region}.amazonaws.com",
            fipsHostname: "servicediscovery-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:servicediscovery:{region}:{account-id}:{resource-id}",
            principal: "servicediscovery.amazonaws.com.cn",
            hostname: "servicediscovery.{region}.amazonaws.com.cn",
            fipsHostname: "servicediscovery-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:servicediscovery:{region}:{account-id}:{resource-id}",
            principal: "servicediscovery.amazonaws.com",
            hostname: "servicediscovery.{region}.amazonaws.com",
            fipsHostname: "servicediscovery-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:servicediscovery:{region}:{account-id}:{resource-id}",
            principal: "servicediscovery.c2s.ic.gov",
            hostname: "servicediscovery.{region}.c2s.ic.gov",
            fipsHostname: "servicediscovery-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:servicediscovery:{region}:{account-id}:{resource-id}",
            principal: "servicediscovery.sc2s.sgov.gov",
            hostname: "servicediscovery.{region}.sc2s.sgov.gov",
            fipsHostname: "servicediscovery-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:servicediscovery:{region}:{account-id}:{resource-id}",
            principal: "servicediscovery.cloud.adc-e.uk",
            hostname: "servicediscovery.{region}.cloud.adc-e.uk",
            fipsHostname: "servicediscovery-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:servicediscovery:{region}:{account-id}:{resource-id}",
            principal: "servicediscovery.csp.hci.ic.gov",
            hostname: "servicediscovery.{region}.csp.hci.ic.gov",
            fipsHostname: "servicediscovery-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:servicediscovery:{region}:{account-id}:{resource-id}",
            principal: "servicediscovery.amazonaws.com",
            hostname: "servicediscovery.{region}.amazonaws.eu",
            fipsHostname: "servicediscovery-fips.{region}.amazonaws.eu",
        },
    },
    servicequotas: {
        aws: {
            arn: "arn:aws:servicequotas:{region}:{account-id}:{resource-id}",
            principal: "servicequotas.amazonaws.com",
            hostname: "servicequotas.{region}.amazonaws.com",
            fipsHostname: "servicequotas-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:servicequotas:{region}:{account-id}:{resource-id}",
            principal: "servicequotas.amazonaws.com.cn",
            hostname: "servicequotas.{region}.amazonaws.com.cn",
            fipsHostname: "servicequotas-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:servicequotas:{region}:{account-id}:{resource-id}",
            principal: "servicequotas.amazonaws.com",
            hostname: "servicequotas.{region}.amazonaws.com",
            fipsHostname: "servicequotas-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:servicequotas:{region}:{account-id}:{resource-id}",
            principal: "servicequotas.c2s.ic.gov",
            hostname: "servicequotas.{region}.c2s.ic.gov",
            fipsHostname: "servicequotas-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:servicequotas:{region}:{account-id}:{resource-id}",
            principal: "servicequotas.sc2s.sgov.gov",
            hostname: "servicequotas.{region}.sc2s.sgov.gov",
            fipsHostname: "servicequotas-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:servicequotas:{region}:{account-id}:{resource-id}",
            principal: "servicequotas.cloud.adc-e.uk",
            hostname: "servicequotas.{region}.cloud.adc-e.uk",
            fipsHostname: "servicequotas-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:servicequotas:{region}:{account-id}:{resource-id}",
            principal: "servicequotas.csp.hci.ic.gov",
            hostname: "servicequotas.{region}.csp.hci.ic.gov",
            fipsHostname: "servicequotas-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:servicequotas:{region}:{account-id}:{resource-id}",
            principal: "servicequotas.amazonaws.com",
            hostname: "servicequotas.{region}.amazonaws.eu",
            fipsHostname: "servicequotas-fips.{region}.amazonaws.eu",
        },
    },
    "session.qldb": {
        aws: {
            arn: "arn:aws:session.qldb:{region}:{account-id}:{resource-id}",
            principal: "session.qldb.amazonaws.com",
            hostname: "session.qldb.{region}.amazonaws.com",
            fipsHostname: "session.qldb-fips.{region}.amazonaws.com",
        },
    },
    shield: {
        aws: {
            arn: "arn:aws:shield:{region}:{account-id}:{resource-id}",
            principal: "shield.amazonaws.com",
            hostname: "shield.{region}.amazonaws.com",
            fipsHostname: "shield-fips.{region}.amazonaws.com",
        },
    },
    signer: {
        aws: {
            arn: "arn:aws:signer:{region}:{account-id}:{resource-id}",
            principal: "signer.amazonaws.com",
            hostname: "signer.{region}.amazonaws.com",
            fipsHostname: "signer-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:signer:{region}:{account-id}:{resource-id}",
            principal: "signer.amazonaws.com.cn",
            hostname: "signer.{region}.amazonaws.com.cn",
            fipsHostname: "signer-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:signer:{region}:{account-id}:{resource-id}",
            principal: "signer.amazonaws.com",
            hostname: "signer.{region}.amazonaws.com",
            fipsHostname: "signer-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:signer:{region}:{account-id}:{resource-id}",
            principal: "signer.amazonaws.com",
            hostname: "signer.{region}.amazonaws.eu",
            fipsHostname: "signer-fips.{region}.amazonaws.eu",
        },
    },
    simspaceweaver: {
        aws: {
            arn: "arn:aws:simspaceweaver:{region}:{account-id}:{resource-id}",
            principal: "simspaceweaver.amazonaws.com",
            hostname: "simspaceweaver.{region}.amazonaws.com",
            fipsHostname: "simspaceweaver-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:simspaceweaver:{region}:{account-id}:{resource-id}",
            principal: "simspaceweaver.amazonaws.com",
            hostname: "simspaceweaver.{region}.amazonaws.com",
            fipsHostname: "simspaceweaver-fips.{region}.amazonaws.com",
        },
    },
    sms: {
        aws: {
            arn: "arn:aws:sms:{region}:{account-id}:{resource-id}",
            principal: "sms.amazonaws.com",
            hostname: "sms.{region}.amazonaws.com",
            fipsHostname: "sms-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:sms:{region}:{account-id}:{resource-id}",
            principal: "sms.amazonaws.com.cn",
            hostname: "sms.{region}.amazonaws.com.cn",
            fipsHostname: "sms-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:sms:{region}:{account-id}:{resource-id}",
            principal: "sms.amazonaws.com",
            hostname: "sms.{region}.amazonaws.com",
            fipsHostname: "sms-fips.{region}.amazonaws.com",
        },
    },
    "sms-voice": {
        aws: {
            arn: "arn:aws:sms-voice:{region}:{account-id}:{resource-id}",
            principal: "sms-voice.amazonaws.com",
            hostname: "sms-voice.{region}.amazonaws.com",
            fipsHostname: "sms-voice-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:sms-voice:{region}:{account-id}:{resource-id}",
            principal: "sms-voice.amazonaws.com",
            hostname: "sms-voice.{region}.amazonaws.com",
            fipsHostname: "sms-voice-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:sms-voice:{region}:{account-id}:{resource-id}",
            principal: "sms-voice.amazonaws.com",
            hostname: "sms-voice.{region}.amazonaws.eu",
            fipsHostname: "sms-voice-fips.{region}.amazonaws.eu",
        },
    },
    snowball: {
        aws: {
            arn: "arn:aws:snowball:{region}:{account-id}:{resource-id}",
            principal: "snowball.amazonaws.com",
            hostname: "snowball.{region}.amazonaws.com",
            fipsHostname: "snowball-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:snowball:{region}:{account-id}:{resource-id}",
            principal: "snowball.amazonaws.com.cn",
            hostname: "snowball.{region}.amazonaws.com.cn",
            fipsHostname: "snowball-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:snowball:{region}:{account-id}:{resource-id}",
            principal: "snowball.amazonaws.com",
            hostname: "snowball.{region}.amazonaws.com",
            fipsHostname: "snowball-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:snowball:{region}:{account-id}:{resource-id}",
            principal: "snowball.c2s.ic.gov",
            hostname: "snowball.{region}.c2s.ic.gov",
            fipsHostname: "snowball-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:snowball:{region}:{account-id}:{resource-id}",
            principal: "snowball.sc2s.sgov.gov",
            hostname: "snowball.{region}.sc2s.sgov.gov",
            fipsHostname: "snowball-fips.{region}.sc2s.sgov.gov",
        },
    },
    sns: {
        aws: {
            arn: "arn:aws:sns:{region}:{account-id}:{resource-id}",
            principal: "sns.amazonaws.com",
            hostname: "sns.{region}.amazonaws.com",
            fipsHostname: "sns-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:sns:{region}:{account-id}:{resource-id}",
            principal: "sns.amazonaws.com.cn",
            hostname: "sns.{region}.amazonaws.com.cn",
            fipsHostname: "sns-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:sns:{region}:{account-id}:{resource-id}",
            principal: "sns.amazonaws.com",
            hostname: "sns.{region}.amazonaws.com",
            fipsHostname: "sns-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:sns:{region}:{account-id}:{resource-id}",
            principal: "sns.c2s.ic.gov",
            hostname: "sns.{region}.c2s.ic.gov",
            fipsHostname: "sns-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:sns:{region}:{account-id}:{resource-id}",
            principal: "sns.sc2s.sgov.gov",
            hostname: "sns.{region}.sc2s.sgov.gov",
            fipsHostname: "sns-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:sns:{region}:{account-id}:{resource-id}",
            principal: "sns.cloud.adc-e.uk",
            hostname: "sns.{region}.cloud.adc-e.uk",
            fipsHostname: "sns-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:sns:{region}:{account-id}:{resource-id}",
            principal: "sns.csp.hci.ic.gov",
            hostname: "sns.{region}.csp.hci.ic.gov",
            fipsHostname: "sns-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:sns:{region}:{account-id}:{resource-id}",
            principal: "sns.amazonaws.com",
            hostname: "sns.{region}.amazonaws.eu",
            fipsHostname: "sns-fips.{region}.amazonaws.eu",
        },
    },
    sqs: {
        aws: {
            arn: "arn:aws:sqs:{region}:{account-id}:{resource-id}",
            principal: "sqs.amazonaws.com",
            hostname: "sqs.{region}.amazonaws.com",
            fipsHostname: "sqs-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:sqs:{region}:{account-id}:{resource-id}",
            principal: "sqs.amazonaws.com.cn",
            hostname: "sqs.{region}.amazonaws.com.cn",
            fipsHostname: "sqs-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:sqs:{region}:{account-id}:{resource-id}",
            principal: "sqs.amazonaws.com",
            hostname: "sqs.{region}.amazonaws.com",
            fipsHostname: "sqs-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:sqs:{region}:{account-id}:{resource-id}",
            principal: "sqs.c2s.ic.gov",
            hostname: "sqs.{region}.c2s.ic.gov",
            fipsHostname: "sqs-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:sqs:{region}:{account-id}:{resource-id}",
            principal: "sqs.sc2s.sgov.gov",
            hostname: "sqs.{region}.sc2s.sgov.gov",
            fipsHostname: "sqs-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:sqs:{region}:{account-id}:{resource-id}",
            principal: "sqs.cloud.adc-e.uk",
            hostname: "sqs.{region}.cloud.adc-e.uk",
            fipsHostname: "sqs-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:sqs:{region}:{account-id}:{resource-id}",
            principal: "sqs.csp.hci.ic.gov",
            hostname: "sqs.{region}.csp.hci.ic.gov",
            fipsHostname: "sqs-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:sqs:{region}:{account-id}:{resource-id}",
            principal: "sqs.amazonaws.com",
            hostname: "sqs.{region}.amazonaws.eu",
            fipsHostname: "sqs-fips.{region}.amazonaws.eu",
        },
    },
    ssm: {
        aws: {
            arn: "arn:aws:ssm:{region}:{account-id}:{resource-id}",
            principal: "ssm.amazonaws.com",
            hostname: "ssm.{region}.amazonaws.com",
            fipsHostname: "ssm-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:ssm:{region}:{account-id}:{resource-id}",
            principal: "ssm.amazonaws.com.cn",
            hostname: "ssm.{region}.amazonaws.com.cn",
            fipsHostname: "ssm-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:ssm:{region}:{account-id}:{resource-id}",
            principal: "ssm.amazonaws.com",
            hostname: "ssm.{region}.amazonaws.com",
            fipsHostname: "ssm-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:ssm:{region}:{account-id}:{resource-id}",
            principal: "ssm.c2s.ic.gov",
            hostname: "ssm.{region}.c2s.ic.gov",
            fipsHostname: "ssm-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:ssm:{region}:{account-id}:{resource-id}",
            principal: "ssm.sc2s.sgov.gov",
            hostname: "ssm.{region}.sc2s.sgov.gov",
            fipsHostname: "ssm-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:ssm:{region}:{account-id}:{resource-id}",
            principal: "ssm.cloud.adc-e.uk",
            hostname: "ssm.{region}.cloud.adc-e.uk",
            fipsHostname: "ssm-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:ssm:{region}:{account-id}:{resource-id}",
            principal: "ssm.csp.hci.ic.gov",
            hostname: "ssm.{region}.csp.hci.ic.gov",
            fipsHostname: "ssm-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:ssm:{region}:{account-id}:{resource-id}",
            principal: "ssm.amazonaws.com",
            hostname: "ssm.{region}.amazonaws.eu",
            fipsHostname: "ssm-fips.{region}.amazonaws.eu",
        },
    },
    "ssm-contacts": {
        aws: {
            arn: "arn:aws:ssm-contacts:{region}:{account-id}:{resource-id}",
            principal: "ssm-contacts.amazonaws.com",
            hostname: "ssm-contacts.{region}.amazonaws.com",
            fipsHostname: "ssm-contacts-fips.{region}.amazonaws.com",
        },
    },
    "ssm-incidents": {
        aws: {
            arn: "arn:aws:ssm-incidents:{region}:{account-id}:{resource-id}",
            principal: "ssm-incidents.amazonaws.com",
            hostname: "ssm-incidents.{region}.amazonaws.com",
            fipsHostname: "ssm-incidents-fips.{region}.amazonaws.com",
        },
    },
    "ssm-quicksetup": {
        aws: {
            arn: "arn:aws:ssm-quicksetup:{region}:{account-id}:{resource-id}",
            principal: "ssm-quicksetup.amazonaws.com",
            hostname: "ssm-quicksetup.{region}.amazonaws.com",
            fipsHostname: "ssm-quicksetup-fips.{region}.amazonaws.com",
        },
    },
    "ssm-sap": {
        aws: {
            arn: "arn:aws:ssm-sap:{region}:{account-id}:{resource-id}",
            principal: "ssm-sap.amazonaws.com",
            hostname: "ssm-sap.{region}.amazonaws.com",
            fipsHostname: "ssm-sap-fips.{region}.amazonaws.com",
        },
    },
    sso: {
        aws: {
            arn: "arn:aws:sso:{region}:{account-id}:{resource-id}",
            principal: "sso.amazonaws.com",
            hostname: "sso.{region}.amazonaws.com",
            fipsHostname: "sso-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:sso:{region}:{account-id}:{resource-id}",
            principal: "sso.amazonaws.com",
            hostname: "sso.{region}.amazonaws.com",
            fipsHostname: "sso-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:sso:{region}:{account-id}:{resource-id}",
            principal: "sso.amazonaws.com.cn",
            hostname: "sso.{region}.amazonaws.com.cn",
            fipsHostname: "sso-fips.{region}.amazonaws.com.cn",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:sso:{region}:{account-id}:{resource-id}",
            principal: "sso.amazonaws.com",
            hostname: "sso.{region}.amazonaws.eu",
            fipsHostname: "sso-fips.{region}.amazonaws.eu",
        },
    },
    states: {
        aws: {
            arn: "arn:aws:states:{region}:{account-id}:{resource-id}",
            principal: "states.amazonaws.com",
            hostname: "states.{region}.amazonaws.com",
            fipsHostname: "states-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:states:{region}:{account-id}:{resource-id}",
            principal: "states.amazonaws.com.cn",
            hostname: "states.{region}.amazonaws.com.cn",
            fipsHostname: "states-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:states:{region}:{account-id}:{resource-id}",
            principal: "states.amazonaws.com",
            hostname: "states.{region}.amazonaws.com",
            fipsHostname: "states-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:states:{region}:{account-id}:{resource-id}",
            principal: "states.c2s.ic.gov",
            hostname: "states.{region}.c2s.ic.gov",
            fipsHostname: "states-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:states:{region}:{account-id}:{resource-id}",
            principal: "states.sc2s.sgov.gov",
            hostname: "states.{region}.sc2s.sgov.gov",
            fipsHostname: "states-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:states:{region}:{account-id}:{resource-id}",
            principal: "states.cloud.adc-e.uk",
            hostname: "states.{region}.cloud.adc-e.uk",
            fipsHostname: "states-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:states:{region}:{account-id}:{resource-id}",
            principal: "states.csp.hci.ic.gov",
            hostname: "states.{region}.csp.hci.ic.gov",
            fipsHostname: "states-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:states:{region}:{account-id}:{resource-id}",
            principal: "states.amazonaws.com",
            hostname: "states.{region}.amazonaws.eu",
            fipsHostname: "states-fips.{region}.amazonaws.eu",
        },
    },
    storagegateway: {
        aws: {
            arn: "arn:aws:storagegateway:{region}:{account-id}:{resource-id}",
            principal: "storagegateway.amazonaws.com",
            hostname: "storagegateway.{region}.amazonaws.com",
            fipsHostname: "storagegateway-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:storagegateway:{region}:{account-id}:{resource-id}",
            principal: "storagegateway.amazonaws.com.cn",
            hostname: "storagegateway.{region}.amazonaws.com.cn",
            fipsHostname: "storagegateway-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:storagegateway:{region}:{account-id}:{resource-id}",
            principal: "storagegateway.amazonaws.com",
            hostname: "storagegateway.{region}.amazonaws.com",
            fipsHostname: "storagegateway-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:storagegateway:{region}:{account-id}:{resource-id}",
            principal: "storagegateway.c2s.ic.gov",
            hostname: "storagegateway.{region}.c2s.ic.gov",
            fipsHostname: "storagegateway-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:storagegateway:{region}:{account-id}:{resource-id}",
            principal: "storagegateway.sc2s.sgov.gov",
            hostname: "storagegateway.{region}.sc2s.sgov.gov",
            fipsHostname: "storagegateway-fips.{region}.sc2s.sgov.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:storagegateway:{region}:{account-id}:{resource-id}",
            principal: "storagegateway.amazonaws.com",
            hostname: "storagegateway.{region}.amazonaws.eu",
            fipsHostname: "storagegateway-fips.{region}.amazonaws.eu",
        },
    },
    "streams.dynamodb": {
        aws: {
            arn: "arn:aws:streams.dynamodb:{region}:{account-id}:{resource-id}",
            principal: "streams.dynamodb.amazonaws.com",
            hostname: "streams.dynamodb.{region}.amazonaws.com",
            fipsHostname: "streams.dynamodb-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:streams.dynamodb:{region}:{account-id}:{resource-id}",
            principal: "streams.dynamodb.amazonaws.com.cn",
            hostname: "streams.dynamodb.{region}.amazonaws.com.cn",
            fipsHostname: "streams.dynamodb-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:streams.dynamodb:{region}:{account-id}:{resource-id}",
            principal: "streams.dynamodb.amazonaws.com",
            hostname: "streams.dynamodb.{region}.amazonaws.com",
            fipsHostname: "streams.dynamodb-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:streams.dynamodb:{region}:{account-id}:{resource-id}",
            principal: "streams.dynamodb.c2s.ic.gov",
            hostname: "streams.dynamodb.{region}.c2s.ic.gov",
            fipsHostname: "streams.dynamodb-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:streams.dynamodb:{region}:{account-id}:{resource-id}",
            principal: "streams.dynamodb.sc2s.sgov.gov",
            hostname: "streams.dynamodb.{region}.sc2s.sgov.gov",
            fipsHostname: "streams.dynamodb-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:streams.dynamodb:{region}:{account-id}:{resource-id}",
            principal: "streams.dynamodb.cloud.adc-e.uk",
            hostname: "streams.dynamodb.{region}.cloud.adc-e.uk",
            fipsHostname: "streams.dynamodb-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:streams.dynamodb:{region}:{account-id}:{resource-id}",
            principal: "streams.dynamodb.csp.hci.ic.gov",
            hostname: "streams.dynamodb.{region}.csp.hci.ic.gov",
            fipsHostname: "streams.dynamodb-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:streams.dynamodb:{region}:{account-id}:{resource-id}",
            principal: "streams.dynamodb.amazonaws.com",
            hostname: "streams.dynamodb.{region}.amazonaws.eu",
            fipsHostname: "streams.dynamodb-fips.{region}.amazonaws.eu",
        },
    },
    sts: {
        aws: {
            arn: "arn:aws:sts:{region}:{account-id}:{resource-id}",
            principal: "sts.amazonaws.com",
            hostname: "sts.{region}.amazonaws.com",
            fipsHostname: "sts-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:sts:{region}:{account-id}:{resource-id}",
            principal: "sts.amazonaws.com.cn",
            hostname: "sts.{region}.amazonaws.com.cn",
            fipsHostname: "sts-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:sts:{region}:{account-id}:{resource-id}",
            principal: "sts.amazonaws.com",
            hostname: "sts.{region}.amazonaws.com",
            fipsHostname: "sts-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:sts:{region}:{account-id}:{resource-id}",
            principal: "sts.c2s.ic.gov",
            hostname: "sts.{region}.c2s.ic.gov",
            fipsHostname: "sts-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:sts:{region}:{account-id}:{resource-id}",
            principal: "sts.sc2s.sgov.gov",
            hostname: "sts.{region}.sc2s.sgov.gov",
            fipsHostname: "sts-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:sts:{region}:{account-id}:{resource-id}",
            principal: "sts.cloud.adc-e.uk",
            hostname: "sts.{region}.cloud.adc-e.uk",
            fipsHostname: "sts-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:sts:{region}:{account-id}:{resource-id}",
            principal: "sts.csp.hci.ic.gov",
            hostname: "sts.{region}.csp.hci.ic.gov",
            fipsHostname: "sts-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:sts:{region}:{account-id}:{resource-id}",
            principal: "sts.amazonaws.com",
            hostname: "sts.{region}.amazonaws.eu",
            fipsHostname: "sts-fips.{region}.amazonaws.eu",
        },
    },
    support: {
        aws: {
            arn: "arn:aws:support:{region}:{account-id}:{resource-id}",
            principal: "support.amazonaws.com",
            hostname: "support.{region}.amazonaws.com",
            fipsHostname: "support-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:support:{region}:{account-id}:{resource-id}",
            principal: "support.amazonaws.com.cn",
            hostname: "support.{region}.amazonaws.com.cn",
            fipsHostname: "support-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:support:{region}:{account-id}:{resource-id}",
            principal: "support.amazonaws.com",
            hostname: "support.{region}.amazonaws.com",
            fipsHostname: "support-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:support:{region}:{account-id}:{resource-id}",
            principal: "support.c2s.ic.gov",
            hostname: "support.{region}.c2s.ic.gov",
            fipsHostname: "support-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:support:{region}:{account-id}:{resource-id}",
            principal: "support.sc2s.sgov.gov",
            hostname: "support.{region}.sc2s.sgov.gov",
            fipsHostname: "support-fips.{region}.sc2s.sgov.gov",
        },
    },
    supportapp: {
        aws: {
            arn: "arn:aws:supportapp:{region}:{account-id}:{resource-id}",
            principal: "supportapp.amazonaws.com",
            hostname: "supportapp.{region}.amazonaws.com",
            fipsHostname: "supportapp-fips.{region}.amazonaws.com",
        },
    },
    swf: {
        aws: {
            arn: "arn:aws:swf:{region}:{account-id}:{resource-id}",
            principal: "swf.amazonaws.com",
            hostname: "swf.{region}.amazonaws.com",
            fipsHostname: "swf-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:swf:{region}:{account-id}:{resource-id}",
            principal: "swf.amazonaws.com.cn",
            hostname: "swf.{region}.amazonaws.com.cn",
            fipsHostname: "swf-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:swf:{region}:{account-id}:{resource-id}",
            principal: "swf.amazonaws.com",
            hostname: "swf.{region}.amazonaws.com",
            fipsHostname: "swf-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:swf:{region}:{account-id}:{resource-id}",
            principal: "swf.c2s.ic.gov",
            hostname: "swf.{region}.c2s.ic.gov",
            fipsHostname: "swf-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:swf:{region}:{account-id}:{resource-id}",
            principal: "swf.sc2s.sgov.gov",
            hostname: "swf.{region}.sc2s.sgov.gov",
            fipsHostname: "swf-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:swf:{region}:{account-id}:{resource-id}",
            principal: "swf.cloud.adc-e.uk",
            hostname: "swf.{region}.cloud.adc-e.uk",
            fipsHostname: "swf-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:swf:{region}:{account-id}:{resource-id}",
            principal: "swf.csp.hci.ic.gov",
            hostname: "swf.{region}.csp.hci.ic.gov",
            fipsHostname: "swf-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:swf:{region}:{account-id}:{resource-id}",
            principal: "swf.amazonaws.com",
            hostname: "swf.{region}.amazonaws.eu",
            fipsHostname: "swf-fips.{region}.amazonaws.eu",
        },
    },
    synthetics: {
        aws: {
            arn: "arn:aws:synthetics:{region}:{account-id}:{resource-id}",
            principal: "synthetics.amazonaws.com",
            hostname: "synthetics.{region}.amazonaws.com",
            fipsHostname: "synthetics-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:synthetics:{region}:{account-id}:{resource-id}",
            principal: "synthetics.amazonaws.com.cn",
            hostname: "synthetics.{region}.amazonaws.com.cn",
            fipsHostname: "synthetics-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:synthetics:{region}:{account-id}:{resource-id}",
            principal: "synthetics.amazonaws.com",
            hostname: "synthetics.{region}.amazonaws.com",
            fipsHostname: "synthetics-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:synthetics:{region}:{account-id}:{resource-id}",
            principal: "synthetics.c2s.ic.gov",
            hostname: "synthetics.{region}.c2s.ic.gov",
            fipsHostname: "synthetics-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:synthetics:{region}:{account-id}:{resource-id}",
            principal: "synthetics.sc2s.sgov.gov",
            hostname: "synthetics.{region}.sc2s.sgov.gov",
            fipsHostname: "synthetics-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:synthetics:{region}:{account-id}:{resource-id}",
            principal: "synthetics.cloud.adc-e.uk",
            hostname: "synthetics.{region}.cloud.adc-e.uk",
            fipsHostname: "synthetics-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:synthetics:{region}:{account-id}:{resource-id}",
            principal: "synthetics.csp.hci.ic.gov",
            hostname: "synthetics.{region}.csp.hci.ic.gov",
            fipsHostname: "synthetics-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:synthetics:{region}:{account-id}:{resource-id}",
            principal: "synthetics.amazonaws.com",
            hostname: "synthetics.{region}.amazonaws.eu",
            fipsHostname: "synthetics-fips.{region}.amazonaws.eu",
        },
    },
    tagging: {
        aws: {
            arn: "arn:aws:tagging:{region}:{account-id}:{resource-id}",
            principal: "tagging.amazonaws.com",
            hostname: "tagging.{region}.amazonaws.com",
            fipsHostname: "tagging-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:tagging:{region}:{account-id}:{resource-id}",
            principal: "tagging.amazonaws.com.cn",
            hostname: "tagging.{region}.amazonaws.com.cn",
            fipsHostname: "tagging-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:tagging:{region}:{account-id}:{resource-id}",
            principal: "tagging.amazonaws.com",
            hostname: "tagging.{region}.amazonaws.com",
            fipsHostname: "tagging-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:tagging:{region}:{account-id}:{resource-id}",
            principal: "tagging.c2s.ic.gov",
            hostname: "tagging.{region}.c2s.ic.gov",
            fipsHostname: "tagging-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:tagging:{region}:{account-id}:{resource-id}",
            principal: "tagging.sc2s.sgov.gov",
            hostname: "tagging.{region}.sc2s.sgov.gov",
            fipsHostname: "tagging-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:tagging:{region}:{account-id}:{resource-id}",
            principal: "tagging.cloud.adc-e.uk",
            hostname: "tagging.{region}.cloud.adc-e.uk",
            fipsHostname: "tagging-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:tagging:{region}:{account-id}:{resource-id}",
            principal: "tagging.csp.hci.ic.gov",
            hostname: "tagging.{region}.csp.hci.ic.gov",
            fipsHostname: "tagging-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:tagging:{region}:{account-id}:{resource-id}",
            principal: "tagging.amazonaws.com",
            hostname: "tagging.{region}.amazonaws.eu",
            fipsHostname: "tagging-fips.{region}.amazonaws.eu",
        },
    },
    tax: {
        aws: {
            arn: "arn:aws:tax:{region}:{account-id}:{resource-id}",
            principal: "tax.amazonaws.com",
            hostname: "tax.{region}.amazonaws.com",
            fipsHostname: "tax-fips.{region}.amazonaws.com",
        },
    },
    textract: {
        aws: {
            arn: "arn:aws:textract:{region}:{account-id}:{resource-id}",
            principal: "textract.amazonaws.com",
            hostname: "textract.{region}.amazonaws.com",
            fipsHostname: "textract-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:textract:{region}:{account-id}:{resource-id}",
            principal: "textract.amazonaws.com",
            hostname: "textract.{region}.amazonaws.com",
            fipsHostname: "textract-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:textract:{region}:{account-id}:{resource-id}",
            principal: "textract.c2s.ic.gov",
            hostname: "textract.{region}.c2s.ic.gov",
            fipsHostname: "textract-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:textract:{region}:{account-id}:{resource-id}",
            principal: "textract.csp.hci.ic.gov",
            hostname: "textract.{region}.csp.hci.ic.gov",
            fipsHostname: "textract-fips.{region}.csp.hci.ic.gov",
        },
    },
    thinclient: {
        aws: {
            arn: "arn:aws:thinclient:{region}:{account-id}:{resource-id}",
            principal: "thinclient.amazonaws.com",
            hostname: "thinclient.{region}.amazonaws.com",
            fipsHostname: "thinclient-fips.{region}.amazonaws.com",
        },
    },
    tnb: {
        aws: {
            arn: "arn:aws:tnb:{region}:{account-id}:{resource-id}",
            principal: "tnb.amazonaws.com",
            hostname: "tnb.{region}.amazonaws.com",
            fipsHostname: "tnb-fips.{region}.amazonaws.com",
        },
    },
    transcribe: {
        aws: {
            arn: "arn:aws:transcribe:{region}:{account-id}:{resource-id}",
            principal: "transcribe.amazonaws.com",
            hostname: "transcribe.{region}.amazonaws.com",
            fipsHostname: "transcribe-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:transcribe:{region}:{account-id}:{resource-id}",
            principal: "transcribe.amazonaws.com.cn",
            hostname: "transcribe.{region}.amazonaws.com.cn",
            fipsHostname: "transcribe-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:transcribe:{region}:{account-id}:{resource-id}",
            principal: "transcribe.amazonaws.com",
            hostname: "transcribe.{region}.amazonaws.com",
            fipsHostname: "transcribe-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:transcribe:{region}:{account-id}:{resource-id}",
            principal: "transcribe.c2s.ic.gov",
            hostname: "transcribe.{region}.c2s.ic.gov",
            fipsHostname: "transcribe-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:transcribe:{region}:{account-id}:{resource-id}",
            principal: "transcribe.csp.hci.ic.gov",
            hostname: "transcribe.{region}.csp.hci.ic.gov",
            fipsHostname: "transcribe-fips.{region}.csp.hci.ic.gov",
        },
    },
    transcribestreaming: {
        aws: {
            arn: "arn:aws:transcribestreaming:{region}:{account-id}:{resource-id}",
            principal: "transcribestreaming.amazonaws.com",
            hostname: "transcribestreaming.{region}.amazonaws.com",
            fipsHostname: "transcribestreaming-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:transcribestreaming:{region}:{account-id}:{resource-id}",
            principal: "transcribestreaming.amazonaws.com.cn",
            hostname: "transcribestreaming.{region}.amazonaws.com.cn",
            fipsHostname: "transcribestreaming-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:transcribestreaming:{region}:{account-id}:{resource-id}",
            principal: "transcribestreaming.amazonaws.com",
            hostname: "transcribestreaming.{region}.amazonaws.com",
            fipsHostname: "transcribestreaming-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:transcribestreaming:{region}:{account-id}:{resource-id}",
            principal: "transcribestreaming.c2s.ic.gov",
            hostname: "transcribestreaming.{region}.c2s.ic.gov",
            fipsHostname: "transcribestreaming-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:transcribestreaming:{region}:{account-id}:{resource-id}",
            principal: "transcribestreaming.csp.hci.ic.gov",
            hostname: "transcribestreaming.{region}.csp.hci.ic.gov",
            fipsHostname: "transcribestreaming-fips.{region}.csp.hci.ic.gov",
        },
    },
    transfer: {
        aws: {
            arn: "arn:aws:transfer:{region}:{account-id}:{resource-id}",
            principal: "transfer.amazonaws.com",
            hostname: "transfer.{region}.amazonaws.com",
            fipsHostname: "transfer-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:transfer:{region}:{account-id}:{resource-id}",
            principal: "transfer.amazonaws.com.cn",
            hostname: "transfer.{region}.amazonaws.com.cn",
            fipsHostname: "transfer-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:transfer:{region}:{account-id}:{resource-id}",
            principal: "transfer.amazonaws.com",
            hostname: "transfer.{region}.amazonaws.com",
            fipsHostname: "transfer-fips.{region}.amazonaws.com",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:transfer:{region}:{account-id}:{resource-id}",
            principal: "transfer.amazonaws.com",
            hostname: "transfer.{region}.amazonaws.eu",
            fipsHostname: "transfer-fips.{region}.amazonaws.eu",
        },
    },
    translate: {
        aws: {
            arn: "arn:aws:translate:{region}:{account-id}:{resource-id}",
            principal: "translate.amazonaws.com",
            hostname: "translate.{region}.amazonaws.com",
            fipsHostname: "translate-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:translate:{region}:{account-id}:{resource-id}",
            principal: "translate.amazonaws.com",
            hostname: "translate.{region}.amazonaws.com",
            fipsHostname: "translate-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:translate:{region}:{account-id}:{resource-id}",
            principal: "translate.c2s.ic.gov",
            hostname: "translate.{region}.c2s.ic.gov",
            fipsHostname: "translate-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:translate:{region}:{account-id}:{resource-id}",
            principal: "translate.csp.hci.ic.gov",
            hostname: "translate.{region}.csp.hci.ic.gov",
            fipsHostname: "translate-fips.{region}.csp.hci.ic.gov",
        },
    },
    trustedadvisor: {
        aws: {
            arn: "arn:aws:trustedadvisor:{region}:{account-id}:{resource-id}",
            principal: "trustedadvisor.amazonaws.com",
            hostname: "trustedadvisor.{region}.amazonaws.com",
            fipsHostname: "trustedadvisor-fips.{region}.amazonaws.com",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:trustedadvisor:{region}:{account-id}:{resource-id}",
            principal: "trustedadvisor.cloud.adc-e.uk",
            hostname: "trustedadvisor.{region}.cloud.adc-e.uk",
            fipsHostname: "trustedadvisor-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:trustedadvisor:{region}:{account-id}:{resource-id}",
            principal: "trustedadvisor.csp.hci.ic.gov",
            hostname: "trustedadvisor.{region}.csp.hci.ic.gov",
            fipsHostname: "trustedadvisor-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:trustedadvisor:{region}:{account-id}:{resource-id}",
            principal: "trustedadvisor.amazonaws.com",
            hostname: "trustedadvisor.{region}.amazonaws.eu",
            fipsHostname: "trustedadvisor-fips.{region}.amazonaws.eu",
        },
    },
    verifiedpermissions: {
        aws: {
            arn: "arn:aws:verifiedpermissions:{region}:{account-id}:{resource-id}",
            principal: "verifiedpermissions.amazonaws.com",
            hostname: "verifiedpermissions.{region}.amazonaws.com",
            fipsHostname: "verifiedpermissions-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:verifiedpermissions:{region}:{account-id}:{resource-id}",
            principal: "verifiedpermissions.amazonaws.com.cn",
            hostname: "verifiedpermissions.{region}.amazonaws.com.cn",
            fipsHostname: "verifiedpermissions-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:verifiedpermissions:{region}:{account-id}:{resource-id}",
            principal: "verifiedpermissions.amazonaws.com",
            hostname: "verifiedpermissions.{region}.amazonaws.com",
            fipsHostname: "verifiedpermissions-fips.{region}.amazonaws.com",
        },
    },
    "voice-chime": {
        aws: {
            arn: "arn:aws:voice-chime:{region}:{account-id}:{resource-id}",
            principal: "voice-chime.amazonaws.com",
            hostname: "voice-chime.{region}.amazonaws.com",
            fipsHostname: "voice-chime-fips.{region}.amazonaws.com",
        },
    },
    voiceid: {
        aws: {
            arn: "arn:aws:voiceid:{region}:{account-id}:{resource-id}",
            principal: "voiceid.amazonaws.com",
            hostname: "voiceid.{region}.amazonaws.com",
            fipsHostname: "voiceid-fips.{region}.amazonaws.com",
        },
    },
    "vpc-lattice": {
        aws: {
            arn: "arn:aws:vpc-lattice:{region}:{account-id}:{resource-id}",
            principal: "vpc-lattice.amazonaws.com",
            hostname: "vpc-lattice.{region}.amazonaws.com",
            fipsHostname: "vpc-lattice-fips.{region}.amazonaws.com",
        },
    },
    waf: {
        aws: {
            arn: "arn:aws:waf:{region}:{account-id}:{resource-id}",
            principal: "waf.amazonaws.com",
            hostname: "waf.{region}.amazonaws.com",
            fipsHostname: "waf-fips.{region}.amazonaws.com",
        },
    },
    "waf-regional": {
        aws: {
            arn: "arn:aws:waf-regional:{region}:{account-id}:{resource-id}",
            principal: "waf-regional.amazonaws.com",
            hostname: "waf-regional.{region}.amazonaws.com",
            fipsHostname: "waf-regional-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:waf-regional:{region}:{account-id}:{resource-id}",
            principal: "waf-regional.amazonaws.com.cn",
            hostname: "waf-regional.{region}.amazonaws.com.cn",
            fipsHostname: "waf-regional-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:waf-regional:{region}:{account-id}:{resource-id}",
            principal: "waf-regional.amazonaws.com",
            hostname: "waf-regional.{region}.amazonaws.com",
            fipsHostname: "waf-regional-fips.{region}.amazonaws.com",
        },
    },
    wafv2: {
        aws: {
            arn: "arn:aws:wafv2:{region}:{account-id}:{resource-id}",
            principal: "wafv2.amazonaws.com",
            hostname: "wafv2.{region}.amazonaws.com",
            fipsHostname: "wafv2-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:wafv2:{region}:{account-id}:{resource-id}",
            principal: "wafv2.amazonaws.com.cn",
            hostname: "wafv2.{region}.amazonaws.com.cn",
            fipsHostname: "wafv2-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:wafv2:{region}:{account-id}:{resource-id}",
            principal: "wafv2.amazonaws.com",
            hostname: "wafv2.{region}.amazonaws.com",
            fipsHostname: "wafv2-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:wafv2:{region}:{account-id}:{resource-id}",
            principal: "wafv2.c2s.ic.gov",
            hostname: "wafv2.{region}.c2s.ic.gov",
            fipsHostname: "wafv2-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:wafv2:{region}:{account-id}:{resource-id}",
            principal: "wafv2.sc2s.sgov.gov",
            hostname: "wafv2.{region}.sc2s.sgov.gov",
            fipsHostname: "wafv2-fips.{region}.sc2s.sgov.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:wafv2:{region}:{account-id}:{resource-id}",
            principal: "wafv2.amazonaws.com",
            hostname: "wafv2.{region}.amazonaws.eu",
            fipsHostname: "wafv2-fips.{region}.amazonaws.eu",
        },
    },
    wellarchitected: {
        aws: {
            arn: "arn:aws:wellarchitected:{region}:{account-id}:{resource-id}",
            principal: "wellarchitected.amazonaws.com",
            hostname: "wellarchitected.{region}.amazonaws.com",
            fipsHostname: "wellarchitected-fips.{region}.amazonaws.com",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:wellarchitected:{region}:{account-id}:{resource-id}",
            principal: "wellarchitected.amazonaws.com",
            hostname: "wellarchitected.{region}.amazonaws.com",
            fipsHostname: "wellarchitected-fips.{region}.amazonaws.com",
        },
    },
    wisdom: {
        aws: {
            arn: "arn:aws:wisdom:{region}:{account-id}:{resource-id}",
            principal: "wisdom.amazonaws.com",
            hostname: "wisdom.{region}.amazonaws.com",
            fipsHostname: "wisdom-fips.{region}.amazonaws.com",
        },
    },
    workdocs: {
        aws: {
            arn: "arn:aws:workdocs:{region}:{account-id}:{resource-id}",
            principal: "workdocs.amazonaws.com",
            hostname: "workdocs.{region}.amazonaws.com",
            fipsHostname: "workdocs-fips.{region}.amazonaws.com",
        },
    },
    workmail: {
        aws: {
            arn: "arn:aws:workmail:{region}:{account-id}:{resource-id}",
            principal: "workmail.amazonaws.com",
            hostname: "workmail.{region}.amazonaws.com",
            fipsHostname: "workmail-fips.{region}.amazonaws.com",
        },
    },
    workspaces: {
        aws: {
            arn: "arn:aws:workspaces:{region}:{account-id}:{resource-id}",
            principal: "workspaces.amazonaws.com",
            hostname: "workspaces.{region}.amazonaws.com",
            fipsHostname: "workspaces-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:workspaces:{region}:{account-id}:{resource-id}",
            principal: "workspaces.amazonaws.com.cn",
            hostname: "workspaces.{region}.amazonaws.com.cn",
            fipsHostname: "workspaces-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:workspaces:{region}:{account-id}:{resource-id}",
            principal: "workspaces.amazonaws.com",
            hostname: "workspaces.{region}.amazonaws.com",
            fipsHostname: "workspaces-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:workspaces:{region}:{account-id}:{resource-id}",
            principal: "workspaces.c2s.ic.gov",
            hostname: "workspaces.{region}.c2s.ic.gov",
            fipsHostname: "workspaces-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:workspaces:{region}:{account-id}:{resource-id}",
            principal: "workspaces.sc2s.sgov.gov",
            hostname: "workspaces.{region}.sc2s.sgov.gov",
            fipsHostname: "workspaces-fips.{region}.sc2s.sgov.gov",
        },
    },
    "workspaces-web": {
        aws: {
            arn: "arn:aws:workspaces-web:{region}:{account-id}:{resource-id}",
            principal: "workspaces-web.amazonaws.com",
            hostname: "workspaces-web.{region}.amazonaws.com",
            fipsHostname: "workspaces-web-fips.{region}.amazonaws.com",
        },
    },
    xray: {
        aws: {
            arn: "arn:aws:xray:{region}:{account-id}:{resource-id}",
            principal: "xray.amazonaws.com",
            hostname: "xray.{region}.amazonaws.com",
            fipsHostname: "xray-fips.{region}.amazonaws.com",
        },
        "aws-cn": {
            arn: "arn:aws-cn:xray:{region}:{account-id}:{resource-id}",
            principal: "xray.amazonaws.com.cn",
            hostname: "xray.{region}.amazonaws.com.cn",
            fipsHostname: "xray-fips.{region}.amazonaws.com.cn",
        },
        "aws-us-gov": {
            arn: "arn:aws-us-gov:xray:{region}:{account-id}:{resource-id}",
            principal: "xray.amazonaws.com",
            hostname: "xray.{region}.amazonaws.com",
            fipsHostname: "xray-fips.{region}.amazonaws.com",
        },
        "aws-iso": {
            arn: "arn:aws-iso:xray:{region}:{account-id}:{resource-id}",
            principal: "xray.c2s.ic.gov",
            hostname: "xray.{region}.c2s.ic.gov",
            fipsHostname: "xray-fips.{region}.c2s.ic.gov",
        },
        "aws-iso-b": {
            arn: "arn:aws-iso-b:xray:{region}:{account-id}:{resource-id}",
            principal: "xray.sc2s.sgov.gov",
            hostname: "xray.{region}.sc2s.sgov.gov",
            fipsHostname: "xray-fips.{region}.sc2s.sgov.gov",
        },
        "aws-iso-e": {
            arn: "arn:aws-iso-e:xray:{region}:{account-id}:{resource-id}",
            principal: "xray.cloud.adc-e.uk",
            hostname: "xray.{region}.cloud.adc-e.uk",
            fipsHostname: "xray-fips.{region}.cloud.adc-e.uk",
        },
        "aws-iso-f": {
            arn: "arn:aws-iso-f:xray:{region}:{account-id}:{resource-id}",
            principal: "xray.csp.hci.ic.gov",
            hostname: "xray.{region}.csp.hci.ic.gov",
            fipsHostname: "xray-fips.{region}.csp.hci.ic.gov",
        },
        "aws-eusc": {
            arn: "arn:aws-eusc:xray:{region}:{account-id}:{resource-id}",
            principal: "xray.amazonaws.com",
            hostname: "xray.{region}.amazonaws.eu",
            fipsHostname: "xray-fips.{region}.amazonaws.eu",
        },
    },
};

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
    | "CODEGURU_REVIEWER"
    | "CODEPIPELINE"
    | "CODESTAR"
    | "CODESTAR_CONNECTIONS"
    | "CODESTAR_NOTIFICATIONS"
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
    | "CUR"
    | "DATABREW"
    | "DATAEXCHANGE"
    | "DATAPIPELINE"
    | "DATASYNC"
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
    | "GAMESPARKS"
    | "GEO"
    | "GLACIER"
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
    | "OAM"
    | "OIDC"
    | "OMICS"
    | "OPSWORKS"
    | "OPSWORKS_CM"
    | "ORGANIZATIONS"
    | "OSIS"
    | "OUTPOSTS"
    | "PARTICIPANT_CONNECT"
    | "PERSONALIZE"
    | "PI"
    | "PINPOINT"
    | "PIPES"
    | "POLLY"
    | "PORTAL_SSO"
    | "PROFILE"
    | "PROJECTS_IOT1CLICK"
    | "PROTON"
    | "QLDB"
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
    | "TEXTRACT"
    | "TNB"
    | "TRANSCRIBE"
    | "TRANSCRIBESTREAMING"
    | "TRANSFER"
    | "TRANSLATE"
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
    CODEGURU_REVIEWER: "codeguru-reviewer",
    CODEPIPELINE: "codepipeline",
    CODESTAR: "codestar",
    CODESTAR_CONNECTIONS: "codestar-connections",
    CODESTAR_NOTIFICATIONS: "codestar-notifications",
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
    CUR: "cur",
    DATA_ATS_IOT: "data-ats.iot",
    DATA_IOT: "data.iot",
    DATA_JOBS_IOT: "data.jobs.iot",
    DATA_MEDIASTORE: "data.mediastore",
    DATABREW: "databrew",
    DATAEXCHANGE: "dataexchange",
    DATAPIPELINE: "datapipeline",
    DATASYNC: "datasync",
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
    FINSPACE: "finspace",
    FINSPACE_API: "finspace-api",
    FIREHOSE: "firehose",
    FMS: "fms",
    FORECAST: "forecast",
    FORECASTQUERY: "forecastquery",
    FRAUDDETECTOR: "frauddetector",
    FSX: "fsx",
    GAMELIFT: "gamelift",
    GAMESPARKS: "gamesparks",
    GEO: "geo",
    GLACIER: "glacier",
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
    OAM: "oam",
    OIDC: "oidc",
    OMICS: "omics",
    OPSWORKS: "opsworks",
    OPSWORKS_CM: "opsworks-cm",
    ORGANIZATIONS: "organizations",
    OSIS: "osis",
    OUTPOSTS: "outposts",
    PARTICIPANT_CONNECT: "participant.connect",
    PERSONALIZE: "personalize",
    PI: "pi",
    PINPOINT: "pinpoint",
    PIPES: "pipes",
    POLLY: "polly",
    PORTAL_SSO: "portal.sso",
    PROFILE: "profile",
    PROJECTS_IOT1CLICK: "projects.iot1click",
    PROTON: "proton",
    QLDB: "qldb",
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
    ROUTE53RESOLVER: "route53resolver",
    RUM: "rum",
    RUNTIME_V2_LEX: "runtime-v2-lex",
    RUNTIME_LEX: "runtime.lex",
    RUNTIME_SAGEMAKER: "runtime.sagemaker",
    S3: "s3",
    S3_CONTROL: "s3-control",
    S3_OUTPOSTS: "s3-outposts",
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
    TEXTRACT: "textract",
    TNB: "tnb",
    TRANSCRIBE: "transcribe",
    TRANSCRIBESTREAMING: "transcribestreaming",
    TRANSFER: "transfer",
    TRANSLATE: "translate",
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
    SAGEMAKER: "sagemaker",
    EXECUTE_API: "execute-api",
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
    },
    "cloudtrail-data": {
        aws: {
            arn: "arn:aws:cloudtrail-data:{region}:{account-id}:{resource-id}",
            principal: "cloudtrail-data.amazonaws.com",
            hostname: "cloudtrail-data.{region}.amazonaws.com",
            fipsHostname: "cloudtrail-data-fips.{region}.amazonaws.com",
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
            principal: "cognito-identity.amazonaws.com",
            hostname: "cognito-identity.{region}.amazonaws.com",
            fipsHostname: "cognito-identity-fips.{region}.amazonaws.com",
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
    },
    "entitlement.marketplace": {
        aws: {
            arn: "arn:aws:entitlement.marketplace:{region}:{account-id}:{resource-id}",
            principal: "entitlement.marketplace.amazonaws.com",
            hostname: "entitlement.marketplace.{region}.amazonaws.com",
            fipsHostname: "entitlement.marketplace-fips.{region}.amazonaws.com",
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
    },
    evidently: {
        aws: {
            arn: "arn:aws:evidently:{region}:{account-id}:{resource-id}",
            principal: "evidently.amazonaws.com",
            hostname: "evidently.{region}.amazonaws.com",
            fipsHostname: "evidently-fips.{region}.amazonaws.com",
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
    },
    grafana: {
        aws: {
            arn: "arn:aws:grafana:{region}:{account-id}:{resource-id}",
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
    },
    kafkaconnect: {
        aws: {
            arn: "arn:aws:kafkaconnect:{region}:{account-id}:{resource-id}",
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
    qldb: {
        aws: {
            arn: "arn:aws:qldb:{region}:{account-id}:{resource-id}",
            principal: "qldb.amazonaws.com",
            hostname: "qldb.{region}.amazonaws.com",
            fipsHostname: "qldb-fips.{region}.amazonaws.com",
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
    },
    "redshift-serverless": {
        aws: {
            arn: "arn:aws:redshift-serverless:{region}:{account-id}:{resource-id}",
            principal: "redshift-serverless.amazonaws.com",
            hostname: "redshift-serverless.{region}.amazonaws.com",
            fipsHostname: "redshift-serverless-fips.{region}.amazonaws.com",
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
    },
    resiliencehub: {
        aws: {
            arn: "arn:aws:resiliencehub:{region}:{account-id}:{resource-id}",
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
    },
    rum: {
        aws: {
            arn: "arn:aws:rum:{region}:{account-id}:{resource-id}",
            principal: "rum.amazonaws.com",
            hostname: "rum.{region}.amazonaws.com",
            fipsHostname: "rum-fips.{region}.amazonaws.com",
        },
    },
    "runtime-v2-lex": {
        aws: {
            arn: "arn:aws:runtime-v2-lex:{region}:{account-id}:{resource-id}",
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
    },
    scheduler: {
        aws: {
            arn: "arn:aws:scheduler:{region}:{account-id}:{resource-id}",
            principal: "scheduler.amazonaws.com",
            hostname: "scheduler.{region}.amazonaws.com",
            fipsHostname: "scheduler-fips.{region}.amazonaws.com",
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
    },
    securitylake: {
        aws: {
            arn: "arn:aws:securitylake:{region}:{account-id}:{resource-id}",
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
    },
    verifiedpermissions: {
        aws: {
            arn: "arn:aws:verifiedpermissions:{region}:{account-id}:{resource-id}",
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
    },
};

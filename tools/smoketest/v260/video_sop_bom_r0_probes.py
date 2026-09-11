#!/usr/bin/env python
"""R0 pre-deploy probes for the Video SOP/BOM Extraction pipeline (spec section 8, R0 table).

Each probe is one function that records what it OBSERVED. There is no PASS here: R0 exists to learn
the values the design guessed at -- the .sync Cause key names, how the Batch overrides cap surfaces,
the Fargate vCPU quota, whether a silent FLAC transcribes -- before any test is pinned to them. A probe
that cannot run prints `[SKIP] <probe> -- <reason>` and never a green line; a probe that raises prints
`[ERROR]` and the run exits 1. Every observation also lands in the JSON report.

Every client is built from one boto3 Session with an explicit Region: under AWS_PROFILE the profile's
own Region can win over AWS_REGION, and a probe would then measure a different deployment. The caller
identity ARN and the Region are printed before the first probe.

The JSON report (`--report`, alias `--json-out`) is what the smoke suite stores with
`suite_video_sop_bom.py --record R0=<report>`: `title`, `verdict`, `rows` and the FLAT_KEYS sit at the
top level (None for a probe that did not run), the full per-probe observations under `probes`.

Probes (`--list-probes`):

    transcribe-conditioned-statement  StartTranscriptionJob from a role holding exactly the D7 statement.
                                      With the CMK: accepted, and the output object's encryption is read
                                      back. Without the key: expected AccessDeniedException. Mismatched
                                      bucket: expected denied. Needs --aux-bucket and --role-arn or
                                      --create-role (the role is deleted afterwards unless --keep-role).
    silent-flac-transcription         the 30 s anullsrc FLAC with LanguageCode=en-US and then with
                                      IdentifyLanguage=True, both requesting subtitles: COMPLETED or
                                      FAILED, FailureReason, SubtitleFileUris presence, pronunciation items.
    language-codes                    each LANGUAGE_CODE value against the Transcribe SDK enum (offline;
                                      the batch-table check is WP00 Task 3).
    bedrock-converse                  converse(maxTokens=1) on the default model id: model access.
    vpc-endpoint-services             describe-vpc-endpoint-services for transcribe and bedrock-runtime.
    fargate-vcpu-quota                the Fargate On-Demand vCPU quota and its default.
    batch-9kb-command                 SubmitJob whose command carries a 9 KB string, in a deployed queue:
                                      how the 8,192-character ECS overrides cap surfaces.
                                      Needs --batch-job-queue and --batch-job-definition.
    batch-failing-container-cause     a job whose shell kills itself with SIGKILL (exit 137): the
                                      DescribeJobs key names a no-callback .sync Cause carries.

Usage:
    python tools/smoketest/v260/video_sop_bom_r0_probes.py --list-probes
    python tools/smoketest/v260/video_sop_bom_r0_probes.py --region us-east-1 --aws-profile <profile> \\
        --aux-bucket <aux bucket> --kms-key-arn <cmk arn> --create-role \\
        --batch-job-queue <queue> --batch-job-definition <job definition> [--only <probe>]

Flag aliases (the smoke suite's spellings): --model-id, --job-queue, --job-definition, --fixture-flac,
--json-out.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from typing import Callable, Dict, Optional, Sequence

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FLAC = os.path.join(HERE, "_video_sop_bom_fixtures", "silence-30s.flac")
DEFAULT_REPORT = os.path.join(HERE, "_video_sop_bom_r0_probes.json")
DEFAULT_BEDROCK_MODEL_ID = "global.anthropic.claude-sonnet-5"
SUPPORTED_LANGUAGES_URL = "https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html"

# vocab.LANGUAGE_CODES of the container package (the offline test pins the two equal); `auto` means
# IdentifyLanguage=True.
LANGUAGE_CODES = (
    "auto", "en-US", "en-GB", "en-AU", "de-DE", "fr-FR", "es-US", "es-ES", "it-IT", "pt-BR",
    "ja-JP", "ko-KR", "zh-CN",
)
# The codes read as `Data input = batch` in the Amazon Transcribe supported-languages table on the date
# given. A code added to LANGUAGE_CODES without being added here (after re-reading the table) fails the
# offline test.
BATCH_CONFIRMED = {
    "date": "2026-09-09",
    "source": SUPPORTED_LANGUAGES_URL,
    "codes": (
        "en-US", "en-GB", "en-AU", "de-DE", "fr-FR", "es-US", "es-ES", "it-IT", "pt-BR", "ja-JP", "ko-KR", "zh-CN",
    ),
}
# The flat view of the observations the smoke suite reads from the top level of the report
# (`--record R0=<report>`; R7b reads fargateOnDemandVcpuQuota, R9b identifyLanguageOnSilence).
FLAT_KEYS = (
    "identifyLanguageOnSilence", "languageCodeOnSilence", "subtitleFileUrisOnSilence",
    "fargateOnDemandVcpuQuota", "bedrockConverseProbe", "transcribeEndpointServiceName",
    "bedrockRuntimeEndpointServiceName", "conditionedStartTranscriptionJob", "overridesCapSurface",
    "noCallbackCauseKeys", "languageCodeEnumSupported",
)
REPORT_TITLE = "Video SOP/BOM R0 pre-deploy probes"
TRANSCRIBE_JOB_PREFIX = "vams-video-sop-bom-"
PROBE_ROLE_PREFIX = "vams-video-sop-bom-r0-probe-"
PROBE_ROLE_POLICY_NAME = "VideoSopBomR0Probe"
AUX_PROBE_PREFIX = "pipelines/genai-video-sop-bom/r0-probe-"
KMS_ACTIONS = [
    "kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*",
    "kms:ListKeys", "kms:CreateGrant", "kms:ListAliases",
]
RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})

PROBES: Dict[str, Callable[["ProbeContext"], None]] = {}
PROBE_SETTLES: Dict[str, str] = {}


def probe(name: str, settles: str):
    def decorate(fn):
        PROBES[name] = fn
        PROBE_SETTLES[name] = settles
        return fn
    return decorate


class Skip(Exception):
    """The probe cannot run here; the reason is printed as a SKIP line."""


class ProbeContext:
    def __init__(self, session, args, region: str, identity: dict) -> None:
        self.session = session
        self.args = args
        self.region = region
        self.identity = identity
        self.partition = identity["Arn"].split(":")[1]
        self.account = identity["Account"]
        self.report: Dict[str, Dict[str, object]] = {}
        self.current = ""

    def client(self, service: str):
        return self.session.client(service, region_name=self.region, config=RETRY_CONFIG)

    def observe(self, key: str, value) -> None:
        print(f"  [OBSERVED] {key} = {value}")
        self.report[self.current][key] = value

    def skip(self, reason: str) -> None:
        raise Skip(reason)


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", type(exc).__name__)


def _error_message(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Message", "")[:300]


def build_probe_role_policy(aux_bucket: str, kms_key_arn: str, partition: str, region: str, account: str) -> dict:
    """Exactly the job role's Transcribe + aux-bucket statements (spec D7, section 3.4), for a probe role."""
    conditions = {"StringEquals": {"transcribe:OutputBucketName": aux_bucket}}
    if kms_key_arn:
        conditions["StringEquals"]["transcribe:OutputEncryptionKMSKeyId"] = kms_key_arn
    statements = [
        {"Sid": "TranscribeStartScopedByOutput", "Effect": "Allow",
         "Action": ["transcribe:StartTranscriptionJob"], "Resource": "*", "Condition": conditions},
        {"Sid": "TranscribeJobReadDelete", "Effect": "Allow",
         "Action": ["transcribe:GetTranscriptionJob", "transcribe:DeleteTranscriptionJob"],
         "Resource": f"arn:{partition}:transcribe:{region}:{account}:transcription-job/{TRANSCRIBE_JOB_PREFIX}*"},
        {"Sid": "AuxBucket", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"],
         "Resource": [f"arn:{partition}:s3:::{aux_bucket}", f"arn:{partition}:s3:::{aux_bucket}/*"]},
    ]
    if kms_key_arn:
        statements.append({"Sid": "AuxKms", "Effect": "Allow", "Action": list(KMS_ACTIONS), "Resource": kms_key_arn})
    return {"Version": "2012-10-17", "Statement": statements}


def language_codes_in_service_model(codes: Sequence[str], region: str) -> Dict[str, bool]:
    """Which codes the Transcribe SDK model accepts as LanguageCode (offline; needs no credentials)."""
    enum = boto3.client("transcribe", region_name=region).meta.service_model.shape_for("LanguageCode").enum
    return {code: code in enum for code in codes if code != "auto"}


def _assumed_session(ctx: ProbeContext, role_arn: str):
    """A Session on the probe role; retried because a freshly created role takes seconds to propagate."""
    sts = ctx.client("sts")
    last: Optional[ClientError] = None
    for _ in range(12):
        try:
            credentials = sts.assume_role(RoleArn=role_arn, RoleSessionName="vams-video-sop-bom-r0")["Credentials"]
            return boto3.Session(aws_access_key_id=credentials["AccessKeyId"],
                                 aws_secret_access_key=credentials["SecretAccessKey"],
                                 aws_session_token=credentials["SessionToken"], region_name=ctx.region)
        except ClientError as exc:
            last = exc
            time.sleep(5)
    raise last  # type: ignore[misc]


def _wait_for_transcription(transcribe, job_name: str, timeout_s: int = 900, poll_s: int = 15) -> dict:
    deadline = time.time() + timeout_s
    while True:
        job = transcribe.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
        if job["TranscriptionJobStatus"] in ("COMPLETED", "FAILED") or time.time() > deadline:
            return job
        time.sleep(poll_s)


def _finish_transcription(ctx: ProbeContext, transcribe, job_name: str, label: str) -> dict:
    job = _wait_for_transcription(transcribe, job_name)
    status = job["TranscriptionJobStatus"]
    ctx.observe(f"{label}.status", status)
    ctx.observe(f"{label}.failure_reason", job.get("FailureReason"))
    if status in ("COMPLETED", "FAILED"):
        transcribe.delete_transcription_job(TranscriptionJobName=job_name)
    return job


def _upload_flac(ctx: ProbeContext, s3, key: str) -> None:
    with open(ctx.args.flac, "rb") as fh:
        body = fh.read()
    kwargs = {"Bucket": ctx.args.aux_bucket, "Key": key, "Body": body}
    if ctx.args.kms_key_arn:
        kwargs.update(ServerSideEncryption="aws:kms", SSEKMSKeyId=ctx.args.kms_key_arn)
    s3.put_object(**kwargs)


def _delete_prefix(s3, bucket: str, prefix: str) -> int:
    deleted = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
            deleted += 1
    return deleted


@probe("transcribe-conditioned-statement",
       "the caller-identity chain and the D7 condition keys: key ARN match, denial without the key, denial on a foreign bucket")
def transcribe_conditioned_statement(ctx: ProbeContext) -> None:
    args = ctx.args
    if not args.aux_bucket:
        ctx.skip("needs --aux-bucket")
    if not os.path.isfile(args.flac):
        ctx.skip(f"FLAC fixture missing at {args.flac}; run make_teardown_videos.py first")
    if not (args.role_arn or args.create_role):
        ctx.skip("needs --role-arn or --create-role")
    iam = ctx.client("iam")
    created: Optional[str] = None
    role_arn = args.role_arn
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        if not role_arn:
            created = f"{PROBE_ROLE_PREFIX}{secrets.token_hex(3)}"
            trust = {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"AWS": f"arn:{ctx.partition}:iam::{ctx.account}:root"},
                "Action": "sts:AssumeRole"}]}
            role_arn = iam.create_role(RoleName=created, AssumeRolePolicyDocument=json.dumps(trust),
                                       Description="VAMS Video SOP/BOM R0 probe role; safe to delete")["Role"]["Arn"]
            iam.put_role_policy(RoleName=created, PolicyName=PROBE_ROLE_POLICY_NAME, PolicyDocument=json.dumps(
                build_probe_role_policy(args.aux_bucket, args.kms_key_arn, ctx.partition, ctx.region, ctx.account)))
        ctx.observe("probe_role", role_arn)
        assumed = _assumed_session(ctx, role_arn)
        s3 = assumed.client("s3", config=RETRY_CONFIG)
        transcribe = assumed.client("transcribe", config=RETRY_CONFIG)
        run_id = secrets.token_hex(4)
        prefix = f"{AUX_PROBE_PREFIX}{run_id}/"
        audio_key = f"{prefix}audio/silence-30s.flac"
        _upload_flac(ctx, s3, audio_key)
        ctx.observe("audio_upload_by_probe_role", "ok")

        def start(label: str, **overrides):
            job_name = f"{TRANSCRIBE_JOB_PREFIX}r0-{run_id}-{label}"
            kwargs = dict(TranscriptionJobName=job_name, Media={"MediaFileUri": f"s3://{args.aux_bucket}/{audio_key}"},
                          MediaFormat="flac", LanguageCode="en-US", OutputBucketName=args.aux_bucket,
                          OutputKey=f"{prefix}transcribe/{job_name}.json")
            kwargs.update(overrides)
            try:
                transcribe.start_transcription_job(**kwargs)
                return job_name, "accepted"
            except ClientError as exc:
                return job_name, f"{_error_code(exc)}: {_error_message(exc)}"

        if args.kms_key_arn:
            job_a, outcome_a = start("withkey", OutputEncryptionKMSKeyId=args.kms_key_arn)
            ctx.observe("start_with_key", outcome_a)
            if outcome_a == "accepted":
                job = _finish_transcription(ctx, transcribe, job_a, "start_with_key")
                out_key = f"{prefix}transcribe/{job_a}.json"
                try:
                    head = s3.head_object(Bucket=args.aux_bucket, Key=out_key)
                    ctx.observe("output.ServerSideEncryption", head.get("ServerSideEncryption"))
                    ctx.observe("output.SSEKMSKeyId", head.get("SSEKMSKeyId"))
                except ClientError as exc:
                    ctx.observe("output.head_object", _error_code(exc))
                listing = s3.list_object_versions(Bucket=args.aux_bucket, Prefix=f"{prefix}transcribe/")
                names = sorted({v["Key"] for v in listing.get("Versions", [])}
                               | {d["Key"] for d in listing.get("DeleteMarkers", [])})
                ctx.observe("output.prefix_objects", names)
                ctx.observe("output.write_access_check_file_present", any(".write_access_check_file" in n for n in names))
                ctx.observe("start_with_key.transcript_uri_present", bool(job.get("Transcript", {}).get("TranscriptFileUri")))
        job_b, outcome_b = start("nokey")
        ctx.observe("start_without_key", outcome_b)
        if outcome_b == "accepted":
            _finish_transcription(ctx, transcribe, job_b, "start_without_key")
        job_c, outcome_c = start("mismatch", OutputBucketName=f"{args.aux_bucket}-mismatch")
        ctx.observe("start_mismatched_bucket", outcome_c)
        if outcome_c == "accepted":
            _finish_transcription(ctx, transcribe, job_c, "start_mismatched_bucket")
        ctx.observe("aux_objects_deleted", _delete_prefix(s3, args.aux_bucket, prefix))
        ctx.observe("cloudtrail_lookup", (
            f"aws cloudtrail lookup-events --region {ctx.region} --start-time {started_at} "
            f"--lookup-attributes AttributeKey=EventSource,AttributeValue=kms.amazonaws.com ; keep events whose "
            f"userIdentity.sessionContext.sessionIssuer.arn is {role_arn} (CloudTrail lags about 15 minutes)"))
    finally:
        if created and not args.keep_role:
            iam.delete_role_policy(RoleName=created, PolicyName=PROBE_ROLE_POLICY_NAME)
            iam.delete_role(RoleName=created)
            ctx.observe("probe_role_deleted", created)


@probe("silent-flac-transcription",
       "COMPLETED versus FAILED on silence, SubtitleFileUris presence, language identification on no speech (owner decision 7 data)")
def silent_flac_transcription(ctx: ProbeContext) -> None:
    args = ctx.args
    if not args.aux_bucket:
        ctx.skip("needs --aux-bucket")
    if not os.path.isfile(args.flac):
        ctx.skip(f"FLAC fixture missing at {args.flac}; run make_teardown_videos.py first")
    s3 = ctx.client("s3")
    transcribe = ctx.client("transcribe")
    run_id = secrets.token_hex(4)
    prefix = f"{AUX_PROBE_PREFIX}{run_id}/"
    audio_key = f"{prefix}audio/silence-30s.flac"
    _upload_flac(ctx, s3, audio_key)
    for label, language in (("en_us", {"LanguageCode": "en-US"}), ("auto", {"IdentifyLanguage": True})):
        job_name = f"{TRANSCRIBE_JOB_PREFIX}r0-{run_id}-{label}"
        kwargs = dict(TranscriptionJobName=job_name, Media={"MediaFileUri": f"s3://{args.aux_bucket}/{audio_key}"},
                      MediaFormat="flac", OutputBucketName=args.aux_bucket,
                      OutputKey=f"{prefix}transcribe/{job_name}.json",
                      Subtitles={"Formats": ["vtt", "srt"], "OutputStartIndex": 1}, **language)
        if args.kms_key_arn:
            kwargs["OutputEncryptionKMSKeyId"] = args.kms_key_arn
        try:
            transcribe.start_transcription_job(**kwargs)
        except ClientError as exc:
            ctx.observe(f"{label}.start", f"{_error_code(exc)}: {_error_message(exc)}")
            continue
        job = _finish_transcription(ctx, transcribe, job_name, label)
        ctx.observe(f"{label}.language_code", job.get("LanguageCode"))
        ctx.observe(f"{label}.identified_language_score", job.get("IdentifiedLanguageScore"))
        ctx.observe(f"{label}.transcript_uri_present", bool(job.get("Transcript", {}).get("TranscriptFileUri")))
        ctx.observe(f"{label}.subtitle_file_uris", job.get("Subtitles", {}).get("SubtitleFileUris"))
        if job["TranscriptionJobStatus"] == "COMPLETED":
            body = s3.get_object(Bucket=args.aux_bucket, Key=f"{prefix}transcribe/{job_name}.json")["Body"].read()
            results = json.loads(body).get("results", {})
            items = results.get("items", [])
            ctx.observe(f"{label}.pronunciation_items", sum(1 for item in items if item.get("type") == "pronunciation"))
            transcripts = results.get("transcripts") or [{}]
            ctx.observe(f"{label}.transcript_text_length", len(transcripts[0].get("transcript", "")))
    ctx.observe("aux_objects_deleted", _delete_prefix(s3, args.aux_bucket, prefix))


@probe("language-codes", "each LANGUAGE_CODE value against the Transcribe SDK enum (offline); the batch-table check is WP00 Task 3")
def language_codes(ctx: ProbeContext) -> None:
    for code, present in language_codes_in_service_model(LANGUAGE_CODES, ctx.region).items():
        ctx.observe(f"sdk_enum.{code}", present)
    ctx.observe("auto", "IdentifyLanguage=True (not a LanguageCode value)")
    ctx.observe("batch_table_url", SUPPORTED_LANGUAGES_URL)


@probe("bedrock-converse", "model access for the default model id in this account and Region (the stage-0 preflight)")
def bedrock_converse(ctx: ProbeContext) -> None:
    bedrock = ctx.client("bedrock-runtime")
    ctx.observe("model_id", ctx.args.bedrock_model_id)
    try:
        response = bedrock.converse(modelId=ctx.args.bedrock_model_id,
                                    messages=[{"role": "user", "content": [{"text": "Reply with one word."}]}],
                                    inferenceConfig={"maxTokens": 1})
        ctx.observe("stopReason", response.get("stopReason"))
        ctx.observe("usage", response.get("usage"))
    except ClientError as exc:
        ctx.observe("error", f"{_error_code(exc)}: {_error_message(exc)}")


@probe("vpc-endpoint-services", "interface endpoint service names for transcribe and bedrock-runtime in this Region")
def vpc_endpoint_services(ctx: ProbeContext) -> None:
    ec2 = ctx.client("ec2")
    wanted = [f"com.amazonaws.{ctx.region}.{service}"
              for service in ("transcribe", "transcribe-fips", "bedrock-runtime", "bedrock-runtime-fips")]
    response = ec2.describe_vpc_endpoint_services(Filters=[{"Name": "service-name", "Values": wanted}])
    found = {detail["ServiceName"]: [t.get("ServiceType") for t in detail.get("ServiceType", [])]
             for detail in response.get("ServiceDetails", [])}
    for name in wanted:
        ctx.observe(name, found.get(name, "absent"))


@probe("fargate-vcpu-quota", "the Fargate On-Demand vCPU quota that serialises concurrent runs (spec D2)")
def fargate_vcpu_quota(ctx: ProbeContext) -> None:
    quotas = ctx.client("service-quotas")
    for page in quotas.get_paginator("list_service_quotas").paginate(ServiceCode="fargate"):
        for quota in page["Quotas"]:
            if "On-Demand vCPU" in quota["QuotaName"]:
                ctx.observe("quota_name", quota["QuotaName"])
                ctx.observe("quota_code", quota["QuotaCode"])
                ctx.observe("applied_value", quota["Value"])
                ctx.observe("adjustable", quota["Adjustable"])
                default = quotas.get_aws_default_service_quota(ServiceCode="fargate", QuotaCode=quota["QuotaCode"])
                ctx.observe("default_value", default["Quota"]["Value"])
                return
    ctx.skip("no fargate quota named 'On-Demand vCPU' was listed")


def _wait_for_batch_job(batch, job_id: str, timeout_s: int = 600, poll_s: int = 15) -> dict:
    deadline = time.time() + timeout_s
    while True:
        job = batch.describe_jobs(jobs=[job_id])["jobs"][0]
        if job["status"] in ("SUCCEEDED", "FAILED") or time.time() > deadline:
            return job
        time.sleep(poll_s)


def _observe_batch_outcome(ctx: ProbeContext, batch, job_id: str) -> None:
    job = _wait_for_batch_job(batch, job_id)
    container = job.get("container", {})
    ctx.observe("status", job["status"])
    ctx.observe("statusReason", job.get("statusReason"))
    ctx.observe("container.exitCode", container.get("exitCode"))
    ctx.observe("container.reason", container.get("reason"))
    attempts = job.get("attempts", [])
    if attempts:
        last = attempts[-1]
        ctx.observe("attempts[-1].statusReason", last.get("statusReason"))
        ctx.observe("attempts[-1].container", {k: last.get("container", {}).get(k) for k in ("exitCode", "reason")})
    ctx.observe("top_level_keys", sorted(job.keys()))
    if job["status"] not in ("SUCCEEDED", "FAILED"):
        batch.terminate_job(jobId=job_id, reason="VAMS Video SOP/BOM R0 probe: poll timeout")
        ctx.observe("terminated_after_timeout", True)


@probe("batch-9kb-command", "how the 8,192-character ECS overrides cap surfaces for a 9 KB command (motivates the definition pointer)")
def batch_9kb_command(ctx: ProbeContext) -> None:
    args = ctx.args
    if not (args.batch_job_queue and args.batch_job_definition):
        ctx.skip("needs --batch-job-queue and --batch-job-definition")
    batch = ctx.client("batch")
    payload = "X" * 9216
    try:
        response = batch.submit_job(jobName=f"vams-video-sop-bom-r0-9kb-{secrets.token_hex(3)}",
                                    jobQueue=args.batch_job_queue, jobDefinition=args.batch_job_definition,
                                    containerOverrides={"command": ["sh", "-c", "exit 0", payload]})
    except ClientError as exc:
        ctx.observe("submit_job", f"{_error_code(exc)}: {_error_message(exc)}")
        return
    ctx.observe("submit_job", "accepted")
    ctx.observe("job_id", response["jobId"])
    _observe_batch_outcome(ctx, batch, response["jobId"])


@probe("batch-failing-container-cause", "the DescribeJobs key names a no-callback .sync Cause carries (exit 137 without SendTaskFailure)")
def batch_failing_container_cause(ctx: ProbeContext) -> None:
    args = ctx.args
    if not (args.batch_job_queue and args.batch_job_definition):
        ctx.skip("needs --batch-job-queue and --batch-job-definition")
    batch = ctx.client("batch")
    response = batch.submit_job(jobName=f"vams-video-sop-bom-r0-exit137-{secrets.token_hex(3)}",
                                jobQueue=args.batch_job_queue, jobDefinition=args.batch_job_definition,
                                containerOverrides={"command": ["sh", "-c", "kill -9 $$"]})
    ctx.observe("job_id", response["jobId"])
    _observe_batch_outcome(ctx, batch, response["jobId"])


def _ran(entry: Dict[str, object]) -> bool:
    """Whether a probe entry holds observations (a skipped or errored probe holds only its reason and timing)."""
    return any(key not in ("skip", "error", "seconds") for key in entry)


def _row_summary(entry: Dict[str, object]) -> str:
    if "skip" in entry:
        return f"SKIP: {entry['skip']}"
    if "error" in entry:
        return f"ERROR: {entry['error']}"
    observed = "; ".join(f"{key}={value}" for key, value in entry.items() if key != "seconds")
    return observed[:300] or "no observations"


def _endpoint(endpoints: Dict[str, object], name: str) -> Optional[str]:
    return name if endpoints.get(name) not in (None, "absent") else None


def summarize(probes: Dict[str, Dict[str, object]], region: str) -> Dict[str, object]:
    """The flat view of the observations (FLAT_KEYS plus title, verdict and ledger rows).

    Every value is None until the probe that produces it has run and observed it; nothing here is a
    verdict -- `verdict` is the ledger's word for "recorded as observed".
    """
    silent = probes.get("silent-flac-transcription", {})
    conditioned = probes.get("transcribe-conditioned-statement", {})
    endpoints = probes.get("vpc-endpoint-services", {})
    nine_kb = probes.get("batch-9kb-command", {})
    bedrock = probes.get("bedrock-converse", {})
    enum = probes.get("language-codes", {})
    submit = nine_kb.get("submit_job")
    if submit is None:
        cap_surface = None
    elif submit != "accepted":
        cap_surface = "validation"
    else:
        cap_surface = f"job-{str(nine_kb.get('status', 'unknown')).lower()}"
    if not _ran(bedrock):
        converse = None
    elif "stopReason" in bedrock:
        converse = "ok"
    else:
        converse = str(bedrock.get("error", "unknown")).split(":")[0]
    return {
        "title": REPORT_TITLE,
        "verdict": "RECORDED",
        "identifyLanguageOnSilence": silent.get("auto.status"),
        "languageCodeOnSilence": silent.get("en_us.status"),
        "subtitleFileUrisOnSilence": (None if "en_us.subtitle_file_uris" not in silent
                                      else bool(silent["en_us.subtitle_file_uris"])),
        "fargateOnDemandVcpuQuota": probes.get("fargate-vcpu-quota", {}).get("applied_value"),
        "bedrockConverseProbe": converse,
        "transcribeEndpointServiceName": _endpoint(endpoints, f"com.amazonaws.{region}.transcribe"),
        "bedrockRuntimeEndpointServiceName": _endpoint(endpoints, f"com.amazonaws.{region}.bedrock-runtime"),
        "conditionedStartTranscriptionJob": None if not _ran(conditioned) else {
            "withKey": conditioned.get("start_with_key"),
            "withoutKey": conditioned.get("start_without_key"),
            "mismatchedBucket": conditioned.get("start_mismatched_bucket"),
        },
        "overridesCapSurface": cap_surface,
        "noCallbackCauseKeys": probes.get("batch-failing-container-cause", {}).get("top_level_keys"),
        "languageCodeEnumSupported": (None if not _ran(enum)
                                      else [code for code in LANGUAGE_CODES[1:] if enum.get(f"sdk_enum.{code}")]),
        "rows": [[name, _row_summary(entry)] for name, entry in probes.items()],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or None,
                        help="Region under test (required; never inferred from the profile)")
    parser.add_argument("--aws-profile", default=os.environ.get("AWS_PROFILE") or None)
    parser.add_argument("--only", action="append", choices=list(PROBES), help="run one probe (repeatable)")
    parser.add_argument("--list-probes", action="store_true", help="print the probes and exit")
    parser.add_argument("--aux-bucket", help="the deployment's auxiliary bucket name")
    parser.add_argument("--kms-key-arn", default="", help="the deployment's CMK ARN when useKmsCmkEncryption is on")
    parser.add_argument("--role-arn", help="an existing role holding exactly the D7 statement")
    parser.add_argument("--create-role", action="store_true", help="create (and afterwards delete) the probe role")
    parser.add_argument("--keep-role", action="store_true", help="leave a created probe role in place")
    parser.add_argument("--flac", "--fixture-flac", dest="flac", default=DEFAULT_FLAC,
                        help="the 30 s anullsrc FLAC fixture")
    parser.add_argument("--bedrock-model-id", "--model-id", dest="bedrock_model_id", default=DEFAULT_BEDROCK_MODEL_ID)
    parser.add_argument("--batch-job-queue", "--job-queue", dest="batch_job_queue",
                        help="a deployed Batch job queue (any Fargate pipeline's)")
    parser.add_argument("--batch-job-definition", "--job-definition", dest="batch_job_definition",
                        help="a deployed Batch job definition whose image has sh")
    parser.add_argument("--report", "--json-out", dest="report", default=DEFAULT_REPORT,
                        help="where the JSON report is written")
    return parser


def default_session_factory(args):
    return boto3.Session(profile_name=args.aws_profile, region_name=args.region)


def main(argv: Optional[Sequence[str]] = None, session_factory=None) -> int:
    args = _parser().parse_args(argv)
    if args.list_probes:
        for name in PROBES:
            print(f"  {name:34s} {PROBE_SETTLES[name]}")
        return 0
    if not args.region:
        print("--region (or AWS_REGION) is required; the Region is never inferred from the profile")
        return 2
    session = (session_factory or default_session_factory)(args)
    try:
        identity = session.client("sts", region_name=args.region, config=RETRY_CONFIG).get_caller_identity()
    except Exception as exc:  # noqa: BLE001 -- the run is pointless without an identity
        print(f"[ERROR] caller identity -- {type(exc).__name__}: {exc}")
        return 1
    print(f"identity: {identity['Arn']}")
    print(f"region:   {args.region}")
    ctx = ProbeContext(session, args, args.region, identity)
    skipped = errors = 0
    for name in (args.only or list(PROBES)):
        print(f"\n=== {name}\n  settles: {PROBE_SETTLES[name]}")
        ctx.current = name
        ctx.report[name] = {}
        started = time.time()
        try:
            PROBES[name](ctx)
        except Skip as exc:
            skipped += 1
            ctx.report[name]["skip"] = str(exc)
            print(f"  [SKIP] {name} -- {exc}")
        except Exception as exc:  # noqa: BLE001 -- recorded, never hidden
            errors += 1
            ctx.report[name]["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  [ERROR] {name} -- {type(exc).__name__}: {exc}")
        ctx.report[name]["seconds"] = round(time.time() - started, 1)
    observed = sum(1 for entry in ctx.report.values() for key in entry if key not in ("skip", "error", "seconds"))
    print(f"\nR0 PROBES: observed {observed}  skipped {skipped}  errors {errors}")
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    document = {"identity": identity["Arn"], "region": args.region,
                **summarize(ctx.report, args.region), "probes": ctx.report}
    with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(document, fh, indent=2, default=str)
    print(f"report: {args.report}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

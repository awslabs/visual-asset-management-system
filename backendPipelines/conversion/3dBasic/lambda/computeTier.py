#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Which compute tier, and which AWS Batch job size, a 3D Basic Conversion run uses.

A run starts on the Lambda function. A run the function refuses or cannot finish returns a named
result (the container's ``conversion_service.escalation_result``) and ``choose_job_size`` picks the
smallest permitted Fargate job whose size-check limit admits the input, or refuses the run with a
message that names the file, its size, the limit, the format pair, the resource and the largest job
size. The container holds the size-check factors; this module reads the limits it returns for each job
size, so the formulas live in one place.

The input configuration's hidden ``computeTier`` key (``auto`` | ``lambda`` | ``batch``) forces a tier
for an administrator or a test, and ``batchMemoryGiB`` (30 | 60 | 120) names the job size of a forced
Batch run (default 30). Neither is a template field."""

MB = 1024 ** 2
GIB = 1024

TIER_AUTO = "auto"
TIER_LAMBDA = "lambda"
TIER_BATCH = "batch"
TIERS = (TIER_LAMBDA, TIER_BATCH)

COMPUTE_TIER_PARAMETER = "computeTier"
BATCH_MEMORY_PARAMETER = "batchMemoryGiB"

# The Fargate job sizes of the tier, as (vCPU, memory MiB). The container's
# size_check.BATCH_TASK_SIZES and the CDK construct's ladder are the same; a drift test compares them.
JOB_SIZES = ((4, 30 * GIB), (8, 60 * GIB), (16, 120 * GIB))
DEFAULT_MAX_MEMORY_GIB = 120
FORCED_DEFAULT_MEMORY_GIB = 30

CONFIG_KEY = "app.pipelines.useConversion3dBasic.batchTier.maxMemoryGiB"


class JobSizeRefused(ValueError):
    """No permitted job size can convert the input; the message is the run's error."""


def requested_tier(parameters):
    """The tier ``parameters`` (a parsed input configuration) forces, or ``auto`` when it names none or
    names a value that is not a tier."""
    if not isinstance(parameters, dict):
        return TIER_AUTO
    value = parameters.get(COMPUTE_TIER_PARAMETER)
    tier = value.strip().lower() if isinstance(value, str) else ""
    return tier if tier in TIERS else TIER_AUTO


def max_memory_gib(value=None):
    """The largest job's memory in GiB the deployment permits (30, 60 or 120); the largest when the
    value is anything else."""
    try:
        gib = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_MEMORY_GIB
    return gib if gib in (size[1] // GIB for size in JOB_SIZES) else DEFAULT_MAX_MEMORY_GIB


def permitted_sizes(max_gib=None):
    """The job sizes up to the deployment's cap, smallest first."""
    cap = max_memory_gib(max_gib) * GIB
    return [size for size in JOB_SIZES if size[1] <= cap]


def size_record(size):
    vcpu, mib = size
    return {"vcpu": vcpu, "memoryMiB": mib, "key": f"{mib // GIB}"}


def forced_batch_size(parameters, max_gib=None):
    """The job size of a run forced onto Batch: ``batchMemoryGiB`` when it names a permitted size, else
    the smallest."""
    permitted = permitted_sizes(max_gib)
    wanted = parameters.get(BATCH_MEMORY_PARAMETER) if isinstance(parameters, dict) else None
    for size in permitted:
        if isinstance(wanted, int) and not isinstance(wanted, bool) and size[1] == wanted * GIB:
            return size_record(size)
    return size_record(permitted[0])


def _mb(value):
    return f"{value / MB:,.1f} MB"


def _limits(escalation):
    raw = escalation.get("limitBytesByTaskMemoryMiB") if isinstance(escalation, dict) else None
    limits = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                limits[int(key)] = int(value) if value is not None else None
            except (TypeError, ValueError):
                continue
    return limits


def choose_job_size(escalation, max_gib=None):
    """The smallest permitted job size that can run the escalated request, as ``{vcpu, memoryMiB, key}``.

    A size qualifies when its memory is more than the memory the run already failed on
    (``minTaskMemoryMiB``) and its size-check limit admits ``inputBytes``. Raises ``JobSizeRefused`` with
    the run's error when none does. A request without sizes (an older container) takes the smallest
    permitted size above ``minTaskMemoryMiB`` and leaves the container's own check to refuse it."""
    permitted = permitted_sizes(max_gib)
    limits = _limits(escalation)
    input_bytes = escalation.get("inputBytes") if isinstance(escalation, dict) else None
    floor = escalation.get("minTaskMemoryMiB") if isinstance(escalation, dict) else 0
    floor = floor if isinstance(floor, int) and not isinstance(floor, bool) else 0
    for size in permitted:
        if size[1] <= floor:
            continue
        limit = limits.get(size[1])
        if isinstance(input_bytes, int) and limit is not None and limit < input_bytes:
            continue
        return size_record(size)
    raise JobSizeRefused(_refusal(escalation, permitted, limits, input_bytes, floor, max_gib))


def _refusal(escalation, permitted, limits, input_bytes, floor, max_gib):
    largest_available = JOB_SIZES[-1]
    largest = permitted[-1]
    name = escalation.get("inputName") or "The input"
    pair = escalation.get("formatPair") or "this conversion"
    resource = escalation.get("resource") or "job memory"
    # The attempt that refused ran on the function; the limit cited here is the largest job's.
    resource = "job memory" if resource == "function memory" else resource
    where = f"{largest[0]} vCPU and {largest[1] // GIB} GiB"
    capped = largest != largest_available
    limit = limits.get(largest[1])
    if isinstance(input_bytes, int) and limit is not None and limit < input_bytes:
        text = (f"{name} is {_mb(input_bytes)}, over the {_mb(limit)} limit for converting {pair} on the "
                f"largest job size this deployment allows ({where}). The limit is set by the {resource}.")
    else:
        text = (f"{name} did not convert in the {floor // GIB} GiB of memory it had, and the largest job "
                f"size this deployment allows ({where}) is not larger.")
    if capped:
        text += (f" The deployment caps jobs at {largest[1] // GIB} GiB ({CONFIG_KEY}); the largest job "
                 f"available is {largest_available[0]} vCPU and {largest_available[1] // GIB} GiB.")
    return text + " Reduce the model's size and run the conversion again."


def refusal_text(error):
    """The text a refused run reports, for the state machine's failure path."""
    return str(error)

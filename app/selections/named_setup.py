"""Owner setup for B8/B9 consumers; no activation or model-generated shell."""

import re
import shlex
from app.task_contracts import TaskConfiguration


def build_named_command(configuration, review_image, security_image):
    task = configuration["taskType"]
    if task not in ("ci_review", "security_analysis"):
        raise ValueError("Review or security configuration required")
    if configuration.get("taskContractVersion") != 1:
        raise ValueError(
            "Edit this historical configuration to adopt task contract version 1"
        )
    cfg = TaskConfiguration.model_validate(configuration.get("taskConfiguration"))
    security = task == "security_analysis"
    if cfg.inputs.files or cfg.inputs.artifacts or cfg.inputs.diff == security:
        raise ValueError(
            "Review requires diff only; security requires a bounded source export"
        )
    image = security_image if security else review_image
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", image):
        raise ValueError("Setup requires reviewed image digests")
    credential = configuration["model"]["credentialEnvVar"]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", credential):
        raise ValueError("Invalid credential variable")
    resources = cfg.resources
    mount = (
        '  -v "$DRIFTPLAIN_SOURCE:/workspace:ro" -e AGENT_WORKSPACE=/workspace \\\n'
        if security
        else '  -v "$DRIFTPLAIN_INPUTS:/inputs:ro" \\\n'
    )
    return (
        "# Run on a Linux cgroup-v2 CI worker with prepared job-private inputs.\n"
        "docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \\\n"
        f"  --cpus {resources.cpu_millis / 1000:g} --memory {resources.memory_mib}m --pids-limit {resources.max_processes} \\\n"
        "  --tmpfs /tmp:rw,nosuid,nodev,size=256m \\\n"
        + mount
        + "  -e MODELMATCH_API_URL -e MODELMATCH_PROJECT_ID -e MODELMATCH_CI_TOKEN \\\n"
        "  -e MODELMATCH_EXECUTION_CONFIG=true -e MODELMATCH_POST_RESULT=true -e BUILD_TAG \\\n"
        f"  -e {credential} --entrypoint /venv/bin/python {shlex.quote(image)} -m agent"
        + ("\n" if security else " --diff /inputs/change.diff\n")
    )


def build_named_stage(command, task):
    title = "Review changes" if task == "ci_review" else "Security analysis"
    return (
        f"stage('{title}') {{\n  steps {{\n    sh '''set -eu\n"
        + command.rstrip()
        + ' > "$DRIFTPLAIN_SCRATCH/result.json"\n'
        + "'''\n  }\n  post {\n    always {\n      dir(env.DRIFTPLAIN_SCRATCH) {\n"
        + "        archiveArtifacts artifacts: 'result.json', allowEmptyArchive: true\n"
        + "      }\n    }\n  }\n}\n"
    )

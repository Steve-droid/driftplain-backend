"""B10 runtime setup seam. Full picker and editable setup UX belong to B13/B14."""

import re
import shlex


def build_execution_command(configuration, launcher_image, editor_image):
    """Trusted node shell fragment; no prompts/commands from the project enter shell text."""
    if configuration["taskType"] not in (
        "other",
        "test_generation",
        "ci_failure_diagnosis",
    ):
        raise ValueError("Writable or custom configuration required")
    if configuration["taskType"] == "test_generation":
        from app.task_contracts import TaskConfiguration
        from app.test_generation_contracts import validate_test_environment

        validate_test_environment(
            TaskConfiguration.model_validate(configuration["taskConfiguration"]),
            configuration["policy"]["language"],
        )
    mode = configuration["executionMode"]
    if mode not in ("single_call", "opencode"):
        raise ValueError("Unsupported mode")
    for image in (
        (launcher_image, editor_image) if mode == "opencode" else (launcher_image,)
    ):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", image):
            raise ValueError("Setup requires reviewed image digests")
    credential = configuration["model"]["credentialEnvVar"]
    if not re.fullmatch("[A-Z][A-Z0-9_]{0,127}", credential):
        raise ValueError("Invalid credential variable")
    diagnosis = configuration["taskType"] == "ci_failure_diagnosis"
    common = """  -e MODELMATCH_API_URL -e MODELMATCH_PROJECT_ID -e MODELMATCH_CI_TOKEN \\
  -e MODELMATCH_EXECUTION_CONFIG=true -e MODELMATCH_POST_RESULT=true -e BUILD_TAG \\
"""
    if diagnosis:
        common += "  -e DRIFTPLAIN_FAILED_STAGE -e DRIFTPLAIN_UPSTREAM_STATUS -e DRIFTPLAIN_UPSTREAM_EXIT_STATUS \\\n"
    base = (
        'export AGENT_BASE_COMMIT="${AGENT_BASE_COMMIT:?Exact upstream commit required}"\n'
        if diagnosis
        else 'export AGENT_BASE_COMMIT="$(git rev-parse HEAD)"\n'
    )
    diff = (
        " --diff /inputs/change.diff"
        if configuration["taskConfiguration"]["inputs"]["diff"]
        else ""
    )
    if mode == "single_call":
        return (
            """# Bind explicitly prepared diff and named artifacts at /inputs. Prompts stay in the API revision.
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \\
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \\
  -v "$PWD:/workspace:ro" -v "$DRIFTPLAIN_INPUTS:/inputs:ro" \\
  -e AGENT_WORKSPACE=/workspace -e AGENT_INPUT_ARTIFACTS=/inputs \\
"""
            + common
            + f"  -e {credential} --entrypoint /venv/bin/python {shlex.quote(launcher_image)} -m agent{diff}\n"
        )
    return (
        """# Trusted launcher only: it controls sibling containers. Never mount this socket into editor/validation.
# DRIFTPLAIN_SCRATCH must be a fresh absolute directory outside the repository; archive result/* afterward.
"""
        + base
        + """docker run --rm --user "$(id -u):$(id -g)" --group-add "$(stat -c %g /var/run/docker.sock)" \\
  -v /var/run/docker.sock:/var/run/docker.sock \\
  -v "$PWD:$PWD:ro" -v "$DRIFTPLAIN_SCRATCH:$DRIFTPLAIN_SCRATCH" \\
  -v "$DRIFTPLAIN_INPUTS:/inputs:ro" \\
  -e "TMPDIR=$DRIFTPLAIN_SCRATCH" -e "AGENT_WORKSPACE=$PWD" \\
  -e "AGENT_OUTPUT_DIR=$DRIFTPLAIN_SCRATCH/result" -e AGENT_BASE_COMMIT \\
  -e AGENT_INPUT_ARTIFACTS=/inputs \\
"""
        + common
        + f"  -e {credential} -e AGENT_OTHER_IMAGE={shlex.quote(editor_image)} \\\n  --entrypoint /venv/bin/python {shlex.quote(launcher_image)} -m agent{diff}\n"
    )


# Compatibility import for the B10 setup seam.
build_other_command = build_execution_command


def build_test_stage(command):
    """Jenkins preserves the nonzero agent exit and archives evidence even on failure."""
    return (
        "stage('Generate tests') {\n  steps {\n    sh '''set -eu\n"
        + command.rstrip()
        + ' > "$DRIFTPLAIN_SCRATCH/result.json"\n'
        + "'''\n  }\n  post {\n    always {\n      dir(env.DRIFTPLAIN_SCRATCH) {\n"
        + "        archiveArtifacts artifacts: 'result/**,result.json', allowEmptyArchive: true\n"
        + "      }\n    }\n  }\n}\n"
    )

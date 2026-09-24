"""B10 runtime setup seam. Full picker and editable setup UX belong to B13/B14."""

import re
import shlex


def build_other_command(configuration, launcher_image, editor_image):
    """Trusted node shell fragment; no prompts/commands from the project enter shell text."""
    if configuration["taskType"] != "other":
        raise ValueError("Other configuration required")
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
    common = """  -e MODELMATCH_API_URL -e MODELMATCH_PROJECT_ID -e MODELMATCH_CI_TOKEN \\
  -e MODELMATCH_EXECUTION_CONFIG=true -e MODELMATCH_POST_RESULT=true -e BUILD_TAG \\
"""
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
export AGENT_BASE_COMMIT="$(git rev-parse HEAD)"
docker run --rm --user "$(id -u):$(id -g)" --group-add "$(stat -c %g /var/run/docker.sock)" \\
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

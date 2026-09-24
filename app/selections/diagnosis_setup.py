"""Failure-preserving Jenkins wrapper for one configured upstream command."""

import shlex
from pathlib import PurePosixPath
from app.task_contracts import TaskConfiguration


def build_diagnosis_stage(command, configuration):
    cfg = TaskConfiguration.model_validate(configuration["taskConfiguration"])
    if cfg.diagnosis is None:
        raise ValueError(
            "Diagnosis setup requires a selected upstream stage and log artifact"
        )
    stage = cfg.diagnosis.stage
    log = shlex.quote(cfg.diagnosis.log_artifact)
    return (
        "// Replace the selected upstream stage with this wrapper; do not add a recursive post-failure hook.\n"
        "// Maintainer supplies DRIFTPLAIN_UPSTREAM_COMMAND and fresh external INPUTS/SCRATCH directories.\n"
        f"stage('{stage}') {{\n  steps {{\n    script {{\n"
        "      def failedCommit = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()\n"
        "      sh '''set -eu\n"
        'test -n "$DRIFTPLAIN_UPSTREAM_COMMAND"\n'
        + 'mkdir -p "$DRIFTPLAIN_INPUTS"\n'
        + f'mkdir -p "$DRIFTPLAIN_INPUTS"/{shlex.quote(str(PurePosixPath(cfg.diagnosis.log_artifact).parent))}\n'
        + f'test ! -e "$DRIFTPLAIN_INPUTS"/{log}\n'
        + "'''\n"
        + "      def upstreamStatus = sh(returnStatus: true, script: '''set -eu\n"
        + f'sh -c "$DRIFTPLAIN_UPSTREAM_COMMAND" > "$DRIFTPLAIN_INPUTS"/{log} 2>&1\n'
        + "''')\n"
        "      if (upstreamStatus == 0) { return } // no diagnosis/model invocation\n"
        "      if (upstreamStatus != 0) {\n"
        "        currentBuild.result = 'FAILURE'\n"
        "        try {\n"
        f"          withEnv(['DRIFTPLAIN_FAILED_STAGE={stage}', 'DRIFTPLAIN_UPSTREAM_STATUS=FAILURE',\n"
        '                   "DRIFTPLAIN_UPSTREAM_EXIT_STATUS=${upstreamStatus}", "AGENT_BASE_COMMIT=${failedCommit}"]) {\n'
        "            def agentStatus = sh(returnStatus: true, script: '''set -eu\n"
        + command.rstrip()
        + ' > "$DRIFTPLAIN_SCRATCH/result.json"\n'
        + "''')\n"
        '            echo "Diagnosis exit ${agentStatus}; original upstream failure is unchanged. See result.json for agent outcome."\n'
        "          }\n"
        "        } finally {\n"
        "          if (currentBuild.currentResult != 'ABORTED') { currentBuild.result = 'FAILURE' }\n"
        "          dir(env.DRIFTPLAIN_SCRATCH) { archiveArtifacts artifacts: 'result/**,result.json', allowEmptyArchive: true }\n"
        f"          dir(env.DRIFTPLAIN_INPUTS) {{ archiveArtifacts artifacts: '{cfg.diagnosis.log_artifact}', allowEmptyArchive: true }}\n"
        "        }\n"
        "        error('Original upstream stage failed')\n"
        "      }\n"
        "    }\n  }\n}\n"
    )

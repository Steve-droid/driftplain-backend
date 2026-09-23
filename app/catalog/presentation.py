"""Reviewed B1 presentation metadata; no runtime support or ranking policy."""

PRESENTATION = {
    "gpqa-diamond": {
        "collection": "reasoning_knowledge",
        "tooltip": "Difficult graduate-level science questions; measures answer accuracy, not code-review ability.",
    },
    "hle": {
        "collection": "reasoning_knowledge",
        "tooltip": "Expert-level questions across many subjects; results depend on whether tools and images are allowed.",
    },
    "mmlu-pro": {
        "collection": "reasoning_knowledge",
        "tooltip": "Challenging multiple-choice knowledge and reasoning questions across subject areas.",
    },
    "aime-2025": {
        "collection": "mathematics",
        "tooltip": "Competition mathematics problems with numerical answers; the exam year is part of the test.",
    },
    "frontiermath-tier4-v2": {
        "collection": "mathematics",
        "tooltip": "Advanced mathematics problems; difficulty tiers are reported separately.",
    },
    "arc-agi-2": {
        "collection": "mathematics",
        "tooltip": "Solving unfamiliar abstract tasks; the task format and scoring differ by benchmark version.",
    },
    "swe-bench-verified": {
        "collection": "coding_terminal",
        "tooltip": "Resolving real repository issues, checked by tests; scores measure a model together with its coding runner.",
    },
    "terminal-bench-4": {
        "collection": "coding_terminal",
        "tooltip": "Completing tasks through a terminal, such as coding and system work, inside test environments.",
    },
    "mrcr-v2": {
        "collection": "long_context_computer_use",
        "tooltip": "Retrieving the requested information from long conversations with similar distractors; context length matters.",
    },
    "osworld-verified": {
        "collection": "long_context_computer_use",
        "tooltip": "Completing computer tasks by interacting with applications; evaluation environment and task subset matter.",
    },
    "deepswe-1-1": {
        "collection": "independent_community",
        "tooltip": "Long software-engineering tasks in repositories, evaluated against their specified requirements.",
    },
    "livecodebench-v5": {
        "collection": "independent_community",
        "tooltip": "Coding-related problems evaluated over dated problem sets; compare the same task and date window.",
    },
    "livebench-2026-06-25": {
        "collection": "independent_community",
        "tooltip": "A collection of reasoning, coding, math and other tasks; choose a category or inspect what its average includes.",
    },
    "codereviewbench": {
        "collection": "ci_specific",
        "tooltip": "Finding known bugs in pull requests; reports how many were found and how many reported findings were correct.",
    },
    "realvuln-3-1-0": {
        "collection": "ci_specific",
        "tooltip": "Finding known vulnerabilities in test applications; strict F3 weights recall nine times as much as precision, so missed vulnerabilities matter more.",
    },
    "testgeneval": {
        "collection": "task_related",
        "tooltip": "Writing or extending Python unit tests; Extra measures adding a test to an existing file. Passing tests do not by themselves prove useful bug coverage.",
    },
    "swt-bench": {
        "collection": "task_related",
        "tooltip": "Generating regression tests that expose an issue and distinguish buggy code from its fix.",
    },
    "logdx-ci": {
        "collection": "task_related",
        "tooltip": "How log-processing methods affect failure diagnosis; distinguish method scores from model scores.",
    },
    "ci-repair-bench": {
        "collection": "task_related",
        "tooltip": "Producing patches for failed CI workflows, validated by rerunning those workflows.",
    },
}

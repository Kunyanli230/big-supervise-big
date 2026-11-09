"""Prompts derived from the source step supervisor; V2 uses its full instruction."""

SYSTEM = "You are a helpful assistant that can interact with a computer shell to solve programming tasks."
INSTANCE = """{{task}}

Work in {{cwd}}. Use exactly one bash tool call per response. Commands run in fresh
subshells; changes to files persist, changes to shell state do not. You may include
a brief visible explanation, but do not provide private hidden reasoning.
Inspect the repository, implement the requirements, and verify with available local
tests. The environment has no network access. Do not commit changes.

When finished, create patch.txt with the git diff of the intended source changes.
Inspect patch.txt in a separate command. Submit with this exact command:
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt
Do not continue after submitting. Hidden verifier files and reference solutions are
not available to you.
"""

# Kept verbatim from study.py@7e8a2e3; selection is before any candidate executes.
INSTRUCTIONS = (
    "You are supervising a ReAct coding agent solving a SWE-bench issue. "
    "Given the issue description and the shared trajectory prefix of completed steps in state, "
    "choose which of the three candidate branches for the current step (each containing Thought + Action, "
    "before action execution) is best to execute next. "
    "Prefer candidates that: (1) have a valid executable bash action, (2) accurately diagnose the root cause "
    "and make concrete progress toward minimal fix, test verification, or final submission without repeating "
    "commands already executed in shared_prefix, and (3) once the fix is verified and git diff is clean, "
    "submit immediately using COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT."
)

FORMAT_ERROR = "{{error}} Return exactly one bash tool call containing a nonempty string command."
OBSERVATION = (
    "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
    "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
)

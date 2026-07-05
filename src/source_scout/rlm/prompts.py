from .schemas import RlmSessionConfig

RLM_SYSTEM_PROMPT = (
    "You are Source Scout's RLM reasoning layer. Use bounded read-only tools to understand "
    "projects, compare candidates, review bundles, and explain eval failures. Do not mutate "
    "user projects, execute cloned repository code, or treat broad catalog retrieval as the "
    "final intelligence layer. Return evidence-linked findings that Codex can inspect."
)


def build_rlm_task_prompt(config: RlmSessionConfig) -> str:
    lines = [
        f"Task: {config.task or '(not set)'}",
        f"Root path: {config.root_path}",
        "Use deterministic tool results as evidence. Prefer exact files and line ranges.",
    ]
    return "\n".join(lines)

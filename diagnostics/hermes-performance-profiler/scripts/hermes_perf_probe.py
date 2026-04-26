#!/usr/bin/env python3
"""
Hermes-Agent Performance Probe

Measures practical Hermes-Agent performance characteristics:
- CLI availability
- startup/help/version command latency
- skill directory size and file count
- end-to-end prompt latency
- basic resource usage
- heuristic bottleneck classification

This script uses only Python standard library modules.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional

try:
    import resource  # Unix only
except Exception:  # pragma: no cover
    resource = None


@dataclass
class CommandResult:
    name: str
    command: list[str]
    ok: bool
    returncode: Optional[int]
    duration_seconds: float
    stdout_bytes: int
    stderr_bytes: int
    stdout_preview: str
    stderr_preview: str
    error: Optional[str] = None
    max_rss_kb: Optional[int] = None


@dataclass
class SkillScan:
    paths_checked: list[str]
    skill_directories: int
    skill_md_files: int
    total_files: int
    total_bytes: int
    largest_files: list[dict]


def split_command(command: str) -> list[str]:
    return shlex.split(command)


def percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * p
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def preview(text: str, limit: int = 600) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def run_command(
    name: str,
    command: list[str],
    timeout: int,
    input_text: Optional[str] = None,
) -> CommandResult:
    start_usage = None
    if resource is not None:
        try:
            start_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        except Exception:
            start_usage = None

    start = time.perf_counter()

    try:
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        duration = time.perf_counter() - start

        max_rss_kb = None
        if resource is not None:
            try:
                end_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                if start_usage is not None:
                    max_rss_kb = max(0, end_usage.ru_maxrss - start_usage.ru_maxrss)
                else:
                    max_rss_kb = end_usage.ru_maxrss
            except Exception:
                max_rss_kb = None

        return CommandResult(
            name=name,
            command=command,
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            duration_seconds=duration,
            stdout_bytes=len(completed.stdout.encode("utf-8", errors="replace")),
            stderr_bytes=len(completed.stderr.encode("utf-8", errors="replace")),
            stdout_preview=preview(completed.stdout),
            stderr_preview=preview(completed.stderr),
            max_rss_kb=max_rss_kb,
        )

    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            name=name,
            command=command,
            ok=False,
            returncode=None,
            duration_seconds=duration,
            stdout_bytes=len(stdout.encode("utf-8", errors="replace")),
            stderr_bytes=len(stderr.encode("utf-8", errors="replace")),
            stdout_preview=preview(stdout),
            stderr_preview=preview(stderr),
            error=f"timeout after {timeout}s",
        )
    except FileNotFoundError as exc:
        duration = time.perf_counter() - start
        return CommandResult(
            name=name,
            command=command,
            ok=False,
            returncode=None,
            duration_seconds=duration,
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_preview="",
            stderr_preview="",
            error=str(exc),
        )
    except Exception as exc:
        duration = time.perf_counter() - start
        return CommandResult(
            name=name,
            command=command,
            ok=False,
            returncode=None,
            duration_seconds=duration,
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_preview="",
            stderr_preview="",
            error=repr(exc),
        )


def common_skill_paths() -> list[Path]:
    home = Path.home()
    paths = [
        home / ".hermes" / "skills",
        Path.cwd() / "skills",
        Path.cwd(),
    ]

    env_paths = os.environ.get("HERMES_SKILLS_PATH") or os.environ.get("HERMES_SKILL_PATH")
    if env_paths:
        for raw in env_paths.split(os.pathsep):
            if raw.strip():
                paths.append(Path(raw).expanduser())

    seen = set()
    unique = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def scan_skills(paths: Iterable[Path]) -> SkillScan:
    paths_checked = []
    skill_directories = 0
    skill_md_files = 0
    total_files = 0
    total_bytes = 0
    largest_files: list[dict] = []

    ignored_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}

    for root in paths:
        paths_checked.append(str(root))
        if not root.exists():
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in ignored_dirs]
            dirpath_obj = Path(dirpath)

            if "SKILL.md" in filenames:
                skill_directories += 1
                skill_md_files += 1

            for filename in filenames:
                path = dirpath_obj / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue

                total_files += 1
                total_bytes += stat.st_size

                largest_files.append(
                    {
                        "path": str(path),
                        "bytes": stat.st_size,
                    }
                )

    largest_files.sort(key=lambda item: item["bytes"], reverse=True)
    largest_files = largest_files[:10]

    return SkillScan(
        paths_checked=paths_checked,
        skill_directories=skill_directories,
        skill_md_files=skill_md_files,
        total_files=total_files,
        total_bytes=total_bytes,
        largest_files=largest_files,
    )


def format_seconds(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}s"


def format_bytes(num: int) -> str:
    value = float(num)
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num} B"


def summarize_durations(results: list[CommandResult]) -> dict:
    durations = [r.duration_seconds for r in results if r.ok]
    failures = [r for r in results if not r.ok]

    if not durations:
        return {
            "count": len(results),
            "successes": 0,
            "failures": len(failures),
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "p95": None,
        }

    return {
        "count": len(results),
        "successes": len(durations),
        "failures": len(failures),
        "min": min(durations),
        "max": max(durations),
        "mean": statistics.mean(durations),
        "median": statistics.median(durations),
        "p95": percentile(durations, 0.95),
    }


def classify_bottleneck(
    help_results: list[CommandResult],
    version_results: list[CommandResult],
    prompt_results: list[CommandResult],
    skill_scan: SkillScan,
) -> tuple[str, list[str], list[str]]:
    evidence: list[str] = []
    actions: list[str] = []

    help_summary = summarize_durations(help_results)
    version_summary = summarize_durations(version_results)
    prompt_summary = summarize_durations(prompt_results)

    help_mean = help_summary["mean"]
    version_mean = version_summary["mean"]
    prompt_mean = prompt_summary["mean"]

    any_prompt_success = prompt_summary["successes"] > 0
    prompt_failures = prompt_summary["failures"]
    total_prompt = prompt_summary["count"]

    startup_mean_candidates = [
        value for value in [help_mean, version_mean] if isinstance(value, float)
    ]
    startup_mean = statistics.mean(startup_mean_candidates) if startup_mean_candidates else None

    if total_prompt > 0 and prompt_failures == total_prompt:
        evidence.append("All prompt benchmark runs failed or timed out.")
        actions.extend(
            [
                "Verify the Hermes command and prompt invocation syntax.",
                "Run with --try-prompt-forms to test common non-interactive CLI forms.",
                "Increase --timeout if the model is expected to be slow.",
                "Run Hermes manually with the same prompt to inspect interactive requirements.",
            ]
        )
        return "command_or_prompt_execution_failure", evidence, actions

    if startup_mean is not None and startup_mean >= 2.0:
        evidence.append(f"Startup/help/version commands are slow: mean {startup_mean:.3f}s.")
        actions.extend(
            [
                "Profile Python imports with `python -X importtime` if Hermes is Python-based.",
                "Avoid slow package-manager wrappers in the benchmark path.",
                "Check whether the home directory or virtual environment is on a slow filesystem.",
            ]
        )
        return "startup_overhead", evidence, actions

    if skill_scan.total_files >= 1000 or skill_scan.total_bytes >= 50 * 1024 * 1024:
        evidence.append(
            f"Skill paths contain {skill_scan.total_files} files and {format_bytes(skill_scan.total_bytes)}."
        )
        actions.extend(
            [
                "Move rarely used skills out of active skill paths.",
                "Remove large generated files, binaries, or cached data from skill directories.",
                "Keep SKILL.md files concise and put only necessary references under references/.",
            ]
        )
        return "skill_filesystem_scan_overhead", evidence, actions

    if any_prompt_success and prompt_mean is not None:
        if startup_mean is not None and prompt_mean >= max(5.0, startup_mean * 5):
            evidence.append(
                f"Prompt latency mean is {prompt_mean:.3f}s while startup mean is {startup_mean:.3f}s."
            )
            actions.extend(
                [
                    "Compare with a faster model or local model.",
                    "Reduce active skill count to lower context and selection overhead.",
                    "Check network latency and provider queueing.",
                    "Run more iterations to distinguish stable latency from variance.",
                ]
            )
            return "model_or_agent_response_latency", evidence, actions

        if prompt_mean >= 10.0:
            evidence.append(f"Prompt benchmark mean is high: {prompt_mean:.3f}s.")
            actions.extend(
                [
                    "Inspect model/provider latency.",
                    "Check whether the prompt triggers unnecessary tools.",
                    "Reduce context size and active skills.",
                ]
            )
            return "high_end_to_end_latency", evidence, actions

    if prompt_failures > 0:
        evidence.append(f"{prompt_failures}/{total_prompt} prompt runs failed.")
        actions.extend(
            [
                "Inspect stderr for failed runs.",
                "Increase timeout or fix intermittent provider/tool failures.",
                "Run the failed command manually.",
            ]
        )
        return "intermittent_failures", evidence, actions

    evidence.append("No severe bottleneck detected from the default probes.")
    actions.extend(
        [
            "Run with more iterations for a more stable distribution.",
            "Benchmark a realistic Hermes task, not only a trivial prompt.",
            "Use cProfile or py-spy for function-level profiling if deeper detail is needed.",
        ]
    )
    return "no_obvious_bottleneck", evidence, actions


def infer_prompt_command(base_command: list[str], prompt: str) -> list[list[str]]:
    """
    Hermes CLI syntax may vary. Try common non-interactive forms.
    """
    return [
        base_command + ["--prompt", prompt],
        base_command + ["-p", prompt],
        base_command + ["run", prompt],
    ]


def markdown_report(data: dict) -> str:
    help_summary = data["summaries"]["help"]
    version_summary = data["summaries"]["version"]
    prompt_summary = data["summaries"]["prompt"]
    skill_scan = data["skill_scan"]
    bottleneck = data["bottleneck"]

    lines = []
    lines.append("# Hermes Performance Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"Likely bottleneck: **{bottleneck['category']}**")
    lines.append("")

    if bottleneck["evidence"]:
        lines.append("### Evidence")
        lines.append("")
        for item in bottleneck["evidence"]:
            lines.append(f"- {item}")
        lines.append("")

    if bottleneck["actions"]:
        lines.append("### Recommended Actions")
        lines.append("")
        for item in bottleneck["actions"]:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## Measurements")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Hermes command | `{{data['hermes_command']}}` |")
    lines.append(f"| Platform | `{{data['platform']}}` |")
    lines.append(f"| Python | `{{data['python']}}` |")
    lines.append(f"| Prompt iterations | {{prompt_summary['count']}} |")
    lines.append(f"| Help mean | {{format_seconds(help_summary['mean'])}} |")
    lines.append(f"| Version mean | {{format_seconds(version_summary['mean'])}} |")
    lines.append(f"| Prompt mean | {{format_seconds(prompt_summary['mean'])}} |")
    lines.append(f"| Prompt median | {{format_seconds(prompt_summary['median'])}} |")
    lines.append(f"| Prompt p95 | {{format_seconds(prompt_summary['p95'])}} |")
    lines.append(f"| Prompt failures | {{prompt_summary['failures']}} |")
    lines.append(f"| Skill directories | {{skill_scan['skill_directories']}} |")
    lines.append(f"| SKILL.md files | {{skill_scan['skill_md_files']}} |")
    lines.append(f"| Skill total files | {{skill_scan['total_files']}} |")
    lines.append(f"| Skill total size | {{format_bytes(skill_scan['total_bytes'])}} |")
    lines.append("")

    lines.append("## Skill Paths Checked")
    lines.append("")
    for path in skill_scan["paths_checked"]:
        lines.append(f"- `{{path}}`")
    lines.append("")

    if skill_scan["largest_files"]:
        lines.append("## Largest Files in Skill Paths")
        lines.append("")
        lines.append("| File | Size |")
        lines.append("|---|---:|")
        for item in skill_scan["largest_files"]:
            lines.append(f"| `{{item['path']}}` | {{format_bytes(item['bytes'])}} |")
        lines.append("")

    lines.append("## Command Results")
    lines.append("")

    for group_name in ["help_results", "version_results", "prompt_results"]:
        lines.append(f"### {{group_name}}")
        lines.append("")
        for result in data[group_name]:
            status = "ok" if result["ok"] else "failed"
            command = " ".join(shlex.quote(part) for part in result["command"])
            lines.append(
                f"- **{{result['name']}}**: {{status}}, "
                f"{{result['duration_seconds']:.3f}}s, "
                f"returncode={{result['returncode']}}, "
                f"stdout={{result['stdout_bytes']}}B, "
                f"stderr={{result['stderr_bytes']}}B"
            )
            lines.append(f"  - command: `{{command}}`")
            if result.get("error"):
                lines.append(f"  - error: `{{result['error']}}`")
            if result.get("stderr_preview"):
                stderr = result["stderr_preview"].replace("\n", "\\n")
                lines.append(f"  - stderr preview: `{{stderr}}`")
        lines.append("")

    lines.append("## Raw JSON")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(data, indent=2, ensure_ascii=False))
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Hermes-Agent performance.")
    parser.add_argument(
        "--hermes-command",
        default="hermes",
        help="Hermes CLI command or script path. Default: hermes",
    )
    parser.add_argument(
        "--prompt",
        default="Say exactly: benchmark-ok",
        help="Prompt for end-to-end latency benchmark.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of prompt benchmark iterations.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout in seconds for each command.",
    )
    parser.add_argument(
        "--try-prompt-forms",
        action="store_true",
        help="Try multiple common Hermes prompt invocation forms.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON only.",
    )

    args = parser.parse_args()

    base_command = split_command(args.hermes_command)

    executable = base_command[0]
    executable_path = shutil.which(executable) if not os.path.exists(executable) else executable

    help_results = [
        run_command("help", base_command + ["--help"], timeout=args.timeout),
    ]

    version_results = [
        run_command("version", base_command + ["--version"], timeout=args.timeout),
    ]

    prompt_results: list[CommandResult] = []
    prompt_forms = infer_prompt_command(base_command, args.prompt)

    if args.try_prompt_forms:
        forms_to_try = prompt_forms
    else:
        forms_to_try = [prompt_forms[0]]

    selected_prompt_command: Optional[list[str]] = None

    for form_index, command in enumerate(forms_to_try):
        probe = run_command(
            name=f"prompt-form-{form_index + 1}-probe",
            command=command,
            timeout=args.timeout,
        )
        prompt_results.append(probe)
        if probe.ok:
            selected_prompt_command = command
            break

    if selected_prompt_command is not None:
        already_successful = 1
        remaining = max(0, args.iterations - already_successful)
        for i in range(remaining):
            prompt_results.append(
                run_command(
                    name=f"prompt-{i + 2}",
                    command=selected_prompt_command,
                    timeout=args.timeout,
                )
            )
    elif not args.try_prompt_forms:
        for i in range(max(0, args.iterations - 1)):
            prompt_results.append(
                run_command(
                    name=f"prompt-{i + 2}",
                    command=forms_to_try[0],
                    timeout=args.timeout,
                )
            )

    skill_scan = scan_skills(common_skill_paths())

    category, evidence, actions = classify_bottleneck(
        help_results=help_results,
        version_results=version_results,
        prompt_results=prompt_results,
        skill_scan=skill_scan,
    )

    data = {
        "hermes_command": args.hermes_command,
        "resolved_executable": executable_path,
        "platform": platform.platform(),
        "python": sys.version.replace("\n", " "),
        "summaries": {
            "help": summarize_durations(help_results),
            "version": summarize_durations(version_results),
            "prompt": summarize_durations(prompt_results),
        },
        "skill_scan": asdict(skill_scan),
        "bottleneck": {
            "category": category,
            "evidence": evidence,
            "actions": actions,
        },
        "help_results": [asdict(result) for result in help_results],
        "version_results": [asdict(result) for result in version_results],
        "prompt_results": [asdict(result) for result in prompt_results],
    }

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(markdown_report(data))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
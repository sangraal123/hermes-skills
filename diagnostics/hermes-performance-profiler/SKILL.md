---
name: hermes-performance-profiler
description: Use when the user wants to benchmark Hermes-Agent performance, measure latency, inspect skill loading overhead, identify runtime bottlenecks, or produce a performance report for Hermes-Agent.
version: 1.0.0
author: sangraal123
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags:
      - diagnostics
      - benchmarking
      - performance
      - profiling
      - bottleneck-analysis
    related_skills: []
    requires_tools:
      - python3
config:
  - key: hermes.command
    description: Hermes-Agent CLI command to benchmark.
    default: hermes
    prompt: Hermes-Agent CLI command, for example `hermes`, `hermes-agent`, or a local script path.
  - key: benchmark.prompt
    description: Prompt used for measuring end-to-end response latency.
    default: "Say exactly: benchmark-ok"
  - key: benchmark.iterations
    description: Number of repeated benchmark runs.
    default: "3"
  - key: benchmark.timeout_seconds
    description: Timeout in seconds for each benchmark command.
    default: "120"
---

# Hermes Performance Profiler

This skill benchmarks Hermes-Agent runtime performance and identifies likely bottlenecks.

Use this skill when the user asks to:

- measure Hermes-Agent performance
- profile Hermes startup time
- check skill loading overhead
- compare prompt response latency
- identify bottlenecks in Hermes-Agent
- produce a performance report
- understand whether latency comes from startup, skill discovery, model response, tools, or local hardware

## What This Skill Measures

The profiler focuses on practical, spec-level performance characteristics of Hermes-Agent:

1. **CLI availability**
   - Whether the Hermes command exists
   - CLI version, if available

2. **Cold command startup latency**
   - Time to execute simple commands such as `--help` or `--version`
   - Useful for identifying Python/package import overhead or environment startup cost

3. **Skill directory scan cost**
   - Counts skills under common Hermes skill directories
   - Estimates filesystem size and number of files
   - Flags large skills, excessive reference files, or heavy assets

4. **End-to-end prompt latency**
   - Runs a small benchmark prompt several times
   - Measures wall-clock duration
   - Reports min, max, mean, median, and p95 where possible

5. **Resource usage**
   - Captures CPU time, peak RSS memory where available
   - Works with standard Python libraries
   - Uses optional `resource` module on Unix-like systems

6. **Bottleneck classification**
   - Startup-heavy
   - Skill-scan-heavy
   - Model/API-latency-heavy
   - Timeout/failure-heavy
   - Memory-heavy
   - Filesystem-heavy

## Quick Start

Ask the agent:

> Hermes-Agentのパフォーマンスを測って、ボトルネックを教えて

The agent should run:

```bash
python3 ~/.hermes/skills/diagnostics/hermes-performance-profiler/scripts/hermes_perf_probe.py \
  --hermes-command hermes \
  --prompt "Say exactly: benchmark-ok" \
  --iterations 3
```

If the Hermes command is different:

```bash
python3 ~/.hermes/skills/diagnostics/hermes-performance-profiler/scripts/hermes_perf_probe.py \
  --hermes-command hermes-agent \
  --iterations 5
```

If Hermes is launched through a local script:

```bash
python3 ~/.hermes/skills/diagnostics/hermes-performance-profiler/scripts/hermes_perf_probe.py \
  --hermes-command "./run-hermes.sh" \
  --iterations 3
```

## Procedure for the Agent

When this skill is invoked:

1. Ask for the Hermes CLI command if it is unclear.
   - Default to `hermes`.
   - Do not assume the command if the user has provided a custom path.

2. Run the benchmark script.

3. Read the generated summary.

4. Explain:
   - the slowest phase
   - whether the bottleneck appears to be startup, skill loading, model latency, tool latency, filesystem size, or memory
   - the most actionable next steps

5. If the benchmark fails:
   - report the exact failing command
   - report stderr
   - suggest how to fix the command or environment

## Interpretation Guide

### Startup bottleneck

Symptoms:

- `--help` or `--version` takes more than 1 second
- Prompt latency is slow even for trivial prompts
- High variance on first run

Likely causes:

- Python import overhead
- slow virtual environment
- cold filesystem cache
- heavy initialization
- package manager shim overhead
- launching through wrappers such as `npx`, `uv`, `poetry`, or shell scripts

Suggested actions:

- run Hermes from a warmed virtual environment
- avoid slow shell wrappers in production
- profile imports with Python import timing
- ensure dependencies are installed locally
- check disk performance

### Skill loading or filesystem bottleneck

Symptoms:

- Many skill directories
- Large `references/`, `assets/`, or generated files
- Slow startup that grows with number of skills
- High file count under `~/.hermes/skills`

Likely causes:

- excessive skills
- large documentation files
- binary assets in skill directories
- generated caches committed into skills
- network-mounted home directory

Suggested actions:

- move rarely used skills out of active skill paths
- reduce large reference files
- avoid large binaries in `assets/`
- keep `SKILL.md` concise
- archive old skills
- avoid network filesystems for active skill directories

### Model/API latency bottleneck

Symptoms:

- CLI startup is fast
- Skill scan is small
- End-to-end prompt latency is still high
- Latency variance is high

Likely causes:

- remote model latency
- provider queueing
- slow network
- large system prompt
- too many active skills increasing prompt/context size

Suggested actions:

- test with a faster model
- reduce active skill count
- reduce verbose skill descriptions
- verify network latency to model provider
- compare local model vs remote model
- run more iterations to distinguish variance from consistent slowness

### Tool execution bottleneck

Symptoms:

- Simple prompt is fast
- Tasks involving shell, browser, web, or GitHub operations are slow
- Bottleneck appears only for certain workflows

Likely causes:

- external CLI slowness
- web/API latency
- repository size
- package manager operations
- slow test suite

Suggested actions:

- benchmark individual tools separately
- cache dependencies
- narrow repository searches
- avoid unnecessary network calls
- add timeout and retry policies

### Memory bottleneck

Symptoms:

- High peak RSS
- System swapping
- Very slow performance under load
- Other applications become sluggish

Likely causes:

- large prompts
- many loaded skills
- large local model
- large file ingestion
- excessive tool output

Suggested actions:

- reduce active skills
- limit file/context ingestion
- summarize large outputs
- use a smaller model
- increase available RAM
- avoid running concurrent heavy agents

## Expected Output

The script prints a Markdown report with sections like:

```markdown
# Hermes Performance Report

## Summary

Likely bottleneck: model_or_agent_response_latency

## Measurements

| Metric | Value |
|---|---:|
| Hermes command | hermes |
| Iterations | 3 |
| Help command mean | 0.42s |
| Prompt mean | 8.31s |
| Prompt p95 | 9.02s |
| Skill directories | 12 |
| Skill files | 84 |
| Skill bytes | 1.7 MB |

## Bottleneck Analysis

The startup command is fast, but prompt execution is slow.
This suggests the bottleneck is likely model/API latency, agent reasoning overhead, or context size rather than CLI startup.
```

## Safety and Limitations

- This skill does not modify Hermes configuration.
- This skill does not send private files anywhere.
- It only runs local CLI commands selected by the user.
- It provides heuristic bottleneck classification, not a full profiler.
- For precise Python-level profiling, run Hermes under `cProfile`, `py-spy`, or another profiler separately.

## Optional Deeper Profiling

If the user wants deeper profiling and Hermes is a Python module or script, suggest:

```bash
python3 -X importtime -m hermes --help 2> importtime.log
```

or:

```bash
python3 -m cProfile -o hermes.prof -m hermes --help
```

Then analyze:

```bash
python3 -m pstats hermes.prof
```

If `py-spy` is available:

```bash
py-spy record -o hermes.svg -- hermes
```

## Response Style

When reporting results to the user:

- Start with the most likely bottleneck.
- Include the top 3 evidence points.
- Include concrete next actions.
- Avoid overclaiming when data is inconclusive.
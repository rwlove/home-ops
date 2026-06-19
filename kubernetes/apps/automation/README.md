# `automation` namespace — cron-driven Claude Code workflows

Phase 4 of the [post-Spark roadmap](../../../docs/src/audit/post-spark-tool-call-leakage-2026-05-18.md):
stateless, periodic, "report on the world" jobs that don't fit the
disciplined-langgraph-specialist path. Cron Claude Code is the right tool
when:

- the task is **read-only / report-shaped** (PR triage, log skim, cost
  trend, vault audit);
- the task **finishes in one Claude session** (no pause for human
  approval, no resume across reactions);
- one-shot Claude Code is cheaper + more capable than a long-running
  specialist for the work.

Tasks that touch cluster state, alert routing, or need multi-step
approval go through the `ai/langgraph-agents` `/inbox` flow instead.

## Status: no active workloads

The `claude-runner` Kustomization that was scaffolded here was retired
in [#12008](https://github.com/rwlove/home-ops/pull/12008) — the
cron-Claude-Code approach was superseded by the `ai/langgraph-agents`
fleet, which now owns the periodic "report on the world" jobs. The
namespace remains for future automation workloads but currently ships
no apps.

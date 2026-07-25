# Agent GitHub Identity Design Proposal

Status: **Proposal — research only, not implemented.** Companion to
[MCP Tool-Level Authorization](mcp_tool_authz_design.md); together these
are the two prerequisites for letting an agent run unattended.
Owner: home-ops
Last updated: 2026-07-25

## Problem statement

Every agent that touches GitHub does so **as `rwlove`**. The in-cluster
opencode server reaches `github_*` tools through the MCP gateway, and
those tools include:

```text
github_create_branch        github_push_files
github_create_or_update_file github_create_pull_request
github_delete_file          github_merge_pull_request
```

`github_merge_pull_request` is the sharp one. Merging to `home-ops` `main`
causes Flux to reconcile into the live cluster, so an agent holding that
tool has an unreviewed path to changing production.

Branch protection was enabled on `main` on 2026-07-25 and closes part of
this — a merge now requires four CI checks to pass. It does **not** close
the review gap, and cannot, for a structural reason:

> **GitHub does not allow an account to approve its own pull request.**

`rwlove` is the sole collaborator. Requiring one approving review would
mean nobody could approve anything, including Rob's own work. So as long
as agents *are* `rwlove`, "agent opens a PR, human approves it" is not
expressible.

The fix is to give agents a **distinct GitHub identity**. That single
change makes agent PRs reviewable, and unlocks a genuine
propose-then-execute loop that matches HOMELAB-SPEC Layer 2 invariant #3.

## Complication: the GitHub MCP is shared

`github-mcp` in `mcp-system` is federated through the MCP gateway and is
consumed by **both** Rob's interactive Claude Code sessions and the
in-cluster opencode server. It authenticates with a single credential
(1Password item `github-mcp`).

So "give opencode a different GitHub identity" cannot be done by swapping
that credential — doing so would change Rob's identity too. Two viable
shapes:

1. **Second MCP server instance.** Deploy `github-agent-mcp` with the
   agent credential and its own tool prefix, and use MCP authz to allow
   opencode only that instance while denying it the human `github_*`
   tools. Requires the [AuthPolicy work](mcp_tool_authz_design.md) — the
   client-side tool gate is not a boundary.
2. **Agents avoid the MCP entirely** and use native `git` with
   credentials that carry the agent identity. Simpler, but only covers
   push; PR creation still needs an API path.

These compose: (2) for git operations now, (1) for API operations once
AuthPolicy lands.

## Identity options

### Option A — GitHub App (recommended)

A GitHub App owned by `rwlove`, installed on the specific repositories.

- Commits and PRs are attributed to `<app-name>[bot]` — a **distinct
  principal**, so Rob can approve its PRs
- Per-repository, per-permission scoping (`contents: write`,
  `pull_requests: write`, and notably **not** `administration`)
- Installation tokens are **short-lived (1 hour)** and minted on demand
  from the App private key — far better than a long-lived PAT
- Can replace the deploy keys entirely: an installation token clones and
  pushes over HTTPS, so the per-repo SSH keys and the
  `ssh.github.com:443` workaround both become unnecessary
- Cost: App must be created in the UI (no API for creation), private key
  stored in 1Password, and something must mint installation tokens. The
  `mcp-gateway-jwt-rotator` CronJob is a working precedent for exactly
  that shape of token-minting job.

### Option B — machine user

A second GitHub account (e.g. `rwlove-agent`) invited as a collaborator.

- Conceptually simple; behaves like any user
- Also a distinct principal, so PRs are approvable
- Cost: another account to own — email, 2FA, recovery, and an audit
  surface that is easy to forget about. Permissions are per-repo
  collaborator roles, coarser than App permissions
- Long-lived credentials unless paired with rotation

### Option C — separate PAT on `rwlove`

**Does not work.** A different token is still the same principal; PRs
remain unapprovable by Rob. Listed only to rule it out.

## Recommendation

**Option A.** The decisive factors are short-lived tokens and the ability
to retire the deploy keys — the current arrangement has two long-lived
private keys sitting in 1Password and re-written into the pod on every
start. An App collapses that to one private key that mints 1-hour tokens.

The 1-hour lifetime does introduce a wrinkle for long agent sessions; see
open question 3.

## Proposed staging

Each stage independently revertible; nothing here requires the AuthPolicy
work except stage 4.

1. **Create the App** — owned by `rwlove`, installed on the six repos the
   agents clone. Permissions: `contents: write`, `pull_requests: write`,
   `metadata: read`. Explicitly not `administration`, not `workflows`.
   Private key into 1Password.
2. **Token minting** — a CronJob or init step that exchanges the App
   private key for an installation token, modelled on
   `mcp-gateway-jwt-rotator`.
3. **Switch git transport** — `stage-repos` clones over HTTPS with the
   installation token instead of deploy keys. Retire the two deploy keys
   and the `ssh.github.com:443` route. Set `GIT_AUTHOR_*` to the bot
   identity so history attributes correctly.
4. **Separate the API path** — deploy `github-agent-mcp` with the App
   credential; use AuthPolicy to permit opencode that instance and deny
   it the shared human `github_*` tools.
5. **Tighten branch protection** — once agents are a distinct principal,
   raise `required_approving_review_count` to 1. Rob can then approve bot
   PRs, and the review gate becomes real rather than nominal.

Stage 5 is the payoff. Everything before it is plumbing.

## Verification

As with the MCP proposal, verification must be **negative**:

- an agent-authored PR **cannot** be merged by the agent once approvals
  are required
- Rob **can** approve and merge that PR
- the agent **cannot** push directly to `main`
- the agent **cannot** reach the human `github_*` tools (post-AuthPolicy)
- Rob's own Claude Code sessions are unaffected throughout

## Open questions

1. **Does `github-mcp` support App credentials**, or only PATs? If only
   PATs, stage 4 needs a machine-user token even if stages 1–3 use an
   App.
2. **Do the agents need `workflows` permission?** Editing anything under
   `.github/workflows/` requires it. Withholding it is a useful
   restriction — agents cannot rewrite CI — but will surface as confusing
   push failures if they try.
3. **Token lifetime vs. session length.** Installation tokens last one
   hour. A long-running server needs refresh-on-401, or a sidecar that
   keeps the credential fresh.
4. **Scope of the App install.** Installing on "all repositories" is
   convenient and wrong; per-repo installation keeps blast radius bounded
   and forces a deliberate decision each time a repo is added.

## What this does not solve

A distinct identity makes agent changes **reviewable**. It does not make
them **safe** — an approved-but-wrong PR still reaches the cluster. It
also does nothing about the `bash` tool or non-GitHub MCP tools; those
are bounded by the Cilium egress policy and (eventually) MCP AuthPolicy
respectively.

## References

- [MCP Tool-Level Authorization Design](mcp_tool_authz_design.md)
- [Orchestration substrate](orchestration_substrate.md) — why the
  guardian queue does not exist yet

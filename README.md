# B2B — Back-to-Back Development

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) · [日本語](README.ja.md)

**Problem:** when an AI coding agent's session ends, or its usage quota or context runs out, its memory is gone, and the next agent has to start over.
**Solution:** B2B keeps that memory in two plain files, `HANDOFF.md` (written by the developer) and `AUDIT.md` (written by the auditor). Two agents, for example Claude Code and Codex, take turns in a developer seat and a read-only auditor seat.
**Why it works:** the auditor has already reviewed every diff, so when the developer runs low it can take over development without re-reading everything, and the former developer comes back as the auditor.

```text
 developer (on air) ──commit──▶ auditor (cue, read-only)
   │  HANDOFF.md                  │  AUDIT.md
   └────── swap seats when quota or context runs low ──────┘
```

"B2B" is a DJ term: two DJs alternate. While one is on air, the other listens on headphones and cues the next track.

## Three modes

| | Baton touch | Relief | Debugging |
| --- | --- | --- | --- |
| What | Planned seat swap | Emergency seat swap | An extended stage of auditing |
| Trigger | User's instruction, or a threshold agreed in advance (e.g. 20% left) | Usage-limit warning, context limit, or an unexpected stop | A high-severity defect whose cause can't be found, or the user's instruction |
| Leaving developer | Stops at a verified checkpoint and writes the full `HANDOFF.md` | Saves only a minimum record at once, or stops without saving | — |
| Before taking over | Incoming developer finishes auditing up to HEAD | Incoming developer confirms the predecessor has stopped and that interrupted operations are safe; otherwise it does not take over | User-approved investigators start from `HANDOFF.md` / `AUDIT.md` and write nothing |
| State / record | `BATON-READY` → `ACCEPTED` | `SWAP-PREPARED (unverified)` → `ACCEPTED` | The developer records each defect in `DEBUG.md`; it ends when the auditor closes the finding |

In every mode there is exactly one writer of code, the audit is read-only, and no permission is widened. The detailed procedures are in [SKILL.md](SKILL.md) (in Japanese).

## Quick start

No installation is needed. Tell your agent:

```text
Read <path to this folder>/SKILL.md and work as the <developer / auditor> of b2b-dev.
```

Ready-made prompts for starting development, auditing, each kind of swap, and debugging are in [templates/start-prompts.md](templates/start-prompts.md).

## How it works

1. After each developer commit, the auditor reviews, read-only, from the *read position* in `AUDIT.md` up to HEAD, including uncommitted changes and untracked new files. The last commit judged "no problems", with every high and medium finding closed, becomes the *passed position*. A finding that waits for the user's decision counts as open.
2. Changes after the passed position are not merged, pushed, or deployed without the user's explicit instruction.
3. When a swap is signalled, the developer saves `HANDOFF.md` (as a baton touch or a relief) and stops writing.
4. The auditor flushes its audit in progress, audits what it has not audited yet, then accepts development and fixes high-severity findings first.
5. The former developer returns as the auditor once its quota recovers. Commits made in the meantime wait as "awaiting audit".

Beyond the normal swap, the procedures also cover one-off delegation of a single fix to the auditor with Owner approval, primary and additional auditors, and execution on the developer's behalf. See [references/coordination.md](references/coordination.md).

## What is automated, and what is not

- **The skill itself is Markdown only.** It does not launch or switch models, monitor usage, or send anything. Seats are swapped by the user pasting a start prompt.
- **The optional [relay](relay/README.md)** (Python 3, no extra packages) automates only these, with the user's permission:
  - invoking a CLI auditor (`call audit`)
  - recording verification commands and external side effects (`verify`, `effect add`)
  - a machine-measured swap record (`swap`, `confirm`, `accept`). `swap` starts from either `BATON-READY` or `SWAP-PREPARED`.
- **Not automated:** confirming that the previous developer has stopped, checking interrupted operations, and organizing a debugging stage. Agents and the user do these by following SKILL.md.
- It never widens permissions for commit, push, production operations, and so on.

## Installation (Claude Code and Codex)

Link both tools directly to this repository so that `SKILL.md` is the single source of the procedures. Do not copy it. The user performs the installation.

| Tool | Per-user link | Invocation |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/b2b-dev` → this repository | `/b2b-dev` |
| Codex | `~/.agents/skills/b2b-dev` → this repository | `$b2b-dev` (also selectable from `/skills` in the CLI and IDE) |

Run this from the repository root (macOS / Linux shell). It stops without overwriting if a folder or link with the same name already exists, including a broken link.

```sh
b2b_repo="$(pwd -P)"
if [ ! -f "$b2b_repo/SKILL.md" ]; then
  echo "Run this from the repository root."
elif [ -e "$HOME/.claude/skills/b2b-dev" ] || [ -L "$HOME/.claude/skills/b2b-dev" ] ||
     [ -e "$HOME/.agents/skills/b2b-dev" ] || [ -L "$HOME/.agents/skills/b2b-dev" ]; then
  echo "An entry with the same name exists. Check its target and contents first."
else
  mkdir -p "$HOME/.claude/skills" "$HOME/.agents/skills" &&
  ln -s "$b2b_repo" "$HOME/.claude/skills/b2b-dev" &&
  ln -s "$b2b_repo" "$HOME/.agents/skills/b2b-dev"
fi
```

If you run a process that copies or replaces skill folders, exclude `b2b-dev` from it first. Keep the repository in a permanent location; if you move or rename it, re-create both links. The [Japanese README](README.ja.md) describes how to check the links (`test -f`, `readlink`, `cmp`, `git status`).

Then open the target project and invoke `/b2b-dev` in Claude Code or `$b2b-dev` in Codex, giving the seat, goal, completion criteria, and branch.

## Example: Claude Code develops, Codex audits

1. Paste "start development" into Claude Code and "start auditing" into Codex.
2. When you decide to swap, paste "prepare a baton touch" into Claude Code. If a usage-limit warning or the context limit forces a hurry, paste "prepare a relief" instead.
3. Paste the matching "take over development" into Codex.
4. When Claude Code's quota recovers, paste "return to the auditor seat".
5. If a serious defect with an unknown cause remains, paste "request a debugging investigation" into an investigator you have authorized.

## Layout

```text
b2b-dev/
├── SKILL.md                  # procedures (read by agents)
├── templates/
│   ├── handoff.md            # template for HANDOFF.md
│   ├── audit.md              # template for AUDIT.md
│   ├── debug.md              # template for DEBUG.md (debugging stage only)
│   └── start-prompts.md      # start prompts for development, audit, swaps, debugging
├── references/
│   ├── coordination.md       # one-off delegation, multiple auditors, execution on behalf, local re-audit
│   └── spec-driven-pilot.md  # optional spec-driven pilot
├── relay/                    # optional CLI audit channel
├── .gitignore                # keeps .b2b/ state out of the repository
├── README.md                 # this file (English)
├── README.ja.md              # Japanese
├── CHANGELOG.md
└── LICENSE
```

SKILL.md, the templates, and the references are written in Japanese. If this README and the Japanese documents disagree, the [Japanese README](README.ja.md) and SKILL.md are authoritative.

## Origin

The skill was rebuilt from an earlier skill that only handled handing over the lead role. The rebuild itself was done B2B-style, with Claude developing and Codex auditing.

## License

[MIT](LICENSE)

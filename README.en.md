# B2B — Back-to-Back Development

**A skill for two coding agents that take turns in the "developer" and "auditor" seats.** (Skill name: `b2b-dev`)

[日本語](README.md) · This is the English version of the Japanese README. If the two disagree, the Japanese README and [SKILL.md](SKILL.md) are authoritative. SKILL.md and the templates are written in Japanese.

Besides the normal seat swap, the skill covers one-off delegation of a single fix to the auditor with Owner approval, primary and additional auditors (for example Argon / Gemini), and execution on behalf of the developer by an auditor. It keeps existing agent sessions, one writer of code, and a separation between "the run succeeded" and "an independent auditor accepted it". See [references/coordination.md](references/coordination.md) and the [start prompts](templates/start-prompts.md). These are procedures; they are not automated by the relay.

"B2B" is a DJ term: two DJs alternate. While one is on air, the other listens on headphones and cues the next track.

```text
 developer (on air) ──commit──▶ auditor (cue)
   │  HANDOFF.md                  │  AUDIT.md
   └──── swap seats when quota runs low ────┘
```

## What it solves

- **Running out of usage quota:** when the developer's quota (for example Claude Code) runs low, the auditor (for example Codex) takes over development. The former developer returns as the auditor once its quota recovers. Quotas from two vendors are used in turn.
- **Lost memory:** memory lives in two files in the working directory, not in a model's context.
  - `HANDOFF.md` (developer): intent, rejected options, last verified state, next step
  - `AUDIT.md` (auditor): how far the audit has read, findings, verdict

  Whichever model sits in whichever seat, at any time, it can resume from the same point.
- **Cost of a swap:** the auditor has already read the diff, so it does not have to read everything again at the swap.

## How it works

1. After each developer commit, the auditor reviews, read-only, from the *read position* in `AUDIT.md` up to HEAD (including uncommitted changes and untracked new files). The last commit judged "no problems", with every high and medium finding closed (a finding waiting for the user's decision counts as open), becomes the *passed position*.
2. Changes after the passed position (awaiting audit) are not merged, pushed, or deployed without the user's explicit instruction.
3. When a swap is signalled, the developer saves `HANDOFF.md` and stops writing. How it saves depends on the type: baton touch or relief (see "Three modes").
4. The auditor flushes its audit in progress, audits what it has not audited yet, then accepts development and, as the developer, fixes high-severity findings first.
5. The former developer returns as the auditor once its quota recovers. Commits made in the meantime wait as "awaiting audit".

## Three modes

The detailed procedures are in [SKILL.md](SKILL.md). In every mode there is one writer of code, the audit is read-only, and no permission is widened.

| Mode | When | Key points |
| --- | --- | --- |
| Baton touch (planned swap) | User's instruction, or a threshold agreed in advance (for example 20% left) | The leaving developer stops at a verified checkpoint, writes the full `HANDOFF.md`, and sets the state to `BATON-READY`. The incoming developer finishes auditing up to HEAD before setting `ACCEPTED`. |
| Relief (emergency swap) | Usage-limit warning, context limit, or an unexpected stop | The leaving developer saves only the minimum record and sets `SWAP-PREPARED (unverified)`. The incoming developer confirms that the predecessor has stopped and that interrupted operations are safe before setting `ACCEPTED`. If it cannot confirm, it does not accept. |
| Debugging (an extended stage of auditing) | A high-severity defect whose cause cannot be identified, or the user's instruction | With the user's permission, one or more investigators are added. Investigators write nothing; they return reproduction steps, hypotheses with evidence, and proposed diffs. The developer records each defect in `DEBUG.md`. It ends when the auditor closes the finding. |

These are Markdown procedures. The relay automates only invoking a CLI auditor, recording verification commands and external side effects (`verify` / `effect add`), and machine-measuring the swap record (`swap` / `confirm` / `accept`). The relay's `swap` can start from either `BATON-READY` or `SWAP-PREPARED`. Confirming that the predecessor stopped, checking interrupted operations, and organizing a debugging stage are not automated.

## What it does not do

- The skill itself does not launch or switch models, monitor usage, or send anything automatically. The optional relay invokes a CLI auditor only with the user's permission. Seats are swapped by the user pasting a start prompt.
- It does not widen permissions for commit, push, production operations, and so on.
- The skill itself is Markdown only. The [relay](relay/README.md) is an optional tool that invokes a CLI auditor the user has authorized.

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
├── README.md
├── README.en.md              # this file
├── CHANGELOG.md
└── LICENSE
```

## Installation

You can use it without installing. Tell the agent:

```text
Read <this folder>/SKILL.md and work as the <developer / auditor> of b2b-dev.
```

### Local installation (Claude Code and Codex)

Link both tools directly to this repository so that `SKILL.md` is the single source of the procedures. Do not copy it or rename products. The user performs the installation.

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

If you run a process that copies or replaces skill folders, exclude `b2b-dev` from it first. Put the repository in a permanent location; if you move or rename it, re-create both links. The Japanese README describes how to check the links (`test -f`, `readlink`, `cmp`, `git status`).

### Invoking

Open the target project and invoke `/b2b-dev` in Claude Code or `$b2b-dev` in Codex, with the seat, goal, completion criteria, and branch. Use [templates/start-prompts.md](templates/start-prompts.md) for the exact wording.

## Example (Claude Code develops, Codex audits)

1. Paste "start development" into Claude Code and "start auditing" into Codex, from the [start prompts](templates/start-prompts.md).
2. When you decide to swap, paste "prepare a baton touch" into Claude Code. If you are in a hurry because of a usage-limit warning or the context limit, paste "prepare a relief".
3. Paste the matching "take over development" into Codex.
4. When Claude Code's quota recovers, paste "return to the auditor seat".
5. If a serious defect with an unknown cause remains, paste "request a debugging investigation" into an investigator you have authorized.

## Origin

This skill is a rebuild of its predecessor, foreman-handoff (a skill that only handled handing over the lead). The rebuild itself was done B2B-style, with Claude developing and Codex auditing.

## License

[MIT License](LICENSE). Anyone may use, copy, modify, and redistribute it free of charge, provided the copyright notice and permission notice are kept.

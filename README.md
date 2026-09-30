# B2B — Back-to-Back Development

**2つのコーディングエージェントが「開発」と「監査」の席を交代しながら進める開発方式のスキルです。**（スキル名：`b2b-dev`）

B2B は、二人の DJ が交互に回す DJ 用語です。片方が音を出しているあいだ、もう片方はヘッドホンで聴いて次に備えます。

```text
 開発役 (on air) ──コミット──▶ 監査役 (cue)
   │  HANDOFF.md                  │  AUDIT.md
   └──── 枠が危なくなったら席を交代 ────┘
```

## 解決すること

- **利用枠が尽きる問題：** 開発役（例：Claude Code）の枠が危なくなったら、監査役（例：Codex）が開発を引き継ぎます。元の開発役は、枠が戻ってから監査の席に着きます。二つの会社の枠を交互に使えます。
- **記憶が消える問題：** 記憶は、モデルの頭の中ではなく、作業フォルダの2つの書類に残します。
  - `HANDOFF.md`（開発役）：意図、捨てた案、最後の検証済み状態、次の一手
  - `AUDIT.md`（監査役）：どこまで監査したか、指摘、判定

  どちらのモデルが、どちらの席に、いつ座っても、同じところから再開できます。
- **交代の費用：** 監査役は差分をすでに読んでいるので、引き継ぎで一から読み直す必要がありません。

## 仕組み

1. 開発役がコミットするたびに、監査役が「読了位置」から HEAD まで（未 commit の変更と、追跡外の新規ファイルを含む）を、読み取りだけで監査します。「問題なし」と判定され、重大な指摘がすべて閉じている（ユーザーの判断待ちは閉じていない扱い）最後のコミットが「合格位置」になります。
2. 合格位置より後の変更（監査待ち）は、ユーザーの明示の指示がない限り、merge・push・deploy しません。
3. 交代の合図（ユーザーの指示、事前に決めた目安、上限の警告、コンテキストの限界）が出たら、開発役は `HANDOFF.md` の冒頭に最小限を保存して、書き込みを止めます。
4. 監査役は、途中の監査を書き出し、まだ監査していない変更を監査します。そのあと開発を受け入れ、開発役として、重大な指摘を最初に直します。
5. 元の開発役は、枠が戻ったら監査役として戻ります。そのあいだに入ったコミットは、監査待ちのまま待ちます。

## しないこと

- モデルの起動や切り替え、使用量の監視、エージェント間の自動送信はしません。席の交代は、ユーザーが開始文を貼って行います。
- commit・push・本番操作などの権限を増やしません。
- 実行スクリプトやネットワーク通信を含みません。中身は Markdown だけです。

## 構成

```text
b2b-dev/
├── SKILL.md                  # 手順（エージェントが読む）
├── templates/
│   ├── handoff.md            # HANDOFF.md の雛形
│   ├── audit.md              # AUDIT.md の雛形
│   └── start-prompts.md      # 開発・監査・交代の開始文
├── README.md
└── LICENSE
```

## 導入

インストールしなくても使えます。エージェントにこう伝えてください。

```text
<このフォルダ>/SKILL.md を読み、b2b-dev の <開発役／監査役> として作業してください。
```

常に使う場合は、フォルダごと各ツールのスキル置き場にコピーします。

| ツール | ユーザー単位の配置例 |
| --- | --- |
| Claude Code | `~/.claude/skills/b2b-dev/` |
| Codex | `~/.agents/skills/b2b-dev/` |

- すでに同じ名前のフォルダがあれば、上書きせずに差分を確かめてください。
- スキル置き場を別のツールと同期するスクリプトなどを使っている場合、同期のときに製品名が書き換えられることがあります（例：「Claude」→「Codex」）。導入後に、`SKILL.md` が原本と同じかを確かめてください。

公式の資料：[Claude Code Skills](https://code.claude.com/docs/en/skills)、[Codex Skills](https://learn.chatgpt.com/docs/build-skills)

## 使い方（例：Claude Code が開発、Codex が監査）

1. Claude Code に「開発を始める」、Codex に「監査を始める」の[開始文](templates/start-prompts.md)を貼ります。
2. Claude Code の枠が危なくなったら、「交代の準備」を貼ります。
3. Codex に「開発を引き継ぐ」を貼ります。
4. Claude Code の枠が戻ったら、「監査席に着く」を貼ります。

## 由来

このスキルは、前身の foreman-handoff（主任の交代だけを扱うスキル）を作り替えたものです。作り替えの作業そのものも、B2B 方式（Claude が開発、Codex が監査）で行いました。

## English summary

B2B (back-to-back, as in two DJs alternating) is a Markdown-only skill for two coding agents that take turns as developer and auditor. The developer is the only writer of code and keeps `HANDOFF.md` current. The auditor reviews read-only, from the last read commit up to HEAD, including uncommitted diffs, and writes only `AUDIT.md`. It records a *read* position and a separate *passed* position. Changes beyond the passed position are not merged, pushed, or deployed without the user's explicit instruction. When the developer's usage or context runs low, it saves a short state record and stops writing. The auditor then flushes its audit, audits any remaining commits, and takes over development. The former developer returns as the auditor once its quota recovers. Memory lives in the two files, not in any model's context. The skill never launches models, never monitors usage, and never widens permissions.

## License

MIT。詳しくは [LICENSE](LICENSE) を参照してください。

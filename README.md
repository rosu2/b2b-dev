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

- スキル本体はモデルの起動・切り替え、使用量の監視、自動送信を行いません。任意の relay は、ユーザーの許可で CLI 監査を呼び出します。席の交代はユーザーが開始文を貼って行います。
- commit・push・本番操作などの権限を増やしません。
- スキル本体は Markdown だけです。[relay](relay/README.md) は、ユーザーが許可した CLI 監査を呼び出す任意の道具です。

## 構成

```text
b2b-dev/
├── SKILL.md                  # 手順（エージェントが読む）
├── templates/
│   ├── handoff.md            # HANDOFF.md の雛形
│   ├── audit.md              # AUDIT.md の雛形
│   └── start-prompts.md      # 開発・監査・交代の開始文
├── relay/                    # 任意の CLI 監査の通路
├── .gitignore                # .b2b/ の状態を公開しない
├── README.md
├── TERMS.md                  # 新規部分に適用する利用条件と、既存MIT部分の区別
└── LICENSE
```

## 導入

インストールしなくても使えます。エージェントにこう伝えてください。

```text
<このフォルダ>/SKILL.md を読み、b2b-dev の <開発役／監査役> として作業してください。
```

### ローカルで導入する（Claude Code・Codex）

`SKILL.md` を唯一の手順本文として、両ツールからこのリポジトリへ直接リンクします。コピーや製品名の置換はしません。導入はユーザーが行います。

| ツール | ユーザー単位のリンク | 呼び出し |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/b2b-dev` → このリポジトリ | `/b2b-dev` |
| Codex | `~/.agents/skills/b2b-dev` → このリポジトリ | `$b2b-dev`（CLI・IDE では `/skills` からも選択可） |

両ツールの公式資料で、上記の配置場所とリンクされたスキルフォルダの読み込みを確認しています。[Claude Code Skills](https://code.claude.com/docs/en/skills)、[OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills)（2026-10-01 確認）。Claude のこの配置はローカルの Claude Code 用です。

**同期処理がある場合：** 導入前に `b2b-dev` を、両方のスキル置き場をコピー・置換する処理の対象から外してください。両リンクは独立して原本を指すようにします。リンクだけでは、未知の同期処理によるリンクの置換や、リンク先の本文の書き換えを防げません。除外できたか不明なら、上の「インストールしなくても使う」方法で原本の `SKILL.md` を直接指定してください。同期処理の特定や設定変更は、このスキルの機能に含みません。

リポジトリは恒久的な場所に置いてから導入してください。移動・改名したら、両リンクを新しい絶対パスへ張り直します。既存リンクの削除・張り直しは、リンク先を確認してユーザーが行ってください。以下の導入例は既存リンクを自動修復しません。

次は macOS・Linux のシェルでユーザーが実行する導入例です。先にこのリポジトリのルートへ移動してください。既存の同名フォルダやリンク（リンク切れを含む）があれば、上書きせずに停止します。

```sh
b2b_repo="$(pwd -P)"
if [ ! -f "$b2b_repo/SKILL.md" ]; then
  echo "このリポジトリのルートで実行してください。"
elif [ -e "$HOME/.claude/skills/b2b-dev" ] || [ -L "$HOME/.claude/skills/b2b-dev" ] ||
     [ -e "$HOME/.agents/skills/b2b-dev" ] || [ -L "$HOME/.agents/skills/b2b-dev" ]; then
  echo "同名の配置があります。リンク先と内容を確認してから導入してください。"
else
  mkdir -p "$HOME/.claude/skills" "$HOME/.agents/skills" &&
  ln -s "$b2b_repo" "$HOME/.claude/skills/b2b-dev" &&
  ln -s "$b2b_repo" "$HOME/.agents/skills/b2b-dev"
fi
```

導入後や同期処理が動いた後は、同じリポジトリのルートで確認します。`test -f` が終了コード0なら本文へ到達でき、リンク切れでは失敗します。`readlink` の出力が両方とも原本の絶対パスかを確認してください。`cmp` はコピーに置き換わった場合の本文の違いを検出します。正常なリンクなら同じファイル同士の比較になるので、原本そのものの改変は検出しません。コピーへの置換自体は `readlink` の失敗で検出します。

```sh
test -f "$HOME/.claude/skills/b2b-dev/SKILL.md"
test -f "$HOME/.agents/skills/b2b-dev/SKILL.md"
readlink "$HOME/.claude/skills/b2b-dev"
readlink "$HOME/.agents/skills/b2b-dev"
cmp SKILL.md "$HOME/.claude/skills/b2b-dev/SKILL.md"
cmp SKILL.md "$HOME/.agents/skills/b2b-dev/SKILL.md"
git --no-optional-locks status --short
```

最後の状態確認は、原本・templates・追跡外ファイルを含むリポジトリ全体の変更を見るためです。意図しない変更があれば、その状態で使わず、原本と同期設定を確認してください。

### 呼び出す

作業対象のプロジェクトを開き、Claude Code では `/b2b-dev`、Codex では `$b2b-dev` に、席・目的・完成条件・branch を添えて呼び出します。具体的な入力文は [templates/start-prompts.md](templates/start-prompts.md) を使ってください。Codex の画面で選択方法が異なる場合は、スキル一覧から選ぶか、原本の `SKILL.md` の絶対パスを指定して読み込ませます。

新しいセッションでスキルが見えることを確認してください。Codex は変更を自動検出しますが、現れなければ再起動します。同じ名前のコピーがほかにもある場合は、どの原本を読んだかを確認します。リンクと本文の一致は、実際の呼び出し成功とは別の確認です。

### Codex 用ファイルの判断

Codex の必須ファイルは、`name`・`description` を持つ `SKILL.md` です。既存の本文がこの条件を満たします。`agents/openai.yaml` は表示・呼び出し方針・ツール依存を設定する任意ファイルで、今回は追加しません。このスキルには専用の表示素材や外部ツール依存がなく、上記の明示呼び出しで使えるためです。Claude 用と Codex 用に本文を分ける必要もありません。[仕様の根拠](https://learn.chatgpt.com/docs/build-skills)

## 使い方（例：Claude Code が開発、Codex が監査）

1. Claude Code に「開発を始める」、Codex に「監査を始める」の[開始文](templates/start-prompts.md)を貼ります。
2. Claude Code の枠が危なくなったら、「交代の準備」を貼ります。
3. Codex に「開発を引き継ぐ」を貼ります。
4. Claude Code の枠が戻ったら、「監査席に着く」を貼ります。

## 由来

このスキルは、前身の foreman-handoff（主任の交代だけを扱うスキル）を作り替えたものです。作り替えの作業そのものも、B2B 方式（Claude が開発、Codex が監査）で行いました。

## English summary

B2B (back-to-back, as in two DJs alternating) is a Markdown-only skill for two coding agents that take turns as developer and auditor. The developer is the only writer of code and keeps `HANDOFF.md` current. The auditor reviews read-only, from the last read commit up to HEAD, including uncommitted diffs, and writes only `AUDIT.md`. It records a *read* position and a separate *passed* position. Changes beyond the passed position are not merged, pushed, or deployed without the user's explicit instruction. When the developer's usage or context runs low, it saves a short state record and stops writing. The auditor then flushes its audit, audits any remaining commits, and takes over development. The former developer returns as the auditor once its quota recovers. Memory lives in the two files, not in any model's context. The skill instructions never launch models, monitor usage, or widen permissions. The optional relay can invoke a CLI auditor with user authorization; it returns an audit through AUDIT.md.

## 利用条件 / License

利用方針は、**個人の学習・趣味などの非商用利用は無料、業務・商用利用は別途の有料契約**です。詳しくは [TERMS.md](TERMS.md) を参照してください。この新条件は、権利者が適用を明示した新規部分に限ります。

**現行の既存MIT部分と、既にMITで配布した版は、引き続き商用利用も無料です。** この方針を記載しても、過去の許諾を取り消したり、現行のスキル全体を有料に変更したりはしません。既存部分の許諾と表示条件は [LICENSE](LICENSE) を参照してください。

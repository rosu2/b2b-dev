# foreman-handoff

**コーディングエージェント同士で「現場監督」を交代するためのスキル。**
Claude Code、Codex などの間で、作業の目的・検証済みの状態・権限を保ったまま、主任の役割を渡します。

使う場面の例：

- Claude の利用枠が尽きかけているが、開発を止めたくない。残りは Codex の枠で続けたい（逆方向も可）。
- コンテキストが限界に近いので、新しいセッションに作業を引き継ぎたい。
- 難しい部分だけを、別の強いモデルに調べさせたい。

## 何をしてくれるか

- **前任**が、正本の進捗記録と現物を照らし合わせた引き継ぎ書（`HANDOFF.md`）を作ります。
- 残りがわずかなときは、**緊急最小版の5点**だけを先に保存します。書き込みは1回で済むので、減りかけている側の枠をほとんど使いません。
- **後任**は、照らし合わせていない項目を最初に確かめ、前任が止まっていることを確認してから作業を続けます。
- 必要なときだけ、決まった書式の依頼文で**難所担当**へ委託できます。

交代でかかる重い作業（読み込み・照合・続行）は、後任の枠で行います。

## しないこと

- モデルやエージェントの起動・切り替え、使用量の監視、エージェント間の自動送信はしません。後任への合図は、ユーザーが開始文を貼って出します。
- commit・push・本番操作などの権限は増やしません。
- 実行スクリプトや、ネットワーク通信を含みません。中身は Markdown だけです。

## 構成

```text
foreman-handoff/
├── SKILL.md                  # 手順（エージェントが読む）
├── templates/
│   ├── handoff.md            # 引き継ぎ書の雛形（緊急最小版つき）
│   └── start-prompts.md      # 前任・後任への開始文の雛形
├── README.md
└── LICENSE
```

## 導入

インストールしなくても使えます。

```text
<このフォルダ>/SKILL.md を読み、その手順で引き継ぎを準備してください。
```

常に使う場合は、フォルダごと各ツールのスキル置き場にコピーします。

| ツール | ユーザー単位の配置例 |
| --- | --- |
| Claude Code | `~/.claude/skills/foreman-handoff/` |
| Codex | `~/.agents/skills/foreman-handoff/` |

- すでに同じ名前のフォルダがあれば、上書きせずに差分を確かめてください。
- 認識のされ方は、ツールのバージョンによって変わります。公式の資料：[Claude Code Skills](https://code.claude.com/docs/en/skills)、[Codex Skills](https://learn.chatgpt.com/docs/build-skills)
- **注意：** スキル置き場を別のツールと同期するスクリプトなどを使っている場合、同期のときにファイル内の製品名が書き換えられることがあります（例：「Claude」→「Codex」）。導入後に、`SKILL.md` が原本と同じかを確かめてください。

## 使い方（Claude Code → Codex の例）

1. **ふだんから：** 長い作業では、検証の区切りごとに `HANDOFF.md` の「最後の検証済み状態」と「次の一手」が更新されます（スキルの「常時更新」）。
2. **枠が尽きかけたら：** Claude Code に、[緊急用の開始文](templates/start-prompts.md#前任へ緊急残りわずか)を貼ります。5点が保存され、Claude Code は書き込みを止めます。
3. **Codex を起動して：** [後任用の開始文](templates/start-prompts.md#後任へ受け入れて続行)を貼ります。Codex が引き継ぎ書と現物を照らし合わせ、`ACCEPTED` にしてから続けます。
4. **枠が回復したら：** 自動では戻りません。戻すときは、[戻し用の開始文](templates/start-prompts.md#元の主任へ戻し)で Claude Code に受け入れ直させます。

## 状態

| 状態 | 意味 |
| --- | --- |
| `PREPARED（緊急・未照合）` | 最小限だけ保存した。後任が最初に照らし合わせる必要がある。 |
| `PREPARED` | 前任が準備を終えた。後任の受け入れはまだ。 |
| `ACCEPTED` | 後任が現在の状態と担当範囲を確認し、作業を引き受けた。 |

## 試運転で確かめたこと

作者の環境で、「残りわずか」を想定して試運転しました。

- 前任が緊急版を1回書き込んだだけで、前任の会話を知らない後任が、引き継ぎ書だけから作業を再開できました。
- 後任が「未照合」の項目を照らし合わせたことで、進捗記録と現物の食い違いが見つかりました。
- 読み取り専用で委託したにもかかわらず、担当者の `git diff` が索引ファイルを更新していました。これを受けて、`--no-optional-locks` を付けることと、受け入れ時に更新時刻を確かめることを手順に加えています。

実際に別の CLI を起動して配送する部分は、このパッケージでは保証しません。初めて使うときは、`PREPARED` と `ACCEPTED` が区別されること、未 commit の変更と権限が保たれることを確かめてください。

## English summary

A Markdown-only skill for handing the "lead agent" role between coding agents (Claude Code ⇄ Codex, or a fresh session). The outgoing lead keeps a `HANDOFF.md` current at each verified checkpoint. When usage or context runs low, it writes a 5-line emergency record and stops writing. The incoming lead first verifies anything marked unverified, confirms the previous lead has stopped, marks the handoff `ACCEPTED`, and continues. The heavy work of reading, checking, and continuing is paid from the incoming agent's quota. The skill never launches models, never monitors usage, and never widens permissions. Copy-paste start prompts are in `templates/start-prompts.md`.

## License

MIT。詳しくは [LICENSE](LICENSE) を参照してください。

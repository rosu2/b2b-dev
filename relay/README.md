# b2b-relay（任意・第1段階）

開発役が CLI の監査役を呼び、HANDOFF.md と AUDIT.md を介して往復する道具です。Python 3 と bash、Git、認証済みの Claude Code が必要です。追加の Python パッケージは不要です。

```sh
relay/b2b call audit --dry-run
relay/b2b call audit
relay/b2b call audit 073a95c..HEAD
relay/b2b status
relay/b2b log
```

範囲の省略時は AUDIT.md の読了位置から現在の HEAD まで。終点は HEAD に限定します。dry-run は実行コマンドと範囲を表示するだけで、監査や状態ファイルの作成は行いません。通常は commit 後、開発の書き込みを止めて呼び出します。

設定は `config.example` の既定値を使います。変える場合は、このリポジトリ内の `.b2b/config.json` に必要な項目だけを書いてください。任意の CLI オプションやコマンド文字列は受け取りません。

- developer：呼び出す開発役の表示名（既定 Codex）
- auditor：第1段階は claude
- model：既定 opus
- timeout_seconds：既定600秒
- max_rounds：同じ解決済みコミット範囲で既定3回

CLI のオプションは起動時にも実機のヘルプに照合します。Claude は `-p`、ツールなし、plan、セッション保存なし、safe-mode で起動します。本文と差分は relay が読み取り、標準入力で渡します。外部へのモデル送信は、ユーザーが CLI 監査を許可した場合だけ行ってください。

監査役はファイルを編集せず、指定した境界行に挟んだ AUDIT.md の全文を返します。relay は見出し・必須節・判定・位置・既存指摘の保持を検証します。合格に開いた指摘がある結果は拒否します。監査前後のファイル内容は Git の追跡外・ignored・Git内部も含めて比較し、差分があればAUDITを反映せず終了30とします。実行状態用の `.b2b/` だけは比較から除外します。監査ツールは無効ですが、CLI 自体の runtime までOSのsandboxで強制する仕組みではありません。

`.b2b/runtime` と `.b2b/tmp` に CLI の状態を置き、自動更新を無効にします。既存の `.credentials.json` がある場合は認証用に runtime 内へコピーし、権限を600にします。認証値・CLIのstderr・入力全文をログには出しません。`.b2b/` は認証情報を含む可能性があるため、追跡・公開しないでください。

Codex は実機で `exec --sandbox read-only --ephemeral` を確認しましたが、それだけで CLI 自身のリポジトリ外への runtime 書き込みを隔離できるとは確認できません。第1段階では `auditor: codex` を指定するとユーザー判断（20）で停止します。Claude の通路を先に実証し、Codex 側の runtime 隔離と swap は次段階です。

終了コード：0 合格、10 修正待ち、20 ユーザー判断、30 エラーまたは違反。同範囲の上限到達か同じ指摘が2回開き直されたら20で停止します。再オープン回数は `.b2b/state.json` に保持します。異なる HEAD の範囲は別の往復として数えます。

呼び出し結果は `.b2b/log.jsonl` に時刻・呼んだ側・呼ばれた側・動作・範囲・判定・終了コード・所要時間・予期しない変更を記録します。起動前の設定・ヘルプ・範囲エラーは監査開始前のエラーで、ログ作成を行いません。

二重起動は `mkdir .b2b/lock` で拒否します。通常終了とtimeoutでは解放します。強制終了で残ったlockは実行中のプロセスがないことをユーザーが確認してから除いてください。push・merge・deploy・導入・swapのコマンドはありません。

検証：`python3 -B -m unittest discover -s relay -p 'test_*.py'`。実際のCLI監査を起動する検証とは別です。

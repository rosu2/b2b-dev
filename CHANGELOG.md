# Changelog

この形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に倣います。版は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [0.1.0] - 未リリース（tag は未作成）

### Changed

- ライセンスを MIT に一本化しました。独自の利用条件を定めた `TERMS.md` を削除し、README の記述を MIT に直しました。`LICENSE` の著作権表示を `b2b-dev contributors` に改めました（本文は変更なし）。
- 席の交代を、二つの型に分けました。
  - **バトンタッチ（計画された交代）：** 降りる側は検証済みの区切りで `HANDOFF.md` を完全版にし、状態を `BATON-READY` にします。上がる側は HEAD までの監査を終えてから `ACCEPTED` にします。
  - **リリーフ（緊急避難の交代）：** 従来の最小記録と `SWAP-PREPARED（未照合）` を、この型の手順として位置づけ直しました。前任の停止を確かめられない場合は受け入れないこと、途中で止まった処理（git の途中状態・`.b2b/lock`・書きかけのファイル・外部への副作用）を読み取りだけで確かめることを明記しました。
  - 二つの違い（合図・`HANDOFF.md` の書き方・引き継ぎ前の監査範囲・状態名）を表にしました。
- 「第三の担当（任意）」節を「デバッグ」節に発展させました。入る条件・編成（調査役は書き込まず、書き手は開発役一人）・記録・終わる条件を定めました。
- `templates/handoff.md`・`audit.md`・`start-prompts.md` に、バトンタッチ／リリーフ／デバッグの状態と開始文を反映しました。
- relay：`swap` が `BATON-READY` の状態からも始められるようにしました（`SWAP-PREPARED` も従来どおり）。試験を1件追加しました。

### Added

- `templates/debug.md`（`DEBUG.md` の雛形）。
- `README.en.md`（英語版 README）。日本語 README の冒頭からリンクしています。
- この `CHANGELOG.md`。

### 取り込んだ未監査の作業

次の作業は、この版の土台として取り込みました。いずれも 0.1.0 の監査対象です。

- 2026-10-18 の文書拡充：単発委任・追加監査・実行代行・局所再監査（`references/coordination.md`）、仕様駆動の試行（`references/spec-driven-pilot.md`）、不具合を直すときの切り分け。
- relay：Codex 監査の失敗時にも Codex 設定の変更を比較する修正。

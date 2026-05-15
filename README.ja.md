# Claude Code × Codex CLI オーケストレーションキット

[English](README.md) | **日本語**

[Claude Code](https://docs.claude.com/en/docs/claude-code) をマネージャ、[Codex CLI](https://developers.openai.com/codex) を実装ワーカーとして連携させるためのスキャフォルディングキットです。Claude が計画・spec 作成・diff レビュー・マージを担当し、Codex CLI が分離された git worktree 上でサンドボックス化されたファイルアクセスのもとで実装を行います。すべての状態がリポジトリ内のファイルに保存されるので、いつでも中断・再開でき、クラッシュからも復旧できます。

単一マシンで長時間の構造化された開発作業を進めるソロ開発者向けに設計されています。

## このキットを使う理由

- **役割の分離**:Claude は計画立案、コード読解、判断が得意。Codex CLI は焦点を絞った単一タスクの実装が得意。本キットはこの2つを重複なく連携させます。
- **Spec 駆動**:すべての実装タスクはコードを書く前に `spec.md` を承認することから始まります。「いい感じにやっといて」で生まれる事故がありません。
- **2段階レビュー**:マージキューに入る前に、2つ目の Codex プロセスが read-only モードで patch をレビューします。静的レビューと semantic レビューの両方の指摘が見られます。
- **状態駆動・セッション非依存**:ledger、progress、audit、lesson のすべてがファイルとして残ります。いつでも停止・再開でき、`rebuild-ledger` でイベントログから全状態を再構築できます。
- **プロジェクトに自己完結**:すべての成果物が `.orchestration/` と `.claude/skills/` 配下に集約されます。グローバル設定不要。プロジェクトの `docs/`、`scripts/`、`templates/` ディレクトリには一切触れません。
- **Test-first オプション**:実装前に失敗するテストを書きたいタスクには `--mode test-first` を使えます。2つの分離された phase でその規律を強制します。
- **学習の蓄積**:承認された罠や注意点が `LEARNED.md` に蓄積され、Codex の prompt に自動注入されます。同じミスが繰り返されません。

## 前提条件

- Python 3.10 以上(`tomllib` のネイティブサポートには 3.11 以上を推奨)
- Git
- Bash 3.2 以上
- [Claude Code](https://docs.claude.com/en/docs/claude-code) のインストールと認証
- [Codex CLI](https://developers.openai.com/codex) のインストールと認証(`codex login`)

## インストール

このキットはスキャフォルディング形式です。`init.sh` がプロジェクトの git 履歴にファイルをコピーします。アップデートは `upgrade.sh` で行い、engine 層のみを更新してカスタマイズした policy ファイルは保護されます。

```bash
# 1. キットをクローン(どこでも構いません。スクリプトが自分自身の場所を解決します)
git clone <kit-url>
cd claude-codex-orchestration

# 2. プロジェクトにインストール(既存の AGENTS.md / CLAUDE.md があればバックアップされます)
./init.sh /path/to/your-project

# 3. プロジェクト用に policy ファイルをカスタマイズ
cd /path/to/your-project
$EDITOR AGENTS.md            # スタック、規約、パスを記入
$EDITOR .codex/config.toml   # 必要なら `model = "..."` のコメントを外す

# 4. プロジェクトのコマンドを orchestration に教える
python3 .orchestration/scripts/orch.py init \
  --install   "pnpm install" \
  --lint      "pnpm lint" \
  --typecheck "pnpm typecheck" \
  --test      "pnpm test" \
  --build     "pnpm build"

# 5. Codex CLI の準備状況を確認
.orchestration/bin/codex-status --suggest

# 6. コミット
git add -A
git commit -m "Add claude-codex-orchestration"
```

実行前にプレビューしたい場合は `./init.sh /path/to/your-project --dry-run` を使います。

**キットの配置場所について**:キットは任意の場所に置けます(`~/repos/`、`~/.local/share/`、`/opt/`、一時的なクローンでも構いません)。`init.sh` と `upgrade.sh` は自分自身のパスを動的に解決し、インストール後の target プロジェクトは元のキットディレクトリを参照しません。`init.sh` 実行後にクローンを削除しても target は動作します。クローンを残しておく理由は、後で `git pull` してから `upgrade.sh` を実行するためだけです。本 README ではクローンを覚えやすい場所に置いておく前提で説明します。

## インストールされる内容

インストール後、プロジェクト内のキット所有パスは次のような構造になります。

```
your-project/
├── .orchestration/              # キット成果物はすべてここに集約
│   ├── scripts/                 # orch.py エンジン + install_orchestration.sh
│   ├── bin/                     # 薄い wrapper: codex-dispatch, codex-review, …
│   ├── schemas/                 # spec / ledger / codex 出力の JSON Schema
│   ├── docs/                    # キットのドキュメント
│   ├── templates/               # spec.md と lesson のテンプレート
│   ├── tasks/                   # タスクごとの作業ディレクトリ(ID 単位)
│   ├── ledger.json              # タスク状態(初回 `init` 時に作成)
│   ├── progress.jsonl           # 全イベントログ
│   ├── audit.jsonl              # セキュリティ関連イベント
│   ├── merge-queue.json         # 直列化されたマージキュー
│   └── LEARNED.md               # 承認済み lesson(Codex prompt に自動注入)
├── .claude/
│   ├── settings.json            # Claude Code の権限設定
│   └── skills/                  # Claude Code 用 orchestration スキル
├── .codex/config.toml           # プロジェクト固有の Codex 設定
├── AGENTS.md                    # ★ ユーザがプロジェクトに合わせて編集
├── CLAUDE.md                    # マネージャポリシー(Claude が読む)
└── .kit-version                 # インストール済みバージョンマーカー
```

プロジェクトの既存の `docs/`、`scripts/`、`templates/`(トップレベル)は変更されません。

## 日常のワークフロー

1タスクの基本ループ:

```bash
# 1. タスクエントリを作成
.orchestration/bin/task-ledger new \
  --title "Add JWT refresh token endpoint" \
  --objective "クライアントが refresh token と新しい access token を交換できるようにする" \
  --paths "src/auth/refresh.ts,tests/auth/refresh.test.ts"
# → T20260514210816-1e38a2 のような task-id が返る

# 2. spec を書く
.orchestration/bin/spec create <task-id> --kind feature
$EDITOR .orchestration/tasks/<task-id>/spec.md
.orchestration/bin/spec validate <task-id>
.orchestration/bin/spec approve <task-id>

# 3. dispatch — Codex が分離された worktree で実装
.orchestration/bin/codex-dispatch <task-id>

# 4. 2つ目の Codex プロセスによる semantic レビュー
.orchestration/bin/codex-review <task-id>

# 5. マージ — 直列化、validation 失敗時はロールバック
.orchestration/bin/merge-arbiter --cleanup
```

実装前に必ず失敗するテストを書かせたい test-first タスクの場合:

```bash
.orchestration/bin/codex-dispatch <task-id> --mode test-first
```

Phase 1 でテストを書き(失敗する必要がある)、Phase 2 でそれをパスさせる実装を行います。Phase 2 は Phase 1 のテストを変更することが禁止されます。

## よく使う操作

```bash
# 状況把握
.orchestration/bin/task-ledger list
.orchestration/bin/stats --format text
.orchestration/bin/stats --format html --output stats.html   # 単一ファイル HTML ダッシュボード

# Codex 環境チェック
.orchestration/bin/codex-status
.orchestration/bin/codex-status --suggest      # 具体的な改善提案つき

# セッション再開
.orchestration/bin/manager-status
python3 .orchestration/scripts/orch.py summarize-session

# 詰まっているタスクの検出
.orchestration/bin/stuck-detector

# 復旧
python3 .orchestration/scripts/orch.py rebuild-ledger --dry-run

# 学習の蓄積
.orchestration/bin/lesson list
.orchestration/bin/lesson add --task <task-id> \
  --context "pnpm workspace" --trap "並列インストール時の peer-dep 衝突" \
  --lesson "lockfile 更新は単一 dispatch で実行する"
```

## キットのアップデート

キットをクローンしてある場所で:

```bash
cd /path/to/claude-codex-orchestration
git pull
./upgrade.sh /path/to/your-project --dry-run    # 変更内容のプレビュー
./upgrade.sh /path/to/your-project              # 適用
```

`upgrade.sh` は engine 層(`.orchestration/{scripts,schemas,bin,docs,templates}` と `.claude/skills/`)のみを置き換えます。`AGENTS.md`、`CLAUDE.md`、`.claude/settings.json`、`.codex/config.toml`、およびすべての runtime データ(`ledger.json`、`progress.jsonl`、`audit.jsonl`、`LEARNED.md`、`tasks/`)は **絶対に変更されません**。policy ファイルの差分は表示されるので、必要に応じて手動で取り込んでください。

アップグレード後はプロジェクト側で engine の差分を確認してください:

```bash
cd /path/to/your-project
git status
git diff -- .orchestration .claude/skills
```

## ドキュメント

プロジェクトへのインストール後、フルドキュメントは `.orchestration/docs/` 配下にあります:

- `ORCHESTRATION_OPERATIONS.md` — 運用リファレンス全体
- `SPEC_WORKFLOW.md` — 良い spec の書き方
- `STATE_MACHINE.md` — タスク状態遷移
- `TEST_FIRST_WORKFLOW.md` — 2-phase dispatch の詳細
- `MODEL_GUIDE.md` — Codex モデル選択戦略
- `PROMPT_STYLE.md` — XML prompt 境界規約
- `FAILURE_MODES.md` — よくある失敗パターンと対処
- `DISASTER_RECOVERY.md` — 完全クラッシュからの復旧
- `DESIGN_DECISIONS.md` — 設計判断の経緯
- `EXTENDING.md` — プロジェクト固有のスキル・拡張ポイントの追加方法
- `PARALLEL_SESSIONS.md` — 複数セッション / 並列 dispatch の同時実行モデル
- `CHANGELOG.md` — バージョン履歴

## 互換性に関する注意

- 本キットは **単一マシン・単一開発者** ワークフロー向けに設計されています。複数開発者やリモート協調のシナリオは対象外です。
- `manager.lock` は **advisory(警告のみ)** です。同じプロジェクト上で 2 つ目の Claude セッションが動いている可能性があれば警告しますが、相互排除を強制はしません。実際の critical section(タスクごとのロック、マージキューのロック)は atomic primitive で守られています。
- Codex CLI のモデル可用性は時期によって変わります。本キットは意図的にモデル名をハードコードしていません。`codex-status` で現在の環境が解決するモデルを確認し、`.codex/config.toml` でプロジェクトごとに設定してください。

## トラブルシューティング

**`codex-status` が `trust_state: unknown` と表示する。**
Codex CLI はプロジェクトの `.codex/config.toml` を読む前に「trust」を要求します。プロジェクトディレクトリで `codex` を一度実行して trust プロンプトを受け入れてから、再確認してください。

**`init.sh` が "orchestration kit appears to be already installed" で中断する。**
代わりに `upgrade.sh` を使ってください。`init.sh` は初回インストール専用です。

**Engine アップグレード後に古いファイルが残っている。**
`upgrade.sh` は、target に存在するが新版のキットには無いファイルを削除しません。これは `.claude/skills/` 配下にユーザがカスタムスキルを追加している可能性を保護するためです。target プロジェクトで `git status` を見て、古いキットファイルがあれば手動で削除してください。

**Codex のコミットが "produce no changes" と表示される。**
`.orchestration/tasks/<task-id>/exit.json` の `pre_head` と実際の diff を確認してください。dispatch は事前 HEAD を記録するので、Codex のコミットは可視化されているはずです。それでも空なら `codex.stdout.jsonl` で tool 呼び出しを確認します。

その他の失敗シナリオは、インストール後に `.orchestration/docs/FAILURE_MODES.md` と `.orchestration/docs/DISASTER_RECOVERY.md` を参照してください。

## ライセンス

[キット作者により指定予定]

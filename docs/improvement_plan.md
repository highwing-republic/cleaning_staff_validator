# 改善計画（設計・アルゴリズムレビュー 2026-10-10 に基づく）

Codex への実装ハンドオフ用。PR 単位に分割し、優先順位順に並べている。

改訂履歴:

- 2026-10-10 初版
- 2026-10-10 改訂 1: 計画レビューの指摘を反映。
  PR-C の残り需要方式を保留へ、PR-B に確定日の扱いを追加、古いプレビューの反映防止（PR-B2）を追加、
  スタッフ無効化を独立 PR（PR-G）へ、PR-A に期間末尾以降の参照を追加、PR-D のテスト準備と
  矛盾時の Solver 打ち切りを修正、F-1 の完了条件を明確化。
- 2026-10-10 改訂 2: 2 回目の計画レビューを反映。
  PR-B2 の指紋に勤務表の状態を含め、反映対象をロック取得後の勤務表で決める。
  PR-G で保持した無効スタッフの勤務を Solver に既存配置として渡し、勤務表調整で無効スタッフを選べるようにする。
  PR-C に分母 0 の扱いを追加。保留事項の表現を「運用上の検討事項」に改める。

共通の制約:

- Solver 本体 `src/shift_generation.py` は DB・Streamlit に依存させない（現状の層構造を維持する）。
- 不足はソフト制約のまま。固定（locked / 確定日）はハード制約のままで、自動で解除しない。
- 「目標勤務日数と公平性のために最低必要人数を上回る配置を許容する」という現行方針は変えない。
- 既存テストの期待値を変える場合は、この文書で明示した箇所に限る。
- 画面文言は若女将向けの平易な日本語。内部用語（scaled / pass など）を画面に出さない。
- `pytest -q` が全件通ること。Windows 環境で OR-Tools の DLL が読めない場合は Linux（devcontainer）で実行する。

---

## PR-A: 期間の前後にある保存済み勤務を連勤判定に使う（High）

### Goal

最大連続勤務日数の判定が期間境界で無視される問題を直す。現状は

- 初回生成（シフト生成画面）が期間前の勤務実績を Solver に渡していない、
- 再生成でも DRAFT の前期間を「全員休み」とみなしている、
- 期間末尾より後に保存済みの勤務があっても参照していない（ローリング運用で前にずらして再生成した場合など）。

### Relevant Files

- `src/services.py` — `generate_schedule`, `build_generation_request`, `build_regeneration_request`, `build_prior_work_history`
- `src/repositories.py` — `list_finalized_working_dates`
- `src/shift_generation.py` — `_add_max_consecutive_constraints`, `_prior_dates`
- `src/models.py` — `GenerationRequest`
- `pages/06_generate.py` — 生成ボタンの呼び出し（146 行付近）
- `tests/test_schedule_services.py` — M17 / M18 / `test_prior_history_*`
- `tests/test_shift_generation.py` — S20 周辺

### Changes

1. `repo.list_finalized_working_dates` を `list_scheduled_working_dates(conn, start, end)` に改名し、
   DRAFT・FINALIZED の両方の `is_working = 1` を返す。
   docstring の理由を「下書きでも現時点の最良の見積りであり、除外すると『前期間は全員休み』という
   より誤った前提になる」に書き換える。
2. `GenerationRequest` に `following_work_history: dict[int, set[str]]`（既定 `{}`）を追加する。
   意味は `prior_work_history` と同じで、期間最終日より後の勤務日。
3. `_add_max_consecutive_constraints`: タイムラインを
   `prior (limit 日) + work_dates + following (limit 日)` にし、`following` の日は `prior` と同様に定数として数える。
   `_prior_dates` と対になる `_following_dates(last_work_date, count)` を追加する。
4. `services.build_following_work_history(conn, work_dates)` を追加する（`build_prior_work_history` と対称。
   遡る日数の決め方も同じ）。
5. `services.generate_schedule(conn, work_dates, prior_work_history=None, following_work_history=None, ...)`:
   `None` のときだけ 4 と `build_prior_work_history` で自動取得する。明示的に渡された値は優先する。
6. `build_regeneration_request` も `following_work_history` を渡す。
7. `pages/06_generate.py` は呼び出し変更なし（5 の既定値で入る）。生成前サマリーに
   「期間の前後にある勤務表を連勤の判定に使っています」のキャプションを 1 行足す。

### Tests

- `test_m18_draft_prior_days_are_not_used_as_history` → 期待値を反転し、
  名前を `test_m18_draft_prior_days_are_used_as_history` にする。
- 追加（Solver 単体）: 期間後 2 日が勤務済み、`max_consecutive_days=2` → 期間最終日が休みになる
  （S20 の末尾版）。期間後に休みが挟まれば制限されないことも確認する。
- 追加（service）: 前期間 2 日が FINALIZED、`max_consecutive_days=2` のスタッフで
  `services.generate_schedule(conn, target)` を履歴を渡さずに呼び、期間初日が休みになること。
- 追加（service）: 前期間が DRAFT でも同じ結果になること。
- 追加（service）: 後期間 2 日が保存済みのとき、`build_regeneration_request` の `following_work_history` に入ること。
- 既存 `test_m17_*`, `test_prior_history_is_used_by_the_regeneration_request` は変更なしで通ること。

### Acceptance Criteria

- 初回生成・再生成のどちらも、期間の前後にある保存済み勤務（下書き・確定）を含めて連勤上限を守る。
- `list_finalized_working_dates` の呼び出しが残っていない。

---

## PR-B: 再生成で「行のないスタッフ」を正しく扱う（High）

### Goal

勤務表保存後に追加した有効スタッフについて、

- DRAFT 日: Solver が配置するのに反映で捨てられ、プレビューの「不足の合計」と保存結果が食い違う。
- 確定日: `fixed_assignments` は既存行だけを固定するため、Solver は新規スタッフを確定日に配置できてしまう。
  反映では確定日を触らないので、ここでも計算と保存結果が食い違う。

検証メッセージ「再生成すると揃います」も成立していない。

### Relevant Files

- `src/services.py` — `build_regeneration_request`, `preview_regenerated_schedule`, `apply_regenerated_schedule`
- `src/models.py` — `ScheduleChange`
- `src/generation_display.py` — `format_schedule_change`
- `src/schedule_validation_adapter.py` — `SCHEDULE_INCOMPLETE` のメッセージ
- `tests/test_schedule_services.py`, `tests/test_schedule_validation_adapter.py`

### Changes

1. `build_regeneration_request`: 確定日について、`request.staff` にいるが行のないスタッフを
   `fixed[(staff_id, work_date)] = 0` として固定する（確定日の内容を変えない原則を Solver 側でも守る）。
2. `ScheduleChange` に `before_exists: bool = True` を追加する。
3. `preview_regenerated_schedule`: 編集可能な日（DRAFT）で `existing is None` のスタッフは
   `before_exists=False, before_is_working=False` の変更として `changes` に含める
   （出勤・休みどちらでも含める。行が新しくできること自体が変更）。
4. `apply_regenerated_schedule`: 同条件のスタッフについて `ScheduleAssignmentRecord` を作って保存する
   （`locked=False`, `source=GENERATED`）。確定日は従来どおり触らない。
5. `format_schedule_change`: `before_exists=False` のときは `"（新規） → 09:00-15:30"` / `"（新規） → 休"`。
6. `SCHEDULE_INCOMPLETE` のメッセージを「下書きの日は『固定を守って再生成』で揃います。確定日は確定を解除してから再生成してください。」にする。

### Constraints

- 無効化されたスタッフ（`request.staff` にいない）の既存行は今までどおり触らない。
- 確定日に行は追加しない。

### Tests

- 追加: 下書き保存後にスタッフを追加 → preview の `changes` に新規行が `before_exists=False` で含まれ、
  apply 後に `repo.get_schedule_assignment` が行を返す。
- 追加（確定日）: 必要 2 名の日を 1 名勤務のまま確定し、その後スタッフを追加 → preview の
  `result` でその日の `staff_shortage == 1`、apply 後の `validate_current_schedule` でも不足 1。
  新規行は作られない。
- 追加: 期間全体で、preview の `result.total_shortage` と apply 後の `validate_current_schedule(...).total_shortage`
  が一致すること（確定日・DRAFT 日の両方を含む期間で）。
- 追加: `format_schedule_change` の新規表示。
- 既存 `test_m10_*`, `test_m11_*`, `test_m14_*`, `test_regeneration_request_fixes_every_cell_of_a_finalized_day`
  が通ること（後者は固定件数が「行のあるスタッフ数」から「有効スタッフ数」に変わり得るため、期待値を
  `len(request.staff)` 基準に直す）。

### Acceptance Criteria

- 確定日・DRAFT 日のどちらでも、プレビューの不足数と反映後の勤務表検証の不足数が一致する。
- 追加したスタッフが DRAFT 日の再生成後に勤務表に現れる。

---

## PR-B2: 古いプレビューの反映を防ぐ（High）

### Goal

プレビュー後に勤務希望・必要人数・スタッフ条件・固定状態が変わっても、反映ボタンは古い Solver 結果を書き込む。
画面は手修正・確定・確定解除でプレビューを捨てているが、他画面（勤務希望・必要人数・スタッフ管理）での変更は検知できない。

### Relevant Files

- `src/services.py` — `preview_regenerated_schedule`, `apply_regenerated_schedule`
- `src/repositories.py` — `apply_regenerated_schedule`
- `src/models.py` — `RegenerationPreview`
- `src/validation.py` — エラーコード追加
- `pages/07_schedule.py`
- `tests/test_schedule_services.py`

### Changes

1. `services.regeneration_fingerprint(request: GenerationRequest, schedule: CurrentScheduleView) -> str` を追加する。
   次を正規化した JSON（キー昇順）の SHA-256。
   - 生成入力: `work_dates`、各スタッフの `(staff_id, role_id, skill_level, max_consecutive_days, target_days_per_week)` と
     各日の `(can_work, time_status, effective_start/end, prefer_off)`、各 `GenerationDay`、`fixed_assignments`、
     `prior/following_work_history`
   - 勤務表の状態: 日付ごとの「未作成 / 下書き / 確定」と、勤務行ごとの
     `(staff_id, is_working, start_time, end_time, locked)`
     （生成入力だけでは、未固定の下書き行が増減・変更されても `fixed_assignments` が変わらず検知できないため）
2. `RegenerationPreview` に `input_fingerprint: str | None = None` を追加し、preview 時に 1 の値を入れる。
3. エラーコード `SCHEDULE_PREVIEW_STALE` を追加する
   （「プレビュー後に勤務希望・必要人数・スタッフ条件・固定のいずれかが変わりました。もう一度『固定を守って再生成』を押してください。」）。
4. `services.apply_regenerated_schedule`:
   `repo.apply_regenerated_schedule(..., prepare=prepare)` を呼ぶ。`prepare` はロック取得後に呼ばれる関数で、
   (a) `get_current_schedule` と `build_regeneration_request` を作り直して 1 を計算し `preview.input_fingerprint` と
   比較する、(b) 一致したときだけ、その時点の勤務表を基準に反映対象の record（DRAFT 日の未固定セル、PR-B の新規行、
   PR-E の未作成日）を組み立てて返す。不一致なら `None` を返す。
   反映対象の決定をトランザクション外で先に行わない（プレビュー時点の勤務表で決めると、ロック前の変更を取りこぼす）。
5. `repo.apply_regenerated_schedule(conn, work_dates, prepare: Callable[[], ApplyPlan | None], solver_status, ...)`:
   `conn.execute("BEGIN IMMEDIATE")` で書き込みロックを取ってから `prepare()` を呼び、`None` なら
   `ROLLBACK` して `StalePreviewError`（新設、`repositories` 内の例外）を送出する。それ以外は返された
   `ApplyPlan(assignments, new_dates)` を同じトランザクションで書く（`with conn:` は BEGIN を遅延させるため、明示的に BEGIN する）。
   service は `StalePreviewError` を捕まえて `SCHEDULE_PREVIEW_STALE` の `ValidationError` に変換する。
   `ApplyPlan` は `repositories` 内の小さな dataclass でよい（`new_dates` は PR-E で使う。PR-B2 時点では空）。
6. `pages/07_schedule.py`: `SCHEDULE_PREVIEW_STALE` を受け取ったらプレビューを `session_state` から捨てる。

### Constraints

- 1 人運用でも守る（別画面で変えた後に戻ってきて反映するケースが対象）。
- fingerprint に時刻や `updated_at` を含めない（同じ内容なら同じ値になること）。

### Tests

- 追加: preview 後に `repo.save_staff_period_preferences` で絶対休みを追加 → apply が
  `SCHEDULE_PREVIEW_STALE` を返し、DB は変わらず `schedule_runs` も増えない。
- 追加: preview 後に必要人数を変更 / スタッフの `max_consecutive_days` を変更 / 別スタッフを固定 → 同上。
- 追加: preview 後に未固定の下書きセルを手修正（固定なし）→ `SCHEDULE_PREVIEW_STALE`
  （生成入力は変わらないが勤務表が変わるケース）。
- 追加: preview 後に別の日を確定 / 確定解除 → 同上。
- 追加: 何も変えなければ apply が成功する（fingerprint が安定している。`updated_at` を含めないこと）。
- 追加: `prepare` が `None` を返したとき、`schedule_runs`・`schedule_assignments` のどちらにも書き込みが残らない。

### Acceptance Criteria

- 生成入力または勤務表のどちらが変わっても、古いプレビューは反映されない。
- 変更検知・反映対象の決定・書き込みが同じトランザクション内で行われる。

---

## PR-C: 目安を勤務可能日数で補正する（Medium）

### Goal

`_effective_target_scales` が勤務可能日数を超える目安を付けると、その人の乖離が常に最大になり
Pass 5（最大乖離の最小化）が他のスタッフに効かなくなる。
また候補日が 0 日のスタッフが自動目安の分母に入っており、他のスタッフの目安を薄めている。

「自動目安を『明示目標分を差し引いた残り需要』から計算する」方式（初版）は、
目標設定者だけで需要を満たす場合に未設定者が 0 日になり、
「必要人数を超えてもよいが勤務を偏らせすぎない」という現行方針と合わないため **保留**（末尾「保留事項」参照）。

### Relevant Files

- `src/shift_generation.py` — `_effective_target_scales`, `generate_shift`, `_build_staff_summaries`
- `src/models.py` — `StaffGenerationSummary`
- `src/generation_display.py` — `format_target_days`
- `pages/06_generate.py` — 「目安」のキャプション
- `tests/test_shift_fairness.py`

### Changes

1. `_effective_target_scales(staff_list, days, work_dates, candidates)` に候補日集合を渡す
   （`generate_shift` 内で `_collect_candidates` の結果を渡す）。戻り値に
   `requested_targets: dict[int, int]`（上限適用前の値）を加える。
2. 計算手順（すべて 7 倍スケール）。現行の「全体需要 ÷ 人数」の考え方は維持する。
   - `period_days = len(work_dates)`、`demand = 7 * Σ required_total_staff（要件設定日のみ）`
   - 各スタッフの `cap = 7 * len(candidates[staff_id])`
   - 明示目標あり: `requested = target_days_per_week * period_days`
   - 目標なしのチェッカー: `requested = 5 * period_days`
   - 自動群 = 目標なし・チェッカー以外のスタッフ。分母 = `cap > 0` のスタッフ数（全ロール・目標の有無を問わない。現行の分母から候補日 0 の人を除いただけ）。
     `requested = round(demand / 分母)`。`requested <= 0` なら目安なし（現行どおり `continue`）
   - 分母が 0（全員が絶対休み・通常勤務時間未設定・曜日なし等で候補日 0）のときは自動目安を作らない。
     明示目標・チェッカー既定も `cap = 0` で `effective = 0` になるため、結果は従来どおり「配置 0・必要人数分の不足」。
     ゼロ除算を起こさないこと。
   - 全員: `effective = min(cap, requested)`
3. `StaffGenerationSummary` に `requested_target_scaled: int | None` を追加する。
   `target_scaled` は上限適用後の値（Solver の乖離計算と一致）、`requested_target_scaled` は本人の希望・自動計算の元の値。
   `deviation_scaled` は従来どおり `target_scaled` に対する乖離。
   `models.py` の docstring に「勤務可能日数を上限にする」「元の値は requested_target_scaled に残す」と書く。
4. `format_target_days`: 上限が効いているとき（`requested_target_scaled > target_scaled`）は
   `"3.0日（希望 5.0日・勤務できる日数で上限）"` のように元の値も出す。自動の場合は `"（自動）"` を従来どおり付ける。
5. `pages/06_generate.py` のキャプションに「目安は勤務できる日数を超えません。超える希望はかっこ内に示します。」を足す。

### Constraints

- 辞書式の 6 段階と各段階の順序は変えない。
- `target_days_per_week` の意味（週あたり希望日数）は変えない。
- 現行方針（最低必要人数を上回る配置を許容）を変えない。

### Tests

- `test_f08_availability_outranks_the_target`: `target_scaled == 21`、`requested_target_scaled == 25`、
  `deviation_scaled == 0` を追加で確認する（`scheduled_days == 3` は変わらない）。
- 追加: 候補日 0 のスタッフ（通常勤務曜日なし）が分母に入らないこと。
  例: 3 名・必要 1 名 × 5 日で 1 名が曜日なし → 残り 2 名の `requested_target_scaled == int(round(7 * 5 / 2))`。
- 追加: 候補日 2 日のスタッフと候補日 5 日のスタッフが混在し、前者の目安が `14` で頭打ちになり、
  後者の配置が前者に引きずられないこと（Pass 5 が機能すること）。
- 追加: 全員が候補日 0（例: 全員通常勤務時間未設定、または全日絶対休み）で必要 2 名 × 5 日 →
  例外なく結果が返り、全日 `scheduled_staff_count == 0`、`staff_shortage == 2`、全員 `has_target is False`。
- 追加: `format_target_days` の上限表示。
- 既存 F04, F05, F06, F10, F11, `test_mixed_staff_with_and_without_targets`,
  `test_dynamic_target_values_all_work`, `test_checker_*` は **変更なし** で通ること。

### Acceptance Criteria

- どのスタッフの `target_scaled` も `7 × 候補日数` を超えない。
- 画面で本人の希望（または自動計算の元の値）が隠れない。
- 既存テストの期待値変更がない。

---

## PR-D: 固定と「最大人数」「連勤上限」の矛盾を事前検出する（Medium）

### Goal

固定（locked / 確定日）と `max_total_staff`・`max_consecutive_days` が矛盾すると、
再生成が原因不明の INFEASIBLE になる。手修正でも連勤上限を超える出勤固定が保存できてしまう。
また現状は矛盾を検出した後も Solver を呼んでいる。

### Relevant Files

- `src/services.py` — `_detect_fixed_conflicts`, `preview_regenerated_schedule`, `validate_manual_schedule_changes`
- `src/models.py` — `FixedAssignmentConflict`, `RegenerationPreview`
- `src/validation.py` — エラーコード追加
- `pages/07_schedule.py` — conflict の表示（242 行付近）
- `tests/test_schedule_services.py`

### Changes

1. `FixedAssignmentConflict.staff_id` と `staff_name` を `| None` にし、日単位の矛盾を表せるようにする。
2. `_detect_fixed_conflicts(request)` に次を追加する。
   - 日別: 要件設定日で `max_total_staff is not None` かつ 出勤固定の人数 > 上限 →
     `staff_id=None` の conflict（理由: 「出勤で固定された人数（N 名）が最大人数（M 名）を超えています。」）
   - スタッフ別: `max_consecutive_days` があるスタッフについて、
     `prior_work_history`・出勤固定の日付・`following_work_history` を日付順に並べ、
     連続する勤務日の最長長さが上限を超えたら conflict
     （理由: 「出勤固定だけで最大連続勤務日数（N 日）を超えています（MM/DD〜MM/DD）。」）。
     期間内の未固定日は数えない（Solver が休みにできるため）。
3. `preview_regenerated_schedule`: conflict が 1 件でもあれば `generate_shift` を呼ばず、
   `result=None`、`errors=[SCHEDULE_FIXED_CONFLICT]`（新コード。「固定された勤務が現在の条件と矛盾しています。下の一覧を確認してください。」）で返す。
   `can_apply` は従来どおり `False` になる。
4. `validate_manual_schedule_changes`: `is_working=True` の変更について、
   対象スタッフの `[最初の変更日 - limit, 最後の変更日 + limit]` の保存済み勤務行（期間外も含む。PR-A の
   `list_scheduled_working_dates` を使う）に変更を重ね、連続勤務が上限を超える場合は新エラー
   `SCHEDULE_MAX_CONSECUTIVE_EXCEEDED`（「この変更で最大連続勤務日数（N 日）を超えます。」）を返す。
5. `pages/07_schedule.py`: `staff_id=None` の conflict は日付だけで表示する。
   `result is None` かつ conflict ありのときは、現在の「再生成できませんでした」の分岐に入らず conflict 一覧だけを出す。

### Constraints

- 検出のみ。固定を自動で解除しない。
- `max_total_staff` はハード制約のまま。

### Tests

テスト用の矛盾データは service の検証を通さず `repo.save_staff_manual_changes` で直接作るか、
保存後に `repo.update_staff` で `max_consecutive_days` を下げて作る（4 の実装後は手修正を 1 日ずつ
保存しても最後の保存で拒否されるため）。

- 追加: `max_total_staff=1` の日に 2 名を出勤固定 → conflict が 1 件、`staff_id is None`、
  `preview.result is None`、`errors` に `SCHEDULE_FIXED_CONFLICT`。
- 追加: 3 日連続の出勤固定を repo で作り、`max_consecutive_days=2` に変更 → conflict。
- 追加: 前期間 2 日が保存済み勤務 + 期間初日を出勤固定 → conflict。期間末尾の 2 日が保存済み勤務 + 最終日を出勤固定 → conflict。
- 追加: 手修正で 3 日連続の出勤に変更 → `SCHEDULE_MAX_CONSECUTIVE_EXCEEDED`、DB は変わらない。
  期間外の前日 2 日が勤務済みのとき、期間初日を出勤にする変更も拒否される。
- 既存 `test_m19_*` は `preview.result is None` になる点だけ期待値を直す。

### Acceptance Criteria

- 固定だけで成立しない組み合わせは、Solver を回す前に conflict として日付・理由付きで表示される。
- 手修正で連勤上限を超える出勤は保存できない。

---

## PR-E: 再生成が未作成日も作る（ローリング運用のつなぎ目）（Medium）

### Goal

既存日を含む期間は初回保存できず、再生成は未作成日を作らないため、
重なる期間の「続き」を既存の固定・確定を文脈にして作る手段がない。

### Relevant Files

- `src/services.py` — `preview_regenerated_schedule`, `apply_regenerated_schedule`
- `src/repositories.py` — `apply_regenerated_schedule`
- `src/models.py` — `RegenerationPreview`
- `pages/06_generate.py`（保存セクションの案内文）, `pages/07_schedule.py`（未作成日の警告文）
- `tests/test_schedule_services.py`

### Changes

1. `RegenerationPreview` に `created_dates: list[str]` を追加する。
2. `preview_regenerated_schedule`: `target_dates = draft_dates + missing_dates`。
   `existing_dates` が空の場合の `SCHEDULE_DAY_NOT_FOUND` は残す（最初の作成はシフト生成画面から）。
   「すべて確定済み」の判定は `draft_dates` も `missing_dates` も空のときだけにする。
   未作成日の全スタッフ分を `changes` に `before_exists=False`（PR-B）で含める。
3. `apply_regenerated_schedule`（service）: PR-B2 の `prepare` の中で、ロック取得後の勤務表を基準に
   未作成日を判定し、その全スタッフ分の record と `new_dates` を `ApplyPlan` に入れる。
   PR-B2 の指紋は日付ごとの「未作成 / 下書き / 確定」を含むため、プレビュー後に未作成日が作られれば不一致になる。
4. `repo.apply_regenerated_schedule`: `ApplyPlan.new_dates` について、同じトランザクション内で
   `schedule_days` の DRAFT 行を INSERT（`latest_run_id=run_id`）してから assignments を書く（FK の順序）。
   既存の DRAFT 日の `latest_run_id` 更新は現状どおり。
5. 画面文言:
   - `pages/06_generate.py` 保存セクション: 「すでに勤務表がある日を含む場合は、『勤務表調整』の
     『固定を守って再生成』で続きの日も作成できます。」
   - `pages/07_schedule.py` 未作成日の警告: 「『固定を守って再生成』で作成されます。」

### Constraints

- 確定日は触らない。固定セルは触らない。
- `schedule_runs` は 1 回の反映につき 1 行（現状どおり）。

### Tests

- 追加: 10/20〜11/02 を保存・一部固定・一部確定 → 10/28〜11/10 で preview →
  11/03〜11/10 が `created_dates`、固定・確定は不変、apply 後に 11/03〜11/10 の行と DRAFT 日が存在する。
- 追加: `build_regeneration_request` の `fixed_assignments` に既存日の固定・確定が含まれること。
- 追加: 反映の途中失敗で `schedule_days` の新規行だけ残らないこと（M15 と同じ monkeypatch 方式）。
- 追加: preview 後に未作成日のうち 1 日を別途作成（シフト生成画面相当）→ apply が `SCHEDULE_PREVIEW_STALE`。
- 既存 `test_preview_of_a_fully_finalized_period_reports_it`、`test_current_schedule_marks_missing_days` は変更なし。

### Acceptance Criteria

- 重なる期間で再生成すると、既存日の固定・確定を守ったまま未作成日が下書きとして作られる。
- 反映は 1 トランザクションで、途中失敗時に `schedule_days` だけ残らない。

---

## PR-G: スタッフ無効化時に勤務表を整える（Medium・独立）

### Goal

無効化したスタッフの出勤行は勤務表に残り、検証では人数に数えられ、Solver は知らない。
ただし固定済み・確定日の勤務を自動で消すのは「固定は守る」原則に反するため、範囲を限定し、衝突は通知に留める。
保持した勤務は Solver にも既存配置として渡す（渡さないと、最大 1 名の日に無効スタッフの固定出勤が残ったまま
有効スタッフをもう 1 名配置でき、保存後に 2 名になる）。
また勤務表調整画面は有効スタッフしか選べないため、残った勤務を手で休みにできない。

### Relevant Files

- `src/services.py` — `deactivate_staff`（新規）, `build_generation_request`, `build_regeneration_request`
- `src/repositories.py` — `deactivate_staff`, `list_staff_schedule_assignments`, `list_staff_details`
- `src/models.py` — 戻り値型（新規 `DeactivationResult`）
- `src/day_conditions.py` — 変更なし（無効スタッフの条件は services 側で組み立てる）
- `pages/01_staff.py` — 無効化ボタン
- `pages/07_schedule.py` — スタッフ選択（58 行付近, 121 行付近）と勤務表グリッド
- `tests/test_staff_master_services.py`, `tests/test_schedule_services.py`, `tests/test_ui_pages.py`

### Changes

1. `services.deactivate_staff(conn, staff_id, from_date: str) -> DeactivationResult` を追加する。
   - 対象: `work_date >= from_date` の勤務行だけ。`from_date` より前は一切触らない。
   - DRAFT 日かつ `locked=False` かつ `is_working=True` の行 → 休み（`source=MANUAL`, `locked=False`）に更新。
   - DRAFT 日かつ `locked=True` かつ出勤、または確定日の出勤 → 変更せず `DeactivationResult.kept_working_dates` に日付を入れる。
   - 最後に `repo.deactivate_staff`。全体を 1 トランザクションで行う（repo に `deactivate_staff_with_schedule(...)` を追加するか、
     既存 2 関数をトランザクション内呼び出し用に分ける）。
2. `DeactivationResult(staff_id, cleared_dates: list[str], kept_working_dates: list[str])`。
3. `pages/01_staff.py`: 無効化ボタンの横に「適用開始日」（既定は翌日）を置き、結果の `kept_working_dates` があれば
   「固定または確定済みのため出勤のまま残した日: MM/DD, …。休みにする場合は『勤務表調整』でこのスタッフを選んで
   変更してください（確定した日は先に確定を解除してください）。」と警告する。
4. **保持した勤務を Solver に渡す**（`build_generation_request` / `build_regeneration_request`）:
   期間内に勤務行がある無効スタッフも `request.staff` に含める。その `day_conditions` は services 側で組み立て、
   - 現在の勤務行が出勤かつ（固定または確定日）の日: `can_work=True`、時刻は勤務行の `start_time/end_time`、
     `time_status=OK`。`fixed_assignments` に 1 で入れる。
   - それ以外の日: `can_work=False`（候補外）。行がある日は `fixed_assignments` に 0 で入れる
     （未固定の下書き出勤が残っていれば再生成で休みになる。無効化前の古い行の掃除を兼ねる）。
   無効スタッフは `target_days_per_week=None` かつ自動目安の分母にも入れない
   （`_effective_target_scales` に渡す `candidates` で候補日が固定日だけになるため、`StaffGenerationSummary` の
   目安は「なし」にする。`GenerationStaff` に `is_active: bool = True` を追加し、自動群から除く）。
   初回生成（行がない）では従来どおり無効スタッフを含めない。
5. `pages/07_schedule.py`: スタッフ選択肢と勤務表グリッドに「期間内に勤務行がある無効スタッフ」も含め、
   表示名を「○○（無効）」にする。無効スタッフは休みへの変更だけ許可し、出勤への変更は
   `validate_manual_schedule_changes` で新エラー `SCHEDULE_STAFF_INACTIVE`（「無効化されたスタッフは出勤にできません。」）にする。
   `repo.list_staff_details(include_inactive=True)` と期間内の勤務行から対象を絞る service 関数
   `list_schedule_staff(conn, work_dates) -> list[StaffDetail]`（有効スタッフ + 行のある無効スタッフ、ロール・従業員番号順）を追加する。

### Constraints

- 固定・確定の勤務は自動で変えない。
- 過去（`from_date` 未満）は変えない。
- 無効スタッフを新規に配置しない（候補日は保持した固定・確定の出勤日だけ）。

### Tests

- 追加: 未固定の DRAFT 出勤が `from_date` 以降だけ休みになり、`from_date` 前は変わらない。
- 追加: 固定出勤・確定日の出勤は残り、`kept_working_dates` に入る。
- 追加: 途中失敗で `active` も勤務行も変わらない（monkeypatch 方式）。
- 追加（Solver 連携）: `max_total_staff=1`・必要 1 名の日に無効スタッフの固定出勤が残っている状態で
  再生成 → preview の `scheduled_staff_count == 1`、有効スタッフは配置されず、apply 後の勤務表検証で
  `STAFF_OVER_MAX` が出ない。preview の不足数と検証の不足数が一致する。
- 追加（Solver 連携）: 無効スタッフのロール・スキルが日別のロール条件・スキル条件に数えられること
  （リーダー必要 1 名の日に無効リーダーの固定出勤が残っていれば、有効リーダーを追加配置しない）。
- 追加（Solver 連携）: 無効スタッフの未固定の下書き出勤が再生成で休みになること。
- 追加（画面）: `list_schedule_staff` が行のある無効スタッフを含み、行のない無効スタッフを含まないこと。
  無効スタッフを出勤に変更すると `SCHEDULE_STAFF_INACTIVE`。休みへの変更は保存できる。

### Acceptance Criteria

- 無効化後、指定日以降の未固定の下書き出勤が残らない。固定・確定の出勤は残り、画面に日付が示される。
- 保持した出勤は再生成の人数・ロール・スキル計算に含まれ、「無効化 → 再生成 → 保存」で最大人数超過や不足数の食い違いが起きない。
- 勤務表調整画面で、勤務行のある無効スタッフを選んで残った出勤を休みにできる。

---

## PR-F: 小さな改善をまとめて（Low）

独立した小項目。1 PR にまとめてよい。

### F-1 段階間のヒントと「どこまで最適化したか」の記録

- `src/shift_generation.py::_solve_lexicographic`: 各段階の後に `model.ClearHints()` →
  前段の解を `model.AddHint(var, value)` で全 `x` に与える（前段の解は次段でも実行可能なので探索が速くなる）。
- `ScheduleGenerationResult` に `pass_results: list[tuple[str, str]]`（段階名, その段階の solver status）と
  `total_passes: int` を追加する。「完了」は **最適性を証明できた（OPTIMAL）段階** を指し、
  FEASIBLE で時間切れになった段階は「実行可能な案は得たが最適化は未完了」として区別する。
- 画面の「詳しい情報」に「最適化 6 段階のうち 3 段階を完了、4 段階目は時間内に最適化できず途中の案を使用」のように出す。
- テスト:
  - `test_six_passes_stay_within_the_total_time_budget` に `pass_results` が空でなく `len <= 6` であることを追加。
  - ヒントの有無で結果（不足・出勤日数・目標乖離）が同じであることは、**全段階が OPTIMAL で終わる小規模ケース**
    （例: 3 名 × 5 日、時間上限なし）に限定して確認する。時間上限下では解が変わり得るため比較しない。

### F-2 不足合計の二重計上を画面で分ける

- `pages/06_generate.py`, `pages/07_schedule.py` の「不足の合計」の下に
  「人数不足 / ロール不足 / スキル不足 は重なることがあります」のキャプションを足す。
  または 3 つを別メトリクスにする。ロジックは変えない。

### F-3 チェッカーの週 5 日既定を定数に出す

- `src/constants.py` に `DEFAULT_TARGET_DAYS_PER_WEEK_BY_ROLE = {ROLE_CHECKER: 5}` を置き、
  `_effective_target_scales` はそれを参照する。将来ロールマスターへ移す前段階。

### F-4 期間セレクタの共有

- `src/ui_common.py::select_period` の既定キーを全画面共通（`period`）にし、画面ごとの `key_prefix` 引数をやめる。
  呼び出し側 4 画面を修正。テスト `tests/test_ui_pages.py` の該当箇所を更新。

### F-5 README に保存先の注意を追記

- 「運用上の注意」に「Streamlit Community Cloud 等のクラウド環境ではファイルが再起動・再デプロイで消えるため、
  `data/app.db` を永続化できる環境（ローカル PC など）で運用すること」を追記する。コード変更なし。

---

## 保留事項（運用上の検討事項・利用者の判断待ち）

### 超過配置をどこまで許容するか（自動目安の計算方式）

現行（PR-C 後も同じ）: 自動目安 = 全体の最低需要 ÷ 候補日のあるスタッフ数。
明示目標を持つスタッフの分も全体需要に含めて割るため、目標設定者が多いほど最低必要人数を上回る配置が増える。
これは不具合ではなく、「目標勤務日数と公平性を優先し、必要なら最低必要人数を上回る配置を許容する」
という現行方針の帰結であり、どこまで超過を許すかは運用上の検討事項。

代替案: 自動目安 = (全体需要 − 明示目標とチェッカー既定の合計) ÷ 自動群の人数。
超過配置は減るが、目標設定者だけで需要を満たす場合に自動群が 0 日になる。

採用する場合の実装メモ:

- `round(remaining / 人数)` を全員に配ると合計が残り需要を超える
  （例: 残り 7・4 名 → 各 2 で合計 8）。商を全員に、余りを `staff_id` 昇順で 1 ずつ配る。
- 自動群に「最低 N 日」の下限を設けるかどうかを決める（0 日を避けたい場合）。
- 期待値が変わる既存テスト: `test_mixed_staff_with_and_without_targets`（B が 0 日）、
  `test_f10_day_off_request_outranks_the_target`（`total_workdays` 7 → 5）。

判断に必要な問い: 「目標を出している人だけで必要人数が埋まるとき、目標を出していない人の勤務を 0 日にしてよいか」。

---

## 実装順序と見積もり

| 順 | PR | 規模 | 依存 |
| --- | --- | --- | --- |
| 1 | PR-A | 小〜中 | なし |
| 2 | PR-B | 小〜中 | なし |
| 3 | PR-B2 | 中 | PR-B（`RegenerationPreview` の変更をまとめる） |
| 4 | PR-C | 中 | なし |
| 5 | PR-D | 中 | PR-A（前後の履歴） |
| 6 | PR-E | 中 | PR-B, PR-B2 |
| 7 | PR-G | 中 | PR-B2（反映対象を `prepare` 内で決める）, PR-C（自動目安の分母から無効スタッフを除く） |
| 8 | PR-F | 小の集合 | PR-C（F-3） |

各 PR のレビュー観点: 仕様との一致、固定・確定を壊さないこと、トランザクション境界、
既存テストの期待値変更が本文書の記載どおりであること。

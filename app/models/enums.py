"""仕様で定義された選択肢。画面表示は日本語、保存値は英字とする。"""

from __future__ import annotations

# 仕様第6.2章：出所分類
SOURCE_CLASSES: dict[str, str] = {
    "classroom_existing": "教室の既存作品",
    "prepared_for_3d": "3D向けに準備",
    "real_photo": "実際の写真",
    "reference": "お手本",
    "staff_original": "運営・講師の自作",
}

# 仕様第6.2章：題材タグ（1アセットにつき1つ。ASSUMPTION A-6）
SUBJECT_TAGS: dict[str, str] = {
    "single_character": "単体キャラ",
    "animal": "動物",
    "goods": "雑貨",
    "person": "人物",
    "landscape": "風景",
    "multi_object": "複数対象",
    "fine_detail": "細部多め",
    "other": "その他",
}

# 仕様第9章：利用同意
CONSENT_STATUSES: dict[str, str] = {
    "granted": "同意あり",
    "not_required": "不要（自作・お手本等）",
    "missing": "未取得",
}
CONSENT_MISSING = "missing"

# 仕様第9章：加工種別（選択式・複数可）
EDIT_TYPES: dict[str, str] = {
    "background_removal": "背景除去",
    "crop": "トリミング",
    "resize": "リサイズ",
    "contrast": "コントラスト調整",
    "other": "その他",
}

VARIANT_KINDS: dict[str, str] = {"original": "原画像", "processed": "加工版"}

# 仕様第7章
PURPOSES: dict[str, str] = {
    "benchmark": "ベンチマーク",
    "classroom_simulation": "授業想定",
}

# 仕様第8章：技術状態
TECH_STATUSES: dict[str, str] = {
    "queued": "受付済み",
    "submitting": "送信中",
    "running": "生成中",
    "downloading": "保存中",
    "ready_for_review": "評価待ち",
    "submission_unknown": "受付結果不明",
    "provider_failed": "事業者側で失敗",
    "download_failed": "保存失敗",
    "validation_failed": "検査で不合格",
    "monitoring_paused": "確認を一時停止",
    "cancelled": "中止",
}
TERMINAL_TECH_STATUSES = frozenset({"ready_for_review", "provider_failed", "cancelled"})

# 仕様第8章・第10章：品質判定（技術状態とは別管理）
VERDICTS: dict[str, str] = {
    "unreviewed": "未評価",
    "pass": "合格",
    "retry_recommended": "再生成候補",
    "unsuitable": "題材不適",
}

# 仕様第10章：評価軸（5段階）
REVIEW_AXES: dict[str, str] = {
    "score_fidelity": "元画像の特徴",
    "score_shape": "全周の形状",
    "score_color": "色・模様",
    "score_appeal": "魅力・完成度",
    "score_mobile": "スマホ表示操作性",
}
SCORE_PASS_THRESHOLD = 4

# 仕様第10章：重大不具合タグ
DEFECT_TAGS: dict[str, str] = {
    "face_broken": "顔の崩れ",
    "part_missing": "部品欠損",
    "extra_part": "余分な部品",
    "fusion": "融合",
    "back_broken": "背面破綻",
    "pattern_changed": "模様の大幅変更",
    "background_mixed": "背景混入",
    "load_failed": "読込不可",
    "unoperable": "操作不能",
    "other": "その他",
}

# 仕様第9章・第11章：費用の種別
COST_KINDS: dict[str, str] = {
    "estimate": "見積",
    "confirmed_manual": "手動確認実績",
    "failed_unreconciled": "失敗・要照合",
}

ARTIFACT_KINDS: dict[str, str] = {"glb": "GLB", "thumbnail": "サムネイル"}

PROVIDERS: dict[str, str] = {"mock": "モック", "tripo": "Tripo", "meshy": "Meshy"}

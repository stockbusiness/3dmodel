"""事業者接続の状態（管理画面用）。仕様第4章・第6.3章・第12章。

**この module は APIキーの値を一切返さない。**
返すのは「設定されているか」「形式が想定どおりか」という真偽だけで、
値そのもの・先頭数文字・長さは画面にもログにも出さない（仕様第12章）。

キーは環境変数からのみ読む。画面から設定・保存はしない（`docs/decisions.md` S-6）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import get_settings

# 事業者ごとの、キーの環境変数名と想定される先頭。
# 先頭の確認は docs/provider-contracts.md（Tripo 第1.2節・Meshy 第2.2節）による。
PROVIDER_KEYS: dict[str, dict[str, str]] = {
    "tripo": {
        "env": "TRIPO_API_KEY",
        "prefix": "tsk_",
        # 公式SDK 0.4.2 は先頭が違うとコンストラクタで例外を上げる
        "prefix_is_enforced": "yes",
    },
    "meshy": {
        "env": "MESHY_API_KEY",
        "prefix": "msy_",
        "prefix_is_enforced": "no",
    },
}

# 実生成を行う事業者（モックは除く）
REAL_PROVIDERS = tuple(PROVIDER_KEYS)


@dataclass(frozen=True)
class Check:
    """診断1件。`ok=False` は実生成に進めないことを意味する。"""

    label: str
    ok: bool
    detail: str
    is_warning: bool = False


def key_is_set(provider: str) -> bool:
    """キーが環境変数に設定されているか。値は返さない。"""
    spec = PROVIDER_KEYS.get(provider)
    if spec is None:
        # モック等、キーを必要としない事業者
        return True
    return bool(os.environ.get(spec["env"], "").strip())


def key_prefix_ok(provider: str) -> bool:
    """キーの先頭が想定どおりか。値は返さない。未設定なら False。"""
    spec = PROVIDER_KEYS.get(provider)
    if spec is None:
        return True
    key = os.environ.get(spec["env"], "").strip()
    return bool(key) and key.startswith(spec["prefix"])


def download_hosts(provider: str) -> frozenset[str]:
    settings = get_settings()
    if provider == "tripo":
        return settings.tripo_allowed_hosts
    if provider == "meshy":
        return settings.meshy_allowed_hosts
    return frozenset()


def download_hosts_configured(provider: str) -> bool:
    """成果物の配信ホストが設定されているか（仕様第12章）。

    モック等、実ホストを持たない事業者は対象外として True を返す。
    """
    if provider not in PROVIDER_KEYS:
        return True
    return bool(download_hosts(provider))


def blocking_reasons(provider: str) -> list[str]:
    """実生成に進めない理由（事業者側の設定に起因するもの）を日本語で返す。

    仕様第6.3章が挙げる不可条件のうち「キー未設定」だけがここに当たる。
    プリセット側の理由は `app.services.presets.selectable_reasons` が持つ。

    **配信ホスト未設定はここに入れない**（`docs/decisions.md` A-44）。
    仕様第6.3章の一覧に無いうえ、事業者の配信ホストは実物のURLでしか確認できず、
    ここで送信を止めると「確認するための1件」すら出せなくなるため。
    実際の防御は `download_guard` が行い、未設定なら取得を拒否する。
    未設定であることは `diagnose()` が注意として返す。
    """
    reasons: list[str] = []
    spec = PROVIDER_KEYS.get(provider)
    if spec is None:
        return reasons
    if not key_is_set(provider):
        reasons.append(f"APIキーが設定されていません（環境変数 {spec['env']}）")
    elif not key_prefix_ok(provider) and spec["prefix_is_enforced"] == "yes":
        reasons.append(f"APIキーの形式が想定と違います（{spec['prefix']} で始まる必要があります）")
    return reasons


def diagnose(provider: str) -> list[Check]:
    """外部通信を行わない自己診断。いつでも実行してよい。"""
    settings = get_settings()
    spec = PROVIDER_KEYS.get(provider)
    checks: list[Check] = []

    if spec is None:
        checks.append(Check("APIキー", True, "この事業者はキーを必要としません"))
        return checks

    env_name = spec["env"]
    prefix = spec["prefix"]

    if not key_is_set(provider):
        checks.append(
            Check("APIキー", False, f"未設定です。環境変数 {env_name} を設定してください")
        )
    elif key_prefix_ok(provider):
        checks.append(Check("APIキー", True, f"設定済みです（{prefix} で始まっています）"))
    elif spec["prefix_is_enforced"] == "yes":
        checks.append(Check("APIキー", False, f"設定されていますが {prefix} で始まっていません"))
    else:
        checks.append(
            Check(
                "APIキー",
                True,
                f"設定済みです。ただし想定の {prefix} で始まっていません",
                is_warning=True,
            )
        )

    hosts = download_hosts(provider)
    if hosts:
        checks.append(Check("成果物の配信ホスト", True, "、".join(sorted(hosts))))
    else:
        checks.append(
            Check(
                "成果物の配信ホスト",
                True,
                "未設定です。生成は行えますが、成果物の取得は拒否されます"
                "（仕様第12章）。拒否のメッセージに実際のホスト名が出るので、"
                "それを設定してから「保存だけ再試行」してください（追加課金なし）",
                is_warning=True,
            )
        )

    if settings.live_api_enabled:
        checks.append(Check("実API生成", True, "有効です（LIVE_API_ENABLED=true）"))
    else:
        checks.append(
            Check(
                "実API生成",
                False,
                "無効です（LIVE_API_ENABLED=false）。既定はこちらです",
                is_warning=True,
            )
        )

    if settings.global_cost_cap_usd:
        checks.append(Check("全体上限額", True, f"{settings.global_cost_cap_usd} USD"))
    else:
        checks.append(Check("全体上限額", False, "未設定です。設定するまで実API生成はできません"))

    return checks


def env_hint(provider: str) -> str:
    """`.env` に書く行の見本。値はダミーにする。"""
    spec = PROVIDER_KEYS.get(provider)
    if spec is None:
        return ""
    return f"{spec['env']}={spec['prefix']}ここに事業者から受け取ったキーを貼る"

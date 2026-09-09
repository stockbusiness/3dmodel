"""運用CLI（仕様第6.1章・第15章）。

初期アカウントはCLIで作成し、平文パスワードをGitやログに出さない。
初期パスワードの固定値は禁止のため、対話入力または自動生成のみを受け付ける
（SECURITY_REVIEW S-1）。
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

from app.auth import hash_password
from app.db import session_scope
from app.models import Operator
from app.services.presets import (
    apply_confirmed_prices,
    apply_preset_availability,
    refresh_placeholder_presets,
    seed_presets,
)

MIN_PASSWORD_LENGTH = 12


def init_db() -> None:
    """マイグレーションを最新まで適用し、プリセットを投入する。

    テーブル定義は Alembic を正とする（create_all は使わない）。
    """
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")

    with session_scope() as db:
        created = seed_presets(db)
        updated = refresh_placeholder_presets(db)
        priced = apply_confirmed_prices(db)
        availability = apply_preset_availability(db)
    print(
        f"DBを最新まで移行しました。プリセットを {created} 件追加し、"
        f"{updated} 件を確認済みの内容に更新し、{priced} 件に確認済みの価格を入れ、"
        f"{len(availability)} 件の有効・無効を更新しました。"
    )


def check_provider(provider: str, connect: bool) -> None:
    """事業者の設定を確認する（管理画面と同じ内容を端末で見るため）。

    **APIキーの値は出さない。** 設定されているか、先頭が想定どおりかだけを示す。
    `--connect` は認証が通るかだけを確かめる。**生成は行わないので課金は発生しない。**
    """
    from app.config import get_settings
    from app.providers.base import ProviderError, UnsupportedOperation
    from app.providers.registry import get_adapter
    from app.services import provider_health

    if provider not in provider_health.PROVIDER_KEYS:
        known = "／".join(provider_health.REAL_PROVIDERS)
        print(f"事業者が見つかりません: {provider}（指定できるのは {known}）", file=sys.stderr)
        raise SystemExit(1)

    print(f"[{provider}] 自己診断（外部通信なし）")
    for item in provider_health.diagnose(provider):
        mark = "注意" if item.is_warning else ("OK" if item.ok else "未 ")
        print(f"  {mark}  {item.label}: {item.detail}")

    reasons = provider_health.blocking_reasons(provider)
    if reasons:
        print("  → 実生成に進めません：" + "／".join(reasons))

    if not connect:
        print("  （認証を確かめるには --connect を付けてください）")
        return

    settings = get_settings()
    if not settings.live_api_enabled:
        print(
            "\n実APIが無効です（APP_LIVE_API_ENABLED=false）。"
            "接続テストは外部へ通信するため、有効にしてから実行してください。",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if reasons:
        print("\n設定が足りないため接続テストを行いません。", file=sys.stderr)
        raise SystemExit(1)

    print("\n接続テスト（生成は行わないので課金は発生しません）")
    try:
        result = get_adapter(provider).check_connection()
    except UnsupportedOperation as exc:
        print(f"  未対応: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ProviderError as exc:
        # 事業者の応答原文は出さない（正規化済みの日本語だけ）
        print(f"  失敗: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"  成功: {result.detail}")
    if result.note:
        print(f"  {result.note}")


def create_operator(login_name: str, display_name: str, generate: bool) -> None:
    with session_scope() as db:
        if db.scalar(select(Operator).where(Operator.login_name == login_name)) is not None:
            print(f"ログイン名 {login_name} は既に存在します。", file=sys.stderr)
            raise SystemExit(1)

        if generate:
            password = secrets.token_urlsafe(16)
        else:
            password = getpass.getpass("パスワード: ")
            confirm = getpass.getpass("パスワード（確認）: ")
            if password != confirm:
                print("パスワードが一致しません。", file=sys.stderr)
                raise SystemExit(1)
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"パスワードは{MIN_PASSWORD_LENGTH}文字以上にしてください。", file=sys.stderr)
            raise SystemExit(1)

        db.add(
            Operator(
                login_name=login_name,
                display_name=display_name or login_name,
                password_hash=hash_password(password),
                is_active=True,
                is_admin=True,
            )
        )
    print(f"アカウント {login_name} を作成しました。")
    if generate:
        # 生成した場合のみ1度だけ表示する。ログには残さない
        print("初期パスワード（この画面にのみ表示します。保管後は閉じてください）:")
        print(password)


def main() -> None:
    parser = argparse.ArgumentParser(description="3Dアート品質検証システムの運用CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="テーブル作成とプリセット投入")

    create = sub.add_parser("create-operator", help="運営・講師アカウントを作る")
    create.add_argument("login_name")
    create.add_argument("--display-name", default="")
    create.add_argument(
        "--generate-password",
        action="store_true",
        help="パスワードを自動生成して1度だけ表示する（対話入力できない環境向け）",
    )

    sub.add_parser("seed-presets", help="プリセットを投入する（既存行は変更しない）")

    check = sub.add_parser(
        "check-provider",
        help="事業者の設定を確認する。--connect を付けると認証だけ確かめる（課金なし）",
    )
    check.add_argument("provider", help="tripo または meshy")
    check.add_argument(
        "--connect",
        action="store_true",
        help="外部へ接続して認証を確かめる。生成は行わないので課金は発生しない。"
        "APP_LIVE_API_ENABLED=true のときだけ実行できる",
    )

    args = parser.parse_args()
    if args.command == "init-db":
        init_db()
    elif args.command == "create-operator":
        create_operator(args.login_name, args.display_name, args.generate_password)
    elif args.command == "check-provider":
        check_provider(args.provider, args.connect)
    elif args.command == "seed-presets":
        with session_scope() as db:
            created = seed_presets(db)
            updated = refresh_placeholder_presets(db)
            priced = apply_confirmed_prices(db)
            availability = apply_preset_availability(db)
        print(
            f"プリセットを {created} 件追加し、{updated} 件を更新し、"
            f"{priced} 件に確認済みの価格を入れ、"
            f"{len(availability)} 件の有効・無効を更新しました。"
        )


if __name__ == "__main__":
    main()

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
    print(
        f"DBを最新まで移行しました。プリセットを {created} 件追加し、"
        f"{updated} 件を確認済みの内容に更新し、{priced} 件に確認済みの価格を入れました。"
    )


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

    args = parser.parse_args()
    if args.command == "init-db":
        init_db()
    elif args.command == "create-operator":
        create_operator(args.login_name, args.display_name, args.generate_password)
    elif args.command == "seed-presets":
        with session_scope() as db:
            created = seed_presets(db)
            updated = refresh_placeholder_presets(db)
            priced = apply_confirmed_prices(db)
        print(
            f"プリセットを {created} 件追加し、{updated} 件を更新し、"
            f"{priced} 件に確認済みの価格を入れました。"
        )


if __name__ == "__main__":
    main()

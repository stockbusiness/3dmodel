"""永続ジョブ処理の入口（仕様第15章）。

A1では入口のみを用意する。受付は画面側で同期的にモック処理を行うため、
このワーカーは取り残された queued の生成を拾う保険として動く。

A2で以下を実装する（先回りしない）：
- lease（BEGIN IMMEDIATE ＋条件付きUPDATE、lease_owner / lease_until）
- 「状態確認」と「ダウンロード」を別の処理単位に分ける
- 状態の分岐と復帰経路、submission_unknown、monitoring_paused
- 全外部通信のタイムアウト
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
import uuid

from sqlalchemy import select

from app.db import session_scope
from app.models import Generation
from app.services.generation import process_generation

logger = logging.getLogger("worker")

POLL_SECONDS = 5


def worker_identity() -> str:
    """lease_owner に使う識別子（ASSUMPTION A-9）。

    同一ホストでPIDが再利用されても衝突しないよう、起動ごとのUUIDを含める。
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def process_once(limit: int = 10) -> int:
    """queued の生成を最大 limit 件処理し、処理した件数を返す。"""
    processed = 0
    with session_scope() as db:
        pending = db.scalars(
            select(Generation)
            .where(Generation.tech_status == "queued")
            .order_by(Generation.created_at.asc())
            .limit(limit)
        ).all()
        for generation in pending:
            logger.info("生成を処理します id=%s provider=%s", generation.id, generation.provider)
            process_generation(db, generation)
            processed += 1
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="3Dアート品質検証システムのワーカー")
    parser.add_argument("--once", action="store_true", help="1回だけ処理して終了する")
    parser.add_argument("--interval", type=int, default=POLL_SECONDS, help="待機秒数")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    identity = worker_identity()
    logger.info("ワーカーを開始します owner=%s", identity)

    if args.once:
        count = process_once()
        logger.info("%d 件を処理しました", count)
        return

    while True:
        try:
            count = process_once()
            if count:
                logger.info("%d 件を処理しました", count)
        except Exception:  # noqa: BLE001 - 1件の失敗でワーカーを落とさない
            logger.exception("処理中に例外が発生しました")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

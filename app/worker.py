"""永続ジョブ処理（仕様第8章・第15章）。

web とは別プロセスで動く。DBから短い処理単位で対象を取り出し、
外部通信のあいだDBのトランザクションを持たない。

1周で行うこと：
1. lease が切れた対象の回収（submitting のままなら submission_unknown へ）
2. 送信（同時外部タスクの上限内で1件）
3. 状態確認（対象を全件、順に）
4. ダウンロード（1件だけ。長い保存が他タスクの状態確認を止めないようにする）
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import time
import uuid

from app.config import get_settings
from app.services.worker_steps import run_cycle

logger = logging.getLogger("worker")

_stopping = False


def worker_identity() -> str:
    """lease_owner に使う識別子（ASSUMPTION A-9）。

    同一ホストでPIDが再利用されても衝突しないよう、起動ごとのUUIDを含める。
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _handle_signal(signum, _frame) -> None:
    global _stopping
    logger.info("停止の指示を受け取りました signal=%s", signum)
    _stopping = True


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="3Dアート品質検証システムのワーカー")
    parser.add_argument("--once", action="store_true", help="1周だけ処理して終了する")
    parser.add_argument(
        "--interval", type=int, default=settings.worker_interval_seconds, help="待機秒数"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    identity = worker_identity()
    logger.info("ワーカーを開始します owner=%s", identity)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if args.once:
        logger.info("1周の結果: %s", run_cycle(identity))
        return

    while not _stopping:
        try:
            result = run_cycle(identity)
            if any(result.values()):
                logger.info("1周の結果: %s", result)
        except Exception:  # noqa: BLE001 - 1件の失敗でワーカーを落とさない
            logger.exception("処理中に例外が発生しました")
        for _ in range(args.interval):
            if _stopping:
                break
            time.sleep(1)
    logger.info("ワーカーを終了します owner=%s", identity)


if __name__ == "__main__":
    main()

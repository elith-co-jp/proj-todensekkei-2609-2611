"""画面・書き出しに出す時刻を日本時間（JST, UTC+9）にそろえる（DD-0036）。

保存（DB の DateTime 列）は従来どおり「タイムゾーンなしの UTC」（models.utc_now_naive）。
API・ファイル名・書き出しファイルに出すときだけ、ここで日本時間に直し、`+09:00` を付けて返す。
日本には夏時間がないため固定の +9 時間で十分で、Windows でも tzdata なしで動く。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")


def now_jst() -> datetime:
    """いまの日本時間（秒未満は切り捨て）。"""
    return datetime.now(JST).replace(microsecond=0)


def to_jst(dt: datetime | None) -> datetime | None:
    """DB の値（タイムゾーンなしは UTC とみなす）を日本時間に直す。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(JST)


def iso_jst(dt: datetime | None) -> str | None:
    """API に返す形: `2026-11-20T17:10:00+09:00`。None はそのまま None。"""
    v = to_jst(dt)
    return v.replace(microsecond=0).isoformat() if v else None


def now_iso_jst() -> str:
    """書き出しファイルの `exported_at` などに入れる、いまの日本時間。"""
    return now_jst().isoformat()


def file_stamp_jst() -> str:
    """ダウンロードのファイル名に付ける日本時間（例 `20261120_171000`）。"""
    return now_jst().strftime("%Y%m%d_%H%M%S")

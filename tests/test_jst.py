"""画面・書き出しの時刻が日本時間になっていること（DD-0036）。"""

import re
from datetime import UTC, datetime

from models import utc_now_naive
from services.jst import file_stamp_jst, iso_jst, now_iso_jst, to_jst


def test_db_value_is_utc_and_shown_as_jst():
    # DB には UTC（タイムゾーンなし）で入り、API には +09:00 付きの日本時間で出る。
    assert iso_jst(datetime(2026, 11, 20, 8, 10, 5)) == "2026-11-20T17:10:05+09:00"
    assert iso_jst(datetime(2026, 11, 20, 20, 0, 0)) == "2026-11-21T05:00:00+09:00"
    assert iso_jst(datetime(2026, 11, 20, 8, 10, 5, 123456)) == "2026-11-20T17:10:05+09:00"
    assert iso_jst(None) is None
    assert to_jst(datetime(2026, 11, 20, 8, 0, tzinfo=UTC)).hour == 17


def test_now_values_are_jst():
    assert now_iso_jst().endswith("+09:00")
    assert re.fullmatch(r"\d{8}_\d{6}", file_stamp_jst())
    # 保存用の値（UTC）を JST に直すと、いまの日本時間とほぼ一致する
    diff = abs((to_jst(utc_now_naive()) - datetime.fromisoformat(now_iso_jst())).total_seconds())
    assert diff < 5

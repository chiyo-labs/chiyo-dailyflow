# app/extract.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


@dataclass
class ExtractResult:
    dt: datetime
    matched_date: str  # 何で日付決めたか（デバッグ用）
    matched_time: str  # 何で時刻決めたか（デバッグ用）


def _now_jst() -> datetime:
    return datetime.now(JST)


def extract_datetime(text: str, now: datetime | None = None) -> ExtractResult:
    """
    日本語の文章から日時を抽出する（タイトルは抽出しない）

    対応:
      - 日付: 今日 / 明日 / 明後日 / あさって / M/D / MM/DD / M月D日
      - 時刻: H:MM / HH:MM / H時 / H時半
    """
    if now is None:
        now = _now_jst()

    src = text.strip()
    if not src:
        raise ValueError("文章が空です。")

    # -------------------------
    # 日付を決める
    # -------------------------
    target_day: date | None = None
    matched_date = ""

    # 相対日付
    if re.search(r"(今日)", src):
        target_day = now.date()
        matched_date = "今日"
    elif re.search(r"(明日)", src):
        target_day = (now + timedelta(days=1)).date()
        matched_date = "明日"
    elif re.search(r"(明後日|あさって)", src):
        target_day = (now + timedelta(days=2)).date()
        matched_date = "明後日/あさって"

    # 明示日付（M月D日）
    if target_day is None:
        m = re.search(r"(\d{1,2})月(\d{1,2})日", src)
        if m:
            mm = int(m.group(1))
            dd = int(m.group(2))
            year = now.year
            candidate = date(year, mm, dd)
            # 過去なら翌年扱い（翌年の予定として解釈）
            if candidate < now.date():
                candidate = date(year + 1, mm, dd)
            target_day = candidate
            matched_date = f"{mm}月{dd}日"

    # 明示日付（M/D or MM/DD）
    if target_day is None:
        m = re.search(r"(\d{1,2})/(\d{1,2})", src)
        if m:
            mm = int(m.group(1))
            dd = int(m.group(2))
            year = now.year
            candidate = date(year, mm, dd)
            if candidate < now.date():
                candidate = date(year + 1, mm, dd)
            target_day = candidate
            matched_date = f"{mm}/{dd}"

    # 日付が見つからなければ「今日」で扱う
    if target_day is None:
        target_day = now.date()
        matched_date = "日付指定なし→今日"

    # -------------------------
    # 時刻を決める
    # -------------------------
    hour: int | None = None
    minute: int | None = None
    matched_time = ""

    # HH:MM / H:MM
    m = re.search(r"(\d{1,2}):(\d{2})", src)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        matched_time = f"{hour}:{minute:02d}"
    else:
        # H時 / H時半
        m = re.search(r"(\d{1,2})時(半)?", src)
        if m:
            hour = int(m.group(1))
            minute = 30 if m.group(2) else 0
            matched_time = f"{hour}時{'半' if minute==30 else ''}"

    if hour is None or minute is None:
        raise ValueError("時刻が見つかりませんでした（例: 19時 / 19時半 / 19:00 の形で入れてね）")

    # バリデーション
    if not (0 <= hour <= 23):
        raise ValueError("時刻の『時』が不正です（0〜23）")
    if not (0 <= minute <= 59):
        raise ValueError("時刻の『分』が不正です（0〜59）")

    dt = datetime(
        target_day.year, target_day.month, target_day.day,
        hour, minute, tzinfo=JST
    )

    return ExtractResult(dt=dt, matched_date=matched_date, matched_time=matched_time)
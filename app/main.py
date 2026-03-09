# app/main.py
from __future__ import annotations

import sys
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from storage import load_data, save_data
from extract import extract_datetime


JST = ZoneInfo("Asia/Tokyo")

def has_conflict(data: list[dict], date_str: str, time_str: str) -> list[dict]:
    """同じ date & time の予定があれば返す（簡易版：開始時刻一致）"""
    hits = []
    for item in data:
        if item.get("date") == date_str and item.get("time") == time_str:
            hits.append(item)
    return hits

def _today_str() -> str:
    return datetime.now(JST).date().strftime("%Y-%m-%d")


def _date_to_str(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _normalize_time_str(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _parse_show_arg(arg: str) -> date | None:
    """
    コマンド引数から表示日付を決める:
      today / tomorrow / dayafter
      YYYY-MM-DD
      M/D or MM/DD
    """
    now = datetime.now(JST)

    a = arg.strip().lower()

    if a in ("today", "今日"):
        return now.date()
    if a in ("tomorrow", "明日"):
        return (now + timedelta(days=1)).date()
    if a in ("dayafter", "明後日", "あさって"):
        return (now + timedelta(days=2)).date()

    # YYYY-MM-DD
    try:
        if "-" in a and len(a) >= 8:
            return datetime.strptime(a, "%Y-%m-%d").date()
    except ValueError:
        pass

    # M/D
    try:
        if "/" in a:
            mm, dd = a.split("/", 1)
            mm = int(mm)
            dd = int(dd)
            year = now.year
            cand = date(year, mm, dd)
            if cand < now.date():
                cand = date(year + 1, mm, dd)
            return cand
    except Exception:
        pass

    return None


def _filter_events_for_date(data: list[dict], target: date) -> list[dict]:
    key = _date_to_str(target)
    events = [e for e in data if e.get("date") == key]
    # timeでソート（HH:MM 前提）
    events.sort(key=lambda x: x.get("time", "99:99"))
    return events


def show_events_for_date(target: date) -> None:
    data = load_data()
    events = _filter_events_for_date(data, target)

    title = f"【{target.month}月{target.day}日の予定】"
    print(title)
    print()

    if not events:
        print("予定はありません。")
        return

    for i, e in enumerate(events, 1):
        t = e.get("time", "")
        s = e.get("title", "")
        print(f"{i}. {t} - {s}")

def add_event_flow() -> None:
    data = load_data()
    import re
    print("\n予定文を貼ってください（例: 明日19時集合 / 3/5 19:00）：")
    text = input("> ").strip()

    try:
        res = extract_datetime(text)
    except ValueError as e:
        print(f"\n❌ 抽出できませんでした: {e}")
        return

    dt = res.dt
    date_str = dt.date().strftime("%Y-%m-%d")
    time_str = dt.strftime("%H:%M")

    print("\n抽出結果:")
    print(f"  日付: {date_str}（根拠: {res.matched_date}）")
    print(f"  時刻: {time_str}（根拠: {res.matched_time}）")

    # 重複チェック（同日同時刻）
    def conflicts_for(d: str, t: str):
        return [e for e in data if e.get("date") == d and e.get("time") == t]

    conflicts = conflicts_for(date_str, time_str)

    # 重複がある場合：メニューを出す
    if conflicts:
        print("\n⚠️ 警告: 同じ日時の予定がすでにあります")
        for e in conflicts:
            print(f"  - {e.get('time')} {e.get('title')}")

        print("\nどうしますか？")
        print("1: そのまま追加（重複OK）")
        print("2: キャンセル")
        print("3: 時間を変更して登録（同じ日）")
        choice = input("> ").strip()

        if choice == "2":
            print("キャンセルしました。")
            return

        if choice == "3":
            while True:
                new_time = input("新しい時間 (HH:MM): ").strip()
                # ざっくりバリデーション
                if not re.match(r"^\d{1,2}:\d{2}$", new_time):
                    print("❌ 時間形式が不正です。例: 09:30")
                    continue
                hh, mm = new_time.split(":")
                try:
                    hh_i = int(hh)
                    mm_i = int(mm)
                    if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
                        raise ValueError
                except ValueError:
                    print("❌ 時刻が不正です（00:00〜23:59）")
                    continue

                # 既にその時間も埋まってたら再提示
                more = conflicts_for(date_str, f"{hh_i:02d}:{mm_i:02d}")
                if more:
                    print("⚠️ その時間も既に予定があります。別の時間にしてください。")
                    for e in more:
                        print(f"  - {e.get('time')} {e.get('title')}")
                    continue

                time_str = f"{hh_i:02d}:{mm_i:02d}"
                break

        elif choice != "1":
            print("無効な選択です。キャンセルしました。")
            return

    else:
        ans = input("\nこの日時で登録しますか？ (y/n): ").strip().lower()
        if ans != "y":
            print("キャンセルしました。")
            return

    title = input("タイトルを入力: ").strip()
    if not title:
        print("❌ タイトルが空です。キャンセルしました。")
        return

    event = {"date": date_str, "time": time_str, "title": title}
    data.append(event)
    save_data(data)

    print("\n✅ 保存しました！")
    print(f"  {date_str} {time_str} - {title}")

    print()
    show_events_for_date(dt.date())

def show_flow() -> None:
    print("\n表示したい日付を入力してください")
    print("例: today / tomorrow / dayafter / 2026-03-05 / 3/5")
    arg = input("> ").strip()
    target = _parse_show_arg(arg)
    if not target:
        print("❌ 日付が解釈できませんでした。例の形式で入力してね。")
        return
    print()
    show_events_for_date(target)


def menu_loop() -> None:
    print("chiyo-dailyflow 起動")
    while True:
        print("\n1: 予定を追加（文章から日時抽出）")
        print("2: 予定を見る（今日・明日・明後日・日付指定）")
        print("3: 終了")
        choice = input("選択してください: ").strip()

        if choice == "1":
            add_event_flow()
        elif choice == "2":
            show_flow()
        elif choice == "3":
            print("終了します")
            break
        else:
            print("無効な選択です")


def main():
    # 引数があれば「表示コマンド」として動かす（例: python app/main.py today）
    if len(sys.argv) >= 2:
        target = _parse_show_arg(sys.argv[1])
        if not target:
            print("❌ 引数の日付が解釈できませんでした。")
            sys.exit(1)
        show_events_for_date(target)
        return

    # 引数がなければメニュー
    menu_loop()


if __name__ == "__main__":
    main()
from fastapi import FastAPI, Request
from app.extract import extract_datetime
from app.storage import load_data, save_data
import requests
import re
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

from google.oauth2 import service_account
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler

# ==========================
# .env をプロジェクト直下から読み込む
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # .../app
PROJECT_ROOT = os.path.dirname(BASE_DIR)                # .../chiyo-dailyflow
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

load_dotenv(dotenv_path=ENV_PATH)

# ==========================
# LINE設定
# ==========================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# ==========================
# Google設定
# ==========================
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service_account.json")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

CALENDAR_IDS = {
    "副業": os.getenv("CALENDAR_FUKUGYO"),
    "看護師": os.getenv("CALENDAR_KANGO"),
    "プライベート": os.getenv("CALENDAR_PRIVATE"),
}

CALENDAR_CHOICES = {
    "1": "副業",
    "2": "看護師",
    "3": "プライベート",
}

print("------ ENV CHECK ------")
print("TOKEN exists:", bool(LINE_CHANNEL_ACCESS_TOKEN))
print("SECRET exists:", bool(LINE_CHANNEL_SECRET))
print("副業ID:", CALENDAR_IDS["副業"])
print("看護師ID:", CALENDAR_IDS["看護師"])
print("プライベートID:", CALENDAR_IDS["プライベート"])
print("SERVICE_ACCOUNT_FILE exists:", os.path.exists(SERVICE_ACCOUNT_FILE))
print("-----------------------")

app = FastAPI()

# ユーザーごとの日時入力待ち
pending_events = {}

# ユーザーごとのカレンダー選択待ち / 重複確認待ち
pending_calendar_selection = {}

# push通知用
last_user_id = None


def reply_message(reply_token: str, text: str):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    response = requests.post(url, headers=headers, json=body)
    print("LINE返信:", response.status_code, response.text)


def push_message(user_id: str, text: str):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    response = requests.post(url, headers=headers, json=body)
    print("LINE push:", response.status_code, response.text)


import json

def get_calendar_service():
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if service_account_json:
        info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=SCOPES
        )
    else:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=SCOPES
        )

    return build("calendar", "v3", credentials=credentials)

def create_event_one_calendar(summary: str, start_dt: datetime, calendar_name: str):
    service = get_calendar_service()
    end_dt = start_dt + timedelta(hours=1)

    calendar_id = CALENDAR_IDS[calendar_name]

    event_body = {
        "summary": summary,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "Asia/Tokyo",
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "Asia/Tokyo",
        },
    }

    created_event = service.events().insert(
        calendarId=calendar_id,
        body=event_body
    ).execute()

    print(f"Googleカレンダー登録成功: {calendar_name} / {created_event.get('id')}")
    return created_event.get("id")


def check_conflict(calendar_name: str, start_dt: datetime):
    """
    指定カレンダー内で同時間帯の予定を取得する
    （前後30分の範囲でチェック）
    """
    service = get_calendar_service()
    calendar_id = CALENDAR_IDS[calendar_name]

    time_min = (start_dt - timedelta(minutes=30)).isoformat()
    time_max = (start_dt + timedelta(minutes=30)).isoformat()

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    return events_result.get("items", [])


def get_events_by_date_from_all_calendars(target_date):
    service = get_calendar_service()

    start_of_day = datetime.combine(target_date, datetime.min.time()).isoformat() + "+09:00"
    end_of_day = datetime.combine(target_date, datetime.max.time()).isoformat() + "+09:00"

    result = {}

    for calendar_name, calendar_id in CALENDAR_IDS.items():
        try:
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=start_of_day,
                timeMax=end_of_day,
                singleEvents=True,
                orderBy="startTime"
            ).execute()

            events = events_result.get("items", [])
            result[calendar_name] = events

        except Exception as e:
            print(f"{calendar_name} の取得エラー:", repr(e))
            result[calendar_name] = []

    return result


def format_grouped_events(target_date, events_dict):
    now = datetime.now().date()

    if target_date == now:
        lines = ["【今日の予定】", ""]
    elif target_date == now + timedelta(days=1):
        lines = ["【明日の予定】", ""]
    else:
        lines = [f"【{target_date.month}月{target_date.day}日の予定】", ""]

    has_events = False

    for calendar_name, events in events_dict.items():
        if events:
            has_events = True
            lines.append(f"📅 {calendar_name}")

            for event in events:
                summary = event.get("summary", "(タイトルなし)")
                start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date"))

                if start and "T" in start:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    time_str = dt.strftime("%H:%M")
                    lines.append(f"  {time_str} {summary}")
                else:
                    lines.append(f"  終日 {summary}")

            lines.append("")

    if not has_events:
        lines.append("予定はありません")

    return "\n".join(lines)


def send_schedule_notification(day_offset: int):
    global last_user_id

    if not last_user_id:
        print("通知先ユーザーIDがまだありません")
        return

    target_date = (datetime.now() + timedelta(days=day_offset)).date()
    events_dict = get_events_by_date_from_all_calendars(target_date)
    message = format_grouped_events(target_date, events_dict)

    push_message(last_user_id, message)
    print(f"通知送信完了 day_offset={day_offset}")


@app.get("/")
def root():
    return {"message": "DailyFlow server running"}


@app.post("/callback")
async def callback(request: Request):
    try:
        body = await request.json()

        print("LINEから受信:")
        print(body)

        events = body.get("events", [])
        if not events:
            return "ok"

        event = events[0]

        if event["type"] == "message" and event["message"]["type"] == "text":
            text = event["message"]["text"].strip()
            reply_token = event["replyToken"]
            user_id = event["source"]["userId"]

            global last_user_id
            last_user_id = user_id

            print("受信メッセージ:", text)

            # -------------------------
            # 重複確認待ち
            # -------------------------
            if user_id in pending_calendar_selection and pending_calendar_selection[user_id].get("awaiting_conflict_confirmation"):
                pending = pending_calendar_selection[user_id]

                if text == "1":
                    try:
                        create_event_one_calendar(
                            pending["title"],
                            pending["dt"],
                            pending["calendar"]
                        )
                    except Exception as cal_error:
                        print("Googleカレンダー登録エラー:", repr(cal_error))
                        reply_message(
                            reply_token,
                            f"予定登録でエラーが出ました\n{cal_error}"
                        )
                        del pending_calendar_selection[user_id]
                        return "ok"

                    event_data = {
                        "date": pending["date"],
                        "time": pending["time"],
                        "title": pending["title"],
                        "calendar": pending["calendar"],
                    }

                    data = load_data()
                    data.append(event_data)
                    save_data(data)

                    reply_text = (
                        f"予定を登録しました\n"
                        f"{event_data['date']} {event_data['time']}\n"
                        f"{event_data['title']}\n"
                        f"登録先: {event_data['calendar']}"
                    )
                    reply_message(reply_token, reply_text)
                    del pending_calendar_selection[user_id]
                    return "ok"

                if text == "2":
                    reply_message(reply_token, "登録をキャンセルしました")
                    del pending_calendar_selection[user_id]
                    return "ok"

                reply_message(reply_token, "1: はい / 2: キャンセル で入力してください")
                return "ok"

            # -------------------------
            # カレンダー選択待ち
            # -------------------------
            if user_id in pending_calendar_selection and not pending_calendar_selection[user_id].get("awaiting_conflict_confirmation"):
                if text not in CALENDAR_CHOICES:
                    reply_message(
                        reply_token,
                        "登録先を選んでください\n1: 副業\n2: 看護師\n3: プライベート"
                    )
                    return "ok"

                selected_calendar = CALENDAR_CHOICES[text]
                pending = pending_calendar_selection[user_id]

                # 重複チェック
                try:
                    conflicts = check_conflict(selected_calendar, pending["dt"])
                except Exception as conflict_error:
                    print("重複チェックエラー:", repr(conflict_error))
                    reply_message(
                        reply_token,
                        f"重複チェックでエラーが出ました\n{conflict_error}"
                    )
                    del pending_calendar_selection[user_id]
                    return "ok"

                if conflicts:
                    lines = ["⚠️ 同じ時間帯に予定があります", ""]
                    for e in conflicts:
                        summary = e.get("summary", "(タイトルなし)")
                        start = e.get("start", {}).get("dateTime", "")
                        if start:
                            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                            time_str = dt.strftime("%H:%M")
                            lines.append(f"{time_str} {summary}")

                    lines.append("")
                    lines.append("それでも登録しますか？")
                    lines.append("1: はい")
                    lines.append("2: キャンセル")

                    pending_calendar_selection[user_id]["calendar"] = selected_calendar
                    pending_calendar_selection[user_id]["awaiting_conflict_confirmation"] = True

                    reply_message(reply_token, "\n".join(lines))
                    return "ok"

                # 重複なしならそのまま登録
                event_data = {
                    "date": pending["date"],
                    "time": pending["time"],
                    "title": pending["title"],
                    "calendar": selected_calendar,
                }

                data = load_data()
                data.append(event_data)
                save_data(data)

                try:
                    create_event_one_calendar(
                        pending["title"],
                        pending["dt"],
                        selected_calendar
                    )
                except Exception as cal_error:
                    print("Googleカレンダー登録エラー:", repr(cal_error))
                    reply_message(
                        reply_token,
                        f"タイトルは受け取りましたが、Googleカレンダー登録でエラーが出ました\n{cal_error}"
                    )
                    del pending_calendar_selection[user_id]
                    return "ok"

                reply_text = (
                    f"予定を登録しました\n"
                    f"{event_data['date']} {event_data['time']}\n"
                    f"{event_data['title']}\n"
                    f"登録先: {selected_calendar}"
                )
                reply_message(reply_token, reply_text)

                del pending_calendar_selection[user_id]
                return "ok"

            # -------------------------
            # タイトル入力待ち
            # -------------------------
            if user_id in pending_events:
                pending = pending_events[user_id]
                title = text

                pending_calendar_selection[user_id] = {
                    "dt": pending["dt"],
                    "date": pending["date"],
                    "time": pending["time"],
                    "title": title,
                    "awaiting_conflict_confirmation": False,
                }

                reply_message(
                    reply_token,
                    "登録先を選んでください\n1: 副業\n2: 看護師\n3: プライベート"
                )

                del pending_events[user_id]
                return "ok"

            # -------------------------
            # 今日の予定
            # -------------------------
            if text == "今日の予定":
                today = datetime.now().date()
                events_today = get_events_by_date_from_all_calendars(today)
                reply_text = format_grouped_events(today, events_today)
                reply_message(reply_token, reply_text)
                return "ok"

            # -------------------------
            # 明日の予定
            # -------------------------
            if text == "明日の予定":
                tomorrow = (datetime.now() + timedelta(days=1)).date()
                events_tomorrow = get_events_by_date_from_all_calendars(tomorrow)
                reply_text = format_grouped_events(tomorrow, events_tomorrow)
                reply_message(reply_token, reply_text)
                return "ok"

            # -------------------------
            # 日付指定の予定
            # 例: 3/10の予定
            # -------------------------
            match = re.match(r"(\d{1,2})/(\d{1,2})の予定", text)
            if match:
                month = int(match.group(1))
                day = int(match.group(2))

                now = datetime.now()
                year = now.year
                target = datetime(year, month, day).date()

                if target < now.date():
                    year += 1
                    target = datetime(year, month, day).date()

                events_target = get_events_by_date_from_all_calendars(target)
                reply_text = format_grouped_events(target, events_target)
                reply_message(reply_token, reply_text)
                return "ok"

            # -------------------------
            # 通知テスト
            # -------------------------
            if text == "朝通知テスト":
                send_schedule_notification(0)
                reply_message(reply_token, "朝通知をpush送信しました")
                return "ok"

            if text == "夜通知テスト":
                send_schedule_notification(1)
                reply_message(reply_token, "夜通知をpush送信しました")
                return "ok"

            # -------------------------
            # 日時抽出 → タイトル待ちへ
            # -------------------------
            try:
                res = extract_datetime(text)

                date_str = res.dt.strftime("%Y-%m-%d")
                time_str = res.dt.strftime("%H:%M")

                pending_events[user_id] = {
                    "dt": res.dt,
                    "date": date_str,
                    "time": time_str
                }

                dt_obj = res.dt
                reply_text = (
                    f"{dt_obj.month}月{dt_obj.day}日 {dt_obj.hour}時{dt_obj.minute:02d}分ですね。\n"
                    f"予定名を入力してください。"
                )
                reply_message(reply_token, reply_text)
                return "ok"

            except Exception as parse_error:
                print("日時抽出エラー:", repr(parse_error))
                reply_message(reply_token, "日時を読み取れませんでした")
                return "ok"

        return "ok"

    except Exception as e:
        print("callbackエラー:", repr(e))
        raise


# ==========================
# スケジューラー
# ==========================
scheduler = BackgroundScheduler(timezone="Asia/Tokyo")

# 朝6:00 今日の予定
scheduler.add_job(lambda: send_schedule_notification(0), "cron", hour=6, minute=0)

# 夜21:00 明日の予定
scheduler.add_job(lambda: send_schedule_notification(1), "cron", hour=21, minute=0)

scheduler.start()
print("通知スケジューラー開始: 6:00 今日 / 21:00 明日")
# -*- coding: utf-8 -*-
"""
公式LINEの友だち数・ブロック数を LINE Messaging API から取って line/line_<アカウント>.json に書く。

Threads運用ダッシュボード（index.html）が funnel_<アカウント>.json に重ねて読む。
funnel_*.json は Mac 側の更新で丸ごと書き換わるので、LINEの数字は別ファイルに分けている。

使うAPI: GET https://api.line.me/v2/bot/insight/followers?date=YYYYMMDD
  followers … 友だち追加された数（ブロックした人も含む累計）
  blocks    … ブロックされている数
  targetedReaches … 属性が推定できてメッセージが届く人数（参考）
  前日分まで取れる。当日分は無い。

トークン（チャネルアクセストークン）は GitHub の Secrets に置く。リポジトリに書かないこと。
  LINE_TOKEN_A … 属人アカウント（クラウドワークス裏側）用の公式LINE（「いけ」から始まるもの）

実行: LINE_TOKEN_A=xxx python3 line_insight.py      … 前回の続きから昨日までを取る
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
BASE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SITE_DIR") or os.path.join(BASE, "..")
START = "2026-08-01"          # ファイルが無いときに、ここからさかのぼって取る
ACCOUNTS = {"A": "LINE_TOKEN_A"}


def insight(token, day):
    url = "https://api.line.me/v2/bot/insight/followers?date=" + day.strftime("%Y%m%d")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def run(key, token):
    out = os.path.join(SITE, "line", "line_%s.json" % key)
    try:
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"history": []}
    have = {h["date"] for h in data["history"]}
    yesterday = (datetime.now(JST) - timedelta(days=1)).date()
    # 直近3日は数字が後から確定することがあるので取り直す
    day = min(datetime.strptime(START, "%Y-%m-%d").date(), yesterday)
    added = 0
    while day <= yesterday:
        iso = day.isoformat()
        if iso not in have or day >= yesterday - timedelta(days=2):
            try:
                j = insight(token, day)
            except urllib.error.HTTPError as e:
                print(key, iso, "取得できませんでした", e.code)
                day += timedelta(days=1)
                continue
            if j.get("status") == "ready" and j.get("followers") is not None:
                data["history"] = [h for h in data["history"] if h["date"] != iso]
                data["history"].append({"date": iso, "followers": j["followers"],
                                        "blocks": j.get("blocks"),
                                        "targetedReaches": j.get("targetedReaches")})
                added += 1
        day += timedelta(days=1)
    # LINE ID（@から始まる）。診断ページの「相談する」ボタンが、この公式LINEのトークを開くのに使う
    try:
        req = urllib.request.Request("https://api.line.me/v2/bot/info", headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=30) as r:
            info = json.load(r)
        data["basicId"] = info.get("basicId")
        data["displayName"] = info.get("displayName")
    except urllib.error.HTTPError as e:
        print(key, "LINE IDを取れませんでした", e.code)
    data["history"].sort(key=lambda h: h["date"])
    data["generated_at"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    last = data["history"][-1] if data["history"] else {}
    print(key, "更新", added, "日分 / 最新", last)


def main():
    ran = False
    for key, env in ACCOUNTS.items():
        token = os.environ.get(env, "").strip()
        if not token:
            print(key, "のトークン（%s）が未登録なので飛ばします" % env)
            continue
        run(key, token)
        ran = True
    if not ran:
        print("トークンが1つも登録されていません")


if __name__ == "__main__":
    main()

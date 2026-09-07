# ── GitHub Actions 用に自動変換したファイル ──────────────
# 元ファイル: dm_export.py
#
# 直接編集しないこと。元ファイルを直してから変換をやり直す。
# シートIDは scripts/sheets.json から読む（リポジトリには入れない）。
# -*- coding: utf-8 -*-
"""
InstagramDM 稼働量ダッシュボードのデータを作って GitHub Pages へ反映する。

2026-08-26、GAS（dm_dashboard_gas.js）とブラウザ経由の定期タスクをやめて
これ1本にした。理由:
  - GASは Apps Script 側にトリガーを入れないと動かず、実際8/24を最後に止まっていた
  - ブラウザ経由の定期タスクは Mac が起きていてアプリも動いていないと発火しない
  - 対象のスプレッドシートは認証なしで gviz から読めることが分かった
    （curl で 200 が返る）ので、Python だけで完結できる

読むもの:
  池田 / 松本 の「Instagram DM管理」… 日別タブ(1〜31)。3行目からデータで
      B=アカウント名 C=新規DM数 D=リスト E=返信(見込み) F=返信(見込み外) G=アポ取り数
      末尾に「合計」行があるので必ず除外する（入れると2倍になる）
  AGシートの「インフルエンサーDM」… 1件1行のアポ台帳
      A面談日 B時間 C経由 Dアカウント名 … H前日確認 I着席 J契約 K定着

実行: python3 dm_export.py            … 書き出して push
      python3 dm_export.py --dry      … 中身を表示するだけ
"""

import csv
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
BASE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SITE_DIR") or os.path.join(BASE, "kpi_dashboard_site")
OUT = os.path.join(SITE, "dm", "dm_data.json")

# DM管理シートは人ごと・月ごとにファイルが分かれる。
# 月が替わったら、その人の行に ("2026-10", "新しいID") を足すだけでよい。
# その月のシートが無い人は、その月は0件として扱う（黙って前月を見にいかない）。

# ── 設定の読み込み ──────────────────────────
# スプレッドシートIDは公開リポジトリに置けないので、別ファイルから読む。
# このファイルは .gitignore に入れてある。
# Actions では Secrets の SHEETS_JSON から実行時に書き出される。
def _sheets():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.environ.get("SHEETS_JSON_PATH") or os.path.join(here, "sheets.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(
            "設定ファイルが見つかりません: %s\n"
            "ローカルなら scripts/sheets.json を置いてください。\n"
            "Actions なら Secrets の SHEETS_JSON を確認してください。" % path)


SHEETS = _sheets()

PEOPLE = [
    {"key": "ikeda", "name": "池田", "sheets": {
        "2026-08": SHEETS["dm_ikeda_2026-08"],
        "2026-09": SHEETS["dm_ikeda_2026-09"],
    }},
    # 谷村は対象外（2026-09-07 本人指示）
    {"key": "matsumoto", "name": "松本", "sheets": {
        "2026-09": SHEETS["dm_matsumoto_2026-09"],
    }},
]
# アポ・着座・契約を数えるAGシート。月ごとにファイルが分かれるので、
# 月が替わったらここに1行足す。8月に固定されていて9月が0件になっていた
# （2026-09-07 修正）。
FUNNEL_SHEETS = {
    "2026-08": SHEETS["ag_2026-08"],
    "2026-09": SHEETS["ag_2026-09"],
}
FUNNEL_TAB = "インフルエンサーDM"


def funnel_id(now):
    ym = now.strftime("%Y-%m")
    if ym in FUNNEL_SHEETS:
        return FUNNEL_SHEETS[ym]
    print("★%s のAGシートが未登録です。%s のシートで数えます"
          % (ym, max(FUNNEL_SHEETS)))
    return FUNNEL_SHEETS[max(FUNNEL_SHEETS)]

# 日別タブの列（0始まり）
C_ACCOUNT, C_DM, C_LIST, C_REPLY1, C_REPLY2, C_APPT = 1, 2, 3, 4, 5, 6
# 見出しが1行の日と2行の日が混在している（19/21/22日は1行、他は2行）。
# 「先頭N行を飛ばす」と決め打ちすると、1行の日は先頭のアカウントを丸ごと落とす。
# 実際それで19/21/22日が10件ずつ足りなくなっていた。行の中身で判定する。
HEADER_WORDS = ("担当者", "アカウント名", "見込み", "返信数", "返信率", "")

# インフルエンサーDMタブの列（0始まり）
F_DATE, F_VIA, F_SAT, F_CONTRACT = 0, 2, 8, 9


def fetch(sheet_id, tab):
    url = ("https://docs.google.com/spreadsheets/d/%s/gviz/tq"
           "?tqx=out:csv&sheet=%s&headers=0" % (sheet_id, urllib.parse.quote(str(tab))))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return list(csv.reader(io.StringIO(r.read().decode("utf-8", "replace"))))


def num(v):
    s = re.sub(r"[,，\s件%]", "", str(v or ""))
    try:
        return float(s)
    except ValueError:
        return 0


def read_person(sheet_id):
    daily, accounts = [], {}
    for day in range(1, 32):
        s = {"day": day, "dm": 0, "list": 0, "reply": 0, "appt": 0}
        try:
            rows = fetch(sheet_id, day)
        except Exception:
            daily.append(s)
            continue
        total_row = None
        for row in rows:
            if len(row) <= C_APPT:
                row = row + [""] * (C_APPT + 1 - len(row))
            if str(row[0]).strip() == "合計" or str(row[C_ACCOUNT]).strip() == "合計":
                total_row = row
                continue
            acc = str(row[C_ACCOUNT]).strip()
            dm, li = num(row[C_DM]), num(row[C_LIST])
            nums = dm + li + num(row[C_REPLY1]) + num(row[C_REPLY2]) + num(row[C_APPT])
            # 見出しだけの行は飛ばす。ただし見出しの文言のまま数字が入っている行が
            # 実在する（谷村さんのシートは全日そう）ので、数字があれば拾う。
            if acc in HEADER_WORDS and nums == 0:
                continue
            # アカウント名が空でも数字が入っている行がある（谷村さんのシートは
            # 名前列がまるごと空、池田さんも一部の日で1行だけ空）。名前が無いから
            # と落とすと日次合計が足りなくなるので、合計には必ず入れて、
            # 内訳一覧にだけ「(名前なし)」としてまとめる。
            if acc in HEADER_WORDS:
                acc = "(名前なし)"
            rep = num(row[C_REPLY1]) + num(row[C_REPLY2])
            ap = num(row[C_APPT])
            s["dm"] += dm; s["list"] += li; s["reply"] += rep; s["appt"] += ap
            a = accounts.setdefault(acc, {"account": acc, "genre": "",
                                          "dm": 0, "list": 0, "reply": 0, "appt": 0})
            a["dm"] += dm; a["list"] += li; a["reply"] += rep; a["appt"] += ap
        # シートの合計行と自分の集計がズレたら黙って通さない
        if total_row is not None:
            t = num(total_row[C_DM])
            if abs(t - s["dm"]) > 0.5:
                print("  ※%d日: シートの合計%.0f と 積み上げ%.0f がズレています"
                      % (day, t, s["dm"]))
        daily.append(s)
    for v in accounts.values():
        for k in ("dm", "list", "reply", "appt"):
            v[k] = int(v[k])
    for d in daily:
        for k in ("dm", "list", "reply", "appt"):
            d[k] = int(d[k])
    return daily, sorted(accounts.values(), key=lambda x: x["account"])


def month_of(v):
    s = str(v or "").strip()
    m = re.search(r"(\d{4})\s*[/\-年]\s*(\d{1,2})", s)
    if m:
        return int(m.group(2))
    m = re.search(r"(\d{1,2})\s*[/\-月]\s*(\d{1,2})", s)
    return int(m.group(1)) if m else 0


def yes(v):
    return str(v or "").strip().lower() in ("true", "はい", "○", "◯", "1", "✓")


def read_funnel(month, now):
    out = {}
    try:
        rows = fetch(funnel_id(now), FUNNEL_TAB)
    except Exception as e:
        print("★営業シートを読めませんでした: %s" % str(e)[:80])
        return out
    for row in rows[1:]:
        if len(row) <= F_CONTRACT:
            continue
        if not str(row[F_DATE]).strip() or month_of(row[F_DATE]) != month:
            continue
        via = str(row[F_VIA]).strip()
        if not via:
            continue
        f = out.setdefault(via, {"appt": 0, "sat": 0, "contract": 0})
        f["appt"] += 1
        if yes(row[F_SAT]):
            f["sat"] += 1
        if yes(row[F_CONTRACT]):
            f["contract"] += 1
    return out


def main():
    now = datetime.now(JST)
    funnel = read_funnel(now.month, now)
    ym = now.strftime("%Y-%m")
    people = {}
    for p in PEOPLE:
        sid = p["sheets"].get(ym)
        if not sid:
            print("読み取り中: %s → %s のシートが未登録。0件として出します" % (p["name"], ym))
            people[p["key"]] = {"name": p["name"],
                                "daily": [{"day": d, "dm": 0, "list": 0, "reply": 0, "appt": 0}
                                          for d in range(1, 32)],
                                "accounts": [],
                                "funnel": funnel.get(p["name"], {"appt": 0, "sat": 0, "contract": 0})}
            continue
        print("読み取り中: %s（%s）" % (p["name"], ym))
        daily, accounts = read_person(sid)
        people[p["key"]] = {"name": p["name"], "daily": daily, "accounts": accounts,
                            "funnel": funnel.get(p["name"], {"appt": 0, "sat": 0, "contract": 0})}
    data = {"generated_at": now.strftime("%Y-%m-%d %H:%M"),
            "month": now.strftime("%Y-%m"), "people": people}

    for k, p in people.items():
        f = p["funnel"]
        print("  %s: DM%d件 / 返信%d / アポ%d・着座%d・契約%d"
              % (p["name"], sum(d["dm"] for d in p["daily"]),
                 sum(d["reply"] for d in p["daily"]),
                 f["appt"], f["sat"], f["contract"]))

    if "--dry" in sys.argv:
        print("（--dry のため書き出していません）")
        return

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("書き出し: %s" % OUT)

    if os.environ.get("GITHUB_ACTIONS"):
        print("Actions上なのでpushはワークフローに任せます")
        return

    # 同じリポジトリに他のジョブも push するので、必ず先に取り込んでから出す
    def git(*a):
        return subprocess.run(["git"] + list(a), cwd=SITE,
                              capture_output=True, text=True)
    git("fetch", "origin", "main")
    git("pull", "--rebase", "origin", "main")
    git("add", "dm/dm_data.json")
    r = git("commit", "-m", "update dm %s" % data["generated_at"])
    if r.returncode != 0:
        print("変更なし（前回から差分なし）")
        return
    r = git("push", "origin", "main")
    print("公開しました" if r.returncode == 0 else "★pushに失敗: " + r.stderr[:200])


if __name__ == "__main__":
    main()

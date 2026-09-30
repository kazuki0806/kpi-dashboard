# -*- coding: utf-8 -*-
"""
「売上と歩留まり」ページの週次・月次の数字（sales/kpi_daily.json）を作る。

Addness の KPI（営業/CW フォルダ）と同じ数え方にそろえている。
数え方を変えるときは、Addness のゴール
「CW営業の面談数・成約率・単価を毎週見られるようにする」の本文も直すこと。

読むもの（AGシート。月ごとにファイルが分かれる。IDは sheets.json の ag_YYYY-MM）
  営業(CWトスアップ) … 1件1行の台帳。5行目からデータ
      I=ステータス J=アポ取り日 K=トスアップ予定 M=トスアップ実際
      N=アポ予定 P=アポ実際 S=プレ実際 V=契約実際
  契約者 … 2行目からデータ
      C=コース G=種別 P=契約日 V=キャンセル日

日ごとに数えるもの（どの日付で数えるか）
  scheduled … 1回目の面談の予定（トスアップ予定日）。ステータス「アポ被り」は除く
  seated    … 着座（トスアップ実際の日）
  handoff   … 着座してクローザーのアポ予定が入った件数（トスアップ実際の日）
  pre       … プレ実施（プレ実際の日）
  noshow    … ステータス「トスアップ飛び」（トスアップ予定日）
  contracts … 契約者タブ・種別=CWトスアップ・キャンセル日なし（契約日）
  sales     … 同じ行のコース金額（税込。45万→495,000 など）の合計
  threads_contracts … 契約者タブ・種別にthreadsを含む（契約日）

個人名は出力しない（日ごとの件数と金額だけ）。
読めた月の日付だけを書き換え、それ以外の日（過去の月）は前回の値を残す。
7〜9月の値は、2026-09-30 に Addness へ入れたのと同じ値で始めている。

実行: python3 sales_kpi_export.py          … 書き出す
      python3 sales_kpi_export.py --dry    … 月の合計を表示するだけ
"""

import csv
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
BASE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SITE_DIR") or os.path.join(BASE, "..")
OUT = os.path.join(SITE, "sales", "kpi_daily.json")

METRICS = ["scheduled", "seated", "handoff", "pre", "noshow",
           "contracts", "sales", "threads_contracts"]
TARGET_MONTHLY_SALES = 10000000  # 月1000万（Addnessのゴールの目標）

# 営業(CWトスアップ) の列（0始まり）
L_STATUS, L_BOOKED, L_TU_PLAN, L_TU_DONE = 8, 9, 10, 12
L_APO_PLAN, L_APO_DONE, L_PRE_DONE, L_CON_DONE = 13, 15, 18, 21
L_FIRST_ROW = 4
# 契約者 の列（0始まり）
K_COURSE, K_KIND, K_DATE, K_CANCEL = 2, 6, 15, 21


def _sheets():
    path = os.environ.get("SHEETS_JSON_PATH") or os.path.join(BASE, "sheets.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit("設定ファイルが見つかりません: %s" % path)


def fetch(sheet_id, tab):
    url = ("https://docs.google.com/spreadsheets/d/%s/gviz/tq"
           "?tqx=out:csv&sheet=%s&headers=0" % (sheet_id, urllib.parse.quote(str(tab))))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return list(csv.reader(io.StringIO(r.read().decode("utf-8", "replace"))))


def to_date(v, ym):
    """シートの日付（2026/09/03、2026-09-03 10:00:00、9/3、9月3日 など）を date に。"""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    m = re.match(r"(\d{4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"(\d{1,2})[/月](\d{1,2})", s)
        if not m:
            return None
        y = int(ym[:4])
        mo, d = map(int, m.groups())
        # 1月のファイルに入った12月の日付などは前の年にする
        if mo - int(ym[5:7]) > 6:
            y -= 1
        elif int(ym[5:7]) - mo > 6:
            y += 1
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def course_yen(course):
    """「CWスタンダード(60万)」→ 660,000（税込）。金額が読めなければ 0。"""
    m = re.search(r"([\d.]+)\s*万", str(course or ""))
    return int(round(float(m.group(1)) * 10000 * 1.1)) if m else 0


def cell(row, i):
    return row[i] if i < len(row) else ""


def count_month(ledger, contracts, ym):
    """1か月分の台帳から、日付→指標の件数を作る。"""
    out = {}

    def add(d, key, n=1):
        if d is None:
            return
        out.setdefault(d.isoformat(), dict.fromkeys(METRICS, 0))[key] += n

    for row in ledger[L_FIRST_ROW:]:
        status = str(cell(row, L_STATUS)).strip()
        plan = to_date(cell(row, L_TU_PLAN), ym)
        done = to_date(cell(row, L_TU_DONE), ym)
        if plan and status != "アポ被り":
            add(plan, "scheduled")
        if status == "トスアップ飛び":
            add(plan, "noshow")
        if done:
            add(done, "seated")
            if to_date(cell(row, L_APO_PLAN), ym):
                add(done, "handoff")
        add(to_date(cell(row, L_PRE_DONE), ym), "pre")

    for row in contracts[1:]:
        kind = str(cell(row, K_KIND)).strip()
        d = to_date(cell(row, K_DATE), ym)
        if not d or str(cell(row, K_CANCEL)).strip():
            continue
        if kind == "CWトスアップ":
            add(d, "contracts")
            add(d, "sales", course_yen(cell(row, K_COURSE)))
        elif "threads" in kind.lower():
            add(d, "threads_contracts")
    # その月の日付だけを返す（前後の月は、その月のファイルを正とする）
    return {k: v for k, v in out.items() if k.startswith(ym)}


def main():
    dry = "--dry" in sys.argv
    now = datetime.now(JST)
    sheets = _sheets()
    months = sorted(k[3:] for k in sheets if re.fullmatch(r"ag_\d{4}-\d{2}", k))
    # 締まっていない今月と先月だけ読み直す（それより前は前回の値を残す）
    months = [m for m in months if m >= (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")]
    if now.strftime("%Y-%m") not in months:
        print("★%s のAGシートが sheets.json に未登録です（ag_%s を足してください）"
              % (now.strftime("%Y-%m"), now.strftime("%Y-%m")))

    try:
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"days": {}}

    for ym in months:
        sid = sheets["ag_%s" % ym]
        try:
            ledger = fetch(sid, "営業(CWトスアップ)")
            contracts = fetch(sid, "契約者")
        except Exception as e:
            print("%s のAGシートを読めませんでした: %s（前回の値を残します）" % (ym, e))
            continue
        fresh = count_month(ledger, contracts, ym)
        if not fresh:
            print("%s は1件も数えられませんでした。列がずれていないか確認してください" % ym)
            continue
        # その月の日付を全部入れ替える（0件の日は0で埋める）
        first = date(int(ym[:4]), int(ym[5:7]), 1)
        last = min((first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1),
                   now.date())
        d = first
        while d <= last:
            data["days"][d.isoformat()] = fresh.get(d.isoformat(), dict.fromkeys(METRICS, 0))
            d += timedelta(days=1)
        tot = {k: sum(v[k] for kk, v in data["days"].items() if kk.startswith(ym)) for k in METRICS}
        print(ym, tot)

    data["generated_at"] = now.strftime("%Y-%m-%d %H:%M")
    data["target_monthly_sales"] = TARGET_MONTHLY_SALES
    data["metrics"] = METRICS
    data["days"] = dict(sorted(data["days"].items()))
    if dry:
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0, separators=(",", ":"))
    print("書き出しました:", OUT)


if __name__ == "__main__":
    main()

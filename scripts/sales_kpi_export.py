# -*- coding: utf-8 -*-
"""
「売上と歩留まり」ページの週次・月次の数字（sales/kpi_daily.json）を作る。

Addness の KPI（営業/CW フォルダ）と同じ数え方にそろえている。
数え方を変えるときは、Addness のゴール
「CW営業の面談数・成約率・単価を毎週見られるようにする」の本文も直すこと。

読むもの（AGシート。月ごとにファイルが分かれる。IDは sheets.json の ag_YYYY-MM）
  営業(CWトスアップ) … 1件1行の台帳。5行目からデータ
      列の位置は月によって違う（7月は「リストイン」「ランク」の列がある）ので、
      4行目の見出し（ステータス・トスアップ・クローザー）と、
      1行目の段（トスアップ・アポ・プレ）の「予定／時間／実際」から探す。
  契約者 … 1行目の見出し（コース・種別・トスアップ・担当・契約日・キャンセル日）から探す。2行目からデータ

日ごとに数えるもの（どの日付で数えるか）
  scheduled … 1回目の面談の予定（トスアップ予定日）。ステータス「アポ被り」は除く
  seated    … 着座（トスアップ実際の日）
  handoff   … 着座してクローザーのアポ予定が入った件数（トスアップ実際の日）
  pre       … プレ実施（プレ実際の日）
  noshow    … ステータス「トスアップ飛び」（トスアップ予定日）
  contracts … 契約者タブ・種別=CWトスアップ・キャンセル日なし（契約日）
  sales     … 同じ行のコース金額（税込。45万→495,000 など）の合計
  threads_contracts … 契約者タブ・種別にthreadsを含む（契約日）

担当ごと・コース別の内訳（--points で書き出す。Addness の KPI に入れる用）
  tossup … トスアップ担当（台帳の「トスアップ」、契約は契約者タブの「トスアップ」）
  closer … クローザー（プレは台帳の「クローザー」、契約は契約者タブの「担当」）
  course … ライト／スタンダード／プレミアム（契約者タブの「コース」）
  case_type … 案件の種類（動画編集／SNS運用／デザイン）。台帳の見出し「案件の種類」の列（シートのGASが
              TimeRexの予約から書く。AF列）。契約は、契約者タブの「クライアント名」と同じ名前の台帳の行の種類
              （前の月の台帳の行も見る）。台帳にこの列が無い月は case_type の内訳を作らない
  空欄は「未記入」にする。当月累計の率も作る（close_rate_mtd・pre_close_rate_mtd・avg_price_mtd）。
  内訳には担当者の名前が入るので、--points の書き出し先はリポジトリの外にする（公開しない）。

個人名は出力しない（日ごとの件数と金額だけ）。
読めた月の日付だけを書き換え、それ以外の日（過去の月）は前回の値を残す。
7〜9月の値は、2026-09-30 に Addness へ入れたのと同じ値で始めている。

実行: python3 sales_kpi_export.py          … 書き出す（sheets.json のIDを gviz で読む）
      python3 sales_kpi_export.py --dry    … 月の合計を表示するだけ
      python3 sales_kpi_export.py --xlsx 2026-10=ag10.xlsx [--xlsx 2026-09=ag9.xlsx]
          … Drive から書き出した xlsx を読む（毎日の定期実行はこれを使う。
            gviz に届かない環境でも動く。openpyxl が要る）
      --points DIR [--from YYYY-MM-DD] [--to YYYY-MM-DD]
          … Addness に入れる点（合計＋内訳）を KPI ごとに DIR/<キー>.json と
            DIR/<キー>.parquet に書き出す（pyarrow が要る）。期間の既定は、
            読んだ月の初日から昨日まで。DIR はリポジトリの外にすること。
            DIR/summary.json に、点の数・期間と、証憑ごとの期間・ファイル名・size_bytes・checksum が入る。
            証憑の期間は evidence_block() の固定の区切り（Addness が部分的に重なる証憑を受け付けないため）
      --dry と一緒に使うと、kpi_daily.json は書き換えずに点だけ書き出す
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

L_FIRST_ROW = 4   # 台帳のデータは5行目から
BLANK = "未記入"
# 内訳のキー（Addness の KPI の declared_dims と同じ名前）
DIMS = {
    "scheduled": ["tossup", "case_type"], "seated": ["tossup", "case_type"],
    "handoff": ["tossup", "case_type"], "noshow": ["tossup", "case_type"],
    "pre": ["tossup", "closer"],
    "contracts": ["tossup", "closer", "course", "case_type"],
    "sales": ["tossup", "closer", "course", "case_type"],
    "threads_contracts": [],
}
# 当月累計の率（分子, 分母, 倍率, 内訳のキー）
RATES = {
    "close_rate_mtd": ("contracts", "seated", 100, ["tossup", "case_type"]),  # 着座→契約 %
    "pre_close_rate_mtd": ("contracts", "pre", 100, ["closer"]),       # プレ→契約 %
    "avg_price_mtd": ("sales", "contracts", 1, ["closer"]),            # 平均単価 円
}


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


def read_xlsx(path, tab):
    """xlsx のタブを、gviz の CSV と同じ「文字列の行のリスト」にする。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for r in wb[tab].iter_rows(values_only=True):
        out.append([c.strftime("%Y/%m/%d %H:%M:%S") if isinstance(c, datetime)
                    else ("" if c is None else str(c)) for c in r])
    return out


def cell(row, i):
    return row[i] if i < len(row) else ""


def _norm(v):
    return re.sub(r"\s+", "", str(v or ""))


def _find(head, name, what):
    for j, c in enumerate(head):
        if _norm(c) == name:
            return j
    raise ValueError("%sに「%s」の見出しがありません" % (what, name))


def ledger_cols(ledger):
    """台帳の列の位置を見出しから探す（月によって列が足し引きされるため）。"""
    head, groups = ledger[L_FIRST_ROW - 1], ledger[0]

    def stage(name):
        for j, c in enumerate(groups):
            if (_norm(c) == name and _norm(cell(head, j)) == "予定"
                    and _norm(cell(head, j + 2)) == "実際"):
                return j
        raise ValueError("台帳に「%s」の予定／実際の列がありません" % name)

    return {
        "status": _find(head, "ステータス", "台帳"),
        "tossup": _find(head, "トスアップ", "台帳"),
        "closer": _find(head, "クローザー", "台帳"),
        "tu_plan": stage("トスアップ"), "tu_done": stage("トスアップ") + 2,
        "apo_plan": stage("アポ"), "pre_done": stage("プレ") + 2,
        "name": 0,
        "case_type": next((j for j, c in enumerate(head) if _norm(c) == "案件の種類"), None),
    }


def contract_cols(contracts):
    head = contracts[0]
    return {k: _find(head, n, "契約者タブ") for k, n in [
        ("course", "コース"), ("kind", "種別"), ("tossup", "トスアップ"),
        ("closer", "担当"), ("date", "契約日"), ("cancel", "キャンセル日"),
        ("client", "クライアント名")]}


# 顧客名 → 案件の種類（台帳から。月をまたいだ契約に使うので、古い月から順に読んで足していく）
NAME_TYPE = {}


def _name_key(v):
    return re.sub(r"[\s　・.\-_|｜()（）]|さん$|様$", "", str(v or "")).lower()


def course_name(course):
    s = str(course or "")
    for k in ("ライト", "スタンダード", "プレミアム"):
        if k in s:
            return k
    return "その他"


def count_month(ledger, contracts, ym, breakdown=None):
    """1か月分の台帳から、日付→指標の件数を作る。
    breakdown（dict）を渡すと、日付→指標→内訳キー→値→件数 もそこへ足す。"""
    out = {}
    L, K = ledger_cols(ledger), contract_cols(contracts)

    def add(d, key, n=1, dims=None):
        if d is None or not d.isoformat().startswith(ym):
            return  # その月の日付だけ（前後の月は、その月のファイルを正とする）
        out.setdefault(d.isoformat(), dict.fromkeys(METRICS, 0))[key] += n
        if breakdown is not None:
            day = breakdown.setdefault(d.isoformat(), {})
            for dk in DIMS[key]:
                if dk == "case_type" and "case_type" not in (dims or {}):
                    continue  # 台帳に「案件の種類」の列が無い月は内訳を作らない
                v = str((dims or {}).get(dk) or "").strip() or BLANK
                b = day.setdefault(key, {}).setdefault(dk, {})
                b[v] = b.get(v, 0) + n

    for row in ledger[L_FIRST_ROW:]:
        status = str(cell(row, L["status"])).strip()
        plan = to_date(cell(row, L["tu_plan"]), ym)
        done = to_date(cell(row, L["tu_done"]), ym)
        who = {"tossup": cell(row, L["tossup"]), "closer": cell(row, L["closer"])}
        if L["case_type"] is not None:
            who["case_type"] = cell(row, L["case_type"])
            if str(who["case_type"]).strip():
                NAME_TYPE[_name_key(cell(row, L["name"]))] = str(who["case_type"]).strip()
        if plan and status != "アポ被り":
            add(plan, "scheduled", dims=who)
        if status == "トスアップ飛び":
            add(plan, "noshow", dims=who)
        if done:
            add(done, "seated", dims=who)
            if to_date(cell(row, L["apo_plan"]), ym):
                add(done, "handoff", dims=who)
        add(to_date(cell(row, L["pre_done"]), ym), "pre", dims=who)

    for row in contracts[1:]:
        kind = str(cell(row, K["kind"])).strip()
        d = to_date(cell(row, K["date"]), ym)
        if not d or str(cell(row, K["cancel"])).strip():
            continue
        if kind == "CWトスアップ":
            who = {"tossup": cell(row, K["tossup"]), "closer": cell(row, K["closer"]),
                   "course": course_name(cell(row, K["course"]))}
            if L["case_type"] is not None:
                who["case_type"] = NAME_TYPE.get(_name_key(cell(row, K["client"])), "")
            add(d, "contracts", dims=who)
            add(d, "sales", course_yen(cell(row, K["course"])), dims=who)
        elif "threads" in kind.lower():
            add(d, "threads_contracts")
    return out


def build_points(data, breakdown, months, d_from, d_to):
    """Addness の record_analytics_metric_points にそのまま渡せる点を、KPIごとに作る。
    合計（dims なし）は 0 の日も入れる。内訳は 0 でない値だけ（run の期間内で無い値は 0 扱い）。"""
    pts = {k: [] for k in list(METRICS) + list(RATES)}
    days = sorted(k for k in data["days"] if k[:7] in months and d_from <= k <= d_to)
    for day in days:
        tot, br = data["days"][day], breakdown.get(day, {})
        for k in METRICS:
            pts[k].append({"period": day, "value": tot[k]})
            for dk in DIMS[k]:
                for v, n in sorted(br.get(k, {}).get(dk, {}).items()):
                    if n:
                        pts[k].append({"period": day, "value": n, "dims": {dk: v}})
    # 当月累計の率：月初からその日までの合計で割る
    for ym in months:
        acc_t, acc_b = dict.fromkeys(METRICS, 0), {}
        for day in sorted(k for k in data["days"] if k.startswith(ym)):
            for k in METRICS:
                acc_t[k] += data["days"][day][k]
            for k, by in breakdown.get(day, {}).items():
                for dk, vals in by.items():
                    for v, n in vals.items():
                        key = (k, dk, v)
                        acc_b[key] = acc_b.get(key, 0) + n
            if not (d_from <= day <= d_to):
                continue
            for rk, (num, den, mul, dks) in RATES.items():
                if acc_t[den]:
                    pts[rk].append({"period": day,
                                    "value": round(acc_t[num] * mul / acc_t[den], 1)})
                for dk in dks:
                    cats = {v for (k, kk, v) in acc_b if kk == dk and k in (num, den)}
                    for v in sorted(cats):
                        d_ = acc_b.get((den, dk, v), 0)
                        if d_:
                            pts[rk].append({"period": day, "dims": {dk: v},
                                            "value": round(acc_b.get((num, dk, v), 0) * mul / d_, 1)})
    return pts


def evidence_block(day):
    """その日の点を入れる証憑（Addness の lake file）の期間。
    Addness は、既にある証憑と期間がまったく同じか、まったく重ならない証憑しか受け付けない。
    そのため期間は日付から決まる固定の区切りにする（月の途中でも月末までを期間にしてよい）。
    2026-09-30 までと 2026-10-01 は、最初に入れた時の区切りに合わせている。"""
    d = date.fromisoformat(day)
    if d <= date(2026, 9, 30):
        return "2026-07-01", "2026-09-30"
    if d == date(2026, 10, 1):
        return "2026-10-01", "2026-10-01"
    if d <= date(2026, 10, 31):
        return "2026-10-02", "2026-10-31"
    end = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return d.replace(day=1).isoformat(), end.isoformat()


def write_points(pts, out_dir):
    """KPIごとの点を DIR/<キー>.json に、証憑を区切りごとの parquet
    （period, dim_key, dim_value, value）に書く。summary.json に証憑の一覧を書く。"""
    import hashlib
    os.makedirs(out_dir, exist_ok=True)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        pa = None
        print("pyarrow が無いので parquet は作りません")
    summary = {}
    for k, rows in pts.items():
        if not rows:
            continue
        with open(os.path.join(out_dir, k + ".json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        info = {"points": len(rows), "from": min(r["period"] for r in rows),
                "to": max(r["period"] for r in rows), "evidence": []}
        blocks = {}
        for r in rows:
            blocks.setdefault(evidence_block(r["period"]), []).append(r)
        for (b_from, b_to), brows in sorted(blocks.items()):
            ev = {"period_from": b_from, "period_to": b_to, "rows": len(brows)}
            if pa is not None:
                path = os.path.join(out_dir, "%s__%s_%s.parquet" % (k, b_from, b_to))
                dk = [next(iter(r["dims"])) if r.get("dims") else "" for r in brows]
                pq.write_table(pa.table({
                    "period": [r["period"] for r in brows],
                    "dim_key": dk,
                    "dim_value": [r["dims"][d] if d else "" for r, d in zip(brows, dk)],
                    "value": [float(r["value"]) for r in brows],
                }), path)
                raw = open(path, "rb").read()
                ev.update(file=os.path.basename(path), size_bytes=len(raw),
                          checksum=hashlib.sha256(raw).hexdigest())
            info["evidence"].append(ev)
        summary[k] = info
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    for k, v in summary.items():
        print("点", k, v["points"], "件", v["from"], "〜", v["to"], "／証憑",
              ", ".join("%s〜%s" % (e["period_from"], e["period_to"]) for e in v["evidence"]))


def main():
    dry = "--dry" in sys.argv
    now = datetime.now(JST)
    xlsx, opt = {}, {}
    for i, a in enumerate(sys.argv):
        if a == "--xlsx" and i + 1 < len(sys.argv):
            ym, path = sys.argv[i + 1].split("=", 1)
            xlsx[ym] = path
        elif a in ("--points", "--from", "--to") and i + 1 < len(sys.argv):
            opt[a] = sys.argv[i + 1]
    breakdown, read_ok, changed = {}, [], []
    if xlsx:
        months = sorted(xlsx)
    else:
        sheets = _sheets()
        months = sorted(k[3:] for k in sheets if re.fullmatch(r"ag_\d{4}-\d{2}", k))
        # 締まっていない今月と先月だけ読み直す（それより前は前回の値を残す）
        months = [m for m in months if m >= (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")]
    if now.strftime("%Y-%m") not in months:
        print("★%s のAGシートがありません（sheets.json の ag_%s か --xlsx で渡してください）"
              % (now.strftime("%Y-%m"), now.strftime("%Y-%m")))

    try:
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"days": {}}

    for ym in months:
        try:
            if ym in xlsx:
                ledger = read_xlsx(xlsx[ym], "営業(CWトスアップ)")
                contracts = read_xlsx(xlsx[ym], "契約者")
            else:
                sid = sheets["ag_%s" % ym]
                ledger = fetch(sid, "営業(CWトスアップ)")
                contracts = fetch(sid, "契約者")
        except Exception as e:
            print("%s のAGシートを読めませんでした: %s（前回の値を残します）" % (ym, e))
            continue
        try:
            fresh = count_month(ledger, contracts, ym, breakdown)
        except ValueError as e:
            print("%s のAGシートの列を読めませんでした: %s（前回の値を残します）" % (ym, e))
            continue
        if not fresh:
            print("%s は1件も数えられませんでした。列がずれていないか確認してください" % ym)
            continue
        # その月の日付を全部入れ替える（0件の日は0で埋める）
        first = date(int(ym[:4]), int(ym[5:7]), 1)
        last = min((first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1),
                   now.date())
        d = first
        while d <= last:
            val = fresh.get(d.isoformat(), dict.fromkeys(METRICS, 0))
            old = data["days"].get(d.isoformat())
            if old is not None and old != val and d < now.date():
                changed.append(d.isoformat())
            data["days"][d.isoformat()] = val
            d += timedelta(days=1)
        tot = {k: sum(v[k] for kk, v in data["days"].items() if kk.startswith(ym)) for k in METRICS}
        print(ym, tot)
        read_ok.append(ym)

    data["generated_at"] = now.strftime("%Y-%m-%d %H:%M")
    data["target_monthly_sales"] = TARGET_MONTHLY_SALES
    data["metrics"] = METRICS
    data["days"] = dict(sorted(data["days"].items()))
    # 毎日の取り込みは、この日から後を Addness に入れ直す（締めでの書き換えを拾うため）
    print("前回から合計が変わった日（今日を除く）:", ", ".join(changed) or "なし")
    if "--points" in opt:
        d_from = opt.get("--from", min(read_ok) + "-01" if read_ok else "9999")
        d_to = opt.get("--to", (now - timedelta(days=1)).strftime("%Y-%m-%d"))
        out_dir = os.path.abspath(opt["--points"])
        if out_dir.startswith(os.path.abspath(SITE) + os.sep):
            raise SystemExit("--points の書き出し先はリポジトリの外にしてください（担当者名が入るため）")
        write_points(build_points(data, breakdown, read_ok, d_from, d_to), out_dir)
    if dry:
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0, separators=(",", ":"))
    print("書き出しました:", OUT)


if __name__ == "__main__":
    main()

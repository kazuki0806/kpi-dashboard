# -*- coding: utf-8 -*-
"""
投稿確認（kazuki0806/cw-tracker）の掲載データを、Addness の KPI に入れる日ごとの点にする。

読むもの（cw-tracker のクローン）
  data/jobs.json  … 案件ごとの掲載日・担当メンバー・観測（応募者数）。GitHub Actions が1日3回更新する
  members.json    … メンバーの名前・週ノルマ・参画日・状態（active/paused/left/banned）

日ごとに数えるもの（掲載日 postedAt を JST の暦日にして数える。発注者IDが一致した案件だけ）
  posted      … CW 掲載数（その日に掲載した案件の数）
  applicants  … CW 応募者数（その日に掲載した案件の、最新の観測の応募者数の合計。
                 応募は後から増えるので、直近の日は毎日数え直す）
  quota_done  … CW ノルマ達成人数（今週累計）。週は日曜始まり（cw-tracker の aggregate.mjs と同じ）。
                 その日までに参画している active メンバーのうち、その週にその日までに
                 週ノルマ（weeklyQuota）以上を掲載した人数。各日の値は「その日までの累計」で、土曜日の値がその週の確定
  quota_rate  … CW ノルマ達成率（今週累計）＝ quota_done ÷ 対象メンバー数 × 100（%、小数1桁）
  apo_rate_mtd … CW 応募→アポ率（当月累計）＝ 月初からその日までの アポ取り ÷ 応募者 × 100（%、小数1桁）。
                 アポ取りは Addness の「CW アポ取り数」の日ごとの値（--apo で渡す JSON）。渡さなければ作らない

内訳（dims）
  member … メンバーの名前（members.json の name）。posted と applicants に付ける。
           名前が入るので、書き出し先はリポジトリの外にすること（公開しない）
  case_type … 案件の種類（動画編集／SNS運用／デザイン／その他）。posted と applicants に付ける。
           案件タイトルから case_type() の順で決める（「SNS運用」と書いてあれば SNS運用、
           次に動画・リール・ショートなどで動画編集、次にデザイン・画像・Canva などでデザイン、
           残りの SNS・Instagram は SNS運用、どれも無ければその他）。
           配分（2026-10-07 決定：動画編集60%・SNS運用25%・デザイン15%）と比べるための内訳

実行:
  python3 cw_posting_export.py --tracker /path/to/cw-tracker --points DIR [--from YYYY-MM-DD] [--to YYYY-MM-DD]
      [--apo apo.json]
  --from/--to の既定は 2026-07-01 〜 昨日（JST）。
  --apo は [{"period":"YYYY-MM-DD","value":N}, ...] の形（get_analytics_recipe の推移をそのまま）。
  DIR/<キー>.json … record_analytics_metric_points にそのまま渡す点
  DIR/<キー>__FROM_TO.parquet … 証憑（period, dim_key, dim_value, value）。期間は月ごと
  DIR/summary.json … キーごとの点の数・期間・証憑の一覧（period_from・period_to・file・size_bytes・checksum・rows）
"""

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
DAY = timedelta(days=1)
DEFAULT_FROM = "2026-07-01"

METRICS = {
    "posted": "CW 掲載数",
    "applicants": "CW 応募者数",
    "quota_done": "CW ノルマ達成人数（今週累計）",
    "quota_rate": "CW ノルマ達成率（今週累計）",
    "apo_rate_mtd": "CW 応募→アポ率（当月累計）",
}
DIMS = {"posted": ["member", "case_type"], "applicants": ["member", "case_type"]}
CASE_TYPES = ["動画編集", "SNS運用", "デザイン", "その他"]


def jst_date(posted_at, precision):
    """cw-tracker の aggregate.mjs と同じ変換。day はそのまま、second は JST の暦日に直す。"""
    if precision == "day":
        return posted_at[:10]
    ts = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    return ts.astimezone(JST).date().isoformat()


def case_type(title):
    """案件タイトルから種類を決める。上から順に見る（「Canva」入りの SNS運用 の募集は SNS運用 にする）。"""
    t = title or ""
    if "SNS運用" in t:
        return "SNS運用"
    if re.search(r"動画|リール|ショート|YouTube|映像|TikTok", t, re.I):
        return "動画編集"
    if re.search(r"デザイン|画像|Canva|バナー|サムネ", t, re.I):
        return "デザイン"
    if re.search(r"SNS|Instagram|インスタ|Threads", t, re.I):
        return "SNS運用"
    return "その他"


def week_start(d):
    """日曜始まり。"""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def load(tracker):
    with open(os.path.join(tracker, "data", "jobs.json"), encoding="utf-8") as f:
        jobs = json.load(f)["jobs"]
    with open(os.path.join(tracker, "members.json"), encoding="utf-8") as f:
        m = json.load(f)
    members = m["members"] if isinstance(m, dict) else m
    return jobs, members


def daily(jobs, members):
    """日 → {posted, applicants, by_member:{name:{...}}, by_case:{種類:{...}}}。発注者ID一致だけ。"""
    name = {x["id"]: x["name"] for x in members}
    days = {}
    for j in jobs.values():
        if not j.get("employerMatches") or not j.get("postedAt"):
            continue
        day = jst_date(j["postedAt"], j.get("postedDatePrecision") or "day")
        last = j["observations"][-1] if j.get("observations") else {}
        app = last.get("applicantCount") or 0
        who = name.get(j["memberId"], j["memberId"])
        rec = days.setdefault(day, {"posted": 0, "applicants": 0, "by_member": {}, "by_case": {}})
        rec["posted"] += 1
        rec["applicants"] += app
        for group, key in (("by_member", who), ("by_case", case_type(j.get("title")))):
            b = rec[group].setdefault(key, {"posted": 0, "applicants": 0})
            b["posted"] += 1
            b["applicants"] += app
    return days


def quota(jobs, members, d):
    """その日 d までの今週累計の達成人数と対象人数。"""
    ws = week_start(d)
    posted = {}
    for j in jobs.values():
        if not j.get("employerMatches") or not j.get("postedAt"):
            continue
        day = date.fromisoformat(jst_date(j["postedAt"], j.get("postedDatePrecision") or "day"))
        if ws <= day <= d:
            posted[j["memberId"]] = posted.get(j["memberId"], 0) + 1
    active = [x for x in members
              if x.get("status") == "active" and x.get("startedAt")
              and date.fromisoformat(x["startedAt"]) <= d]
    done = sum(1 for x in active if posted.get(x["id"], 0) >= int(x.get("weeklyQuota") or 1))
    return done, len(active)


def build_points(jobs, members, d_from, d_to, apo=None):
    days = daily(jobs, members)
    pts = {k: [] for k in METRICS}
    d = d_from
    while d <= d_to:
        key = d.isoformat()
        rec = days.get(key, {"posted": 0, "applicants": 0, "by_member": {}, "by_case": {}})
        for k in ("posted", "applicants"):
            pts[k].append({"period": key, "value": rec[k]})
            for who, v in sorted(rec["by_member"].items()):
                if v[k]:
                    pts[k].append({"period": key, "value": v[k], "dims": {"member": who}})
            # 種類は実績0の日も0を入れる（ダッシュボードの内訳グラフで取得失敗と区別するため）
            for ct in CASE_TYPES:
                v = rec["by_case"].get(ct, {}).get(k, 0)
                pts[k].append({"period": key, "value": v, "dims": {"case_type": ct}})
        done, n = quota(jobs, members, d)
        pts["quota_done"].append({"period": key, "value": done})
        if n:
            pts["quota_rate"].append({"period": key, "value": round(done * 100.0 / n, 1)})
        d += DAY
    if apo is not None:
        apo_by = {p["period"]: float(p["value"]) for p in apo}
        d = d_from.replace(day=1)
        acc_apo = acc_app = 0.0
        while d <= d_to:
            if d.day == 1:
                acc_apo = acc_app = 0.0
            key = d.isoformat()
            acc_apo += apo_by.get(key, 0.0)
            acc_app += days.get(key, {}).get("applicants", 0)
            if d >= d_from and acc_app and key in apo_by:
                pts["apo_rate_mtd"].append({"period": key, "value": round(acc_apo * 100.0 / acc_app, 1)})
            d += DAY
    return pts


def evidence_block(day):
    """証憑の期間は月ごと（Addness は、既にある証憑と期間がまったく同じか、まったく重ならないものしか受け付けない）。"""
    d = date.fromisoformat(day)
    end = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - DAY
    return d.replace(day=1).isoformat(), end.isoformat()


def write_points(pts, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    import pyarrow as pa
    import pyarrow.parquet as pq
    summary = {}
    for k, rows in pts.items():
        if not rows:
            continue
        with open(os.path.join(out_dir, k + ".json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        info = {"title": METRICS[k], "points": len(rows), "from": min(r["period"] for r in rows),
                "to": max(r["period"] for r in rows), "evidence": []}
        blocks = {}
        for r in rows:
            blocks.setdefault(evidence_block(r["period"]), []).append(r)
        for (b_from, b_to), brows in sorted(blocks.items()):
            path = os.path.join(out_dir, "%s__%s_%s.parquet" % (k, b_from, b_to))
            dk = [next(iter(r["dims"])) if r.get("dims") else "" for r in brows]
            pq.write_table(pa.table({
                "period": [r["period"] for r in brows],
                "dim_key": dk,
                "dim_value": [r["dims"][d] if d else "" for r, d in zip(brows, dk)],
                "value": [float(r["value"]) for r in brows],
            }), path)
            raw = open(path, "rb").read()
            info["evidence"].append({"period_from": b_from, "period_to": b_to, "rows": len(brows),
                                     "file": os.path.basename(path), "size_bytes": len(raw),
                                     "checksum": hashlib.sha256(raw).hexdigest()})
        summary[k] = info
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    for k, v in summary.items():
        print("点", k, v["points"], "件", v["from"], "〜", v["to"], "／証憑",
              ", ".join("%s〜%s" % (e["period_from"], e["period_to"]) for e in v["evidence"]))


def main():
    opt = {}
    for i, a in enumerate(sys.argv):
        if a in ("--tracker", "--points", "--from", "--to", "--apo") and i + 1 < len(sys.argv):
            opt[a] = sys.argv[i + 1]
    if "--tracker" not in opt or "--points" not in opt:
        raise SystemExit(__doc__)
    today = datetime.now(JST).date()
    d_from = date.fromisoformat(opt.get("--from", DEFAULT_FROM))
    d_to = date.fromisoformat(opt.get("--to", (today - DAY).isoformat()))
    out_dir = os.path.abspath(opt["--points"])
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if out_dir.startswith(here + os.sep):
        raise SystemExit("--points の書き出し先はリポジトリの外にしてください（メンバー名が入るため）")
    apo = None
    if "--apo" in opt:
        with open(opt["--apo"], encoding="utf-8") as f:
            apo = json.load(f)
    jobs, members = load(opt["--tracker"])
    pts = build_points(jobs, members, d_from, d_to, apo)
    # 月ごとの合計（確認用）
    for k in ("posted", "applicants"):
        tot = {}
        for p in pts[k]:
            if not p.get("dims"):
                tot[p["period"][:7]] = tot.get(p["period"][:7], 0) + p["value"]
        print(k, tot)
    write_points(pts, out_dir)


if __name__ == "__main__":
    main()

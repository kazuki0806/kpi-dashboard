# ── GitHub Actions 用に自動変換したファイル ──────────────
# 元ファイル: export_kpi.py
#
# 直接編集しないこと。元ファイルを直してから変換をやり直す。
# シートIDは scripts/sheets.json から読む（リポジトリには入れない）。
"""案件掲載アカウント（5人分）の投稿管理DBを読んで、
このデザイン（PM版ダッシュボード）用のデータファイルを人ごと＋全体で書き出す。
出力: kpi_data_all.json / kpi_data_<key>.json （key は下の PEOPLE 参照）
"""
import json, os, urllib.request, urllib.error
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))


def load_env():
    env = {}
    for line in open(os.path.join(BASE, ".env"), encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v
    return env


ENV = load_env()
TOKEN = ENV["NOTION_TOKEN"]
# 表示名 → (ファイル用キー, ページID)
# ページIDが空の人は、Notionの投稿管理ページがまだ無い（または連携に共有されていない）。
# その人は投稿0件として書き出し、アポ取り数だけ載せる。ここで止めると全員分が作れない。
PEOPLE = {
    "八千古嶋": ("yachi", ENV.get("DB_八千古嶋", "")),
    "池田": ("ikeda", ENV.get("DB_池田", "")),
    "谷村": ("tanimura", ENV.get("DB_谷村", "")),
    "河野": ("kono", ENV.get("DB_河野", "")),
    "濱田": ("hamada", ENV.get("DB_濱田", "")),
    "姫路": ("himeji", ENV.get("DB_姫路", "")),
}


def api(url, method="GET", data=None):
    req = urllib.request.Request(
        url, method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"})
    if data is not None:
        req.data = json.dumps(data).encode()
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        return json.load(e)


def find_inner_dbs(page_id):
    """ページ直下の表を全部拾う（アカウントごとに分かれているため）"""
    out, cursor = [], None
    while True:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += "&start_cursor=" + cursor
        ch = api(url)
        for b in ch.get("results", []):
            if b.get("type") == "child_database":
                out.append({"id": b["id"], "title": b["child_database"].get("title", "")})
        if ch.get("has_more"):
            cursor = ch.get("next_cursor")
        else:
            break
    return out


def query_all(db_id):
    results, cursor = [], None
    while True:
        body = {"page_size": 100, "sorts": [{"property": "投稿日", "direction": "ascending"}]}
        if cursor:
            body["start_cursor"] = cursor
        q = api(f"https://api.notion.com/v1/databases/{db_id}/query", "POST", body)
        results.extend(q.get("results", []))
        if q.get("has_more"):
            cursor = q.get("next_cursor")
        else:
            break
    return results


def rich(prop):
    return "".join(x.get("plain_text", "") for x in prop) if prop else ""


# ── 営業シートからアポ取り数を数える ──
#
# 案件掲載ダッシュボードの「アポ取り」は、その人の投稿から生まれたアポの数。
# だから数える相手は リード獲得先（G列）であって、商談を持つ トスアップ（H列）ではない。
# 1行の中を端から探して名前を拾うと、後ろにある トスアップ の名前を拾ってしまい、
# 「投稿していない人にアポが付く」ことになる。列を決め打ちして読む。
#
# 数える日付は アポ取り日（M列）。予約が入った日で数えるので、その日の稼働量と並べられる。
# 予定日（N列）で数えると先の日付に積まれ、当日の動きと突き合わせられない。
import json, os, urllib.parse, csv, io, re

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

# AGシートは月ごとにファイルが分かれる。月が替わったらここに1行足す。
# 8月に固定されていたせいで、9月は全員0件のまま出ていた（2026-09-07 修正）。
APO_SHEETS = {
    "2026-08": SHEETS["ag_2026-08"],
    "2026-09": SHEETS["ag_2026-09"],
}


def apo_sheet_id(when=None):
    """その月のAGシートを返す。未登録の月は一番新しいものを使い、警告を出す。"""
    when = when or datetime.now()
    ym = when.strftime("%Y-%m")
    if ym in APO_SHEETS:
        return APO_SHEETS[ym]
    latest = APO_SHEETS[max(APO_SHEETS)]
    print(f"⚠ {ym} のAGシートが未登録です。{max(APO_SHEETS)} のシートで数えます")
    return latest
APO_TABS = ["営業(threads)"]  # threadsのアポだけを読む（CW等は含めない）
APO_NAME_MAP = {"池田和喜": "池田", "谷村亮介": "谷村", "河野碧": "河野",
                "八千古嶋陽": "八千古嶋", "濱田": "濱田", "姫路加奈子": "姫路"}

# 列は0始まり。シートの見出しと必ず一致させること。
# A顧客名 B性別 C年齢 D職業 E観覧数 F投稿内容 Gリード獲得先 Hトスアップ
# Iクローザー Jクローザーアポ Kランク Lステータス Mアポ取り日 N予定 O時間
APO_COL_NAME, APO_COL_LEAD, APO_COL_STATUS, APO_COL_DATE = 0, 6, 11, 12

# 取り消しになったものは数えない。飛びや見込み外は「アポは取れている」ので数える。
APO_SKIP = ["キャンセル", "被り"]


def apo_month(v):
    """「8/15」「2026/8/15」「8月15日」から月を取り出す。読めなければ None。"""
    s = str(v or "").strip()
    if not s:
        return None
    m = re.search(r"(\d{4})\s*[/\-年]\s*(\d{1,2})", s)
    if m:
        return int(m.group(2))
    m = re.search(r"(\d{1,2})\s*[/\-月]\s*(\d{1,2})", s)
    return int(m.group(1)) if m else None


def read_apo(month=None):
    month = month or datetime.now().month
    counts = {v: 0 for v in APO_NAME_MAP.values()}
    for tab in APO_TABS:
        try:
            url = (f"https://docs.google.com/spreadsheets/d/{apo_sheet_id()}"
                   f"/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(tab)}")
            d = urllib.request.urlopen(url, timeout=25).read().decode("utf-8", "replace")
        except Exception as e:
            # 黙って0にすると「アポが無い月」と見分けがつかない。必ず声を出す。
            print(f"⚠ 営業シート({tab})を読めませんでした: {e}")
            continue
        for r in csv.reader(io.StringIO(d)):
            if len(r) <= APO_COL_DATE:
                continue
            if not str(r[APO_COL_NAME]).strip():
                continue                       # 顧客名が無い行は見出しや空行
            if apo_month(r[APO_COL_DATE]) != month:
                continue                       # 対象月のアポ取り日だけ
            status = str(r[APO_COL_STATUS])
            if any(k in status for k in APO_SKIP):
                continue
            lead = str(r[APO_COL_LEAD])
            for full, short in APO_NAME_MAP.items():
                if full in lead:
                    counts[short] += 1
                    break                      # 1行は1人にしか数えない
    return counts


APO = read_apo()


def write(key, posts, apo=0):
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "posts": posts, "followers": [], "apo": apo}
    with open(os.path.join(BASE, f"kpi_data_{key}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


def export():
    all_posts = []
    summary = {}
    for name, (key, page_id) in PEOPLE.items():
        if not page_id:
            print(f"⚠ {name}: Notionの投稿管理ページが未登録です（.env の DB_{name}）。"
                  f"投稿は0件、アポ取り数だけ書き出します")
            write(key, [], APO.get(name, 0))
            summary[name] = 0
            continue
        dbs = find_inner_dbs(page_id)
        posts = []
        for db in dbs:
            for page in query_all(db["id"]):
                props = page["properties"]
                date_val = props.get("投稿日", {}).get("date") or {}
                start = date_val.get("start", "")
                date_key = start[:10] if start else "unknown"
                hour = int(start[11:13]) if "T" in start and len(start) >= 13 else None
                imp = props.get("インプレッション", {}).get("number")
                likes = props.get("いいね数", {}).get("number")
                comments = props.get("コメント数", {}).get("number")
                sub_sel = props.get("アカウント", {}).get("select") or {}
                # 表の名前（例「アカウント2 @xxx」）からも読めるようにする
                sub = sub_sel.get("name") or (db["title"].split(" ")[0] if db["title"].startswith("アカウント") else None)
                full = rich(props.get("投稿文", {}).get("rich_text", []))
                hook = full.split("\n\n---")[0].replace("\n", " ")[:60]
                posted = props.get("投稿済み", {}).get("checkbox")
                # 投稿済みにチェックが入っていれば載せる（数値未取得は0として扱う）
                if posted or imp is not None:
                    if imp is None:
                        imp = 0
                    # 個別ページでは「どのアカウントか」を頭に付ける
                    label = f"[{sub}] {hook}" if sub else hook
                    p = {"date": date_key, "hour": hour, "imp": imp,
                         "likes": likes, "comments": comments, "sub": sub,
                         "content": label, "text": full[:500]}
                    posts.append(p)
                    # 全体ページでは「誰の・どのアカウントか」を頭に付ける
                    who = f"{name}/{sub}" if sub else name
                    all_posts.append({**p, "content": f"[{who}] {hook}"})
        write(key, posts, APO.get(name, 0))
        summary[name] = len(posts)
    all_posts.sort(key=lambda p: p["date"])
    write("all", all_posts, sum(APO.values()))
    print(f"✅ 書き出し完了（全体 {len(all_posts)}件）")
    for name, c in summary.items():
        print(f"   - {name}: {c}件")


if __name__ == "__main__":
    export()

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

# AGシートは月ごとにファイルが分かれる。sheets.json の ag_YYYY-MM をそのまま全部使う。
# （以前はここに月を手で足していて、足し忘れた月は全員0件のまま出ていた。2026-10-04 修正）
APO_SHEETS = {k[3:]: v for k, v in SHEETS.items() if re.match(r"^ag_\d{4}-\d{2}$", k)}


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
CONTRACT_TAB = "契約者"        # 契約は契約者タブ（種別に threads を含む行）で数える
# リード獲得先（G列）の名前の先頭 → ダッシュボードの表示名。案件掲載アカウントを持つ人を全部並べる。
# 舘林さんは「舘」「館」のどちらで書かれることもあるので両方入れる。
APO_NAME_MAP = {"池田": "池田", "谷村": "谷村", "河野": "河野", "八千古嶋": "八千古嶋",
                "濱田": "濱田", "姫路": "姫路", "関澤": "関澤", "上野": "上野", "小川": "小川",
                "渡邊": "渡邊", "小澤": "小澤", "舘林": "舘林", "館林": "舘林", "宗木": "宗木",
                "林原": "林原", "民家": "民家", "坂本": "坂本", "山﨑": "山﨑", "山崎": "山﨑"}

# 列は0始まり。見出し（「顧客名」のある行）から探し、見つからない時だけこの位置を使う。
# A顧客名 B性別 C年齢 D職業 E観覧数 F投稿内容 Gリード獲得先 Hトスアップ
# Iクローザー Jクローザーアポ Kランク Lステータス Mアポ取り日 N予定 O時間 P実際（トスアップ）
APO_COL_NAME, APO_COL_LEAD, APO_COL_STATUS, APO_COL_DATE, APO_COL_SEATED = 0, 6, 11, 12, 15

# アポ取り：取り消しになったものは数えない。飛びや見込み外は「アポは取れている」ので数える。
APO_SKIP = ["キャンセル", "被り"]
# 着座：トスアップの「実際」に日付が入った行。**見込み外になった人も着座に数える**
# （AGシートの集計と Addness の「threads 着座数」と同じ数え方。以前は見込み外を除いていて、
#   9月が実際の17より少ない3と出ていた。2026-10-04 修正）


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


def _clean(v):
    return re.sub(r"\s+", "", str(v or ""))


def _who(lead):
    """リード獲得先の名前 → 表示名。見つからなければ None。"""
    lead = _clean(lead)
    for head, short in APO_NAME_MAP.items():
        if lead.startswith(head):
            return short
    return None


def _ledger_cols(rows):
    """営業(threads) の見出し行（「顧客名」と「リード獲得先」がある行）から列の位置を探す。
    トスアップの「実際」は、ステータス列より右で最初に出てくる「実際」。"""
    for r in rows[:10]:
        c = [_clean(x) for x in r]
        if "顧客名" in c and "リード獲得先" in c and "ステータス" in c:
            status = c.index("ステータス")
            seated = next((i for i in range(status, len(c)) if c[i] == "実際"), APO_COL_SEATED)
            return {"name": c.index("顧客名"), "lead": c.index("リード獲得先"), "status": status,
                    "date": c.index("アポ取り日") if "アポ取り日" in c else APO_COL_DATE,
                    "seated": seated}
    return {"name": APO_COL_NAME, "lead": APO_COL_LEAD, "status": APO_COL_STATUS,
            "date": APO_COL_DATE, "seated": APO_COL_SEATED}


def fetch_rows(sheet_id, tab):
    url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
           f"/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(tab)}")
    d = urllib.request.urlopen(url, timeout=25).read().decode("utf-8", "replace")
    return list(csv.reader(io.StringIO(d)))


def count_funnel(ledger_rows, contract_rows, month):
    """1か月分の 営業(threads) と 契約者 の行から、人ごとの アポ取り・着座・契約 を数える。
    アポ取り＝アポ取り日がその月（キャンセル・被りは除く）、着座＝トスアップ実際がその月、
    契約＝契約者タブで種別に threads を含み契約日がその月でキャンセル日が空。"""
    counts = {v: {"apo": 0, "sat": 0, "contract": 0} for v in set(APO_NAME_MAP.values())}
    L = _ledger_cols(ledger_rows)
    need = max(L.values())
    for r in ledger_rows:
        if len(r) <= need or not str(r[L["name"]]).strip():
            continue                       # 顧客名が無い行は見出しや空行
        who = _who(r[L["lead"]])
        if not who:
            continue                       # 案件掲載の人以外（空欄など）は数えない
        status = str(r[L["status"]])
        if apo_month(r[L["date"]]) == month and not any(k in status for k in APO_SKIP):
            counts[who]["apo"] += 1
        if apo_month(r[L["seated"]]) == month:
            counts[who]["sat"] += 1
    hdr = None
    for r in contract_rows:
        c = [_clean(x) for x in r]
        if hdr is None:
            if "種別" in c and "契約日" in c:
                hdr = {k: c.index(k) for k in ("種別", "契約日", "キャンセル日", "リード獲得先") if k in c}
            continue
        if len(hdr) < 4 or len(r) <= max(hdr.values()):
            continue
        if "threads" not in str(r[hdr["種別"]]).lower() or str(r[hdr["キャンセル日"]]).strip():
            continue
        who = _who(r[hdr["リード獲得先"]])
        if who and apo_month(r[hdr["契約日"]]) == month:
            counts[who]["contract"] += 1
    return counts


def read_funnel(month=None):
    month = month or datetime.now().month
    sheet = apo_sheet_id()
    ledger, contracts = [], []
    for tab in APO_TABS:
        try:
            ledger += fetch_rows(sheet, tab)
        except Exception as e:
            # 黙って0にすると「アポが無い月」と見分けがつかない。必ず声を出す。
            print(f"⚠ 営業シート({tab})を読めませんでした: {e}")
    try:
        contracts = fetch_rows(sheet, CONTRACT_TAB)
    except Exception as e:
        print(f"⚠ 契約者タブを読めませんでした: {e}")
    return count_funnel(ledger, contracts, month)


FUNNEL = read_funnel() if __name__ == "__main__" else {}


def write(key, posts, funnel=None):
    f = funnel or {"apo": 0, "sat": 0, "contract": 0}
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "posts": posts, "followers": [],
           "apo": f["apo"], "sat": f["sat"], "contract": f["contract"]}
    with open(os.path.join(BASE, f"kpi_data_{key}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


def export():
    all_posts = []
    summary = {}
    for name, (key, page_id) in PEOPLE.items():
        if not page_id:
            print(f"⚠ {name}: Notionの投稿管理ページが未登録です（.env の DB_{name}）。"
                  f"投稿は0件、アポ取り数だけ書き出します")
            write(key, [], FUNNEL.get(name))
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
        write(key, posts, FUNNEL.get(name))
        summary[name] = len(posts)
    all_posts.sort(key=lambda p: p["date"])
    write("all", all_posts, {k: sum(v[k] for v in FUNNEL.values()) for k in ("apo", "sat", "contract")})
    print(f"✅ 書き出し完了（全体 {len(all_posts)}件）")
    for name, c in summary.items():
        print(f"   - {name}: {c}件")


if __name__ == "__main__":
    export()

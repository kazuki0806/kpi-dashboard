# ── GitHub Actions 用に自動変換したファイル ──────────────
# 元ファイル: build_dashboard.py
#
# 直接編集しないこと。元ファイルを直してから変換をやり直す。
# シートIDは scripts/sheets.json から読む（リポジトリには入れない）。
"""既存のPM版ダッシュボード(このデザイン)を、案件掲載の5人版に変換して index.html を作る。
元ファイルは触らず、コピーして ACCOUNTS・ブランド・保存キーだけ差し替える。
"""
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.environ.get("SITE_DIR",
                   "/Users/ikedakazuyoshi/業務/kpi_dashboard_site"), "index.html")

html = open(SRC, encoding="utf-8").read()

# 1) アカウント定義を「全体＋5人」に差し替え
old_accounts = '''const ACCOUNTS = {
  A: {name:"クラウドワークス裏側", handle:"@cw_uragawa", kpi2:"無料相談", url:"kpi_data_A.json"},
  B: {name:"駆け込み寺", handle:"@kakekomi_tera_", kpi2:"無料相談", url:"kpi_data_B.json"},
  C: {name:"AI生存スキル", handle:"@seizonnsukiru", kpi2:"アポ", url:"kpi_data_C.json"},
};'''
new_accounts = '''const ACCOUNTS = {
  all:{name:"全体", handle:"案件掲載アカウント", kpi2:"アポ", url:"kpi_data_all.json"},
  yachi:{name:"八千古嶋", handle:"", kpi2:"アポ", url:"kpi_data_yachi.json"},
  ikeda:{name:"池田", handle:"", kpi2:"アポ", url:"kpi_data_ikeda.json"},
  tanimura:{name:"谷村", handle:"", kpi2:"アポ", url:"kpi_data_tanimura.json"},
  kono:{name:"河野", handle:"", kpi2:"アポ", url:"kpi_data_kono.json"},
  hamada:{name:"濱田", handle:"", kpi2:"アポ", url:"kpi_data_hamada.json"},
  himeji:{name:"姫路", handle:"", kpi2:"アポ", url:"kpi_data_himeji.json"},
};'''
assert old_accounts in html, "ACCOUNTS定義が見つかりません"
html = html.replace(old_accounts, new_accounts)

# 2) 初期表示アカウントを全体に
html = html.replace('let CUR = "A";', 'let CUR = "all";')

# 3) タブのラベルを「名前」だけに（元は「A：名前」）
html = html.replace('b.textContent=k+"："+ACCOUNTS[k].name;', 'b.textContent=ACCOUNTS[k].name;')

# 4) ブランド・タイトル
html = html.replace("<title>Threads運用ダッシュボード</title>", "<title>案件掲載ダッシュボード</title>")
html = html.replace(">Threads運用</div>", ">案件掲載</div>")

# 5) 保存キーを分離（他ダッシュボードとデータが混ざらないように）
html = html.replace("uni_kpi_", "annken_kpi_").replace("uni_theme", "annken_theme")

# 6-0) 上段カードを「今日の投稿・平均インプレ・コメント数・アポ取り」に（フォロワー/オプチャを外す）
# 「フォロワー」のカードから、その並びが終わる ; までをまるごと差し替える。
# PM版にカードが足されても外れないよう、行数や並びに依存させない。
new_cards = '''    statCard("コメント数", fmt(posts.reduce((s,p)=>s+(Number(p.comments)||0),0)), "", {cls:"flat", txt:"累計"}, "") +
    statCard("アポ取り", fmt((d.apo!=null?d.apo:0)), "件", {cls:"flat", txt:"営業シート"}, "");'''
html, n = re.subn(r'    statCard\("フォロワー".*?;\n', new_cards + "\n", html, count=1, flags=re.S)
assert n == 1, "上段カードの並びが見つかりません（PM版のHTMLを確認してください）"

# 6-1) 今月の目標は アポ だけにする（フォロワー・オプチャを外す）
# 目標行も同じ理由で、goalRows の中身をまるごと入れ替える
# 閉じ方が ]; だったり ].filter(...) だったりするので、中身だけを入れ替えて
# 閉じ括弧から後ろはそのまま残す
new_goals = '''  const goalRows=[
    {n:"アポ取り",c:(d.apo!=null?d.apo:0),g:g.appt,u:"件"},
'''
html, n = re.subn(r'  const goalRows=\[.*?\n(?=  \])', new_goals, html, count=1, flags=re.S)
assert n == 1, "目標行が見つかりません（PM版のHTMLを確認してください）"

# 6-2) 手入力ボタン（オプチャ/アポの手入力）は不要なので隠す
html = html.replace('<div class="fab">', '<div class="fab" style="display:none">')

# 6) 「最近の投稿」一覧に コメント 列を追加
html = html.replace(
    '<th style="text-align:right">インプレ</th><th style="text-align:right">いいね</th></tr></thead><tbody>`+\n    rr.map',
    '<th style="text-align:right">インプレ</th><th style="text-align:right">いいね</th><th style="text-align:right">コメント</th></tr></thead><tbody>`+\n    rr.map')
html = html.replace(
    '      <td style="text-align:right">${fmt(p.likes)}</td></tr>`).join("")+\n    `</tbody></table><div style="font-size:11px',
    '      <td style="text-align:right">${fmt(p.likes)}</td>\n      <td style="text-align:right">${fmt(p.comments)}</td></tr>`).join("")+\n    `</tbody></table><div style="font-size:11px')


# 人ごとの1日の目標投稿数（池田は5件、他はアカウント3つ＝3件）
html = html.replace(
    'function goals(acct){ return Object.assign({}, GOAL_DEFAULT, lsGet("annken_kpi_"+acct+"_goals", {})); }',
    'const GOAL_BY_ACCOUNT = {ikeda:{posts:3}, yachi:{posts:3}, tanimura:{posts:3}, kono:{posts:3}, hamada:{posts:3}, himeji:{posts:3}, all:{posts:18}};\n'
    'function goals(acct){ return Object.assign({}, GOAL_DEFAULT, GOAL_BY_ACCOUNT[acct]||{}, lsGet("annken_kpi_"+acct+"_goals", {})); }'
)

with open(os.path.join(BASE, "index.html"), "w", encoding="utf-8") as f:
    f.write(html)
print("✅ index.html を生成しました（このデザイン・案件掲載5人版）")

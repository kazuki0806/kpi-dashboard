# -*- coding: utf-8 -*-
"""
公式LINE（属人アカウント用）の新しい導線を、指定した1人にだけ試す。

やること（ほかの友だちには何も届かない）
  1. 友だちの中から、表示名が TEST_NAME の人を探す
  2. 新しいリッチメニュー（line/richmenu_A.jpg）を作り、その人にだけ付ける
  3. その人にだけ、あいさつ文（追加直後に送る予定の文面）を送る

元に戻すとき: MODE=undo で実行すると、その人からリッチメニューを外し、作ったメニューを消す。

環境変数
  LINE_TOKEN_A … チャネルアクセストークン（GitHub Secrets）
  TEST_NAME    … 試す人の表示名（例: 池田和喜）
  TEST_USER_ID … 試す人のユーザーID（U〜）。友だち一覧のAPIは認証済みアカウントでないと使えないので、
                 自分で試すときは LINE Developers の「あなたのユーザーID」を入れる
  CHECK_URL    … 3分チェックのURL
  NOTE_URL     … note記事のURL
  BOOK_URL     … 無料相談の日程調整URL（空なら、押すと「相談」と送られる）
  MODE         … try（既定。1人にだけ）/ default（全員の既定メニューにする）/ undo
"""

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.line.me/v2/bot"
DATA = "https://api-data.line.me/v2/bot"
TOKEN = os.environ["LINE_TOKEN_A"].strip()
BASE = os.path.dirname(os.path.abspath(__file__))
MENU_NAME = "test-3buttons"


def call(method, url, body=None, ctype="application/json", raw=False):
    data = None
    if body is not None:
        data = body if raw else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + TOKEN, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8") or "{}"
            return json.loads(txt)
    except urllib.error.HTTPError as e:
        print("エラー", method, url.split("/bot")[-1], e.code, e.read().decode("utf-8")[:300])
        raise


def find_user(name):
    # LINE Developers の「チャネル基本設定」→「あなたのユーザーID」（U から始まる）を渡せば、一覧を探さない
    if os.environ.get("TEST_USER_ID", "").startswith("U"):
        return os.environ["TEST_USER_ID"].strip()
    ids, start = [], None
    while True:
        url = API + "/followers/ids?limit=1000" + ("&start=" + start if start else "")
        try:
            j = call("GET", url)
        except urllib.error.HTTPError:
            sys.exit("友だちの一覧を取れませんでした（認証済み・プレミアムアカウント以外は使えないAPIです）。"
                     "池田さんのLINEからこの公式LINEに何かメッセージを送ってから、もう一度試してください。")
        ids += j.get("userIds", [])
        start = j.get("next")
        if not start:
            break
    print("友だち", len(ids), "人")
    for uid in ids:
        try:
            p = call("GET", API + "/profile/" + uid)
        except urllib.error.HTTPError:
            continue
        if name in p.get("displayName", ""):
            print("見つかりました:", p["displayName"])
            return uid
    sys.exit("表示名に「%s」を含む友だちが見つかりませんでした" % name)


def make_menu():
    check, note = os.environ["CHECK_URL"], os.environ["NOTE_URL"]
    menu = {
        "size": {"width": 2500, "height": 1686}, "selected": True,
        "name": MENU_NAME, "chatBarText": "メニュー",
        "areas": [
            {"bounds": {"x": 0, "y": 0, "width": 1350, "height": 1686},
             "action": {"type": "uri", "label": "3分チェック", "uri": check}},
            {"bounds": {"x": 1350, "y": 0, "width": 1150, "height": 843},
             "action": ({"type": "uri", "label": "無料相談", "uri": os.environ["BOOK_URL"]}
                        if os.environ.get("BOOK_URL") else
                        {"type": "message", "label": "無料相談", "text": "相談"})},
            {"bounds": {"x": 1350, "y": 843, "width": 1150, "height": 843},
             "action": {"type": "uri", "label": "記事を読む", "uri": note}},
        ],
    }
    mid = call("POST", API + "/richmenu", menu)["richMenuId"]
    with open(os.path.join(BASE, "..", "line", "richmenu_A.jpg"), "rb") as f:
        call("POST", DATA + "/richmenu/%s/content" % mid, f.read(), ctype="image/jpeg", raw=True)
    return mid


def make_default():
    """全員の既定メニューにする（前に作ったテスト用のメニューは消す）。"""
    for m in call("GET", API + "/richmenu/list").get("richmenus", []):
        if m.get("name") == MENU_NAME:
            call("DELETE", API + "/richmenu/" + m["richMenuId"])
    mid = make_menu()
    call("POST", API + "/user/all/richmenu/" + mid)
    print("全員の既定メニューにしました:", mid)


def try_it(uid):
    check = os.environ["CHECK_URL"]
    undo(uid)
    mid = make_menu()
    call("POST", API + "/user/%s/richmenu/%s" % (uid, mid))
    print("リッチメニューを付けました:", mid)

    greet = ("追加ありがとうございます！\nThreadsで「ClaudeCode×高単価クライアントワーク」を発信している、いけです。\n\n"
             "さっそく、お約束の特典をお渡しします👇\n\n"
             "【3分チェック】\nClaude Code×クライアントワーク\nあなたはいま、どの壁にいる？\n▶ " + check + "\n\n"
             "15問にチェックを入れるだけで、\n「案件選び」「納品」「単価」のどこで止まっているかが分かります。\n\n"
             "結果をもとに、\n・どの案件から始めればいいか\n・いまの納品物で単価を上げるには何が足りないか\n"
             "を一緒に整理したい方は、このLINEで「相談」と送ってください。\n（無料・30分・オンライン。希望した方だけです）")
    call("POST", API + "/message/push",
         {"to": uid, "messages": [{"type": "text", "text": "【テスト送信】新しいあいさつ文です"}, {"type": "text", "text": greet}]})
    print("あいさつ文を送りました")


def undo(uid):
    try:
        call("DELETE", API + "/user/%s/richmenu" % uid)
    except urllib.error.HTTPError:
        pass
    for m in call("GET", API + "/richmenu/list").get("richmenus", []):
        if m.get("name") == MENU_NAME:
            call("DELETE", API + "/richmenu/" + m["richMenuId"])
            print("消しました:", m["richMenuId"])


if __name__ == "__main__":
    if os.environ.get("MODE") == "default":
        make_default()
    else:
        uid = find_user(os.environ["TEST_NAME"])
        (undo if os.environ.get("MODE") == "undo" else try_it)(uid)

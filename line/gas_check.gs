/**
 * 公式LINE（いけ｜ClaudeCode×高単価クライアントワーク）の「3分チェック」
 *
 * トークで「3分チェック」と送る（リッチメニューの左を押す）と、質問が4つ届く。
 * ボタンで答えると、最後に「あなたの課題は〇〇です」と、次にやること3つが届く。
 * 回答はスプレッドシート「3分チェック回答」に1行ずつ残る（LINEの名前つき）。
 *
 * 使い方
 *   1. このコードを Google Apps Script に貼る
 *   2. 「プロジェクトの設定」→「スクリプト プロパティ」に LINE_TOKEN（チャネルアクセストークン）を入れる
 *   3. 「デプロイ」→「新しいデプロイ」→ 種類「ウェブアプリ」、実行ユーザー「自分」、アクセス「全員」
 *   4. 出てきたURLを LINE Developers の「Messaging API設定」→「Webhook URL」に貼り、「Webhookの利用」をオン
 *
 * 「相談」などほかの言葉には反応しない（LINE公式の応答メッセージがそのまま動く）。
 */

const START_WORDS = ['3分チェック', '３分チェック', 'チェック', '診断'];
const CONSULT_WORD = '相談';

// 質問。walls は「当てはまる数」を0〜5で聞く。
const QUESTIONS = [
  {
    key: 'status',
    text: '【1/4】いまの状況にいちばん近いものを選んでください。',
    choices: ['まだ応募したことがない', '応募中・まだ0件', '単発の案件はある', '月額の案件もある'],
  },
  {
    key: 'w1',
    text: '【2/4】①案件選びの壁\n次のうち、当てはまるのはいくつですか？\n\n' +
      '・案件を「報酬の高さ」だけで選んだことがある\n' +
      '・募集文から、相手が毎月どんな作業に時間を取られているか想像できない\n' +
      '・単発の案件と、続きそうな案件の見分け方が分からない\n' +
      '・応募しても返事が来ない理由が分からない\n' +
      '・1日に何件応募するか決めていない',
    choices: ['0', '1', '2', '3', '4', '5'],
  },
  {
    key: 'w2',
    text: '【3/4】②納品の壁\n次のうち、当てはまるのはいくつですか？\n\n' +
      '・納品したあと、相手から感想や点数をもらったことがない\n' +
      '・「言われたことはやった」で納品を終えている\n' +
      '・ポートフォリオの作品を、相手の目線で選べていない\n' +
      '・Claude Codeを、作業を速くする道具としてしか使っていない\n' +
      '・納品物を、自分以外の誰かに見てもらったことがない',
    choices: ['0', '1', '2', '3', '4', '5'],
  },
  {
    key: 'w3',
    text: '【4/4】③単価の壁\n次のうち、当てはまるのはいくつですか？\n\n' +
      '・1件あたりの報酬が、半年前とほとんど変わっていない\n' +
      '・納品のときに、次の仕事を提案したことがない\n' +
      '・「月額でお願いします」と言われたことがない\n' +
      '・相手の作業を「来月から要らなくなる形」にした経験がない\n' +
      '・金額を自分で決めたことがない（相手の提示額で受けている）',
    choices: ['0', '1', '2', '3', '4', '5'],
  },
];

// 結果
const RESULTS = {
  w1: {
    title: '①案件選びの壁',
    body: 'まだ「入口」を選べていない段階です。\n' +
      '応募の数を増やしても、入口がずれていると返事は来ません。最初の1件は、報酬より「相手が毎月同じ作業を抱えているか」で選びます。',
    steps: [
      '募集文に「毎月」「継続」「担当者1名」とある案件だけに絞る',
      '応募文の1行目に「募集文を読んで分かったこと」を書く（自己紹介から始めない）',
      '1日の応募数を決めて、2週間続ける',
    ],
  },
  w2: {
    title: '②納品の壁',
    body: '頑張っているのに、評価が見えない段階です。\n' +
      'クラウドワークスでは、落ちた理由も納品の点数も返ってきません。ここは一人で数を増やしても抜けにくい壁です。',
    steps: [
      '納品物と一緒に「気づいたこと1つ」を添えて送る',
      'ポートフォリオに「相手の何を楽にしたか」を1行ずつ書く',
      '選ぶ側の目で、いまの納品物を一度見てもらう',
    ],
  },
  w3: {
    title: '③単価の壁',
    body: 'スキルはあるのに、単価が止まっている段階です。\n' +
      '単価は作業の速さではなく「納品のあとに何を提案するか」で決まります。',
    steps: [
      '次の納品で、周りの仕事（投稿文・サムネ・数字の振り返り）もまとめて引き受けると提案する',
      '相手が毎月手を止めている作業を1つ見つけて、Claude Codeで「来月から要らない形」にする',
      '金額は作業時間ではなく「相手が浮く時間」で話す',
    ],
  },
  even: {
    title: '3つの壁がほぼ同じ',
    body: '独学で始めて、まだ1件目の手前にいる人に多い結果です。\n' +
      'あれこれ同時に直そうとせず、①案件選びから順に進めるのがいちばんの近道です。',
    steps: [
      'まず「先のある案件」の選び方だけを決める',
      '1件目を取ったら、納品のときに周りの仕事を提案する',
      '迷ったところは、選ぶ側の目で一度見てもらう',
    ],
  },
  none: {
    title: '大きな壁は見当たりません',
    body: 'すでに「案件選び」「納品」「単価」の基本はできています。\n' +
      'ここからは、1社の中で仕事を広げて月額を増やす段階です。',
    steps: [
      'いまの取引先で、相手が毎月手を止めている作業を洗い出す',
      'その作業をClaude Codeで仕組みにして、別の仕事として提案する',
      '同じ業種の別の相手に、同じ仕組みを持っていく',
    ],
  },
};

function doPost(e) {
  const body = JSON.parse(e.postData.contents || '{}');
  (body.events || []).forEach(handleEvent_);
  return ContentService.createTextOutput('ok');
}

function handleEvent_(ev) {
  if (ev.type !== 'message' || ev.message.type !== 'text') return;
  const uid = ev.source && ev.source.userId;
  if (!uid) return;
  const text = String(ev.message.text || '').trim();
  const cache = CacheService.getScriptCache();
  const key = 'chk_' + uid;

  if (START_WORDS.indexOf(text) >= 0) {
    cache.put(key, JSON.stringify({ step: 0, answers: {} }), 21600);
    return ask_(ev.replyToken, 0, 'ありがとうございます！3分チェックを始めます。\nボタンを押して答えてください👇');
  }

  const raw = cache.get(key);
  if (!raw) return; // チェックの途中でない言葉には反応しない
  const st = JSON.parse(raw);
  const q = QUESTIONS[st.step];
  if (q.choices.indexOf(text) < 0) {
    return ask_(ev.replyToken, st.step, '下のボタンから選んでください🙏');
  }
  st.answers[q.key] = text;
  st.step += 1;
  if (st.step < QUESTIONS.length) {
    cache.put(key, JSON.stringify(st), 21600);
    return ask_(ev.replyToken, st.step);
  }
  cache.remove(key);
  finish_(ev.replyToken, uid, st.answers);
}

function ask_(replyToken, step, lead) {
  const q = QUESTIONS[step];
  const msgs = [];
  if (lead) msgs.push({ type: 'text', text: lead });
  msgs.push({
    type: 'text',
    text: q.text,
    quickReply: {
      items: q.choices.map(c => ({ type: 'action', action: { type: 'message', label: c.slice(0, 20), text: c } })),
    },
  });
  reply_(replyToken, msgs);
}

function judge_(a) {
  const s = { w1: +a.w1, w2: +a.w2, w3: +a.w3 };
  const max = Math.max(s.w1, s.w2, s.w3);
  if (max <= 1) return 'none';
  const tops = ['w1', 'w2', 'w3'].filter(k => s[k] === max);
  if (tops.length === 3) return 'even';
  return tops[0]; // 同点のときは①→②→③の順に、手前の壁を優先する
}

function finish_(replyToken, uid, a) {
  const kind = judge_(a);
  const r = RESULTS[kind];
  const name = profileName_(uid);
  const score = '①案件選び ' + a.w1 + '／5　②納品 ' + a.w2 + '／5　③単価 ' + a.w3 + '／5';
  const text =
    (name ? name + 'さんの' : 'あなたの') + '課題は\n【' + r.title + '】です。\n\n' +
    score + '\n\n' + r.body + '\n\n' +
    '▼次にやること\n' + r.steps.map((s, i) => (i + 1) + '. ' + s).join('\n') + '\n\n' +
    '「自分の場合はどこから手をつければいいか」を一緒に整理したい方は、下の「相談する」を押してください（無料・30分・オンライン）。';
  reply_(replyToken, [{
    type: 'text',
    text: text,
    quickReply: { items: [{ type: 'action', action: { type: 'message', label: '相談する', text: CONSULT_WORD } }] },
  }]);
  log_(uid, name, a, r.title);
}

function profileName_(uid) {
  try {
    const res = UrlFetchApp.fetch('https://api.line.me/v2/bot/profile/' + uid, {
      headers: { Authorization: 'Bearer ' + token_() }, muteHttpExceptions: true,
    });
    return res.getResponseCode() === 200 ? JSON.parse(res.getContentText()).displayName : '';
  } catch (err) {
    return '';
  }
}

function log_(uid, name, a, title) {
  const props = PropertiesService.getScriptProperties();
  let id = props.getProperty('SHEET_ID');
  let ss;
  if (id) {
    ss = SpreadsheetApp.openById(id);
  } else {
    ss = SpreadsheetApp.create('3分チェック回答（いけ公式LINE）');
    props.setProperty('SHEET_ID', ss.getId());
    ss.getSheets()[0].appendRow(['日時', 'LINEの名前', 'ユーザーID', 'いまの状況', '①案件選び', '②納品', '③単価', '結果']);
  }
  ss.getSheets()[0].appendRow([new Date(), name, uid, a.status, +a.w1, +a.w2, +a.w3, title]);
}

function reply_(replyToken, messages) {
  UrlFetchApp.fetch('https://api.line.me/v2/bot/message/reply', {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token_() },
    payload: JSON.stringify({ replyToken: replyToken, messages: messages }),
    muteHttpExceptions: true,
  });
}

function token_() {
  return PropertiesService.getScriptProperties().getProperty('LINE_TOKEN');
}

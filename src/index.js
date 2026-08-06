const BASE_STYLES = String.raw`
  :root {
    color-scheme: light;
    --bg: #f7f6f3;
    --surface: #ffffff;
    --text: #292524;
    --muted: #665f59;
    --line: #e8e2dc;
    --accent: #bd081c;
    --accent-dark: #8f0b19;
    --soft: #fff1f2;
    --shadow: 0 18px 55px rgb(41 37 36 / 9%);
  }

  * { box-sizing: border-box; }

  html { scroll-behavior: smooth; }

  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans",
      "Yu Gothic UI", "Yu Gothic", Meiryo, sans-serif;
    font-size: 16px;
    line-height: 1.85;
  }

  a { color: var(--accent-dark); text-underline-offset: 3px; }

  .shell {
    width: min(920px, calc(100% - 32px));
    margin: 48px auto;
  }

  .card {
    padding: clamp(28px, 6vw, 68px);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 24px;
    box-shadow: var(--shadow);
  }

  .brand {
    display: inline-flex;
    align-items: center;
    gap: 9px;
    margin-bottom: 28px;
    color: var(--accent);
    font-size: 0.82rem;
    font-weight: 800;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }

  .brand-dot {
    width: 10px;
    height: 10px;
    border-radius: 999px;
    background: var(--accent);
  }

  h1 {
    margin: 0;
    font-size: clamp(2.15rem, 7vw, 4.3rem);
    line-height: 1.08;
    letter-spacing: -0.045em;
  }

  h2 {
    margin: 48px 0 14px;
    padding-left: 14px;
    border-left: 4px solid var(--accent);
    font-size: clamp(1.22rem, 4vw, 1.5rem);
    line-height: 1.45;
  }

  h3 { margin: 0 0 8px; font-size: 1.05rem; }

  p, li { color: var(--muted); }

  ul, ol { padding-left: 1.4rem; }

  li + li { margin-top: 8px; }

  .lead {
    max-width: 720px;
    margin: 24px 0 0;
    font-size: clamp(1.05rem, 2.5vw, 1.2rem);
  }

  .pill {
    display: inline-block;
    margin-top: 24px;
    padding: 7px 13px;
    border-radius: 999px;
    background: var(--soft);
    color: var(--accent-dark);
    font-size: 0.88rem;
    font-weight: 700;
  }

  .features {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
    margin-top: 40px;
  }

  .feature {
    padding: 22px;
    border: 1px solid var(--line);
    border-radius: 16px;
    background: #fcfbfa;
  }

  .feature p { margin: 0; font-size: 0.94rem; }

  .action {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 48px;
    margin-top: 34px;
    padding: 11px 20px;
    border-radius: 10px;
    background: var(--accent);
    color: #fff;
    font-weight: 700;
    text-decoration: none;
  }

  .action:hover { background: var(--accent-dark); }

  .notice {
    margin: 28px 0 0;
    padding: 18px 20px;
    border-radius: 12px;
    background: var(--soft);
  }

  .notice p { margin: 0; color: var(--text); }

  code {
    padding: 0.12em 0.4em;
    border-radius: 5px;
    background: #f0ece8;
    color: #4a423c;
    font-family: Consolas, "SFMono-Regular", monospace;
    font-size: 0.92em;
  }

  footer {
    padding: 24px 12px 0;
    text-align: center;
    font-size: 0.88rem;
  }

  @media (max-width: 720px) {
    .shell { width: 100%; margin: 0; }
    .card { border: 0; border-radius: 0; box-shadow: none; }
    .features { grid-template-columns: 1fr; }
    h2 { margin-top: 38px; }
  }
`;

function layout({ title, description, body }) {
  return `<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="${description}">
  <meta name="robots" content="index,follow">
  <title>${title}</title>
  <style>${BASE_STYLES}</style>
</head>
<body>
  <main class="shell">
    <article class="card">${body}</article>
    <footer>
      <p>© 2026 Note to Automation — 個人利用のPinterest投稿支援アプリ</p>
    </footer>
  </main>
</body>
</html>`;
}

const HOME_HTML = layout({
  title: "Note to Automation | アプリ概要",
  description: "個人利用のPinterest投稿支援アプリ Note to Automation の概要",
  body: `
    <div class="brand"><span class="brand-dot" aria-hidden="true"></span>Application overview</div>
    <h1>Note to<br>Automation</h1>
    <p class="lead">
      note記事からPinterest向けの画像と投稿文を作成する、
      個人利用のPinterest投稿支援アプリです。
    </p>
    <span class="pill">Personal use only</span>

    <div class="features" aria-label="アプリの特徴">
      <section class="feature">
        <h3>投稿素材を作成</h3>
        <p>入力した記事URL、タイトル、要約をもとに、Pinterest向け画像と投稿文を作成します。</p>
      </section>
      <section class="feature">
        <h3>本人が最終確認</h3>
        <p>投稿前に、画像・タイトル・説明文・リンク・投稿先ボードをユーザー本人が確認します。</p>
      </section>
      <section class="feature">
        <h3>本人のボードへ投稿</h3>
        <p>確認操作の後、Pinterest公式APIを利用して、ユーザー本人のボードへ投稿します。</p>
      </section>
    </div>

    <div class="notice">
      <p>
        本アプリは開発者本人が使用する個人用ツールであり、
        一般ユーザー向けに提供するサービスではありません。
      </p>
    </div>

    <a class="action" href="/privacy">プライバシーポリシーを見る</a>
  `,
});

const PRIVACY_HTML = layout({
  title: "プライバシーポリシー | Note to Automation",
  description: "Note to Automationにおける情報の取り扱いについて",
  body: `
    <div class="brand"><span class="brand-dot" aria-hidden="true"></span>Privacy policy</div>
    <h1>プライバシー<br>ポリシー</h1>
    <p class="lead">
      本ポリシーは、個人利用の「Note to Automation」（以下「本アプリ」）における
      情報の取り扱いを説明するものです。
    </p>

    <section id="information-collected">
      <h2>1. 取得する情報</h2>
      <ul>
        <li>ユーザーが入力する公開済みnote記事のURL、タイトル、要約、任意のキーワード</li>
        <li>任意でアップロードする背景画像</li>
        <li>生成したPinterest向け画像、タイトル、説明文、代替テキスト、画像内コピー</li>
        <li>Pinterest APIから取得するアカウント表示情報、ボード名、Board ID、Pin IDおよび処理結果</li>
        <li>ローカルに保存する投稿日時、記事URL、投稿状態、画像の保存場所とハッシュ値、エラー内容</li>
        <li>Pinterestアクセストークン、およびAI生成を利用する場合のOpenAI APIキー</li>
      </ul>
      <p>
        本アプリはPinterestのログインパスワードを取得しません。また、入力された記事URLの内容を
        自動取得またはスクレイピングしません。
      </p>
    </section>

    <section id="purpose">
      <h2>2. 情報の利用目的</h2>
      <ul>
        <li>Pinterest向け画像および投稿文を作成するため</li>
        <li>投稿内容と投稿先をユーザー本人に確認してもらうため</li>
        <li>本人が選択したPinterestボードへPinを投稿するため</li>
        <li>APIへの接続確認、ボード一覧表示、投稿結果確認を行うため</li>
        <li>同じ画像と記事URLによる二重投稿を防止するため</li>
      </ul>
      <p>本アプリは、ユーザー本人が内容を確認して投稿ボタンを押した場合にのみ投稿処理を行います。</p>
    </section>

    <section id="token">
      <h2>3. Pinterestアクセストークンの取り扱い</h2>
      <ul>
        <li>Pinterest公式の認証手続きで発行されたトークンを、本人がローカルの<code>.env</code>へ設定します。</li>
        <li>トークンはPinterest APIの認証にのみ利用し、ソースコードや本公開ページへ掲載しません。</li>
        <li><code>.env</code>はGitの管理対象外とし、トークンを投稿履歴へ保存しません。</li>
        <li>現在、OAuth認可コード交換、トークン更新、連携解除の完全自動化機能はありません。</li>
      </ul>
    </section>

    <section id="retention">
      <h2>4. 保存期間</h2>
      <p>現在の本アプリには、ローカル情報を一定期間後に自動削除する機能はありません。</p>
      <ul>
        <li>入力情報と生成文は、主にアプリ実行中のセッション内で保持されます。</li>
        <li>生成画像は、本人がローカルの<code>output</code>フォルダから削除するまで保存されます。</li>
        <li>投稿履歴は、本人が<code>data/post_history.db</code>を削除するまで保存されます。</li>
        <li>認証情報は、本人が<code>.env</code>から削除または変更するまで保存されます。</li>
      </ul>
    </section>

    <section id="third-parties">
      <h2>5. 第三者提供の有無</h2>
      <p>本アプリの運営者は、取り扱う情報を販売せず、広告目的で第三者へ提供しません。</p>
      <ul>
        <li><strong>Pinterest:</strong> 接続確認、ボード取得、および本人が確認したPinの投稿に必要な情報を公式APIへ送信します。</li>
        <li><strong>OpenAI（任意）:</strong> AI生成を有効にした場合のみ、記事URL、タイトル、要約、キーワードをAPIへ送信します。</li>
      </ul>
      <p>外部サービスへ送信された情報は、各サービスの規約およびプライバシーポリシーに従って取り扱われます。</p>
    </section>

    <section id="disconnect">
      <h2>6. Pinterest連携を解除する方法</h2>
      <ol>
        <li>本アプリを終了します。</li>
        <li><code>.env</code>のPinterestアクセストークンの値を削除して保存します。</li>
        <li>必要に応じてPinterestの設定画面から本アプリへのアクセスを解除します。</li>
      </ol>
      <p>本アプリ内には、Pinterest側でトークンを失効させる機能はありません。</p>
    </section>

    <section id="deletion">
      <h2>7. 情報の削除方法</h2>
      <ul>
        <li>入力中の情報は、本アプリを終了することでセッションから削除できます。</li>
        <li>生成画像は、ローカルの<code>output</code>フォルダから削除できます。</li>
        <li>投稿履歴は、本アプリ終了後に<code>data/post_history.db</code>を削除することで消去できます。</li>
        <li>認証情報は、<code>.env</code>内の値またはファイルを削除することで消去できます。</li>
        <li>投稿済みPinは、Pinterest上で本人が削除する必要があります。</li>
      </ul>
    </section>

    <section id="security">
      <h2>8. セキュリティ対策</h2>
      <ul>
        <li>APIキーとアクセストークンをソースコードへ直接記載しません。</li>
        <li><code>.env</code>、生成画像、投稿履歴データベースをGitの管理対象外にします。</li>
        <li>Pinterest公式APIとの通信には、初期設定でHTTPSを使用します。</li>
        <li>投稿前確認、1件テスト投稿、画像ハッシュによる二重投稿防止を行います。</li>
        <li>アプリのデータは本人のローカルPC内で管理し、運営者用の外部データベースを使用しません。</li>
      </ul>
    </section>

    <section id="contact">
      <h2>9. 問い合わせ先</h2>
      <p>
        本アプリは開発者本人のみが利用する個人用ツールです。
        本ポリシーに関する連絡は、Pinterest Developer Appに登録された開発者連絡先を通じて受け付けます。
      </p>
    </section>

    <section id="dates">
      <h2>10. 制定日と更新日</h2>
      <p>制定日: 2026年8月4日<br>最終更新日: 2026年8月5日</p>
    </section>

    <div class="notice">
      <p>本アプリはPinterest、noteまたはOpenAIが提供・運営するサービスではありません。</p>
    </div>
    <a class="action" href="/">アプリ概要へ戻る</a>
  `,
});

const SECURITY_HEADERS = {
  "Content-Type": "text/html; charset=UTF-8",
  "Cache-Control": "public, max-age=300",
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

function htmlResponse(html, method = "GET") {
  return new Response(method === "HEAD" ? null : html, {
    status: 200,
    headers: SECURITY_HEADERS,
  });
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD", "Content-Type": "text/plain; charset=UTF-8" },
      });
    }

    if (url.pathname === "/") {
      return htmlResponse(HOME_HTML, request.method);
    }

    if (url.pathname === "/privacy" || url.pathname === "/privacy.html") {
      return htmlResponse(PRIVACY_HTML, request.method);
    }

    return new Response("Not Found", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=UTF-8" },
    });
  },
};

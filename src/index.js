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
      <p>© 2026 AI Operating System — 個人利用のAI制作・SNS投稿支援アプリ</p>
    </footer>
  </main>
</body>
</html>`;
}

const HOME_HTML = layout({
  title: "AI Operating System | アプリ概要",
  description: "AIによる調査・制作と、本人承認によるSNS投稿を支援する個人用アプリの概要",
  body: `
    <div class="brand"><span class="brand-dot" aria-hidden="true"></span>Application overview</div>
    <h1>AI Operating<br>System</h1>
    <p class="lead">
      複数のAIによる調査・企画・コンテンツ制作と、TikTokなど各SNSへの投稿を支援する
      個人利用のアプリです。公開前には必ず利用者本人が内容と投稿先を確認します。
    </p>
    <span class="pill">Personal use only</span>

    <div class="features" aria-label="アプリの特徴">
      <section class="feature">
        <h3>調査・制作</h3>
        <p>複数のAIを使い、根拠を確認しながら企画、文章、画像、短尺動画の案を作成します。</p>
      </section>
      <section class="feature">
        <h3>本人が最終確認</h3>
        <p>投稿前に、本文、メディア、公開範囲、商用コンテンツ表示、投稿先を本人が確認します。</p>
      </section>
      <section class="feature">
        <h3>公式APIで投稿</h3>
        <p>明示的な同意後だけ、TikTokなど各サービスの公式APIを使って本人のアカウントへ投稿します。</p>
      </section>
    </div>

    <div class="notice">
      <p>
        本アプリは開発者本人が使用する個人用ツールであり、
        一般ユーザー向けに提供するサービスではありません。
      </p>
    </div>

    <a class="action" href="/privacy">プライバシーポリシー</a>
    <a class="action" href="/terms">利用規約</a>
  `,
});

const PRIVACY_HTML = layout({
  title: "プライバシーポリシー | Note to Automation",
  description: "Note to Automationにおける情報の取り扱いについて",
  body: `
    <div class="brand"><span class="brand-dot" aria-hidden="true"></span>Privacy policy</div>
    <h1>プライバシー<br>ポリシー</h1>
    <p class="lead">
      本ポリシーは、個人利用の「AI Operating System」（以下「本アプリ」）における
      情報の取り扱いを説明するものです。
    </p>

    <section id="information-collected">
      <h2>1. 取得する情報</h2>
      <ul>
        <li>利用者が入力するテーマ、URL、タイトル、要約、キーワード、制作指示</li>
        <li>利用者が選択する画像・動画と、AIが生成した文章・画像・動画案</li>
        <li>TikTokなど接続先APIから取得するアカウント表示情報、公開範囲、投稿IDおよび処理結果</li>
        <li>ローカルに保存する承認履歴、投稿日時、投稿状態、ファイルの保存場所とハッシュ値、エラー内容</li>
        <li>SNSのOAuthトークン、および利用者が設定したAIサービスのAPIキー</li>
      </ul>
      <p>
        本アプリは接続先SNSのログインパスワードを取得しません。OAuthトークンとAPIキーの本体は
        公開サイトや投稿履歴データベースへ保存しません。
      </p>
    </section>

    <section id="purpose">
      <h2>2. 情報の利用目的</h2>
      <ul>
        <li>市場調査、企画、文章、画像、動画などの制作を支援するため</li>
        <li>投稿内容、公開範囲、商用表示、投稿先を利用者本人に確認してもらうため</li>
        <li>本人が明示的に同意した内容を、本人のSNSアカウントへ投稿するため</li>
        <li>API接続、投稿処理、公開状態、パフォーマンスを確認するため</li>
        <li>承認後の変更や二重投稿を検出し、誤投稿を防止するため</li>
      </ul>
      <p>本アプリは、ユーザー本人が内容を確認して投稿ボタンを押した場合にのみ投稿処理を行います。</p>
    </section>

    <section id="token">
      <h2>3. 認証情報の取り扱い</h2>
      <ul>
        <li>OAuth認証で発行されたトークンは、接続したサービスの公式APIにのみ利用します。</li>
        <li>トークン本体はWindows Credential Managerへ保存し、データベースには参照情報だけを保存します。</li>
        <li>AIサービスのAPIキーと手動設定する認証情報は、Git対象外のローカル<code>.env</code>で管理します。</li>
        <li>認証情報をソースコード、本公開ページ、承認履歴、監査ログへ掲載しません。</li>
      </ul>
    </section>

    <section id="retention">
      <h2>4. 保存期間</h2>
      <p>現在の本アプリには、ローカル情報を一定期間後に自動削除する機能はありません。</p>
      <ul>
        <li>入力情報と生成文は、主にアプリ実行中のセッション内で保持されます。</li>
        <li>生成メディアは、本人がローカルの制作Assetフォルダから削除するまで保存されます。</li>
        <li>承認・投稿・学習履歴は、本人がローカルのアプリデータを削除するまで保存されます。</li>
        <li>認証情報は、本人が連携解除またはローカル設定から削除するまで保存されます。</li>
      </ul>
    </section>

    <section id="third-parties">
      <h2>5. 第三者提供の有無</h2>
      <p>本アプリの運営者は、取り扱う情報を販売せず、広告目的で第三者へ提供しません。</p>
      <ul>
        <li><strong>TikTok等の接続先SNS:</strong> 本人確認、投稿設定取得、および本人が承認したコンテンツの投稿に必要な情報を公式APIへ送信します。</li>
        <li><strong>OpenAI、Anthropic、Google等（任意）:</strong> AI機能を実行した場合のみ、利用者が入力した制作情報を選択したAPIへ送信します。</li>
      </ul>
      <p>外部サービスへ送信された情報は、各サービスの規約およびプライバシーポリシーに従って取り扱われます。</p>
    </section>

    <section id="disconnect">
      <h2>6. SNS連携を解除する方法</h2>
      <ol>
        <li>本アプリの公開・学習センターで対象接続を選択します。</li>
        <li>連携解除を実行し、ローカルのトークンを削除します。</li>
        <li>必要に応じて接続先SNSの設定画面からも本アプリへのアクセスを解除します。</li>
      </ol>
      <p>対応するサービスでは、連携解除時に公式APIを使ったトークン失効も実行します。</p>
    </section>

    <section id="deletion">
      <h2>7. 情報の削除方法</h2>
      <ul>
        <li>入力中の情報は、本アプリを終了することでセッションから削除できます。</li>
        <li>生成メディアと承認・投稿履歴は、本人のローカルPCから削除できます。</li>
        <li>OAuth接続はアプリの連携解除から、APIキーは<code>.env</code>から削除できます。</li>
        <li>投稿済みコンテンツは、各SNS上で本人が削除する必要があります。</li>
      </ul>
    </section>

    <section id="security">
      <h2>8. セキュリティ対策</h2>
      <ul>
        <li>APIキーとアクセストークンをソースコードへ直接記載しません。</li>
        <li><code>.env</code>、生成画像、投稿履歴データベースをGitの管理対象外にします。</li>
        <li>AIサービスとSNSの公式APIとの通信にはHTTPSを使用します。</li>
        <li>OAuth state、PKCE、投稿前承認、Version Lock、Hashによる二重投稿防止を行います。</li>
        <li>アプリのデータは本人のローカルPC内で管理し、運営者用の外部データベースを使用しません。</li>
      </ul>
    </section>

    <section id="contact">
      <h2>9. 問い合わせ先</h2>
      <p>
        本アプリは開発者本人のみが利用する個人用ツールです。
        本ポリシーに関する連絡は、各Developer Appに登録された開発者連絡先を通じて受け付けます。
      </p>
    </section>

    <section id="dates">
      <h2>10. 制定日と更新日</h2>
      <p>制定日: 2026年8月4日<br>最終更新日: 2026年8月30日</p>
    </section>

    <div class="notice">
      <p>本アプリはTikTok、Pinterest、X、Meta、OpenAI、AnthropicまたはGoogleが提供・運営するサービスではありません。</p>
    </div>
    <a class="action" href="/">アプリ概要へ戻る</a>
  `,
});

const TERMS_HTML = layout({
  title: "利用規約 | AI Operating System",
  description: "個人利用のAI Operating Systemに関する利用条件",
  body: `
    <div class="brand"><span class="brand-dot" aria-hidden="true"></span>Terms of use</div>
    <h1>利用規約</h1>
    <p class="lead">
      本規約は、開発者本人が利用する「AI Operating System」（以下「本アプリ」）の
      利用条件を定めるものです。
    </p>

    <section>
      <h2>1. 提供する機能</h2>
      <p>本アプリは、AIによる調査・企画・制作と、利用者本人の承認に基づくSNS投稿を支援します。</p>
    </section>

    <section>
      <h2>2. 投稿前の確認</h2>
      <ul>
        <li>利用者は、本文、メディア、投稿先、公開範囲、広告・AI生成表示を投稿前に確認します。</li>
        <li>著作権、肖像権、商標権、音源利用条件、各SNSの規約を確認する責任は利用者にあります。</li>
        <li>本アプリは、明示的に承認された固定版だけを公式APIへ送信します。</li>
      </ul>
    </section>

    <section>
      <h2>3. 禁止事項</h2>
      <ul>
        <li>違法、有害、権利侵害、虚偽または各SNSの規約に反するコンテンツの投稿</li>
        <li>第三者のアカウントや認証情報の無断使用</li>
        <li>API制限、審査制約、公開範囲または安全制御の回避</li>
      </ul>
    </section>

    <section>
      <h2>4. 外部サービス</h2>
      <p>
        AIおよびSNS機能は各社の公式APIに依存します。障害、仕様変更、審査、利用上限により
        機能が停止または制限される場合があります。
      </p>
    </section>

    <section>
      <h2>5. 免責と停止</h2>
      <p>
        利用者は投稿結果を本人のアカウントで確認します。誤投稿や不正利用のおそれがある場合、
        本アプリの全体停止機能を使い、接続先SNSでも連携を解除します。
      </p>
    </section>

    <section>
      <h2>6. 更新</h2>
      <p>制定日: 2026年8月30日<br>最終更新日: 2026年8月30日</p>
    </section>

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

    if (url.pathname === "/terms" || url.pathname === "/terms.html") {
      return htmlResponse(TERMS_HTML, request.method);
    }

    return new Response("Not Found", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=UTF-8" },
    });
  },
};

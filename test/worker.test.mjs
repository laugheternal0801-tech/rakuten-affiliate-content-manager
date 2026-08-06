import assert from "node:assert/strict";
import test from "node:test";

import worker from "../src/index.js";

const BASE_URL = "https://note-to-automation.example";

async function fetchPath(path, init) {
  return worker.fetch(new Request(`${BASE_URL}${path}`, init));
}

test("GET / returns the Japanese app overview with status 200", async () => {
  const response = await fetchPath("/");
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /text\/html/);
  assert.match(html, /Note to Automation/);
  assert.match(html, /個人利用のPinterest投稿支援アプリ/);
  assert.match(html, /一般ユーザー向けに提供するサービスではありません/);
  assert.match(html, /href="\/privacy"/);
  assert.match(html, /viewport/);
});

for (const path of ["/privacy", "/privacy.html"]) {
  test(`GET ${path} returns the same privacy policy with status 200`, async () => {
    const response = await fetchPath(path);
    const html = await response.text();

    assert.equal(response.status, 200);
    assert.match(html, /プライバシーポリシー/);
    assert.match(html, /取得する情報/);
    assert.match(html, /第三者提供の有無/);
    assert.match(html, /2026年8月5日/);
  });
}

test("both privacy routes return identical content", async () => {
  const privacy = await (await fetchPath("/privacy")).text();
  const privacyHtml = await (await fetchPath("/privacy.html")).text();
  assert.equal(privacy, privacyHtml);
});

test("HEAD routes return 200 without a body", async () => {
  for (const path of ["/", "/privacy", "/privacy.html"]) {
    const response = await fetchPath(path, { method: "HEAD" });
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "");
  }
});

test("unknown paths and unsupported methods are rejected", async () => {
  assert.equal((await fetchPath("/missing")).status, 404);
  assert.equal((await fetchPath("/", { method: "POST" })).status, 405);
});

test("public source does not contain credential values or personal contact data", async () => {
  const source = await (await import("node:fs/promises")).readFile(
    new URL("../src/index.js", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(source, /pina_[A-Za-z0-9_-]+/);
  assert.doesNotMatch(source, /Bearer\s+[A-Za-z0-9._-]+/);
  assert.doesNotMatch(source, /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { readApiJson } from "../lib/api-response.ts";

test("never displays Render 502 HTML, CSS, or embedded base64", async () => {
  const html = '<html><style>@font-face { font-family: "Roboto"; src: url(data:font/woff2;base64,VERY_PRIVATE_BLOB); }</style></html>';
  const response = new Response(html, { status: 502, headers: { "content-type": "text/html" } });
  await assert.rejects(readApiJson(response), (error) => {
    assert.match(error.message, /HTTP 502/);
    assert.doesNotMatch(error.message, /Roboto|@font-face|base64|VERY_PRIVATE_BLOB|<html>/);
    assert.ok(error.message.length < 180);
    return true;
  });
});

test("preserves short actionable JSON API errors", async () => {
  const response = Response.json({ detail: "GitHub request limit reached. Try again later." }, { status: 400 });
  await assert.rejects(readApiJson(response), /GitHub request limit reached/);
});

test("returns normal successful JSON", async () => {
  const data = { repo_id: "abc123", issue: { number: 18613 } };
  assert.deepEqual(await readApiJson(Response.json(data)), data);
});

test("rejects invalid JSON even with HTTP 200 without leaking body", async () => {
  const response = new Response('<style>SECRET_CSS</style>', { status: 200 });
  await assert.rejects(readApiJson(response), (error) => {
    assert.match(error.message, /HTTP 200/);
    assert.doesNotMatch(error.message, /SECRET_CSS/);
    return true;
  });
});

test("does not display very long JSON detail", async () => {
  const response = Response.json({ detail: "x".repeat(10000) }, { status: 400 });
  await assert.rejects(readApiJson(response), (error) => {
    assert.ok(error.message.length < 200);
    return true;
  });
});

// Run against the site's HTTP server and a headless Chrome debugging endpoint.
// No dependencies: Node's built-in WebSocket speaks Chrome DevTools Protocol.
import assert from "node:assert/strict";

const origin = process.env.SITE_ORIGIN || "http://127.0.0.1:8792";
const debuggerOrigin = process.env.CHROME_DEBUGGER || "http://127.0.0.1:8793";
const target = await (await fetch(`${debuggerOrigin}/json/new?about:blank`, { method: "PUT" })).json();
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(resolve => socket.addEventListener("open", resolve, { once: true }));
let nextId = 0;
const pending = new Map();
const listeners = new Map();
const errors = [];
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++nextId;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
});
socket.addEventListener("message", ({ data }) => {
  const event = JSON.parse(data);
  if (event.id) {
    const request = pending.get(event.id);
    pending.delete(event.id);
    if (event.error) request.reject(new Error(JSON.stringify(event.error)));
    else request.resolve(event.result);
  } else {
    for (const handler of listeners.get(event.method) || []) handler(event.params);
  }
});
const on = (name, handler) => listeners.set(name, [...(listeners.get(name) || []), handler]);
const evaluate = async expression => {
  const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async expression => {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(expression)) return;
    await wait(50);
  }
  throw new Error(`Timed out: ${expression}`);
};
let fixture;
let hold = false;
let held = [];
const fulfill = (requestId, data, responseCode = 200) => send("Fetch.fulfillRequest", {
  requestId, responseCode, responseHeaders: [{ name: "Content-Type", value: "application/json" }],
  body: Buffer.from(JSON.stringify(data)).toString("base64")
});
on("Runtime.exceptionThrown", event => errors.push(event.exceptionDetails));
on("Runtime.consoleAPICalled", event => { if (event.type === "error") errors.push(event); });
on("Fetch.requestPaused", event => {
  if (hold) held.push(event);
  else if (fixture !== undefined && event.request.url.endsWith("/data/progress.json")) fulfill(event.requestId, fixture);
  else send("Fetch.continueRequest", { requestId: event.requestId });
});
await send("Page.enable");
await send("Runtime.enable");
await send("Fetch.enable", { patterns: [{ urlPattern: "*/data/*.json" }] });
const navigate = async page => {
  await send("Page.navigate", { url: `${origin}/${page}` });
  await until(`document.readyState === 'complete' && location.pathname === '/${page}'`);
};
const rendered = () => until("document.querySelectorAll('[data-grid] > section').length === 7");
const clean = async () => {
  assert.equal(errors.length, 0, JSON.stringify(errors));
  assert.equal(await evaluate("!!document.querySelector('[onerror], [onload], img[src=x]') || !!window.injected"), false);
};
const metric = label => evaluate(`Array.from(document.querySelectorAll('.kv > div')).find(el => el.querySelector('.k').textContent === ${JSON.stringify(label)}).querySelector('.num').textContent`);
try {
  for (const page of ["index.html", "ideas.html", "factory.html", "progress.html"]) {
    await navigate(page);
    await until("document.querySelector('[data-state]').textContent !== ''");
    if (page === "progress.html") await rendered();
    await clean();
    console.log(`PASS ${page}: no JavaScript console errors`);
  }
  for (const data of [{}, { worlds: [{ charter: { edition: 1 } }] }, { worlds: [{ evaluation: { tiers_reached: 2 }, consequences: {}, versions: [{}], objectives: [{}] }] }]) {
    fixture = data;
    await navigate("progress.html");
    await rendered();
    assert.equal(await evaluate("document.querySelector('[data-grid]').textContent.includes('undefined') || document.querySelector('[data-grid]').textContent.includes('NaN')"), false);
    assert.equal(await evaluate("document.querySelector('[data-headline]').textContent"), "—");
    await clean();
    console.log(`PASS partial fixture ${JSON.stringify(data)}`);
  }
  const attack = '\"><img src=x onerror="window.injected=true">';
  fixture = { status: { headline: attack }, worlds: [{ name: attack, versions: [{ v: attack, from: attack }], pathologies: { stable_failure: { now: attack, history: [attack, "__proto__", "constructor"] } }, evaluation: { compute_share: { evaluators: attack }, variance: [attack] } }] };
  await navigate("progress.html");
  await rendered();
  await wait(100);
  assert.equal(await evaluate("document.querySelector('[data-state]').textContent"), attack);
  assert.equal(await evaluate("document.querySelector('.vword').textContent"), "—");
  await clean();
  console.log("PASS malicious headline, verdict/history, version and numeric values are inert");
  for (const spend of [{ hosting: 2000000 }, { data: 2000000 }, {}, { data: 0, hosting: 0 }, { data: 1000000, hosting: 2000000 }]) {
    fixture = { worlds: [{ consequences: { spend_micro_usd: spend } }] };
    await navigate("progress.html");
    await rendered();
    const expected = Object.hasOwn(spend, "data") && Object.hasOwn(spend, "hosting") ? (spend.data ? "$3.00" : "$0.00") : "—";
    assert.equal(await metric("Data and hosting"), expected);
  }
  console.log("PASS missing spend stays unknown; explicit zero remains $0.00");
  fixture = undefined;
  for (const failStale of [false, true]) {
    hold = true; held = [];
    await navigate("progress.html");
    await until("!!document.querySelector('[data-example]')");
    await evaluate("document.querySelector('[data-example]').click(); document.querySelector('[data-example]').click(); document.querySelector('[data-example]').click()");
    for (let i = 0; i < 100 && held.length < 3; i++) await wait(20);
    assert.equal(held.length, 3);
    const realRequest = held.find(event => event.request.url.endsWith("progress.json"));
    const examples = held.filter(event => event.request.url.endsWith("example.json"));
    await fulfill(examples[1].requestId, { example: true, status: { headline: "SELECTED EXAMPLE" } });
    await rendered();
    if (failStale) {
      await send("Fetch.failRequest", { requestId: examples[0].requestId, errorReason: "Failed" });
      await send("Fetch.failRequest", { requestId: realRequest.requestId, errorReason: "Failed" });
    } else {
      await fulfill(examples[0].requestId, { example: true, status: { headline: "STALE EXAMPLE" } });
      await fulfill(realRequest.requestId, { status: { headline: "STALE REAL" } });
    }
    await wait(150);
    assert.equal(await evaluate("document.querySelector('[data-headline]').textContent"), "SELECTED EXAMPLE");
    assert.equal(await evaluate("document.querySelector('[data-example]').getAttribute('aria-pressed')"), "true");
    assert.equal(await evaluate("document.querySelector('[data-obs]').classList.contains('is-example')"), true);
    console.log(`PASS rapid toggle: stale ${failStale ? "errors" : "successes"} cannot replace selected source`);
    hold = false;
  }
  hold = true; held = [];
  await navigate("progress.html");
  await evaluate("document.querySelector('[data-example]').click(); document.querySelector('[data-example]').click()");
  for (let i = 0; i < 100 && held.length < 2; i++) await wait(20);
  assert.equal(held.length, 2);
  await fulfill(held.find(event => event.request.url.endsWith('progress.json')).requestId, { status: { headline: 'SELECTED REAL' } });
  await rendered();
  await fulfill(held.find(event => event.request.url.endsWith('example.json')).requestId, { example: true, status: { headline: 'STALE EXAMPLE' } });
  await wait(100);
  assert.equal(await evaluate("document.querySelector('[data-headline]').textContent"), 'SELECTED REAL');
  assert.equal(await evaluate("document.querySelector('[data-example]').getAttribute('aria-pressed')"), 'false');
  assert.equal(await evaluate("document.querySelector('[data-obs]').classList.contains('is-example')"), false);
  hold = false;
  console.log('PASS rapid toggle ending on real ignores delayed example');
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] });
  await navigate("index.html");
  await evaluate("document.querySelector('[data-field]').scrollIntoView()");
  await wait(300);
  const count = () => evaluate("document.querySelector('[data-count]').textContent");
  let before = await count();
  await wait(350);
  assert.notEqual(await count(), before);
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await wait(100);
  before = await count();
  const still = await evaluate("document.querySelector('canvas').toDataURL()");
  await wait(350);
  assert.equal(await count(), before);
  assert.equal(await evaluate("document.querySelector('canvas').toDataURL()"), still);
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] });
  await wait(350);
  assert.notEqual(await count(), before);
  assert.equal(await evaluate("Object.isFrozen(window.FL) && !Object.getOwnPropertyDescriptor(window, 'FL').writable && !Object.getOwnPropertyDescriptor(window, 'FL').configurable"), true);
  await clean();
  console.log("PASS reduced motion dynamically stops/restarts canvas; helper interface frozen and nonreplaceable");
  console.log("PASS all browser regressions");
} finally {
  await send("Page.close");
  socket.close();
}

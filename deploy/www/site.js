(function () {
  "use strict";

  var SAMPLE = new URLSearchParams(location.search).get("sample") === "1";
  var SOURCE = SAMPLE ? "wake.sample.json" : "wake.json";
  var DAY_NS = 86400e9;
  var DASH = "—";

  function $(id) { return document.getElementById(id); }

  function num(v) {
    return (typeof v === "number" && isFinite(v)) ? v : null;
  }

  function usd(micro) {
    var n = num(micro);
    if (n === null) return DASH;
    return (n / 1e6).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function count(v) {
    var n = num(v);
    return n === null ? DASH : n.toLocaleString("en-US");
  }

  function dateUTC(ns) {
    var n = num(ns);
    if (n === null) return DASH;
    return new Date(n / 1e6).toISOString().slice(0, 16).replace("T", " ") + " UTC";
  }

  function text(tag, content, cls) {
    var el = document.createElement(tag);
    el.textContent = content;
    if (cls) el.className = cls;
    return el;
  }

  function table(headers, rows) {
    var wrap = document.createElement("div");
    wrap.className = "scroll";
    var t = document.createElement("table");
    var tr = document.createElement("tr");
    headers.forEach(function (h) { tr.appendChild(text("th", h)); });
    t.appendChild(tr);
    rows.forEach(function (cells) {
      var r = document.createElement("tr");
      cells.forEach(function (c) { r.appendChild(text("td", c[0], c[1])); });
      t.appendChild(r);
    });
    wrap.appendChild(t);
    return wrap;
  }

  function replace(id, node) {
    var host = $(id);
    host.textContent = "";
    if (typeof node === "string") host.textContent = node;
    else host.appendChild(node);
  }

  function pairs(id, rows) {
    var dl = $(id);
    dl.textContent = "";
    rows.forEach(function (row) {
      dl.appendChild(text("dt", row[0]));
      dl.appendChild(text("dd", row[1]));
    });
  }

  function series(wake) {
    var ws = wake && wake.wallet_series;
    var s = ws && Array.isArray(ws.series) ? ws.series : [];
    return s.filter(function (p) { return num(p.ts) !== null && num(p.balance) !== null; });
  }

  function startNs(wake) {
    var s = series(wake);
    if (s.length) return s[0].ts;
    var last = num(wake.last_event_time_ns), up = num(wake.uptime_ns);
    if (last !== null && up !== null) return last - up;
    return null;
  }

  function ticks(lo, hi, n) {
    if (hi <= lo) hi = lo + 1;
    var raw = (hi - lo) / n;
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var step = [1, 2, 5, 10].map(function (m) { return m * mag; })
      .filter(function (s) { return s >= raw; })[0];
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + step / 2; v += step) out.push(+v.toFixed(10));
    return { values: out, step: step };
  }

  function label(v, step) {
    var d = step >= 1 ? 0 : Math.min(6, Math.ceil(-Math.log10(step)));
    return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  function chart(wake) {
    var s = series(wake);
    if (s.length < 2) return DASH;
    var start = s[0].ts;
    var pts = s.map(function (p) { return [(p.ts - start) / DAY_NS, p.balance / 1e6]; });
    var xs = pts.map(function (p) { return p[0]; }), ys = pts.map(function (p) { return p[1]; });
    var xt = ticks(0, Math.max.apply(null, xs), 5);
    var ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
    if (ymax === ymin) { ymin -= 1; ymax += 1; }
    var yt = ticks(ymin, ymax, 4);
    var x0 = 0, x1 = xt.values[xt.values.length - 1];
    var y0 = yt.values[0], y1 = yt.values[yt.values.length - 1];
    if (y0 > ymin) y0 = ymin; if (y1 < ymax) y1 = ymax;

    var host = $("chart");
    var W = Math.max(300, Math.min(640, host.clientWidth || 640));
    var H = Math.round(W * 0.42), L = 64, R = 12, T = 12, B = 36;
    var sx = function (x) { return L + (x - x0) / (x1 - x0) * (W - L - R); };
    var sy = function (y) { return T + (y1 - y) / (y1 - y0) * (H - T - B); };

    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Thinking wallet, USD, over days since start");

    function line(a, b, c, d) {
      var el = document.createElementNS(ns, "line");
      el.setAttribute("x1", a); el.setAttribute("y1", b);
      el.setAttribute("x2", c); el.setAttribute("y2", d);
      el.setAttribute("class", "axis");
      svg.appendChild(el);
    }
    function tx(x, y, s, anchor) {
      var el = document.createElementNS(ns, "text");
      el.setAttribute("x", x); el.setAttribute("y", y);
      el.setAttribute("text-anchor", anchor);
      el.textContent = s;
      svg.appendChild(el);
    }

    line(L, T, L, H - B);
    line(L, H - B, W - R, H - B);
    yt.values.forEach(function (v) {
      if (v < y0 || v > y1) return;
      var y = sy(v);
      line(L - 4, y, L, y);
      tx(L - 8, y + 4, label(v, yt.step), "end");
    });
    xt.values.forEach(function (v) {
      var x = sx(v);
      line(x, H - B, x, H - B + 4);
      tx(x, H - B + 18, label(v, xt.step), "middle");
    });
    tx(W - R, H - 4, "days since start", "end");

    var path = document.createElementNS(ns, "polyline");
    path.setAttribute("class", "line");
    path.setAttribute("points", pts.map(function (p) {
      return sx(p[0]).toFixed(1) + "," + sy(p[1]).toFixed(1);
    }).join(" "));
    svg.appendChild(path);
    return svg;
  }

  function status(wake, ended) {
    if (!wake) {
      pairs("status", [
        ["State", "Not started"], ["Days running", DASH], ["Thinking wallet (USD)", DASH],
        ["Trading equity (USD)", DASH], ["Realised profit or loss (USD)", DASH],
        ["Model calls made", DASH], ["Last update (UTC)", DASH]
      ]);
      return;
    }
    var s = series(wake);
    var wallet = s.length ? s[s.length - 1].balance : null;
    var pf = wake.portfolio || {};
    var calls = null;
    var inv = wake.invocations_by_assembly && wake.invocations_by_assembly.counts;
    if (inv && typeof inv === "object") {
      calls = Object.keys(inv).reduce(function (a, k) { return a + (num(inv[k]) || 0); }, 0);
    }
    var up = num(wake.uptime_ns);
    var rows = [["State", ended ? "Ended" : "Running"]];
    if (up !== null) rows.push(["Days running", (up / DAY_NS).toFixed(2)]);
    if (num(wallet) !== null) rows.push(["Thinking wallet (USD)", usd(wallet)]);
    if (num(pf.equity_micro) !== null) rows.push(["Trading equity (USD)", usd(pf.equity_micro)]);
    if (num(pf.realized_to_date_micro) !== null) rows.push(["Realised profit or loss (USD)", usd(pf.realized_to_date_micro)]);
    if (calls !== null) rows.push(["Model calls made", count(calls)]);
    if (num(wake.last_event_time_ns) !== null) rows.push(["Last update (UTC)", dateUTC(wake.last_event_time_ns)]);
    pairs("status", rows);
  }

  function population(wake) {
    var roster = wake && wake.roster;
    var current = roster && Array.isArray(roster.current) ? roster.current : [];
    var rows = [];
    current.forEach(function (r) {
      var n = num(r.count) || 1;
      for (var i = 0; i < n; i++) rows.push([[String(r.kind), ""], [String(r.model_id), "mono"]]);
    });
    if (!rows.length) return DASH;
    return table(["Role", "Model"], rows);
  }

  function transfers(wake) {
    var pots = wake && wake.pots;
    var list = pots && Array.isArray(pots.transfers) ? pots.transfers : [];
    if (!list.length) return "None";
    return table(["Date (UTC)", "Direction", "Amount (USD)", "Status"], list.map(function (t) {
      return [[dateUTC(t.ts_ns), "mono"], [t.direction ? String(t.direction) : DASH, ""],
              [usd(t.amount_micro), "num"], [String(t.status || DASH), ""]];
    }));
  }

  function pots(wake) {
    var c = (wake && wake.pots && wake.pots.current) || {};
    pairs("pots", [
      ["Venue (USD)", usd(c.venue)], ["Reserve (USD)", usd(c.reserve)],
      ["Venice (USD)", usd(c.venice)], ["OpenRouter (USD)", usd(c.seed)]
    ]);
  }

  function amendments(wake) {
    var ch = wake && wake.charter;
    var list = ch && Array.isArray(ch.amendments) ? ch.amendments : [];
    if (!list.length) return "None";
    return table(["Date (UTC)", "What changed", "Outcome"], list.map(function (a) {
      var parts = [];
      if (a.add && a.add.length) parts.push("added " + a.add.join(", "));
      if (a.replace && a.replace.length) parts.push("replaced " + a.replace.join(", "));
      if (a.remove && a.remove.length) parts.push("removed " + a.remove.join(", "));
      var what = parts.length ? parts.join("; ") : DASH;
      var outcome;
      if (num(a.activated) !== null) outcome = "activated" + (num(a.edition) !== null ? ", edition " + a.edition : "");
      else if (num(a.refused) !== null) outcome = "refused" + (a.reason ? ": " + a.reason : "");
      else if (num(a.passed) !== null) outcome = "passed";
      else outcome = "proposed";
      var when = a.proposed !== null && a.proposed !== undefined ? a.proposed : (a.activated || a.refused || a.passed);
      return [[dateUTC(when), "mono"], [what, "wrap"], [outcome, "wrap"]];
    }));
  }

  var last = null;
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (last) replace("chart", chart(last)); }, 150);
  });

  function render(wake, ended) {
    last = wake;
    $("sample").hidden = !SAMPLE;
    var start = wake ? startNs(wake) : null;
    $("started").textContent = start === null ? "Not started" : "Started " + dateUTC(start);
    status(wake, ended);
    replace("chart", wake ? chart(wake) : DASH);
    replace("population", wake ? population(wake) : DASH);
    replace("transfers", wake ? transfers(wake) : "None");
    pots(wake);
    replace("amendments", wake ? amendments(wake) : "None");
    $("postmortem").hidden = !ended;
  }

  function load() {
    var wake = null, ended = false;
    var a = fetch(SOURCE, { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { wake = (j && typeof j === "object") ? j : null; })
      .catch(function () {});
    var b = SAMPLE ? Promise.resolve() : fetch("postmortem.html", { method: "HEAD", cache: "no-store" })
      .then(function (r) { ended = r.ok; })
      .catch(function () {});
    Promise.all([a, b]).then(function () { render(wake, ended); });
  }

  load();
  setInterval(load, 60000);
})();

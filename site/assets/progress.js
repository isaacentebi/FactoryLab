/* FactoryLab — the observatory. Renders data/progress.json (real) or, on request,
   data/example.json (fabricated, always under an EXAMPLE banner). Aggregates only. */
(function () {
  "use strict";

  var root = document.querySelector("[data-obs]");
  if (!root) return;
  var grid = root.querySelector("[data-grid]");
  var tabs = document.querySelector("[data-worlds]");
  var toggle = document.querySelector("[data-example]");
  var headEl = document.querySelector("[data-headline]");
  var lineEl = document.querySelector("[data-line]");
  var dot = document.querySelector("[data-dot]");
  var liveEl = document.querySelector("[data-live]");
  var updEl = document.querySelector("[data-updated]");

  var real = null, example = null, showing = "real", current = 0;

  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  };
  var DASH = '<span class="dash">—</span>';
  var fmtDay = function (iso) { return iso ? window.FL.fmtDate(String(iso).slice(0, 10)) : DASH; };
  // Money arrives as integer micro-USD; the page shows whole dollars and cents only.
  var usd = function (micro) {
    if (typeof micro !== "number") return DASH;
    var cents = Math.round(micro / 10000);
    return "$" + (cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  var pct = function (x) { return typeof x === "number" ? Math.round(x * 100) + "%" : DASH; };
  var num = function (x) { return typeof x === "number" ? x.toLocaleString("en-US") : DASH; };

  var PATHOLOGIES = [
    ["stable_failure", "Stable failure"],
    ["thrash", "Thrash"],
    ["overfitting", "Overfitting"],
    ["learning_death", "Learning death"]
  ];
  var VERDICT = { present: "Present", absent: "Absent", unknown: "Can't tell yet" };

  var spark = function (label, series, fmt) {
    var id = "s" + Math.random().toString(36).slice(2, 8);
    if (!series || !series.length) {
      return '<div class="spark"><p class="k">' + esc(label) + '</p><svg viewBox="0 0 200 56" preserveAspectRatio="none" aria-hidden="true">' +
        '<line x1="0" y1="54" x2="200" y2="54" stroke="#2b2b28" stroke-width="1" stroke-dasharray="3 4"/></svg><p class="val">' + DASH + "</p></div>";
    }
    var max = Math.max.apply(null, series), min = Math.min.apply(null, series);
    if (max === min) { max += 1; }
    var n = series.length;
    var pts = series.map(function (v, i) {
      var x = n === 1 ? 100 : (i / (n - 1)) * 200;
      var y = 50 - ((v - min) / (max - min)) * 44;
      return [x, y];
    });
    var last = pts[pts.length - 1];
    return '<div class="spark" data-spark="' + id + '" data-series="' + esc(JSON.stringify(series)) + '" data-fmt="' + (fmt || "") + '">' +
      '<p class="k">' + esc(label) + "</p>" +
      '<svg viewBox="0 0 200 56" preserveAspectRatio="none" role="img" aria-label="' + esc(label) + ", " + n + ' daily samples, latest ' + esc(series[n - 1]) + '">' +
      '<polyline fill="none" stroke="#ecebe4" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" points="' +
      pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ") + '"/>' +
      '<line class="cross" x1="0" x2="0" y1="0" y2="56" stroke="#6d6b65" stroke-width="1" vector-effect="non-scaling-stroke" visibility="hidden"/>' +
      '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="3" fill="#ff5a1f"/></svg>' +
      '<p class="val">' + (fmt === "pct" ? pct(series[n - 1]) : series[n - 1]) + " <span>latest · day " + n + "</span></p></div>";
  };

  var wireSparks = function () {
    grid.querySelectorAll("[data-spark]").forEach(function (el) {
      var svg = el.querySelector("svg"), cross = el.querySelector(".cross"), val = el.querySelector(".val");
      var series = JSON.parse(el.getAttribute("data-series"));
      var fmt = el.getAttribute("data-fmt");
      var show = function (i) {
        var v = series[i];
        val.innerHTML = (fmt === "pct" ? pct(v) : v) + " <span>day " + (i + 1) + "</span>";
      };
      var reset = function () { cross.setAttribute("visibility", "hidden"); show(series.length - 1); val.querySelector("span").textContent = "latest · day " + series.length; };
      svg.addEventListener("pointermove", function (e) {
        var r = svg.getBoundingClientRect();
        var f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
        var i = Math.round(f * (series.length - 1));
        var x = series.length === 1 ? 100 : (i / (series.length - 1)) * 200;
        cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
        show(i);
      });
      svg.addEventListener("pointerleave", reset);
    });
  };

  var render = function (data) {
    var w = (data.worlds || [])[current] || null;
    var isEx = !!data.example;
    root.classList.toggle("is-example", isEx);

    // status
    headEl.textContent = data.status.headline;
    lineEl.textContent = data.status.line;
    dot.classList.toggle("on", !!data.status.live);
    liveEl.textContent = data.status.live ? (isEx ? "Example, not live" : "Live") : "Not live";
    updEl.textContent = isEx ? "invented data" : "updated " + fmtDay(data.updated);

    // world tabs
    var worlds = data.worlds || [];
    tabs.innerHTML = '<span class="k" style="margin-right:8px">Worlds</span>' + (worlds.length
      ? worlds.map(function (x, i) {
          return '<button type="button" class="btn" aria-pressed="' + (i === current) + '" data-w="' + i + '">' + esc(x.name) +
            ' <span class="chip ' + (x.state === "running" ? "open" : "") + '">' + esc(x.state) + "</span></button>";
        }).join("")
      : '<span class="chip planned">none launched yet</span>');
    tabs.querySelectorAll("[data-w]").forEach(function (b) {
      b.addEventListener("click", function () { current = +b.getAttribute("data-w"); render(data); });
    });

    var html = "";

    // 1. world and versions
    var days = w && w.genesis ? Math.max(1, Math.round(((w.ended ? Date.parse(w.ended.at) : Date.parse(data.updated)) - Date.parse(w.genesis)) / 864e5)) : null;
    html += '<section class="w4"><p class="k">World</p><h3>' + (w ? esc(w.name) : "No world yet") + "</h3>" +
      '<div class="kv">' +
        '<div><p class="k">Genesis</p><p class="num">' + (w ? fmtDay(w.genesis) : DASH) + "</p></div>" +
        '<div><p class="k">Venue</p><p class="num" style="font-size:16px;line-height:1.3">' + (w ? esc(w.venue) : DASH) + "</p></div>" +
        '<div><p class="k">State</p><p class="num">' + (w ? esc(w.state) + (w.ended ? " · " + esc(w.ended.cause) : "") : DASH) + "</p></div>" +
        '<div><p class="k">Days</p><p class="num">' + (days != null ? days : DASH) + "</p></div>" +
      "</div>";
    html += '<p class="k" style="margin-top:8px">Behavioural versions</p>';
    if (w && w.versions && w.versions.length) {
      var total = w.versions.reduce(function (a, v) {
        var end = v.to ? Date.parse(v.to) : Date.parse(data.updated);
        return a + Math.max(1, end - Date.parse(v.from));
      }, 0);
      html += '<div class="track">' + w.versions.map(function (v, i) {
        var end = v.to ? Date.parse(v.to) : Date.parse(data.updated);
        var share = Math.max(1, end - Date.parse(v.from)) / total;
        return '<span class="' + (v.to ? "" : "cur") + '" style="flex:' + share.toFixed(4) + '" title="v' + v.v + " from " + esc(v.from) + '">v' + v.v + "</span>";
      }).join("") + "</div>";
      html += '<ol class="vlist">' + w.versions.map(function (v) {
        return "<li><b>v" + v.v + "</b><span>" + fmtDay(v.from) + (v.to ? " to " + fmtDay(v.to) : ", current") +
          " · spectral gap " + esc(v.gap) + ". " + esc(v.note) + "</span></li>";
      }).join("") + "</ol>";
    } else {
      html += '<div class="track empty-track" aria-hidden="true"></div><p class="empty">A version is a behaviour, not a configuration: a regime the factory settles into. Each new regime appears here with the date it settled and its spectral gap.</p>';
    }
    html += "</section>";

    // 2. what stays private
    html += '<section class="w2"><p class="k">Never published</p><h3>Minimal disclosure</h3>' +
      '<ul class="private-list"><li>Any seat\'s own scores, history or learner state</li><li>Who judged whom, prompts, router menus</li><li>Wallet addresses, accounts, order or client identifiers</li><li>Balances and open positions</li><li>The diary itself, while the world lives</li></ul>' +
      '<p class="empty">The schematics are public; local state is private. This page shows aggregates and public facts only.</p></section>';

    // 3. objectives: the heart of Class 3
    html += '<section class="w6"><p class="k">Objectives <span class="sig">/</span> the heart of Class 3</p><h3>What the factory decided to want</h3>';
    if (w && w.objectives && w.objectives.length) {
      html += '<ul class="objectives">' + w.objectives.map(function (o) {
        return '<li class="' + esc(o.state) + '"><span class="when">' + fmtDay(o.at) + '</span><span class="what">' + esc(o.text) +
          "<small>via " + esc(o.via) + "</small></span>" + '<span class="chip ' + (o.state === "adopted" ? "merged" : o.state === "proposed" ? "open" : "planned") + '">' + esc(o.state) + "</span></li>";
      }).join("") + "</ul>";
    } else {
      html += '<ul class="objectives" aria-hidden="true"><li class="ghost"><span class="when">—</span><span class="what">Nobody writes these for it.<small>via a metric card, a motion or a registration</small></span><span class="chip planned">waiting</span></li></ul>' +
        '<p class="empty">A Class 3 factory produces its own objectives by negotiating them with its world: cards it proposes, motions it passes, surfaces it registers. Each will appear here as it is proposed, adopted or retired.</p>';
    }
    html += "</section>";

    // 4. pathologies
    html += '<section class="w6"><p class="k">Pathologies <span class="sig">/</span> the gauntlet\'s verdict</p><h3>Does this world show each failure?</h3><div class="verdict-grid">';
    PATHOLOGIES.forEach(function (p) {
      var rec = w && w.pathologies ? w.pathologies[p[0]] : null;
      var now = rec ? rec.now : "unknown";
      html += '<div><p class="k">' + p[1] + '</p><p class="vword ' + now + '">' + (rec ? VERDICT[now] : DASH) + "</p>" +
        '<div class="strip" aria-label="' + p[1] + ' by day">' + (rec && rec.history ? rec.history.map(function (h, i) { return '<i class="' + h + '" title="day ' + (i + 1) + ": " + VERDICT[h] + '"></i>'; }).join("") : "") + "</div></div>";
    });
    html += '</div><p class="empty">Present, absent, or can\'t tell yet, per day. "Can\'t tell yet" is never counted as absent.</p></section>';

    // 5. charter
    var ch = w && w.charter;
    html += '<section><p class="k">Charter' + (ch ? " · edition " + esc(ch.edition) : "") + '</p><h3>Norms, constraints and λ</h3>';
    html += '<p class="k" style="margin-top:4px">Norms</p><p style="margin:0;color:var(--ink-2);font-size:15.5px">' +
      (ch ? ch.norms.map(esc).join(" · ") : "Truthful commitments · Care with scarce resources · Useful inquiry · Capacity to revise inadequate practices <span class=\"dash\">(the launch norms; a world may revise them only through its charter)</span>") + "</p>";
    html += '<table class="cons"><thead><tr><th>Constraint (metric card)</th><th>Answers</th><th class="n">λ</th></tr></thead><tbody>' +
      (ch && ch.constraints.length ? ch.constraints.map(function (c) {
        return "<tr><td>" + esc(c.card) + (c.saturated ? ' <span class="chip open">at cap</span>' : "") + "</td><td>" + esc(c.role) + '</td><td class="n">' + esc(c.lambda) + "</td></tr>";
      }).join("") : '<tr><td>' + DASH + "</td><td>" + DASH + '</td><td class="n">' + DASH + "</td></tr>") + "</tbody></table>";
    html += spark("Total λ posted, the shadow price", ch ? ch.lambda_total : null);
    html += '<p class="k">Revisions</p>' + (ch && ch.revisions.length ? '<ol class="vlist">' + ch.revisions.map(function (r) {
      return '<li><b class="sig">●</b><span>' + fmtDay(r.at) + ' <span class="chip">' + esc(r.kind) + "</span> " + esc(r.text) + "</span></li>";
    }).join("") + "</ol>" : '<p class="empty">Amendments and new editions, as the committee drawn by lot adopts them.</p>');
    html += "</section>";

    // 6. evaluation
    var ev = w && w.evaluation;
    html += '<section><p class="k">Evaluation</p><h3>Who spends the compute</h3>';
    if (ev) {
      var cs = ev.compute_share;
      html += '<div class="share" role="img" aria-label="Compute share: evaluators ' + pct(cs.evaluators) + ", producers " + pct(cs.producers) + ", governance " + pct(cs.governance) + '">' +
        '<span class="ev" style="flex:' + cs.evaluators + '">Evaluators ' + pct(cs.evaluators) + "</span>" +
        '<span class="pr" style="flex:' + cs.producers + '">Producers ' + pct(cs.producers) + "</span>" +
        '<span class="gv" style="flex:' + cs.governance + '">Gov. ' + pct(cs.governance) + "</span></div>";
    } else {
      html += '<div class="share empty-share" aria-hidden="true"></div>';
    }
    html += '<div class="kv"><div><p class="k">Tiers reached</p><p class="num">' + (ev ? num(ev.tiers_reached) : DASH) + '</p></div><div><p class="k">Model families judging</p><p class="num">' + (ev ? num(ev.families) : DASH) + "</p></div></div>";
    html += '<p class="k">Early warning</p><div class="sparks">' +
      spark("Ensemble disagreement", ev && ev.disagreement) +
      spark("Variance", ev && ev.variance) +
      spark("Autocorrelation", ev && ev.autocorrelation) + "</div>";
    html += "</section>";

    // 7. consequences
    var co = w && w.consequences;
    var sp = co && co.spend_micro_usd, tr = co && co.treasury_micro_usd, st = co && co.settled;
    html += '<section class="w6"><p class="k">Realized consequences <span class="sig">/</span> totals only</p><h3>What happened in the world</h3>' +
      '<div class="kv kv4">' +
        '<div><p class="k">Settled outcomes</p><p class="num">' + (st ? num(st.count) : DASH) + "</p></div>" +
        '<div><p class="k">Trades that paid off</p><p class="num">' + (st ? pct(st.paid_off) : DASH) + "</p></div>" +
        '<div><p class="k">Declines that were right</p><p class="num">' + (st ? pct(st.declines_right) : DASH) + "</p></div>" +
        '<div><p class="k">Spent on inference</p><p class="num">' + (sp ? usd(sp.inference) : DASH) + "</p></div>" +
        '<div><p class="k">Venue fees</p><p class="num">' + (sp ? usd(sp.venue_fees) : DASH) + "</p></div>" +
        '<div><p class="k">Data and hosting</p><p class="num">' + (sp ? usd((sp.data || 0) + (sp.hosting || 0)) : DASH) + "</p></div>" +
        '<div><p class="k">Treasury in</p><p class="num">' + (tr ? usd(tr["in"]) : DASH) + "</p></div>" +
        '<div><p class="k">Treasury out</p><p class="num">' + (tr ? usd(tr.out) : DASH) + "</p></div>" +
      "</div><p class=\"empty\">Outcomes are graded net of the venue's fees and funding, one horizon after each decision, on the venue's clock. No balance and no open position is shown.</p></section>";

    grid.innerHTML = html;
    wireSparks();
  };

  var load = function (which) {
    showing = which;
    current = 0;
    if (toggle) {
      toggle.setAttribute("aria-pressed", which === "example" ? "true" : "false");
      toggle.firstChild.nodeValue = which === "example" ? "Hide the example " : "Show a fabricated example ";
    }
    if (which === "example") {
      (example ? Promise.resolve(example) : fetch("data/example.json", { cache: "no-cache" }).then(function (r) { return r.json(); }))
        .then(function (d) { example = d; if (!d.example) throw new Error("example.json must say example: true"); render(d); })
        .catch(function () { grid.innerHTML = '<section class="w6"><p class="empty">The example could not be loaded.</p></section>'; });
    } else {
      window.FL.progress().then(function (d) { real = d; render(d); }).catch(function () {
        headEl.textContent = "Status unavailable.";
        lineEl.textContent = "data/progress.json could not be read. Serve the site over HTTP (see site/README.md).";
      });
    }
  };

  if (toggle) toggle.addEventListener("click", function () { load(showing === "example" ? "real" : "example"); });
  load(/[?&]example=1\b/.test(location.search) ? "example" : "real");
})();

(function () {
  "use strict";

  var host = document.getElementById("works");
  if (!host) return;

  var NS = "http://www.w3.org/2000/svg";
  var XL = "http://www.w3.org/1999/xlink";
  var TICK = 4, LOOP = 12, CAP_T = 24;
  var REDUCED = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var LABEL = "Diagram of one wake: the tick wakes the seats, verdicts pass from evaluators to producers and from meta to evaluators, the charter is priced, orders go to Hyperliquid and fills return, each wake draws on the wallet, the treasury route returns through Base and Venice, every event is sealed into the ledger, and a kill switch stands apart.";

  /* two hand-placed layouts: objects, edges (as point lists), labels */
  function WIDE() {
    return {
      W: 640, H: 392, cap: 16,
      clock: [40, 42], r: 16,
      cols: [150, 172, 194], rows: [48, 76, 104], ant: [150, 150],
      wake: [[56, 42], [179, 42], [179, 47]],
      a1: [[212, 83], [240, 69], [212, 56]],
      a2: [[212, 111], [240, 97], [212, 84]],
      orders: [[198, 48], [198, 18], [568, 18], [568, 43]],
      fills: [[580, 44], [580, 28], [206, 28], [206, 47]],
      rules: [[201, 118], [201, 136], [346, 136], [346, 129]],
      charter: [320, 40],
      venue: [532, 44, 88, 48],
      events: [[40, 58], [40, 208], [149, 208]],
      ledger: [150, 200],
      treasury: [[576, 92], [576, 290], [61, 290]],
      base: [460, 290], venice: [300, 290],
      wallet: [20, 250],
      kill: [24, 360],
      labels: [
        ["tick", 62, 30, "start", 0], ["wake", 110, 37, "middle", 1],
        ["producers", 140, 59, "end", 0], ["evaluators", 140, 87, "end", 0],
        ["meta", 140, 115, "end", 0], ["antagonist", 140, 161, "end", 0],
        ["verdicts", 236, 87, "start", 1],
        ["orders", 400, 14, "middle", 1], ["fills", 400, 40, "middle", 1],
        ["rules", 270, 131, "middle", 1],
        ["charter", 346, 150, "middle", 0],
        ["venue", 576, 66, "middle", 0], ["Hyperliquid", 576, 80, "middle", 0],
        ["events", 48, 140, "start", 1],
        ["ledger", 150, 234, "start", 0],
        ["treasury", 584, 200, "start", 1],
        ["Base", 460, 310, "middle", 0], ["Venice", 300, 310, "middle", 0],
        ["wallet", 68, 270, "start", 0],
        ["kill", 66, 371, "start", 0]
      ]
    };
  }

  function NARROW() {
    return {
      W: 340, H: 480, cap: 13,
      clock: [27, 28], r: 12,
      cols: [20, 42, 64], rows: [70, 98, 126], ant: [20, 172],
      wake: [[27, 40], [27, 69]],
      a1: [[82, 105], [110, 91], [82, 78]],
      a2: [[82, 133], [110, 119], [82, 106]],
      orders: [[68, 70], [68, 52], [290, 52], [290, 209]],
      fills: [[302, 210], [302, 60], [76, 60], [76, 69]],
      rules: [[71, 140], [71, 215]],
      charter: [45, 216],
      venue: [220, 210, 100, 48],
      events: [[15, 28], [10, 28], [10, 458], [19, 458]],
      ledger: [20, 450],
      treasury: [[270, 258], [270, 400], [61, 400]],
      base: [200, 400], venice: [120, 400],
      wallet: [20, 340],
      kill: [288, 300],
      labels: [
        ["tick", 46, 32, "start", 0], ["wake", 34, 58, "start", 1],
        ["producers", 168, 81, "start", 0], ["evaluators", 168, 109, "start", 0],
        ["meta", 168, 137, "start", 0], ["antagonist", 168, 183, "start", 0],
        ["verdicts", 106, 109, "start", 1],
        ["orders", 180, 48, "middle", 1], ["fills", 250, 74, "middle", 1],
        ["rules", 78, 180, "start", 1],
        ["charter", 71, 322, "middle", 0],
        ["venue", 270, 230, "middle", 0], ["Hyperliquid", 270, 246, "middle", 0],
        ["events", 14, 200, "start", 1],
        ["ledger", 20, 442, "start", 0],
        ["treasury", 278, 380, "start", 1],
        ["Base", 200, 420, "middle", 0], ["Venice", 120, 420, "middle", 0],
        ["wallet", 68, 360, "start", 0],
        ["kill", 304, 330, "middle", 0]
      ]
    };
  }

  /* dot schedule: [path, start, duration, cycle filter] within a 4 s tick; treasury runs once per 12 s loop */
  var SCHED = [
    ["wake", 0.0, 0.5, null], ["events", 0.1, 1.0, null],
    ["orders", 1.1, 0.8, null], ["orders", 1.35, 0.8, null], ["orders", 1.6, 0.8, 1],
    ["a1", 1.95, 0.4, null], ["a1", 2.1, 0.4, null],
    ["fills", 2.4, 0.8, null], ["fills", 2.7, 0.8, null],
    ["a2", 2.75, 0.4, null], ["a2", 2.9, 0.4, null],
    ["rules", 3.1, 0.6, 2]
  ];
  var ROWS = [0.5, 1.3, 2.1], STAG = 0.12, ON = 0.6, ANT = [0.9, 0.6];
  var TREAS = [9.0, 2.4];
  var CARD_BASE = [30, 18, 24, 34, 14, 26], CARD_DELTA = [10, -8, 12, -6, 8, -10];

  function el(name, attrs, parent) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) if (attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function pts(a) { return a.map(function (p) { return p.join(","); }).join(" "); }
  function quad(q) { return "M" + q[0].join(",") + " Q" + q[1].join(",") + " " + q[2].join(","); }

  function polyAt(p, f) {
    var segs = [], total = 0, i, d;
    for (i = 1; i < p.length; i++) { d = Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]); segs.push(d); total += d; }
    d = f * total;
    for (i = 0; i < segs.length; i++) {
      if (d <= segs[i] || i === segs.length - 1) {
        var k = segs[i] ? Math.min(1, d / segs[i]) : 0;
        return [p[i][0] + (p[i + 1][0] - p[i][0]) * k, p[i][1] + (p[i + 1][1] - p[i][1]) * k];
      }
      d -= segs[i];
    }
    return p[p.length - 1];
  }
  function quadAt(q, f) {
    var a = q[0], c = q[1], b = q[2], m = 1 - f;
    return [m * m * a[0] + 2 * m * f * c[0] + f * f * b[0], m * m * a[1] + 2 * m * f * c[1] + f * f * b[1]];
  }
  function at(L, key, f) { return (key === "a1" || key === "a2") ? quadAt(L[key], f) : polyAt(L[key], f); }

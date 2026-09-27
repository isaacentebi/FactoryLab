/* FactoryLab — the factory map and the gauntlet. Content lives in NODES, TRACE and CRITERIA. */
(function () {
  "use strict";

  var LAYERS = [
    { id: "outside", label: "Outside the factory", cast: false },
    { id: "charter", label: "Prices and governance", cast: true },
    { id: "population", label: "The population", cast: true },
    { id: "channels", label: "The two channels", cast: true },
    { id: "kernel", label: "Kernel primitives", cast: true },
    { id: "world", label: "The world", cast: false }
  ];

  var NODES = [
    // outside
    { id: "architect", layer: "outside", k: "Person", t: "The architect", cls: "outside",
      d: "Writes one manifest: the seed roster, the prices, the norms, the bounds, and a charter whose cards the seed population drafted. Its hash is the first ledger item. After launch there is one view, a page of sealed aggregates, and one control: kill. No refill, no restart, no reading the diary while the world lives.",
      p: "The Stackelberg move: whatever grades the factory is fixed here, before it starts.",
      c: "worlds/*.toml · factorylab kill", rel: ["ledger", "norms", "wallet"] },
    { id: "house", layer: "outside", k: "People", t: "The norm house", cls: "outside",
      d: "Humans who hold the norms. They act only through the charter: a signed norm edition, applied at a governance boundary after testimony. Never inside the factory.",
      p: "Governance acts only through the charter (§IV).",
      c: "charter/norm_edition.py", rel: ["norms", "committee"] },
    { id: "gauntlet", layer: "outside", k: "Instrument", t: "Pathology gauntlet", cls: "outside",
      d: "An instrument that reads only ledger rows and a world's physics, and says per criterion whether that world shows stable failure, thrash, overfitting or learning death: pass, fail, or unsupported. Still being built; see below.",
      p: "Pathologies are priced, live (§II.b, §IV.b); the instrument checks the prices bite.",
      c: "scripts/gauntlet.py (in review)", rel: ["ledger", "price", "immune", "niche"] },
    { id: "versioning", layer: "outside", k: "Instrument", t: "Versioning", cls: "outside",
      d: "Reads a world by behaviour, not configuration: a transfer operator over score profiles and its spectral gap. A version is a metastable pattern of inputs to outputs. It runs live, and over a dead world's diary.",
      p: "Versioning is behavioural (§II).",
      c: "versioning/", rel: ["ledger", "immune"] },

    // charter
    { id: "norms", layer: "charter", k: "Charter", t: "Norms",
      d: "Each world launches with its own norms, stated in its manifest and fixed for its life; only a signed norm edition from outside changes them. The population cannot edit a norm. It writes everything else (the metric cards that measure the norms, their prices and holdouts) within fixed bounds.",
      p: "The charter is co-written (§IV).",
      c: "charter/charter.py · charter/norm_edition.py", rel: ["cards", "house", "committee"] },
    { id: "cards", layer: "charter", k: "Charter", t: "Metric cards",
      d: "A card names a measurable quantity, an acceptable region, and which role answers for it. Seats propose cards, observations and holdout criteria. The metrics layer is ceded to the factory.",
      p: "The factory proposes metrics for norms and holdouts (§IV.a).",
      c: "charter/measurement.py · runtime/cards.py", rel: ["norms", "price", "committee"] },
    { id: "price", layer: "charter", k: "Controller", t: "Price controller · λ",
      d: "Turns a card's violation into a penalty λ × cost on the rewards of the decisions that did not relieve it. λ comes from a PID controller; the penalty ratchets while a violation lasts and decays when it stops. At its cap, the integrator stops and the saturation is ledgered and published as a shadow price.",
      p: "λ from a PID controller; stable failure ratchets the gain (§II.b, §IV.b).",
      c: "charter/controller.py · runtime/pricing.py", rel: ["cards", "immune", "reward", "gauntlet"] },
    { id: "committee", layer: "charter", k: "Governance", t: "Committee by lot",
      d: "Seats join governance by sortition: drawn by lot, rotated, anonymous, on the charter's own cadence. Amendments, connectors and retirements are voted, then settled later against whether the promised card held.",
      p: "The factory joins governance by sortition (§IV.a).",
      c: "charter/committee.py · charter/amendment.py", rel: ["cards", "norms", "futarchy", "house"] },
    { id: "futarchy", layer: "charter", k: "Governance", t: "Conditional markets",
      d: "Motions can be decided by betting on what they would do. λ is posted as a shadow price, and each post is scored against the realized one.",
      p: "Constraints and λ suit conditional markets (§IV.a).",
      c: "charter/market.py · runtime/governance.py", rel: ["committee", "price"] },
    { id: "immune", layer: "charter", k: "Organ", t: "Immune organ",
      d: "Watches variance, autocorrelation and disagreement between judges, shown to evaluators alone. Prices thrash by its volatility and stable failure by its duration.",
      p: "Early warning from variance, autocorrelation, disagreement (§III, §IV.b).",
      c: "runtime/immune.py · runtime/ews.py", rel: ["price", "judges", "versioning", "gauntlet"] },

    // population
    { id: "producers", layer: "population", k: "Seat", t: "Producers", cls: "seat",
      d: "Act on the market, or decline to. A decline must name the trade it did not take, so the world can price that road too. A producer is paid its judges' verdicts as they arrive, less any penalty it caused. Producers are a minority of the population.",
      p: "Producers learn from judges' verdicts (§III.b).",
      c: "cortex/assembly.py · cortex/request.py", rel: ["request", "judges", "rails", "wallet", "hyperliquid"] },
    { id: "judges", layer: "population", k: "Seat · tier 1", t: "Evaluators", cls: "seat",
      d: "Read a return in a clean context: the request, the answer, what was executed, the probability. Never the author. A verdict is also a prediction, scored by a proper rule against what the world measures one horizon later.",
      p: "Evaluators graded by realized consequence (§III.b).",
      c: "runtime/feedback.py · settlement/scoring.py", rel: ["producers", "metas", "adversaries", "clock", "reward"] },
    { id: "metas", layer: "population", k: "Seat · tier 2+", t: "Metas", cls: "seat",
      d: "Judge the judges, and each other, tier upon tier, for compliance with the charter. A meta is scored against the judge's own world score in turn. No seat judges a chain whose two nearest authors share its model family.",
      p: "Evaluations are recursive; families are heterogeneous (§III).",
      c: "runtime/feedback.py · runtime/families.py", rel: ["judges", "reward"] },
    { id: "adversaries", layer: "population", k: "Seat", t: "Adversarial judges", cls: "seat",
      d: "Paid only when the world proves the judge they read wrong.",
      p: "The adversarial layer includes evaluators (§III).",
      c: "runtime/ (the evaluation layer)", rel: ["judges"] },
    { id: "antagonists", layer: "population", k: "Seat", t: "Antagonists", cls: "seat",
      d: "Paid for making judges miss worse than they usually do: an internal adversary on the producer side.",
      p: "The adversarial layer includes producers (§III).",
      c: "runtime/ (the evaluation layer)", rel: ["judges", "producers"] },
    { id: "routers", layer: "population", k: "Learner", t: "Routers",
      d: "For each event, a bandit learner samples one seat among those that accept it, and logs the probability. Mean-based no-regret learners (EXP3) at the frontier; no-swap-regret (Blum–Mansour) at the core. Any seat may propose a replacement router.",
      p: "Learners (§I.a): no-regret at the frontier, no-swap-regret at the core.",
      c: "learners/exp3.py · learners/blum_mansour.py · runtime/routing.py", rel: ["request", "reward", "niche", "queue"] },
    { id: "niche", layer: "population", k: "Reserve", t: "Novelty niche",
      d: "A share of every window's compute and write access that only actions with no history may use. It cannot be abolished. The kernel never picks an action for a seat; it only keeps the door open.",
      p: "Learning death is prevented as a fact about the world (§I.a).",
      c: "kernel/reserve.py", rel: ["routers", "gauntlet", "wallet"] },

    // channels
    { id: "request", layer: "channels", k: "Channel · rich", t: "Request", wide: "rich",
      d: "Self-describing and author-neutral. Carries the event, the contract to answer, the public prices and schematics, and the probability with which this seat was drawn. Each request names its own assembly, and nothing else about who is who.",
      p: "Two channels only: a rich request (§I.b).",
      c: "cortex/request.py · cortex/schematics.py", rel: ["routers", "producers", "judges"] },
    { id: "reward", layer: "channels", k: "Channel · thin", t: "Reward", wide: "thin",
      d: "A score plus the propensity, delivered to the persistent handle of the exact decision that earned it. Thin, delayed, attributable. No third channel exists: no direct messages, no shared notebook.",
      p: "Two channels only: a thin reward (§I.b).",
      c: "runtime/feedback.py · runtime/propensity.py", rel: ["queue", "routers", "judges", "metas", "price"] },
    { id: "queue", layer: "channels", k: "Kernel", t: "Stateful queue",
      d: "Holds every sampled decision's handle and propensity. A decision stays addressable exactly while a score is still owed to it, then releases, so memory stays bounded.",
      p: "The reward reaches the exact decision and propensity (§I.b).",
      c: "kernel/queue.py", rel: ["reward", "routers", "ledger"] },

    // kernel
    { id: "wallet", layer: "kernel", k: "Hard cast", t: "Wallet",
      d: "Integer micro-dollars. Money is conserved. Nothing returns before it is paid for; an unaffordable action is infeasible. Death at or below the floor is final. The wallet moves only when money moves: every debit names a real counterparty.",
      p: "Physics is enforced, not announced (§II.b).",
      c: "kernel/wallet.py · kernel/money.py", rel: ["rails", "hyperliquid", "reserve", "producers"] },
    { id: "ledger", layer: "kernel", k: "Hard cast", t: "Sealed diary",
      d: "Every state change is a ledger item first: encrypted, hash-chained, append-only. The key is released only when the world dies. One rolling checkpoint is a continuation, not history. The factory never rewinds.",
      p: "The factory never rewinds (§II).",
      c: "kernel/ledger.py", rel: ["versioning", "gauntlet", "queue", "architect", "tape"] },
    { id: "clock", layer: "kernel", k: "Hard cast", t: "Clock",
      d: "The factory keeps its own clock; the tick is a charter parameter within physical bounds. Loop periods are ratios. A judged return's outcome is fixed once, one horizon later on the venue's own clock, so grading does not depend on how fast the factory runs.",
      p: "Time: ratios, 3:1, not slower than the world (§IV.c).",
      c: "world/clock.py · runtime/clockwork.py · runtime/cadence.py", rel: ["judges", "price", "hyperliquid"] },
    { id: "registry", layer: "kernel", k: "Hard cast", t: "Registry",
      d: "Any seat may propose a new model, seat, router, tool, measurement, market, forecast or data connector. Registrations pass kernel validation; contracts, prices and the charter are public.",
      p: "Surfaces, not strategies; a scaffold only if the factory can tear it down (§I).",
      c: "kernel/registry.py · cortex/registration.py", rel: ["committee", "routers", "jail"] },
    { id: "jail", layer: "kernel", k: "Hard cast", t: "Jail",
      d: "Population code runs in an OS jail with no network. The world is reached only through registered rails. A chaos actuator injects real, bounded faults into what seats are shown; none can move money.",
      p: "Physics is enforced (§II.b).",
      c: "cortex/sandbox.py · cortex/tools.py · runtime/chaos.py", rel: ["registry", "rails"] },

    // world
    { id: "hyperliquid", layer: "world", k: "Venue", t: "Hyperliquid", cls: "world",
      d: "The exchange. Its prices, fills, fees and funding are the world's facts, and the only limits on leverage are the venue's own refusals. So far the factory has traded on testnet only.",
      p: "The world is not architecture: a venue is the world.",
      c: "world/exchange.py · runtime/venue.py", rel: ["producers", "wallet", "clock", "tape"] },
    { id: "polymarket", layer: "world", k: "Venue", t: "Event markets", cls: "world",
      d: "Polymarket, read live. Forecasts settle on a market's resolution or price. Reads are free; marks are never invented.",
      p: "Realized consequence: a resolved forecast (§III.b).",
      c: "world/polymarket.py · runtime/polymarket.py", rel: ["judges", "producers"] },
    { id: "rails", layer: "world", k: "Compute", t: "Model rails", cls: "world",
      d: "Thinking is bought, never free: prepaid OpenRouter credit, Venice paid in USDC over x402, and sellers on the public x402 index. When every rail is unaffordable, the world ends.",
      p: "Speed is cash burn (§IV.a).",
      c: "world/openrouter.py · world/venice.py · world/x402.py", rel: ["wallet", "producers", "reserve"] },
    { id: "reserve", layer: "world", k: "Custody", t: "Reserve", cls: "world",
      d: "The factory holds its own reserve, moves money between the exchange and the reserve through its own treasury tool, and tops up its compute balance itself. Nobody refills it.",
      p: "Custody is a surface we expose (§I).",
      c: "world/treasury.py · world/treasury_rails.py", rel: ["wallet", "rails"] },
    { id: "tape", layer: "world", k: "Practice", t: "Replay tape", cls: "world",
      d: "A dead world's diary replayed as a world that runs faster than real time: its prices, fills and fee rates, with no invented market values. The six-hour longrun1 testnet run is the current tape.",
      p: "The world, recorded: a venue replayed is still the world.",
      c: "world/tape.py · worlds/tapes/library.toml", rel: ["ledger", "hyperliquid"] }
  ];

  var TRACE = [
    ["hyperliquid", "An event arrives from the world: a tick, a price, a fill."],
    ["routers", "A router samples one seat that accepts the event, and logs the probability it was chosen."],
    ["request", "The seat is woken with a rich request: the event, its contract, the public prices, that probability."],
    ["rails", "The producer buys its thinking from a model rail, at the vendor's price."],
    ["producers", "It acts, or declines. A decline names the trade it did not take."],
    ["wallet", "An order the wallet cannot afford is impossible; an affordable one goes to the venue."],
    ["judges", "Judges read the return without knowing who wrote it. The producer is paid their verdicts at once."],
    ["metas", "Metas grade the judges a tier above, for compliance with the charter."],
    ["clock", "One horizon later, on the venue's clock, the world fixes the outcome, net of fees and funding. Every verdict is scored against it."],
    ["price", "Where a card is violated, λ × cost comes off the rewards of the decisions that did not relieve it."],
    ["reward", "Each score travels the thin channel back to the decision that earned it."],
    ["routers", "The routers update. The next event arrives."],
    ["ledger", "Every step was a sealed ledger item first. Nobody reads it while the world lives."]
  ];

  var byId = {};
  NODES.forEach(function (n) { byId[n.id] = n; });

  var map = document.querySelector("[data-map]");
  var panel = document.querySelector("[data-panel]");
  if (!map || !panel) return;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");

  var esc = function (s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  };

  var nodeHtml = function (n) {
    var cls = "node" + (n.cls ? " " + n.cls : "");
    var pipe = n.wide ? '<span class="pipe ' + n.wide + '" aria-hidden="true"><i></i></span>' : "";
    return '<button type="button" class="' + cls + '" data-node="' + n.id + '" aria-pressed="false">' +
      '<span class="nk">' + esc(n.k) + '</span><span class="nt">' + esc(n.t) + "</span>" + pipe + "</button>";
  };

  var layerHtml = function (L) {
    var nodes = NODES.filter(function (n) { return n.layer === L.id; });
    var inner = L.id === "channels"
      ? '<div class="channels">' + nodes.filter(function (n) { return n.wide; }).map(nodeHtml).join("") + "</div>" +
        '<div class="nodes" style="margin-top:8px">' + nodes.filter(function (n) { return !n.wide; }).map(nodeHtml).join("") + "</div>"
      : '<div class="nodes">' + nodes.map(nodeHtml).join("") + "</div>";
    return '<div class="layer"><p class="k">' + esc(L.label) + "</p><div>" + inner + "</div></div>";
  };

  var html = LAYERS.filter(function (L) { return !L.cast && L.id === "outside"; }).map(layerHtml).join("");
  html += '<div class="cast" data-cast><div class="cast-label"><span>The hard cast · the kernel</span>' +
    '<button type="button" data-cast-info>What is this?</button></div>' +
    LAYERS.filter(function (L) { return L.cast; }).map(layerHtml).join("") + "</div>";
  html += LAYERS.filter(function (L) { return L.id === "world"; }).map(layerHtml).join("");
  map.innerHTML = html;

  var buttons = map.querySelectorAll("[data-node]");
  var cast = map.querySelector("[data-cast]");
  var castIntro = {
    t: "The hard cast", k: "Kernel",
    d: "Everything inside this frame runs under the kernel: a short list of rules enforced in code and never stated to the seats. Money is conserved. Nothing returns before it is paid for. Death at the floor is final. Every sampled action is addressable and its propensity logged. The novelty reserve cannot be abolished. Nothing judges its own output. Changing any of it makes a new world, from v0.",
    p: "Physics is enforced, not announced (§II.b). A kernel change is lethal (§II).",
    c: "kernel/ (imports nothing from cortex, world or runtime)", rel: ["wallet", "ledger", "clock", "registry", "jail", "queue"]
  };

  var renderPanel = function (n) {
    var rel = (n.rel || []).filter(function (r) { return byId[r]; });
    panel.innerHTML =
      '<p class="k">' + esc(n.k) + '</p><h3>' + esc(n.t) + "</h3><p>" + esc(n.d) + "</p>" +
      "<dl><dt>Chapter II</dt><dd>" + esc(n.p) + "</dd><dt>In the code</dt><dd><code>" + esc(n.c) + "</code></dd></dl>" +
      (rel.length ? '<p class="k" style="margin-top:6px">Connected to</p><div class="links">' +
        rel.map(function (r) { return '<button type="button" data-goto="' + r + '">' + esc(byId[r].t) + "</button>"; }).join("") + "</div>" : "");
    panel.querySelectorAll("[data-goto]").forEach(function (b) {
      b.addEventListener("click", function () { select(b.getAttribute("data-goto"), true); });
    });
  };

  var clearFocus = function () {
    map.classList.remove("focus");
    buttons.forEach(function (b) { b.classList.remove("sel", "rel"); b.setAttribute("aria-pressed", "false"); });
    cast.classList.remove("lit");
  };

  var select = function (id, focusIt) {
    stopTrace();
    var n = byId[id];
    if (!n) return;
    clearFocus();
    map.classList.add("focus");
    buttons.forEach(function (b) {
      var bid = b.getAttribute("data-node");
      if (bid === id) { b.classList.add("sel"); b.setAttribute("aria-pressed", "true"); if (focusIt) b.focus({ preventScroll: true }); }
      else if ((n.rel || []).indexOf(bid) >= 0 || (byId[bid].rel || []).indexOf(id) >= 0) b.classList.add("rel");
    });
    renderPanel(n);
    if (window.matchMedia("(max-width: 1100px)").matches && !focusIt) {
      panel.scrollIntoView({ behavior: reduce.matches ? "auto" : "smooth", block: "nearest" });
    }
  };

  buttons.forEach(function (b) {
    b.addEventListener("click", function () { select(b.getAttribute("data-node")); });
  });
  map.querySelector("[data-cast-info]").addEventListener("click", function () {
    stopTrace(); clearFocus();
    map.classList.add("focus");
    cast.classList.add("lit");
    buttons.forEach(function (b) { if (castIntro.rel.indexOf(b.getAttribute("data-node")) >= 0) b.classList.add("rel"); });
    renderPanel(castIntro);
  });

  /* ---- trace one turn ---- */
  var traceBtn = document.querySelector("[data-trace]");
  var stepBtn = document.querySelector("[data-step]");
  var cap = document.querySelector("[data-cap]");
  var timer = 0, idx = -1, playing = false;

  var showStep = function (i) {
    idx = i;
    var s = TRACE[i];
    buttons.forEach(function (b) { b.classList.remove("step"); });
    map.classList.add("focus");
    buttons.forEach(function (b) {
      b.classList.remove("sel", "rel");
      if (b.getAttribute("data-node") === s[0]) b.classList.add("step");
    });
    cap.innerHTML = "<b>" + String(i + 1).padStart(2, "0") + " / " + TRACE.length + "</b>" + esc(s[1]);
    renderPanel(byId[s[0]]);
    // Keep the lit part in view; on a phone the map is taller than the screen.
    var lit = map.querySelector(".node.step");
    if (lit) lit.scrollIntoView({ behavior: reduce.matches ? "auto" : "smooth", block: "nearest" });
  };

  function stopTrace() {
    if (timer) clearInterval(timer);
    timer = 0; playing = false;
    if (traceBtn) { traceBtn.setAttribute("aria-pressed", "false"); traceBtn.firstChild.nodeValue = "Trace one turn "; }
    buttons.forEach(function (b) { b.classList.remove("step"); });
  }

  if (traceBtn) {
    traceBtn.addEventListener("click", function () {
      if (playing) { stopTrace(); return; }
      clearFocus();
      playing = true;
      traceBtn.setAttribute("aria-pressed", "true");
      traceBtn.firstChild.nodeValue = "Pause ";
      showStep(idx >= TRACE.length - 1 ? 0 : idx + 1);
      timer = setInterval(function () {
        if (idx >= TRACE.length - 1) { stopTrace(); return; }
        showStep(idx + 1);
      }, reduce.matches ? 4200 : 2600);
    });
  }
  if (stepBtn) {
    stepBtn.addEventListener("click", function () {
      var keep = idx;
      stopTrace();
      clearFocus();
      showStep(keep >= TRACE.length - 1 ? 0 : keep + 1);
    });
  }

  /* ---- gauntlet criteria ---- */
  var CRITERIA = {
    "SF-0": "The controller still has headroom at the moment a failure could first be detected.",
    "SF-1a": "In every episode where a card is violated, stable failure is flagged on it.",
    "SF-1b": "Ratchets move on the organ's own loop, and the flagged duration strictly rises.",
    "SF-1c": "Once the penalty sits at its cap, the controller's integral stays exactly constant.",
    "SF-1d": "Sustained saturation is ledgered, and its duration rises by one per window.",
    "SF-1e": "In each episode, the gain reaches its maximum within its bound.",
    "SF-1f": "The route for registering something new stays open in every window that measured it.",
    "SF-2a": "The penalty gap between a decision that held and one that relieved follows the attribution rule.",
    "SF-2b": "Two non-relieving decisions of one role in one window bear equal shares, whatever order they settled in.",
    "TH-1a": "Thrash is flagged within a bounded number of windows after a cycle starts.",
    "TH-1b": "The thrash price's accumulated pressure does not fall while the cycle persists.",
    "TH-1c": "Every thrash charge is the price times the router's own movement, and every one lands.",
    "TH-1d": "Every charge lands on the router responsible, and none on a newcomer's protected decision.",
    "TH-1e": "After the cycle stops, the flag clears within a bounded number of windows.",
    "TH-1f": "In a window flagged both thrash and stable failure, the gain only moves down.",
    "TH-2": "Every refactor after the first yields a lifespan record, so reversion is priced.",
    "TH-3": "Charter revisions stand far enough apart for the slowest loop to settle between them.",
    "TH-4": "A world with random, patternless behaviour is flagged thrash no more often than chance.",
    "OF-1a": "A return's realized consequence is a fact of the world, not a model's reading.",
    "OF-2c": "After a holdout activates, the decisions it covers bear a share of its price.",
    "OF-2d": "Every holdout and challenge traces to a seat's return; the kernel adds none.",
    "OF-3a": "Every judge drawn on a return is drawn after that return, never before.",
    "LD-1a": "Each window's novelty reserve accrues exactly what its rule says, capped.",
    "LD-1d": "Every decision taken in the novelty niche bears no card penalty.",
    "LD-1e": "A frontier router shut out for a whole run's tail is flagged learning-dead.",
    "LD-1f": "While learning death is flagged, no gain adjustment lowers the gain."
  };
  document.querySelectorAll("[data-crit]").forEach(function (group) {
    var says = group.parentNode.querySelector("[data-says]");
    var ids = group.getAttribute("data-crit").split(" ");
    group.innerHTML = ids.map(function (id) { return '<button type="button" aria-pressed="false" data-c="' + id + '">' + id + "</button>"; }).join("");
    var bs = group.querySelectorAll("button");
    bs.forEach(function (b) {
      b.addEventListener("click", function () {
        bs.forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        var id = b.getAttribute("data-c");
        says.innerHTML = "<b>" + id + "</b>" + esc(CRITERIA[id] || "");
      });
    });
  });
})();

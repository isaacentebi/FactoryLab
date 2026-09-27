/* FactoryLab — shared behaviour. No dependencies, no build step. */
(function () {
  "use strict";

  var doc = document.documentElement;
  doc.classList.remove("no-js");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");

  // SMIL animations ignore CSS; pause them for visitors who asked for less motion.
  var pauseSvg = function () {
    if (!reduce.matches) return;
    document.querySelectorAll("svg").forEach(function (s) { if (s.pauseAnimations) s.pauseAnimations(); });
  };
  pauseSvg();
  if (reduce.addEventListener) reduce.addEventListener("change", pauseSvg);

  /* ---------- reveal on scroll ---------- */
  var rv = document.querySelectorAll(".rv");
  if ("IntersectionObserver" in window && !reduce.matches) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });
    rv.forEach(function (el) { io.observe(el); });
  } else {
    rv.forEach(function (el) { el.classList.add("in"); });
  }

  /* ---------- progress data, shared by the bar and the progress page ---------- */
  var dataPromise = null;
  window.FL = window.FL || {};
  window.FL.progress = function () {
    if (!dataPromise) {
      dataPromise = fetch("data/progress.json", { cache: "no-cache" }).then(function (r) {
        if (!r.ok) throw new Error("progress.json: HTTP " + r.status);
        return r.json();
      });
    }
    return dataPromise;
  };
  window.FL.fmtDate = function (iso) {
    if (!iso) return "";
    var m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var p = iso.split("-");
    return parseInt(p[2], 10) + " " + m[parseInt(p[1], 10) - 1] + " " + p[0];
  };

  /* ---------- status bar ---------- */
  var bar = document.querySelector(".bar");
  if (bar) {
    document.body.classList.add("has-bar");
    window.FL.progress().then(function (d) {
      // One cell per world ever launched: sodium while it runs, grey once it has ended.
      var rail = bar.querySelector(".rail");
      var worlds = d.worlds || [];
      if (rail) {
        rail.innerHTML = "";
        worlds.forEach(function (w) {
          var i = document.createElement("i");
          i.className = w.state === "running" ? "now" : "m";
          i.title = w.name + " (" + w.state + ")";
          rail.appendChild(i);
        });
        if (!worlds.length) {
          var i = document.createElement("i");
          i.className = "p";
          i.title = "No world launched yet";
          rail.appendChild(i);
          var s = document.createElement("span");
          s.textContent = "Worlds: none yet";
          s.style.marginLeft = "10px";
          rail.appendChild(s);
        }
      }
      var head = bar.querySelector("[data-head]");
      if (head) head.innerHTML = (d.status.live ? "<b class='sig'>●</b>&nbsp;Live" : "○&nbsp;Not live");
      var ms = bar.querySelector("[data-milestone]");
      if (ms) ms.innerHTML = "<b>" + d.status.headline + "</b>";
    }).catch(function () {
      var ms = bar.querySelector("[data-milestone]");
      if (ms) ms.textContent = "Progress data unavailable";
    });
  }

  /* ---------- the class ladder (home) ---------- */
  var ladder = document.querySelector("[data-ladder]");
  if (ladder) {
    var classes = {
      1: {
        name: "Class 1 automates execution",
        body: "People decide what to want and how to get it. The machine carries out the plan. Every step it takes was written down first by someone who could have read it.",
        takes: "Its input is a plan.",
        steps: [["Objective", "given"], ["Plan", "given"], ["Execution", "auto"]]
      },
      2: {
        name: "Class 2 automates planning",
        body: "People still say what they want; the machine works out how. Most of what is called autonomy today lives here, however clever the planner.",
        takes: "Its input is an objective.",
        steps: [["Objective", "given"], ["Plan", "auto"], ["Execution", "auto"]]
      },
      3: {
        name: "Class 3 automates objectives",
        body: "Nobody hands it a goal. It arrives at what it is trying to do by negotiating with its world: prices, consequences, a charter it helps write. Its architect should not be able to predict what it becomes.",
        takes: "Its input is a world.",
        steps: [["World: prices, consequences, charter", "world"], ["Objective", "auto"], ["Plan", "auto"], ["Execution", "auto"]]
      }
    };
    var tabs = ladder.querySelectorAll("[role=tab]");
    var stageName = document.querySelector("[data-ladder-name]");
    var stageBody = document.querySelector("[data-ladder-body]");
    var stageTakes = document.querySelector("[data-ladder-takes]");
    var stageFlow = document.querySelector("[data-ladder-flow]");
    var select = function (n) {
      var c = classes[n];
      tabs.forEach(function (t) {
        var on = t.getAttribute("data-class") === String(n);
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
      });
      stageName.textContent = c.name;
      stageBody.textContent = c.body;
      stageTakes.textContent = c.takes;
      stageFlow.innerHTML = "";
      c.steps.forEach(function (s) {
        var el = document.createElement("div");
        el.className = "step " + s[1];
        var tag = s[1] === "given" ? "given by people" : s[1] === "world" ? "the world" : "automated";
        el.innerHTML = "<span>" + s[0] + "</span><span class='tag'>" + tag + "</span>";
        stageFlow.appendChild(el);
      });
    };
    tabs.forEach(function (t, idx) {
      t.addEventListener("click", function () { select(t.getAttribute("data-class")); });
      t.addEventListener("keydown", function (e) {
        var k = e.key, to = null;
        if (k === "ArrowRight") to = tabs[(idx + 1) % tabs.length];
        if (k === "ArrowLeft") to = tabs[(idx + tabs.length - 1) % tabs.length];
        if (to) { e.preventDefault(); to.focus(); select(to.getAttribute("data-class")); }
      });
    });
    select(3);
  }

  /* ---------- table of contents highlighting (ideas) ---------- */
  var toc = document.querySelector(".toc");
  if (toc && "IntersectionObserver" in window) {
    var links = {};
    toc.querySelectorAll("a").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    var tio = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          Object.keys(links).forEach(function (k) { links[k].classList.toggle("on", k === e.target.id); });
        }
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    Object.keys(links).forEach(function (id) { var s = document.getElementById(id); if (s) tio.observe(s); });
  }

  /* ---------- hero field: a sketch of the loop (home) ----------
     Each cell is a decision. Judges read a sample of them (a ring); a score comes back
     later along the thin channel (a sodium flash). Nobody else reads anything.
     An illustration, not live data: the caption says so. */
  var field = document.querySelector("[data-field]");
  if (field) {
    var canvas = field.querySelector("canvas");
    var ctx = canvas.getContext("2d");
    var countEl = field.querySelector("[data-count]");
    var judgedEl = field.querySelector("[data-judged]");
    var W = 0, H = 0, dpr = 1, cols = 0, rows = 0, cells = [], cursor = 0, total = 0, judged = 0;
    var CELL = 7, GAP = 3, TOP = 96, SIDE = 24, BOTTOM = 84;
    var seed = 7;
    var rnd = function () { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };

    var layout = function () {
      var r = field.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = Math.max(200, Math.floor(r.width));
      H = Math.max(200, Math.floor(r.height));
      canvas.width = W * dpr; canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var ro = field.querySelector(".readout");
      TOP = (ro ? ro.offsetHeight : 90) + 24;
      cols = Math.max(8, Math.floor((W - SIDE * 2 + GAP) / (CELL + GAP)));
      rows = Math.max(6, Math.floor((H - TOP - BOTTOM + GAP) / (CELL + GAP)));
      cells = new Array(cols * rows);
      cursor = 0;
    };

    var place = function (now) {
      var judgedNow = rnd() < 0.34;
      cells[cursor] = { t: now, j: judgedNow, back: now + 1400 + rnd() * 5200 };
      cursor = (cursor + 1) % cells.length;
      total += 1;
      if (judgedNow) judged += 1;
    };

    var draw = function (now) {
      ctx.clearRect(0, 0, W, H);
      var ox = SIDE, oy = TOP;
      for (var c = 0; c < cols; c++) {
        for (var r = 0; r < rows; r++) {
          var idx = c * rows + r;
          var x = ox + c * (CELL + GAP), y = oy + r * (CELL + GAP);
          var cell = cells[idx];
          if (!cell) { ctx.fillStyle = "#0f0f0e"; ctx.fillRect(x, y, CELL, CELL); continue; }
          // Older decisions go dark: bright at the leading edge, near-black a minute later.
          var age = Math.max(0, now - cell.t);
          var lum = Math.exp(-age / 22000);
          var v = Math.round(22 + 200 * lum);
          ctx.fillStyle = "rgb(" + v + "," + (v - 2) + "," + (v - 8) + ")";
          ctx.fillRect(x, y, CELL, CELL);
          if (cell.j) {
            var since = now - cell.back;
            if (since >= 0 && since < 900) {
              var a = 1 - since / 900;
              ctx.fillStyle = "rgba(255,90,31," + a.toFixed(3) + ")";
              ctx.fillRect(x - 1, y - 1, CELL + 2, CELL + 2);
            } else if (since < 0) {
              ctx.strokeStyle = "rgba(236,235,228,0.55)";
              ctx.lineWidth = 1;
              ctx.strokeRect(x - 1.5, y - 1.5, CELL + 3, CELL + 3);
            }
          }
        }
      }
      if (countEl) countEl.textContent = total.toLocaleString("en-US");
      if (judgedEl) judgedEl.textContent = judged.toLocaleString("en-US");
    };

    var last = 0, acc = 0, raf = 0, visible = true;
    var loop = function (now) {
      if (!last) last = now;
      acc += now - last; last = now;
      var step = 90;
      while (acc > step) { place(now - acc); acc -= step; }
      draw(now);
      raf = visible ? requestAnimationFrame(loop) : 0;
    };

    // History before the visitor arrived: most of the field already filled and faded.
    var prefill = function (now, share) {
      var n = Math.floor(cells.length * share);
      for (var i = 0; i < n; i++) place(now - (n - i) * 90);
    };
    var still = function () {
      var now = performance.now();
      prefill(now, 0.82);
      draw(now);
    };

    layout();
    if (reduce.matches) {
      still();
    } else {
      prefill(performance.now(), 0.6);
      raf = requestAnimationFrame(loop);
      if ("IntersectionObserver" in window) {
        new IntersectionObserver(function (es) {
          visible = es[0].isIntersecting;
          if (visible && !raf) { last = 0; raf = requestAnimationFrame(loop); }
        }).observe(field);
      }
    }
    var rt = 0;
    window.addEventListener("resize", function () {
      clearTimeout(rt);
      rt = setTimeout(function () {
        total = 0; judged = 0; layout();
        if (reduce.matches) still(); else prefill(performance.now(), 0.6);
      }, 150);
    });
  }
})();

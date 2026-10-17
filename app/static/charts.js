// Tiny Chart.js helpers. Data is passed from templates as JSON.
(function () {
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const PALETTE = ["#2f6fde", "#1a8a5a", "#c8412f", "#b7791f", "#7a4fd6", "#1b9aaa", "#d1497b", "#6b7385"];

  function base() {
    if (!window.Chart) return false;
    Chart.defaults.color = css("--muted");
    Chart.defaults.borderColor = css("--line");
    Chart.defaults.font.family = css("--font");
    Chart.defaults.maintainAspectRatio = false;
    return true;
  }
  const eur = (v) => "€" + Math.round(v).toLocaleString("en").replace(/,/g, " ");

  window.pmBar = function (id, labels, sets, opts = {}) {
    if (!base()) return;
    new Chart(document.getElementById(id), {
      data: {
        labels,
        datasets: sets.map((s, i) => ({
          type: s.type || "bar", label: s.label, data: s.data,
          backgroundColor: s.color || PALETTE[i], borderColor: s.color || PALETTE[i],
          borderWidth: s.type === "line" ? 2 : 0, pointRadius: s.type === "line" ? 2 : 0,
          stack: s.stack, order: s.type === "line" ? 0 : 1, tension: 0.25,
        })),
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 10 } },
                   tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${opts.pct ? c.raw.toFixed(2) + "%" : eur(c.raw)}` } } },
        scales: { y: { ticks: { callback: (v) => (opts.pct ? v + "%" : eur(v)) }, stacked: !!opts.stacked },
                  x: { stacked: !!opts.stacked, grid: { display: false } } },
      },
    });
  };

  window.pmLine = function (id, labels, sets, opts = {}) {
    sets.forEach((s) => (s.type = "line"));
    window.pmBar(id, labels, sets, opts);
  };

  window.pmDoughnut = function (id, labels, data) {
    if (!base()) return;
    new Chart(document.getElementById(id), {
      type: "doughnut",
      data: { labels, datasets: [{ data, backgroundColor: PALETTE, borderWidth: 0 }] },
      options: { cutout: "62%", plugins: { legend: { position: "right", labels: { boxWidth: 10 } },
                 tooltip: { callbacks: { label: (c) => `${c.label}: ${eur(c.raw)}` } } } },
    });
  };
})();

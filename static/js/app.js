// Progressive enhancement for the server-rendered dashboard. Every page works without
// this file; it only adds the theme switch, compact navigation, focus management,
// double-submit protection and the response-time chart crosshair.

"use strict";

// --- Theme switch -------------------------------------------------------------------
const themeToggle = document.querySelector("[data-theme-toggle]");
if (themeToggle) {
  themeToggle.hidden = false;
  const root = document.documentElement;
  const isDark = () =>
    root.dataset.theme === "dark" ||
    (!root.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const updateLabel = () =>
    themeToggle.setAttribute("aria-label", isDark() ? "Switch to light theme" : "Switch to dark theme");
  updateLabel();
  themeToggle.addEventListener("click", () => {
    const theme = isDark() ? "light" : "dark";
    root.dataset.theme = theme;
    try {
      localStorage.setItem("watchgrid-theme", theme);
    } catch {
      /* Storage can be unavailable (private windows); the choice then lasts for this page. */
    }
    updateLabel();
  });
}

// --- Navigation: collapsed behind a toggle on small screens, always open on wide ones ---
const navigation = document.querySelector(".navigation");
if (navigation) {
  const compact = matchMedia("(max-width: 800px)");
  const syncNavigation = () => {
    navigation.open = !compact.matches;
  };
  syncNavigation();
  compact.addEventListener("change", syncNavigation);
}

// --- Filters: switching organization clears a project from the previous one ---------
const organizationSelect = document.querySelector('select[name="organization"]');
organizationSelect?.addEventListener("change", () => {
  const project = organizationSelect.form.querySelector('select[name="project"]');
  if (project) project.value = "";
});

// --- Forms: move focus to the error summary, prevent double submission ---------------
document.querySelector("[data-error-summary]")?.focus();

for (const form of document.querySelectorAll('form[method="post"]')) {
  form.addEventListener("submit", () => {
    if (!form.checkValidity()) return;
    const button = form.querySelector('button[type="submit"], button:not([type])');
    if (button) {
      button.disabled = true;
      button.dataset.originalText = button.textContent;
      button.textContent = "Saving…";
    }
  });
}

// Restore buttons when the page comes back from the back/forward cache.
addEventListener("pageshow", () => {
  for (const button of document.querySelectorAll("button[data-original-text]")) {
    button.disabled = false;
    button.textContent = button.dataset.originalText;
  }
});

// --- Response-time chart crosshair ---------------------------------------------------
// Readings come from a json_script block: [{x, y, t, value, failed, code, checks, failures}]
// with x/y as percentages of the plot. The same values are listed in the data table.
for (const figure of document.querySelectorAll("[data-chart]")) {
  const source = document.getElementById(figure.dataset.chart);
  const plot = figure.querySelector(".chart-plot");
  if (!source || !plot) continue;
  const readings = JSON.parse(source.textContent);
  if (!readings.length) continue;

  const crosshair = plot.querySelector(".chart-crosshair");
  const point = plot.querySelector(".chart-point");
  const tooltip = plot.querySelector(".chart-tooltip");
  let active = null;

  const nearest = (percent) => {
    let best = 0;
    for (let i = 1; i < readings.length; i += 1) {
      if (Math.abs(readings[i].x - percent) < Math.abs(readings[best].x - percent)) best = i;
    }
    return best;
  };

  const describe = (reading) => {
    // Values lead; textContent keeps any server-provided text inert.
    const value = document.createElement("strong");
    value.textContent = reading.failed && reading.value === null ? "Failed" : `${reading.value} ms`;
    const time = document.createElement("span");
    time.textContent = reading.t;
    const parts = [value, time];
    if (reading.code) {
      const code = document.createElement("div");
      code.textContent = `HTTP ${reading.code}`;
      parts.push(code);
    }
    if (reading.checks !== null && reading.checks !== undefined) {
      const counts = document.createElement("div");
      counts.textContent = `${reading.checks} checks, ${reading.failures || 0} failed`;
      parts.push(counts);
    }
    tooltip.replaceChildren(...parts);
  };

  const show = (index) => {
    active = index;
    const reading = readings[index];
    crosshair.hidden = false;
    crosshair.style.left = `${reading.x}%`;
    point.hidden = reading.y === null;
    if (reading.y !== null) {
      point.style.left = `${reading.x}%`;
      point.style.top = `${reading.y}%`;
    }
    describe(reading);
    tooltip.hidden = false;
    // Keep the tooltip inside the plot: flip to the left of the crosshair past the middle.
    tooltip.style.left = reading.x > 55 ? "" : `calc(${reading.x}% + 12px)`;
    tooltip.style.right = reading.x > 55 ? `calc(${100 - reading.x}% + 12px)` : "";
  };

  const hide = () => {
    active = null;
    crosshair.hidden = true;
    point.hidden = true;
    tooltip.hidden = true;
  };

  plot.addEventListener("pointermove", (event) => {
    const box = plot.getBoundingClientRect();
    show(nearest(((event.clientX - box.left) / box.width) * 100));
  });
  plot.addEventListener("pointerleave", hide);
  plot.addEventListener("blur", hide);
  plot.addEventListener("keydown", (event) => {
    const steps = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity };
    if (event.key === "Escape") return hide();
    if (!(event.key in steps)) return;
    event.preventDefault();
    const start = active ?? readings.length - 1;
    show(Math.min(readings.length - 1, Math.max(0, start + steps[event.key])));
  });
}

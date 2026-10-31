// Light/dark switch, the narrow-screen menu, the page index, the setup tabs, the logo's flip
// and the copy buttons.

const root = document.documentElement;
const themeButton = document.querySelector("[data-theme-toggle]");

function currentTheme() {
  return root.dataset.theme === "light" ? "light" : "dark";
}

function labelThemeButton() {
  themeButton?.setAttribute("aria-label", currentTheme() === "dark" ? "Switch to light mode" : "Switch to dark mode");
}

themeButton?.addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  root.dataset.theme = next;
  try {
    localStorage.setItem("theme", next);
  } catch {}
  labelThemeButton();
});
labelThemeButton();

const menuButton = document.querySelector(".nav-menu");
const navLinks = document.getElementById("nav-links");

function setMenu(open) {
  menuButton.setAttribute("aria-expanded", String(open));
  navLinks.classList.toggle("is-open", open);
}

menuButton?.addEventListener("click", () => setMenu(menuButton.getAttribute("aria-expanded") !== "true"));
navLinks?.addEventListener("click", (event) => {
  if (event.target.closest("a")) setMenu(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && menuButton?.getAttribute("aria-expanded") === "true") {
    setMenu(false);
    menuButton.focus();
  }
});

// The page index, after the Craft apps' pages. Passed sections stack small at
// the top, the one being read sits large under them, and sections still ahead
// stack small at the bottom. As a section scrolls up into view, its letters
// leave the bottom list one by one, stand on end beside the ruler at that
// section's mark, then fly up into the large slot, while the name before it
// shrinks into the list above. Every letter's place follows the scroll position.
// The ruler is as long as the page and scrolls with it, its marker running from
// its top to its bottom as you go down the page.
const rail = document.querySelector(".rail");
if (rail) {
  const labels = [...rail.querySelectorAll(".rail-label")];
  const sections = labels.map((label) => document.querySelector(label.hash));
  const letters = labels.map((label) => [...label.children]);
  const box = rail.querySelector(".rail-labels");
  const ruler = rail.querySelector(".rail-ruler");
  const scale = rail.querySelector(".rail-scale");
  const digits = [...rail.querySelectorAll(".digit i")];
  const view = rail.querySelector(".rail-view");
  const mark = rail.querySelector(".rail-mark");
  const still = matchMedia("(prefers-reduced-motion: reduce)");

  const BASE = 34; // the letters' own font size; the layouts scale from it
  const SMALL = 16 / BASE;
  const ON_END = 16 / BASE;
  const LINE = 20; // one row of the small lists
  const STAGGER = 0.15; // how much later the last letter leaves than the first
  let widths = []; // each letter's width at BASE
  let layout = null;

  const clamp = (n) => Math.min(1, Math.max(0, n));
  const ease = (t) => t * t * (3 - 2 * t);
  const mix = (a, b, t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, r: a.r + (b.r - a.r) * t, s: a.s + (b.s - a.s) * t });

  // A name written left to right, right-aligned to the box, its top at y.
  function across(i, y, scale) {
    const w = widths[i];
    const total = w.reduce((a, b) => a + b, 0) * scale;
    let x = layout.width - total;
    return w.map((wj) => {
      const at = { x, y, r: 0, s: scale };
      x += wj * scale;
      return at;
    });
  }

  // A name standing on end beside the ruler, read bottom to top, centred on y.
  function onEnd(i, y) {
    const w = widths[i];
    const total = w.reduce((a, b) => a + b, 0) * ON_END;
    let bottom = Math.min(layout.height, Math.max(total, y + total / 2));
    const x = layout.width - BASE * ON_END;
    return w.map((wj) => {
      const at = { x, y: bottom, r: -90, s: ON_END };
      bottom -= wj * ON_END;
      return at;
    });
  }

  function measure() {
    // The rail only shows on wide screens; nothing to place while it's hidden.
    if (getComputedStyle(rail).display === "none") {
      layout = null;
      return;
    }
    for (const group of letters) for (const span of group) span.style.transform = "none";
    widths = letters.map((group) => group.map((span) => span.getBoundingClientRect().width));
    const boxRect = box.getBoundingClientRect();
    const rulerRect = ruler.getBoundingClientRect();
    const scrollable = Math.max(1, root.scrollHeight - innerHeight);
    // Scrolled by the page, a ruler this long brings the tick for the share of
    // the page scrolled to the marker's place, share * the ruler's height.
    const length = scrollable + rulerRect.height;
    scale.style.setProperty("--length", `${length}px`);
    layout = {
      width: boxRect.width,
      height: boxRect.height,
      offset: rulerRect.top - boxRect.top,
      rulerHeight: rulerRect.height,
      length,
      scrollable,
      // Each section's mark: the share of the page scrolled when it reaches the top.
      shares: sections.map((section) => Math.min(1, section.offsetTop / scrollable)),
      last: labels.length,
    };
    draw();
  }

  // 0 while the section is below the window, 1 once its top nears the window's top.
  function arrival(i) {
    if (i === 0) return 1;
    const top = sections[i].getBoundingClientRect().top;
    let t = clamp((innerHeight - top) / (innerHeight - 120));
    if (scrollY >= root.scrollHeight - innerHeight - 2 && top < innerHeight) t = 1;
    return still.matches ? (t >= 0.5 ? 1 : 0) : t;
  }

  function draw() {
    if (!layout) return;
    const progress = clamp(scrollY / layout.scrollable);
    scale.style.transform = `translateY(${-scrollY}px)`;
    mark.style.setProperty("--progress", progress);
    const percent = String(Math.round(progress * 100)).padStart(3, "0");
    digits.forEach((digit, k) => digit.style.setProperty("--d", percent[k]));

    const t = labels.map((_, i) => arrival(i));
    let current = 0;
    t.forEach((ti, i) => {
      if (ti >= 1) current = i;
    });

    labels.forEach((label, i) => {
      const n = letters[i].length;
      const top = i * LINE;
      const big = across(i, top, 1);
      const passed = across(i, top, SMALL);
      const ahead = across(i, layout.height - (layout.last - i) * LINE, SMALL);
      const standing = onEnd(i, layout.offset + layout.shares[i] * layout.length - scrollY);
      const next = i + 1 < labels.length ? t[i + 1] : 0;

      letters[i].forEach((span, j) => {
        // Letters leave the bottom list one after another, the first leaving
        // first, and reach the ruler in the first third of the trip. They wait
        // there until the whole name stands, then rise to the large slot together
        // in the last third, timed by the last letter.
        const lag = n > 1 ? (STAGGER * j) / (n - 1) : 0;
        const tj = clamp(t[i] * (1 + STAGGER) - lag);
        const rise = clamp(t[i] * (1 + STAGGER) - STAGGER);
        let at;
        if (t[i] < 1) {
          if (tj < 0.35) at = mix(ahead[j], standing[j], ease(tj / 0.35));
          else if (rise < 0.65) at = standing[j];
          else at = mix(standing[j], big[j], ease((rise - 0.65) / 0.35));
        } else {
          // Shrinks into the list above as a whole word, so its letters never bunch up.
          at = mix(big[j], passed[j], ease(clamp((next * (1 + STAGGER) - STAGGER - 0.65) / 0.35)));
        }
        span.style.transform = `translate(${at.x}px, ${at.y}px) rotate(${at.r}deg) scale(${at.s})`;
      });

      label.classList.toggle("is-current", i === current);
      if (i === current) label.setAttribute("aria-current", "location");
      else label.removeAttribute("aria-current");
    });
  }

  let pending = false;
  addEventListener(
    "scroll",
    () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        draw();
      });
    },
    { passive: true },
  );

  // With the browser's scrollbar hidden, the ruler scrolls the page. A click
  // goes to the tick under the pointer; a drag moves the marker as far as the pointer moves.
  const pointerY = (event) => event.clientY - ruler.getBoundingClientRect().top;
  let dragY = 0;
  ruler.addEventListener("pointerdown", (event) => {
    if (!layout) return;
    event.preventDefault(); // a drag on the ruler shouldn't select text
    ruler.setPointerCapture(event.pointerId);
    dragY = event.clientY;
    const share = clamp((pointerY(event) + scrollY) / layout.length);
    scrollTo({ top: share * layout.scrollable, behavior: "instant" });
  });
  ruler.addEventListener("pointermove", (event) => {
    view.style.setProperty("--at", pointerY(event));
    if (!layout || !ruler.hasPointerCapture(event.pointerId)) return;
    scrollBy({ top: ((event.clientY - dragY) * layout.scrollable) / layout.rulerHeight, behavior: "instant" });
    dragY = event.clientY;
  });

  rail.classList.add("is-live");
  document.fonts.ready.then(measure);
  new ResizeObserver(measure).observe(document.body);
}

// Set it up: one panel at a time, picked by its tab; arrow keys move between tabs.
const tabs = [...document.querySelectorAll(".setup-tabs [role=tab]")];

function selectTab(tab) {
  for (const other of tabs) {
    const selected = other === tab;
    other.setAttribute("aria-selected", String(selected));
    other.tabIndex = selected ? 0 : -1;
    document.getElementById(other.getAttribute("aria-controls")).hidden = !selected;
  }
}

tabs.forEach((tab, i) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    const next = tabs[(i + step + tabs.length) % tabs.length];
    selectTab(next);
    next.focus();
  });
});
if (tabs.length) selectTab(tabs[0]);

const flip = document.querySelector(".flip");
flip?.addEventListener("click", () => {
  flip.setAttribute("aria-pressed", String(flip.getAttribute("aria-pressed") !== "true"));
});

// data-copy names an element whose text to copy; data-copy-text is the text itself.
for (const button of document.querySelectorAll("[data-copy], [data-copy-text]")) {
  const label = button.innerHTML;
  button.addEventListener("click", async () => {
    const text = button.dataset.copyText ?? document.getElementById(button.dataset.copy).textContent.trim();
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy";
    }
    setTimeout(() => (button.innerHTML = label), 1800);
  });
}

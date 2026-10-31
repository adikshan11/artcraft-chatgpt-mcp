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

// The page index, after the Craft apps' pages. Each section's name has three
// homes. Names of sections still ahead wait small at the bottom. A name stands
// on end in the middle of the ruler beside its section's top and scrolls with
// it, leaving the bottom list once its column reaches its slot there; the list
// closes up behind it. When the column's top reaches a line near the top of the
// window, the name turns into the top list: passed sections small, the one being
// read large under them. Letters travel tail first along a slightly bowed path,
// and every pose follows from the scroll position. The ruler is as long as the
// page and scrolls with it, its marker running from its top to its bottom as
// you go down the page; its numbers make way for a standing name.
const rail = document.querySelector(".rail");
if (rail) {
  const labels = [...rail.querySelectorAll(".rail-label")];
  const sections = labels.map((label) => document.querySelector(label.hash));
  const letters = labels.map((label) => [...label.children]);
  const box = rail.querySelector(".rail-labels");
  const ruler = rail.querySelector(".rail-ruler");
  const scale = rail.querySelector(".rail-scale");
  const ticks = [...scale.children];
  const tickAt = ticks.map((tick) => Number(tick.style.getPropertyValue("--n")) / 100);
  const digits = [...rail.querySelectorAll(".digit i")];
  const view = rail.querySelector(".rail-view");
  const mark = rail.querySelector(".rail-mark");
  const still = matchMedia("(prefers-reduced-motion: reduce)");

  const BASE = 34; // the letters' own font size; every pose scales from it
  const RIDE = 20 / BASE; // standing on the ruler
  const SMALL = 16 / BASE; // the top and bottom lists
  const LINE = 20; // one row of the lists
  const GAP = 26; // between the top list and the large name under it
  const TURN_LINE = 0.12; // where a standing name turns into the top list, as a share of the window height
  const TURN = 200; // px of scroll the turn takes
  const LIFT = 240; // px of scroll a name takes to leave the bottom list for the ruler
  const STAGGER = 0.16; // how much later each letter sets off than the one after it
  const ARC = 28; // how far a letter's path bows in toward the page
  const YIELD = 24; // ruler numbers this close to a standing name fade out
  let widths = []; // each letter's width at BASE
  let layout = null;
  let dragging = false;

  const clamp = (n) => Math.min(1, Math.max(0, n));
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  const settled = (t) => (still.matches ? Math.round(t) : t);

  // Poses give each letter's centre in the box, its turn and its scale.
  // A name written left to right, its last letter against the box's right edge.
  const across = (i, j, y, s) => ({ x: layout.width - (layout.total[i] - layout.before[i][j] - widths[i][j] / 2) * s, y, r: 0, s });
  // A name standing on the ruler, read bottom to top, its top at `top`.
  const standing = (i, j, top) => ({ x: layout.rideX, y: top + (layout.total[i] - layout.before[i][j] - widths[i][j] / 2) * RIDE, r: -90, s: RIDE });

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
      top: boxRect.top,
      rideX: rulerRect.left + rulerRect.width / 2 - boxRect.left,
      rulerTop: rulerRect.top,
      rulerHeight: rulerRect.height,
      length,
      scrollable,
      sections: sections.map((section) => {
        const r = section.getBoundingClientRect();
        return { top: r.top + scrollY, bottom: r.bottom + scrollY };
      }),
      total: widths.map((w) => w.reduce((a, b) => a + b, 0)),
      before: widths.map((w) => w.map((_, j) => w.slice(0, j).reduce((a, b) => a + b, 0))),
    };
    layout.turns = turnWindows();
    draw();
  }

  // The stretch of scroll over which each name turns into the top list, one name
  // at a time. Normally it's the TURN px before its standing column's top reaches
  // the line. Sections too near the end to bring their name up to the line share
  // what scroll is left once they're in view, in order. `line` marks the first kind.
  function turnWindows() {
    const end = layout.scrollable;
    const windows = layout.sections.map((sec) => {
      const at = sec.top - TURN_LINE * innerHeight;
      // A turn that would be under way at the top of the page starts there instead.
      return at > end ? null : { start: Math.min(Math.max(0, at - TURN), at - 1), end: at, line: true };
    });
    const late = windows.flatMap((w, i) => (i > 0 && !w ? [i] : []));
    if (late.length) {
      const earlier = windows.filter((w, i) => i > 0 && w);
      let from = Math.min(layout.sections[late[0]].bottom - innerHeight, end - 24);
      if (earlier.length) from = Math.max(from, earlier[earlier.length - 1].end);
      const share = (end - from) / late.length;
      late.forEach((i, k) => (windows[i] = { start: from + k * share, end: from + (k + 1) * share, line: false }));
    }
    return windows;
  }

  function draw() {
    if (!layout) return;
    const progress = clamp(scrollY / layout.scrollable);
    scale.style.transform = `translateY(${-scrollY}px)`;
    mark.style.setProperty("--progress", progress);
    const percent = String(Math.round(progress * 100)).padStart(3, "0");
    digits.forEach((digit, k) => digit.style.setProperty("--d", percent[k]));

    const queueLine = layout.height - LINE / 2;
    // How far each name has turned into the top list, and how far it has left
    // the bottom list. The first section is the one being read from the start.
    const phase = layout.sections.map((sec, i) => {
      const top = sec.top - scrollY - layout.top; // the standing column's top: its section's top
      const length = layout.total[i] * RIDE;
      if (i === 0) return { top, length, turn: 1, lift: 1, slot: queueLine };
      const w = layout.turns[i];
      const turn = clamp((scrollY - w.start) / Math.max(1, w.end - w.start));
      return { top, length, turn: settled(turn), lift: 0, slot: queueLine };
    });
    // The bottom list closes up as names leave it, so each slot depends on the names below.
    let below = 0;
    for (let i = phase.length - 1; i > 0; i--) {
      const ph = phase[i];
      ph.slot = queueLine - LINE * below;
      // The scroll position at which the column's foot reaches its slot. A lift
      // that would be under way at the top of the page starts there instead.
      const rise = layout.sections[i].top - layout.top + ph.length - ph.slot;
      const from = Math.max(0, rise - LIFT);
      ph.lift = settled(rise <= 0 ? 1 : clamp((scrollY - from) / Math.max(1, rise - from)));
      below += 1 - ph.lift;
    }

    let turned = 0;
    let turning = 0;
    for (const ph of phase) {
      if (ph.turn >= 1) turned++;
      else turning += ph.turn;
    }
    const current = turning >= 0.5 ? turned : turned - 1;
    const standingSpans = [];

    labels.forEach((label, i) => {
      const { top, length, turn, lift, slot } = phase[i];
      const row = i * LINE + LINE / 2;
      let p = 1;
      let from;
      let to;
      if (turn >= 1) {
        // In the top list. The newest one is large under the others, and shrinks
        // into its row as the next name turns in.
        const shrink = i === turned - 1 ? Math.min(1, turning) : 1;
        from = to = (j) => across(i, j, row + GAP * (1 - shrink), 1 - (1 - SMALL) * shrink);
      } else if (turn > 0) {
        p = turn;
        from = (j) => standing(i, j, top);
        to = (j) => across(i, j, row + GAP, 1);
      } else if (lift < 1) {
        // Rises to the column it will hold the moment it's fully on the ruler,
        // its foot at its slot, so it never runs off the bottom.
        p = lift;
        from = (j) => across(i, j, slot, SMALL);
        to = (j) => standing(i, j, slot - length);
      } else {
        from = to = (j) => standing(i, j, top);
      }

      const n = letters[i].length;
      const span = 1 + (n - 1) * STAGGER;
      letters[i].forEach((el, j) => {
        const e = ease(clamp(p * span - (n - 1 - j) * STAGGER));
        const a = from(j);
        const b = to(j);
        const bendX = (a.x + b.x) / 2 - ARC;
        const bendY = (a.y + b.y) / 2;
        const u = 1 - e;
        const x = u * u * a.x + 2 * u * e * bendX + e * e * b.x;
        const y = u * u * a.y + 2 * u * e * bendY + e * e * b.y;
        const r = a.r + (b.r - a.r) * e;
        const s = a.s + (b.s - a.s) * e;
        el.style.transform = `translate(${x}px, ${y}px) rotate(${r}deg) scale(${s}) translate(${-widths[i][j] / 2}px, ${-BASE / 2}px)`;
      });

      const onRuler = i === 0 ? 0 : lift * (1 - turn);
      if (onRuler > 0) standingSpans.push({ top: layout.top + top, bottom: layout.top + top + length, k: onRuler });
      label.classList.toggle("is-current", i === current);
      if (i === current) label.setAttribute("aria-current", "location");
      else label.removeAttribute("aria-current");
    });

    ticks.forEach((tick, k) => {
      const y = layout.rulerTop + tickAt[k] * layout.length - scrollY;
      let fade = 0;
      for (const s of standingSpans) if (y > s.top - YIELD && y < s.bottom + YIELD) fade = Math.max(fade, s.k);
      tick.style.setProperty("--yield", fade);
    });
  }

  // A turn left half-done when scrolling stops finishes, or undoes, whichever is nearer.
  function settle() {
    if (!layout || still.matches || dragging) return;
    layout.turns.forEach((w, i) => {
      if (i === 0 || !w.line) return;
      const turn = (scrollY - w.start) / Math.max(1, w.end - w.start);
      if (turn > 0.02 && turn < 0.98) scrollTo({ top: turn >= 0.5 ? w.end : w.start, behavior: "smooth" });
    });
  }

  let pending = false;
  let settleTimer = 0;
  addEventListener(
    "scroll",
    () => {
      clearTimeout(settleTimer);
      settleTimer = setTimeout(settle, 400);
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
    dragging = true;
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

  ruler.addEventListener("lostpointercapture", () => (dragging = false));

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

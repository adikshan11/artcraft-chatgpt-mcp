// Light/dark switch, the narrow-screen menu, the page index, the logo's flip and the copy button.

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

// The page index: each section sits at its share of the page's height, the part
// on screen is shaded, and the section being read is marked.
const rail = document.querySelector(".rail");
if (rail) {
  const links = [...rail.querySelectorAll(".rail-links a")];
  const sections = links.map((link) => document.querySelector(link.hash));
  const view = rail.querySelector(".rail-view");

  function markView() {
    const height = root.scrollHeight;
    view.style.setProperty("--view-top", scrollY / height);
    view.style.setProperty("--view-size", innerHeight / height);
    let current = -1;
    sections.forEach((section, i) => {
      if (section.getBoundingClientRect().top < innerHeight * 0.4) current = i;
    });
    links.forEach((link, i) => {
      if (i === current) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  }

  function placeLinks() {
    let last = -Infinity;
    links.forEach((link, i) => {
      // At least 5% apart, so short sections don't stack their names.
      const pos = Math.max((sections[i].offsetTop / root.scrollHeight) * 100, last + 5);
      link.style.setProperty("--pos", `${pos}%`);
      last = pos;
    });
    markView();
  }

  addEventListener("scroll", markView, { passive: true });
  new ResizeObserver(placeLinks).observe(document.body);
}

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

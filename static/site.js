// Light/dark switch, the narrow-screen menu, the tern's flip and the copy button.

const root = document.documentElement;
const themeButton = document.querySelector("[data-theme-toggle]");

function currentTheme() {
  return root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
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

const flip = document.querySelector(".flip");
flip?.addEventListener("click", () => {
  flip.setAttribute("aria-pressed", String(flip.getAttribute("aria-pressed") !== "true"));
});

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    const text = document.getElementById(button.dataset.copy).textContent.trim();
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy";
    }
    setTimeout(() => (button.textContent = "Copy"), 1800);
  });
}

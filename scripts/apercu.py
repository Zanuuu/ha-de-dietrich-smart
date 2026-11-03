"""Captures du panneau (banc tests/frontend/harness.html) pour contrôle visuel.

Lancé par scripts/apercu.sh dans un conteneur Playwright sur hote-docker (le poste
n'a pas de navigateur). Écrit /w/out/*.png ; signale les erreurs de console et les
débordements horizontaux, et sort en erreur s'il y en a.
"""

import os
import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000/tests/frontend/harness.html"
OUT = "/w/out"
os.makedirs(OUT, exist_ok=True)
problems = []

OVERFLOW = """() => {
  const p = document.querySelector('dd-smart-panel');
  const over = [document.documentElement, ...p.shadowRoot.querySelectorAll('*')]
    .filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible'
      && !el.classList.contains('band') && el.tagName !== 'DIALOG');
  if (document.documentElement.scrollWidth > innerWidth + 1) return ['page'];
  return over.map((el) => el.tagName + '.' + el.className);
}"""

with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--lang=fr-FR"])
    for theme in ("light", "dark"):
        for width, height, size in ((390, 844, "mobile"), (1440, 960, "bureau")):
            # Navigateur en français, comme chez l'utilisateur (heures sur 24 h dans les champs).
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1, locale="fr-FR")
            page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
            page.on("pageerror", lambda e: problems.append(f"page: {e}"))
            page.goto(f"{BASE}?theme={theme}")
            page.wait_for_selector("dd-smart-panel >> .week")
            for tab in ("programs", "journal"):
                if tab == "journal":
                    page.locator("dd-smart-panel >> nav button", has_text="Journal").click()
                    page.wait_for_selector("dd-smart-panel >> .journal li")
                page.screenshot(path=f"{OUT}/{tab}-{size}-{theme}.png", full_page=True)
                if over := page.evaluate(OVERFLOW):
                    problems.append(f"débordement {tab} {size} {theme}: {over}")
            if theme == "light":
                page.locator("dd-smart-panel >> nav button", has_text="Programmes").click()
                page.locator("dd-smart-panel >> .wrow").first.click()
                page.wait_for_selector("dd-smart-panel >> dialog[open]")
                page.screenshot(path=f"{OUT}/editor-{size}-{theme}.png")
            page.close()
    browser.close()

for p in problems:
    print(p)
sys.exit(1 if problems else 0)

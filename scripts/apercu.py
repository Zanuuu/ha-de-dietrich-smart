"""Captures du panneau (banc tests/frontend/harness.html) pour contrôle visuel.

Lancé par scripts/apercu.sh dans un conteneur Playwright. Écrit /w/out/*.png ; signale les erreurs de console et les
débordements horizontaux, et sort en erreur s'il y en a.
"""

import os
import sys
from pathlib import Path

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
            for tab in ("programs", "curve", "conso", "journal"):
                if tab == "conso":
                    page.locator("dd-smart-panel >> nav button", has_text="Consommation").click()
                    page.wait_for_selector("dd-smart-panel >> .tiles")
                if tab == "curve":
                    page.locator("dd-smart-panel >> nav button", has_text="Courbe").click()
                    page.wait_for_selector("dd-smart-panel >> .chart svg polyline.prop")
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
    # Simulation sur les 7 derniers jours (rejeu).
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1, locale="fr-FR")
    page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
    page.on("pageerror", lambda e: problems.append(f"page: {e}"))
    page.goto(BASE)
    page.wait_for_selector("dd-smart-panel >> .week")
    page.locator("dd-smart-panel >> nav button", has_text="Courbe").click()
    page.locator("dd-smart-panel >> .seg-ctl button", has_text="7 derniers jours").click()
    page.wait_for_selector("dd-smart-panel >> tbody tr:nth-child(7)")
    page.screenshot(path=f"{OUT}/curve-history-mobile.png", full_page=True)
    if over := page.evaluate(OVERFLOW):
        problems.append(f"débordement simulation: {over}")
    page.close()
    # Export CSV : le navigateur reçoit le fichier, avec le BOM pour Excel.
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1, locale="fr-FR")
    page.on("pageerror", lambda e: problems.append(f"page: {e}"))
    page.goto(BASE)
    page.wait_for_selector("dd-smart-panel >> .week")
    page.locator("dd-smart-panel >> nav button", has_text="Consommation").click()
    with page.expect_download() as dl:
        page.locator("dd-smart-panel >> button", has_text="Jours (CSV)").click()
    d = dl.value
    content = Path(d.path()).read_bytes()
    if d.suggested_filename != "de-dietrich-conso-jours-2026-11-04.csv" or not content.startswith(b"\xef\xbb\xbfJour;"):
        problems.append(f"export CSV : {d.suggested_filename} {content[:20]!r}")
    page.close()
    # Courbe dangereuse pour le plancher : alerte et bascule proposée, puis confirmation.
    for width, height, size in ((390, 844, "mobile"), (1440, 960, "bureau")):
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1, locale="fr-FR")
        page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: problems.append(f"page: {e}"))
        page.goto(f"{BASE}?case=switch")
        page.wait_for_selector("dd-smart-panel >> .week")
        page.locator("dd-smart-panel >> nav button", has_text="Courbe").click()
        page.wait_for_selector("dd-smart-panel >> .warn")
        page.screenshot(path=f"{OUT}/curve-switch-{size}.png", full_page=True)
        if over := page.evaluate(OVERFLOW):
            problems.append(f"débordement courbe bascule {size}: {over}")
        page.locator("dd-smart-panel >> button", has_text="Appliquer la proposition").click()
        page.wait_for_selector("dd-smart-panel >> dialog.confirm[open]")
        page.screenshot(path=f"{OUT}/curve-confirm-{size}.png")
        page.close()
    # Compte non administrateur : aucun bouton d'écriture (le serveur les refuserait).
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1, locale="fr-FR")
    page.on("pageerror", lambda e: problems.append(f"page: {e}"))
    page.goto(f"{BASE}?user=guest")
    page.wait_for_selector("dd-smart-panel >> .week")
    if n := page.locator("dd-smart-panel >> .stepper button").count():
        problems.append(f"non-admin : {n} boutons de consigne visibles")
    page.locator("dd-smart-panel >> .wrow").first.click()
    if page.locator("dd-smart-panel >> dialog[open]").count():
        problems.append("non-admin : l'éditeur de créneaux s'ouvre")
        page.keyboard.press("Escape")
    page.screenshot(path=f"{OUT}/programs-mobile-guest.png", full_page=True)
    page.locator("dd-smart-panel >> nav button", has_text="Journal").click()
    page.wait_for_selector("dd-smart-panel >> .journal li")
    if n := page.locator("dd-smart-panel >> .journal button.link").count():
        problems.append(f"non-admin : {n} liens « Annuler » visibles")
    page.locator("dd-smart-panel >> nav button", has_text="Courbe").click()
    page.wait_for_selector("dd-smart-panel >> .chart svg")
    if n := page.locator("dd-smart-panel >> .stepper button, dd-smart-panel >> .actions button").count():
        problems.append(f"non-admin : {n} boutons de réglage de la courbe visibles")
    page.screenshot(path=f"{OUT}/curve-mobile-guest.png", full_page=True)
    page.close()
    browser.close()

for p in problems:
    print(p)
sys.exit(1 if problems else 0)

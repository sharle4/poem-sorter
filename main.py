#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Poetica – Application Tkinter ultra‑fluide avec recherche instantanée, animations,
filtres séparés (Auteur vs Thèmes) et design modernisé.

Améliorations majeures vs version précédente :
- Performances :
  • Débouçage (debounce) de la recherche et des filtres
  • Pré‑indexation (titres en minuscule, index auteur→poèmes, thème→poèmes)
  • Rendu optimisé (zébrage, insertion groupée, détection d'aucun changement)
- UX / Design :
  • Style moderne (thème ttk "clam", couleurs sobres, espacements généreux)
  • Animations d'apparition des résultats (fondu de surbrillance)
  • Table triable par clic sur l'entête (commentaires/titre/auteur)
  • Boutons d'actions (ouvrir sélection, copier URL, exporter CSV)
  • Panneau de filtres séparé : Auteur (combo) et Thèmes (cases à cocher)
  • Les noms d'auteurs sont EXCLUS des "catégories" (on n'affiche que les thèmes)

Dépendances : requests, beautifulsoup4
  pip install requests beautifulsoup4

Exécution :
  python poetica_ultra_ui.py

Note :
- Le scraping (si poetica_poems.json est absent) reste identique mais on
  filtre les catégories pour supprimer celles correspondant aux auteurs.
- Si vous avez déjà un JSON généré par la version précédente et que des noms
  d'auteurs s'y trouvent dans "categories", vous pouvez relancer un scrape
  (ou utiliser l'option de nettoyage incluse ci‑dessous).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Tuple, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import webbrowser
import os
import csv

# ----------------------------- Configuration ----------------------------- #
BASE_URL = "https://www.poetica.fr/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 1.0  # politesse

OUTPUT_JSON = "poetica_poems.json"
CACHE_INTERMEDIATE_EVERY = 100  # sauvegarde intermédiaire toutes les N entrées

# Pour tester rapidement, fixez un plafond (None pour illimité)
MAX_AUTHORS = None  # ex: 5
MAX_POEMS_PER_AUTHOR = None  # ex: 50

# Apparence (peut être ajustée)
ACCENT = "#4F46E5"   # Indigo doux
BG     = "#F7F7FA"   # Gris très clair
FG     = "#0F172A"   # Bleu nuit très foncé
MUTED  = "#6B7280"   # Gris moyen
ROW_HILITE = "#EEF2FF"  # Accent très pâle pour animation

ANIMATION_ROWS = 20       # nbre max de lignes animées à chaque refresh
ANIMATION_STEPS = 6       # nbre d'étapes du fondu
ANIMATION_DELAY_MS = 30   # délai entre étapes

DEBOUNCE_MS = 180         # délai de debouncing pour recherche/filtre

# ------------------------------- Data Model ------------------------------ #
@dataclass
class Poem:
    title: str
    url: str
    comments: int
    author: str
    categories: List[str]
    # champs dérivés pour accélérer les filtres
    title_lc: str = field(init=False)

    def __post_init__(self):
        self.title_lc = self.title.lower()

# ---------------------------- Utility Functions -------------------------- #

def norm_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    if not u:
        return ""
    # retirer slash final et forcer minuscule
    return u[:-1].lower() if u.endswith('/') else u.lower()


def get_soup(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            print(f"[WARN] HTTP {resp.status_code} for {url}")
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"[ERROR] {e} for {url}")
        return None


def extract_menus(start_url: str = BASE_URL) -> Tuple[List[Dict], List[Dict]]:
    """Retourne (auteurs, categories) depuis les menus.
    Chaque élément est {"name": str, "url": str}.
    """
    soup = get_soup(start_url)
    if soup is None:
        return [], []

    authors, categories = [], []
    for a in soup.select("#menu-poemes-par-auteur li a"):
        name = a.get_text(strip=True)
        url = a.get("href", "").strip()
        if url and name:
            authors.append({"name": name, "url": url})

    for a in soup.select("#menu-poemes-par-theme li a"):
        name = a.get_text(strip=True)
        url = a.get("href", "").strip()
        if url and name:
            categories.append({"name": name, "url": url})

    return authors, categories


def parse_comments_from_article(article: BeautifulSoup) -> int:
    text = " ".join(article.stripped_strings)
    m = re.search(r"(\d+)\s*commentaire", text, flags=re.I)
    if m:
        return int(m.group(1))
    cl = article.select_one("span.comments-link")
    if cl:
        m = re.search(r"(\d+)", cl.get_text(" ", strip=True))
        if m:
            return int(m.group(1))
    return 0


def extract_poems_from_listing(listing_url: str) -> List[Dict]:
    soup = get_soup(listing_url)
    if soup is None:
        return []

    poems: List[Dict] = []
    for article in soup.select("article.post"):
        a = article.select_one("h2.entry-title a, h1.entry-title a, .entry-title a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get("href") or ""
        url = href if href.startswith("http") else urljoin(BASE_URL, href)
        comments = parse_comments_from_article(article)
        poems.append({"title": title, "url": url, "comments": comments})

    return poems


def find_next_page(listing_soup: BeautifulSoup) -> Optional[str]:
    link = listing_soup.select_one("a[rel=next]")
    if link and link.get("href"):
        return link["href"]
    link = listing_soup.select_one("a.next.page-numbers")
    if link and link.get("href"):
        return link["href"]
    return None


def iterate_all_listing_pages(first_url: str):
    url = first_url
    visited = set()
    while url and url not in visited:
        visited.add(url)
        soup = get_soup(url)
        if soup is None:
            break
        yield url, soup
        next_url = find_next_page(soup)
        if next_url and not next_url.startswith("http"):
            next_url = urljoin(url, next_url)
        url = next_url
        time.sleep(DELAY_BETWEEN_REQUESTS)


def fetch_poems_for_author(author_name: str, author_url: str, max_poems: Optional[int] = None) -> List[Dict]:
    collected: List[Dict] = []
    for page_url, soup in iterate_all_listing_pages(author_url):
        for poem in extract_poems_from_listing(page_url):
            poem["author"] = author_name
            collected.append(poem)
            if max_poems and len(collected) >= max_poems:
                return collected
    return collected


def fetch_poem_themes(poem_url: str, author_urls_norm: Set[str]) -> List[str]:
    """Extrait les thèmes (catégories hors auteurs) depuis la page du poème.
    On exclut toute catégorie dont l'URL normalisée appartient au set des URLs auteurs.
    """
    soup = get_soup(poem_url)
    if soup is None:
        return []
    themes: List[str] = []
    selectors = [
        ".cat-links a",
        ".entry-footer .cat-links a",
        ".posted-in a[rel=category]",
        "a[rel=category]",
        ".entry-meta a[rel=category]",
    ]
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href", "").strip()
            txt = a.get_text(strip=True)
            if not txt:
                continue
            # Exclure les catégories qui sont en fait des pages d'auteurs
            if norm_url(href) in author_urls_norm:
                continue
            if txt not in themes:
                themes.append(txt)
        if themes:
            break
    return themes


def load_existing_data(path: str = OUTPUT_JSON) -> List[Poem]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        poems = [Poem(**p) for p in raw]
        return poems
    except Exception as e:
        print(f"[WARN] Unable to load existing data: {e}")
        return []


def save_data(poems: List[Poem], path: str = OUTPUT_JSON) -> None:
    data = [asdict(p) for p in poems]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ------------------------------- Scraper --------------------------------- #

def scrape_all() -> List[Poem]:
    print("[INFO] Extraction des menus (auteurs & catégories)…")
    authors, _site_categories = extract_menus(BASE_URL)
    if not authors:
        print("[ERROR] Aucune entrée trouvée dans le menu des auteurs. Vérifiez la page.")
        return []

    if MAX_AUTHORS is not None:
        authors = authors[: MAX_AUTHORS]

    author_urls_norm: Set[str] = {norm_url(a["url"]) for a in authors}

    poems: List[Poem] = []
    seen_poem_urls: set[str] = set()

    count_auth = 0
    for author in authors:
        count_auth += 1
        name = author["name"]
        url = author["url"]
        print(f"[INFO] Auteur {count_auth}/{len(authors)} : {name}")
        time.sleep(DELAY_BETWEEN_REQUESTS)

        listing_poems = fetch_poems_for_author(name, url, max_poems=MAX_POEMS_PER_AUTHOR)
        print(f"    - {len(listing_poems)} poème(s) listé(s)")

        for i, p in enumerate(listing_poems, 1):
            p_url = p["url"]
            if p_url in seen_poem_urls:
                continue
            seen_poem_urls.add(p_url)

            themes = fetch_poem_themes(p_url, author_urls_norm)
            poem = Poem(
                title=p["title"],
                url=p_url,
                comments=int(p.get("comments", 0)),
                author=name,
                categories=themes,
            )
            poems.append(poem)

            if len(poems) % CACHE_INTERMEDIATE_EVERY == 0:
                print(f"[INFO] Sauvegarde intermédiaire ({len(poems)} poèmes)…")
                save_data(poems, OUTPUT_JSON)
            time.sleep(DELAY_BETWEEN_REQUESTS)

    poems.sort(key=lambda x: x.comments, reverse=True)
    save_data(poems, OUTPUT_JSON)
    print(f"[INFO] Terminé. {len(poems)} poèmes enregistrés dans {OUTPUT_JSON}")
    return poems


# ------------------------------- GUI ------------------------------------- #

class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=BG)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)
        self.scrollable_frame.configure(style="Card.TFrame")

        self.scrollable_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.window_id = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")

        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self.canvas.configure(yscrollcommand=scrollbar.set, bg=BG)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")


class PoeticaApp:
    def __init__(self, poems: List[Poem]):
        self.poems = poems
        self._build_indices()

        self.root = tk.Tk()
        self.root.title("Poetica – Poèmes les plus commentés")
        self.root.geometry("1200x760")
        self.root.configure(bg=BG)

        self._setup_style()
        self._build_widgets()
        

        # état pour debouncing
        self._refresh_job: Optional[str] = None
        self._current_sort = ("comments", True)  # (col, desc)
        
        self._populate()

    # --- Data indices / caches --- #
    def _build_indices(self):
        self.by_author: Dict[str, List[Poem]] = {}
        self.by_theme: Dict[str, List[Poem]] = {}
        for p in self.poems:
            self.by_author.setdefault(p.author, []).append(p)
            for c in p.categories:
                self.by_theme.setdefault(c, []).append(p)

    # --- Styles --- #
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("H1.TLabel", background=BG, foreground=FG, font=("Segoe UI", 18, "bold"))
        style.configure("H2.TLabel", background=BG, foreground=FG, font=("Segoe UI", 12, "bold"))
        style.configure("Accent.TButton", padding=10)
        style.map("Accent.TButton",
                  background=[("active", ACCENT)],
                  foreground=[("active", "white")])
        style.configure("Treeview", font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    # --- UI --- #
    def _build_widgets(self):
        # Top Bar
        top = ttk.Frame(self.root)
        top.pack(side="top", fill="x", padx=20, pady=16)
        ttk.Label(top, text="Poetica — Explorer par popularité", style="H1.TLabel").pack(side="left")

        # Filters Row
        filters = ttk.Frame(self.root)
        filters.pack(side="top", fill="x", padx=20, pady=(0, 10))

        # Auteur (combo)
        left = ttk.Frame(filters)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="Auteur", style="H2.TLabel").pack(anchor="w")
        self.author_var = tk.StringVar(value="Tous les auteurs")
        authors_sorted = sorted(self.by_author.keys())
        self.author_combo = ttk.Combobox(
            left,
            values=["Tous les auteurs"] + authors_sorted,
            textvariable=self.author_var,
            state="readonly",
            width=40,
        )
        self.author_combo.pack(anchor="w", pady=4)
        self.author_combo.bind("<<ComboboxSelected>>", self._on_filters_changed)

        # Recherche instantanée
        middle = ttk.Frame(filters)
        middle.pack(side="left", fill="x", expand=True, padx=14)
        ttk.Label(middle, text="Recherche (titre)", style="H2.TLabel").pack(anchor="w")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(middle, textvariable=self.search_var, width=40)
        self.search_entry.pack(anchor="w", pady=4, fill="x")
        self.search_entry.bind("<KeyRelease>", self._on_filters_changed)

        # Actions
        right = ttk.Frame(filters)
        right.pack(side="right")
        ttk.Button(right, text="Réinitialiser", command=self._reset_filters, style="Accent.TButton").pack(side="left", padx=6)
        ttk.Button(right, text="Ouvrir sélection", command=self._open_selected).pack(side="left", padx=6)
        ttk.Button(right, text="Copier URL", command=self._copy_selected_url).pack(side="left", padx=6)
        ttk.Button(right, text="Exporter CSV", command=self._export_csv).pack(side="left", padx=6)

        # Panneau Thèmes (checklist)
        side = ttk.Frame(self.root)
        side.pack(side="right", fill="y", padx=20, pady=10)
        ttk.Label(side, text="Thèmes", style="H2.TLabel").pack(anchor="w")
        self.cat_panel = ScrollableFrame(side)
        self.cat_panel.pack(fill="y", expand=False)
        self.category_vars: Dict[str, tk.BooleanVar] = {}
        # boutons check all / none
        a = ttk.Frame(side)
        a.pack(fill="x", pady=(6,0))
        ttk.Button(a, text="Tout cocher", command=self._check_all).pack(side="left")
        ttk.Button(a, text="Tout décocher", command=self._uncheck_all).pack(side="left", padx=6)

        # Table des résultats
        table_frame = ttk.Frame(self.root)
        table_frame.pack(side="left", fill="both", expand=True, padx=20, pady=10)

        cols = ("comments", "title", "author", "categories", "url")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings")
        self.tree.heading("comments", text="Commentaires", command=lambda: self._sort_by("comments"))
        self.tree.heading("title", text="Titre", command=lambda: self._sort_by("title"))
        self.tree.heading("author", text="Auteur", command=lambda: self._sort_by("author"))
        self.tree.heading("categories", text="Thèmes")
        self.tree.heading("url", text="URL")
        self.tree.column("comments", width=130, anchor="center")
        self.tree.column("title", width=420)
        self.tree.column("author", width=200)
        self.tree.column("categories", width=320)
        self.tree.column("url", width=0, stretch=False)  # caché mais stocké

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._on_double_click)

        # Status bar
        self.status = tk.StringVar(value="Prêt.")
        sb = ttk.Label(self.root, textvariable=self.status, anchor="w", style="Muted.TLabel")
        sb.pack(side="bottom", fill="x", padx=20, pady=(0,8))

        # Initialisation du panneau de thèmes
        self._refresh_category_panel()

    def _populate(self):
        self._refresh_table(animated=True)

    # --- Helpers filtres --- #
    def _get_active_author(self) -> Optional[str]:
        a = self.author_var.get()
        return None if a == "Tous les auteurs" else a

    def _active_categories(self) -> List[str]:
        return [name for name, var in self.category_vars.items() if var.get()]

    # --- Debounce --- #
    def _on_filters_changed(self, *_):
        if self._refresh_job is not None:
            self.root.after_cancel(self._refresh_job)
        self._refresh_job = self.root.after(DEBOUNCE_MS, self._apply_filters)

    def _apply_filters(self):
        self._refresh_job = None
        self._refresh_category_panel(update_only=True)
        self._refresh_table(animated=True)

    # --- Panneau thèmes --- #
    def _refresh_category_panel(self, update_only: bool=False):
        author = self._get_active_author()
        # thèmes possibles selon auteur
        themes = sorted({c for p in self.poems if (author is None or p.author == author) for c in p.categories})

        if not update_only:
            # full rebuild
            for child in list(self.cat_panel.scrollable_frame.children.values()):
                child.destroy()
            self.category_vars.clear()
            for c in themes:
                var = tk.BooleanVar(value=False)
                chk = ttk.Checkbutton(self.cat_panel.scrollable_frame, text=c, variable=var, command=self._on_filters_changed)
                chk.pack(anchor="w")
                self.category_vars[c] = var
        else:
            # si des thèmes disparaissent / apparaissent, on reconcilie
            existing = set(self.category_vars.keys())
            newset = set(themes)
            # remove obsolete
            for obsolete in existing - newset:
                for child in list(self.cat_panel.scrollable_frame.children.values()):
                    if isinstance(child, ttk.Checkbutton) and child.cget("text") == obsolete:
                        child.destroy()
                self.category_vars.pop(obsolete, None)
            # add new
            for added in sorted(newset - existing):
                var = tk.BooleanVar(value=False)
                chk = ttk.Checkbutton(self.cat_panel.scrollable_frame, text=added, variable=var, command=self._on_filters_changed)
                chk.pack(anchor="w")
                self.category_vars[added] = var

    # --- Actions --- #
    def _uncheck_all(self):
        for var in self.category_vars.values():
            var.set(False)
        self._on_filters_changed()

    def _check_all(self):
        for var in self.category_vars.values():
            var.set(True)
        self._on_filters_changed()

    def _reset_filters(self):
        self.author_var.set("Tous les auteurs")
        self.search_var.set("")
        self._refresh_category_panel(update_only=False)
        self._refresh_table(animated=True)

    def _on_double_click(self, event):
        item = self.tree.focus()
        if not item:
            return
        url = self.tree.set(item, "url")
        if url:
            webbrowser.open(url)

    def _open_selected(self):
        for sel in self.tree.selection():
            url = self.tree.set(sel, "url")
            if url:
                webbrowser.open(url)

    def _copy_selected_url(self):
        items = self.tree.selection()
        if not items:
            return
        urls = [self.tree.set(i, "url") for i in items if self.tree.set(i, "url")]
        if not urls:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(urls))
        self.status.set(f"URL copiée ({len(urls)})")

    def _export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        # exporter les lignes actuellement visibles
        rows = []
        for iid in self.tree.get_children():
            rows.append([
                self.tree.set(iid, "comments"),
                self.tree.set(iid, "title"),
                self.tree.set(iid, "author"),
                self.tree.set(iid, "categories"),
                self.tree.set(iid, "url"),
            ])
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Commentaires", "Titre", "Auteur", "Thèmes", "URL"])
            w.writerows(rows)
        self.status.set(f"Exporté → {path}")

    # --- Tri --- #
    def _sort_by(self, col: str):
        cur_col, cur_desc = self._current_sort
        if col == cur_col:
            desc = not cur_desc
        else:
            desc = (col == "comments")  # par défaut, commentaires décroissant
        self._current_sort = (col, desc)
        self._refresh_table(animated=False)

    # --- Filtrage + affichage --- #
    def _filter_poems(self) -> List[Poem]:
        author = self._get_active_author()
        active_cats = self._active_categories()
        q = self.search_var.get().strip().lower()

        # point de départ : soit tous, soit ceux de l'auteur
        if author is None:
            base = self.poems
        else:
            base = self.by_author.get(author, [])

        res: List[Poem] = []
        if q:
            if active_cats:
                for p in base:
                    if q in p.title_lc and any(c in p.categories for c in active_cats):
                        res.append(p)
            else:
                for p in base:
                    if q in p.title_lc:
                        res.append(p)
        else:
            if active_cats:
                for p in base:
                    if any(c in p.categories for c in active_cats):
                        res.append(p)
            else:
                res = list(base)
        return res

    def _refresh_table(self, animated: bool):
        data = self._filter_poems()

        col, desc = self._current_sort
        if col == "comments":
            data.sort(key=lambda x: x.comments, reverse=desc)
        elif col == "title":
            data.sort(key=lambda x: x.title_lc, reverse=desc)
        elif col == "author":
            data.sort(key=lambda x: x.author.lower(), reverse=desc)

        # Clear table
        self.tree.delete(*self.tree.get_children())

        # Insert avec zébrage et marquage pour animation
        to_animate = []
        for i, p in enumerate(data):
            cats = ", ".join(p.categories)
            iid = self.tree.insert("", "end", values=(p.comments, p.title, p.author, cats, p.url))
            tag = "odd" if i % 2 else "even"
            self.tree.item(iid, tags=(tag,))
            if i < ANIMATION_ROWS:
                to_animate.append(iid)

        # Styles de lignes
        self.tree.tag_configure("even", background="white")
        self.tree.tag_configure("odd", background="#FBFBFE")
        self.tree.tag_configure("hilite", background=ROW_HILITE)

        self.status.set(f"{len(data)} poème(s) – Auteur: {self._get_active_author() or 'Tous'} – Thèmes actifs: {len(self._active_categories())}")

        if animated and to_animate:
            self._animate_rows(to_animate)

    # --- Animation simple (fondu de surbrillance) --- #
    def _animate_rows(self, iids: List[str]):
        # Étape 0: appliquer la surbrillance
        for iid in iids:
            self.tree.item(iid, tags=("hilite",))
        # Puis revenir au zébrage sur plusieurs étapes
        def step(k: int):
            if k >= ANIMATION_STEPS:
                # rétablir zébrage final
                children = self.tree.get_children()
                for idx, iid in enumerate(children):
                    tag = "odd" if idx % 2 else "even"
                    self.tree.item(iid, tags=(tag,))
                return
            # légère alternance (pas de vrai alpha dans Tkinter ; on joue sur les tags)
            # On pourrait simuler une atténuation par alternance hilite/even/odd
            if k % 2 == 0:
                for iid in iids:
                    self.tree.item(iid, tags=("even",))
            else:
                for iid in iids:
                    self.tree.item(iid, tags=("hilite",))
            self.root.after(ANIMATION_DELAY_MS, lambda: step(k+1))
        step(0)

    def run(self):
        self.root.mainloop()


# --------------------------- Entry Point --------------------------------- #

def load_or_scrape() -> List[Poem]:
    poems = load_existing_data(OUTPUT_JSON)
    if poems:
        print(f"[INFO] {len(poems)} poèmes chargés depuis {OUTPUT_JSON}")
        # nettoyage préventif : si des auteurs se trouvent dans categories, on ne peut
        # pas les distinguer ici sans les URLs ; on laisse tel quel. Un re‑scrape fera le tri.
        return poems
    # Sinon on scrape tout
    poems = scrape_all()
    if not poems:
        messagebox.showerror("Erreur", "Impossible de récupérer des données depuis poetica.fr")
    return poems


def main():
    poems = load_or_scrape()
    if not poems:
        return
    app = PoeticaApp(poems)
    app.run()


if __name__ == "__main__":
    main()

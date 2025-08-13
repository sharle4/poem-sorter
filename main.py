from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
import os

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

SCRAPE_ALL_AUTHORS = True
# Pour tester rapidement, fixez un plafond (None pour illimité)
MAX_AUTHORS = None  # ex: 5
MAX_POEMS_PER_AUTHOR = None  # ex: 50

# ------------------------------- Data Model ------------------------------ #
@dataclass
class Poem:
    title: str
    url: str
    comments: int
    author: str
    categories: List[str]

# ---------------------------- Utility Functions -------------------------- #

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
    """Retourne (auteurs, categories) à partir des menus latéraux.
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
    # Plusieurs sites WordPress affichent le lien vers les commentaires ainsi:
    # <span class="comments-link"><a ...>12 commentaires</a></span>
    # On tente plusieurs approches robustes.
    text = " ".join(article.stripped_strings)
    m = re.search(r"(\d+)\s*commentaire", text, flags=re.I)
    if m:
        return int(m.group(1))
    # fallback: chercher un élément spécifique
    cl = article.select_one("span.comments-link")
    if cl:
        m = re.search(r"(\d+)", cl.get_text(" ", strip=True))
        if m:
            return int(m.group(1))
    return 0


def extract_poems_from_listing(listing_url: str) -> List[Dict]:
    """Extrait les poèmes (titre, url, nb commentaires) depuis une page de liste."""
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
    # Essayer rel="next"
    link = listing_soup.select_one("a[rel=next]")
    if link and link.get("href"):
        return link["href"]
    # Essayer .next.page-numbers
    link = listing_soup.select_one("a.next.page-numbers")
    if link and link.get("href"):
        return link["href"]
    return None


def iterate_all_listing_pages(first_url: str):
    """Générateur: pour une URL de catégorie (auteur ou thème), itère toutes les pages."""
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
    """Récupère tous les poèmes d'un auteur via pagination."""
    collected: List[Dict] = []
    for page_url, soup in iterate_all_listing_pages(author_url):
        # Extraire depuis cette page
        for poem in extract_poems_from_listing(page_url):
            poem["author"] = author_name
            collected.append(poem)
            if max_poems and len(collected) >= max_poems:
                return collected
        # Pause politesse assumée dans iterate_all_listing_pages
    return collected


def fetch_poem_categories(poem_url: str) -> List[str]:
    soup = get_soup(poem_url)
    if soup is None:
        return []
    cats: List[str] = []
    # Plusieurs sélecteurs possibles selon le thème WP
    selectors = [
        ".cat-links a",
        ".entry-footer .cat-links a",
        ".posted-in a[rel=category]",
        "a[rel=category]",
        ".entry-meta a[rel=category]",
    ]
    for sel in selectors:
        for a in soup.select(sel):
            txt = a.get_text(strip=True)
            if txt and txt not in cats:
                cats.append(txt)
        if cats:
            break
    return cats


def load_existing_data(path: str = OUTPUT_JSON) -> List[Poem]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return [Poem(**p) for p in raw]
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
    authors, site_categories = extract_menus(BASE_URL)
    if not authors:
        print("[ERROR] Aucune entrée trouvée dans le menu des auteurs. Vérifiez la page.")
        return []

    if MAX_AUTHORS is not None:
        authors = authors[: MAX_AUTHORS]

    poems: List[Poem] = []
    seen_poem_urls: set[str] = set()

    count_auth = 0
    for author in authors:
        count_auth += 1
        name = author["name"]
        url = author["url"]
        print(f"[INFO] Auteur {count_auth}/{len(authors)} : {name}")
        time.sleep(DELAY_BETWEEN_REQUESTS)

        # 1) Liste des poèmes (titre, url, commentaires)
        listing_poems = fetch_poems_for_author(name, url, max_poems=MAX_POEMS_PER_AUTHOR)
        print(f"    - {len(listing_poems)} poème(s) listé(s)")

        # 2) Pour chaque poème: compléter les catégories (requête page du poème)
        for i, p in enumerate(listing_poems, 1):
            p_url = p["url"]
            if p_url in seen_poem_urls:
                continue
            seen_poem_urls.add(p_url)

            cats = fetch_poem_categories(p_url)
            poem = Poem(
                title=p["title"],
                url=p_url,
                comments=int(p.get("comments", 0)),
                author=name,
                categories=cats,
            )
            poems.append(poem)

            if len(poems) % CACHE_INTERMEDIATE_EVERY == 0:
                print(f"[INFO] Sauvegarde intermédiaire ({len(poems)} poèmes)…")
                save_data(poems, OUTPUT_JSON)
            time.sleep(DELAY_BETWEEN_REQUESTS)

    # Tri final (optionnel) – par commentaires décroissant
    poems.sort(key=lambda x: x.comments, reverse=True)
    save_data(poems, OUTPUT_JSON)
    print(f"[INFO] Terminé. {len(poems)} poèmes enregistrés dans {OUTPUT_JSON}")
    return poems


# ------------------------------- GUI ------------------------------------- #

class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)

        self.scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        window = canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")


class PoeticaApp:
    def __init__(self, poems: List[Poem]):
        self.poems = poems
        self.root = tk.Tk()
        self.root.title("Poetica – Tri par commentaires (Auteur & Catégories)")
        self.root.geometry("1100x700")

        self._build_widgets()
        self._populate()

    def _build_widgets(self):
        # Top controls
        top = ttk.Frame(self.root)
        top.pack(side="top", fill="x", padx=10, pady=10)

        ttk.Label(top, text="Auteur:").pack(side="left")
        self.author_var = tk.StringVar(value="Tous les auteurs")
        authors_sorted = sorted({p.author for p in self.poems})
        self.author_combo = ttk.Combobox(
            top,
            values=["Tous les auteurs"] + authors_sorted,
            textvariable=self.author_var,
            state="readonly",
            width=40,
        )
        self.author_combo.pack(side="left", padx=8)
        self.author_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_category_panel())

        # Categories panel (multi-select as checkbuttons)
        right = ttk.Frame(self.root)
        right.pack(side="right", fill="y", padx=10, pady=10)

        ttk.Label(right, text="Catégories (filtre):").pack(anchor="w")
        self.cat_panel = ScrollableFrame(right)
        self.cat_panel.pack(fill="y", expand=False)
        self.category_vars: Dict[str, tk.BooleanVar] = {}

        # Middle: search
        mid = ttk.Frame(self.root)
        mid.pack(side="top", fill="x", padx=10)
        ttk.Label(mid, text="Recherche titre contient:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(mid, textvariable=self.search_var, width=40)
        entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda e: self.refresh_table())

        # Buttons
        btns = ttk.Frame(self.root)
        btns.pack(side="top", fill="x", padx=10, pady=5)
        ttk.Button(btns, text="Tout décocher catégories", command=self._uncheck_all).pack(side="left")
        ttk.Button(btns, text="Tout cocher catégories", command=self._check_all).pack(side="left", padx=6)
        ttk.Button(btns, text="Réinitialiser filtres", command=self._reset_filters).pack(side="left", padx=6)
        ttk.Button(btns, text="Ouvrir sélection", command=self._open_selected).pack(side="left", padx=6)

        # Table
        table_frame = ttk.Frame(self.root)
        table_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        cols = ("comments", "title", "author", "categories", "url")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings")
        self.tree.heading("comments", text="Commentaires")
        self.tree.heading("title", text="Titre")
        self.tree.heading("author", text="Auteur")
        self.tree.heading("categories", text="Catégories")
        self.tree.heading("url", text="URL")
        self.tree.column("comments", width=120, anchor="center")
        self.tree.column("title", width=380)
        self.tree.column("author", width=180)
        self.tree.column("categories", width=280)
        self.tree.column("url", width=0, stretch=False)  # caché mais stocké

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._on_double_click)

        # Status bar
        self.status = tk.StringVar(value="Prêt.")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w").pack(
            side="bottom", fill="x"
        )

        self._refresh_category_panel()

    def _populate(self):
        self.refresh_table()

    def _get_active_author(self) -> Optional[str]:
        a = self.author_var.get()
        return None if a == "Tous les auteurs" else a

    def _active_categories(self) -> List[str]:
        return [name for name, var in self.category_vars.items() if var.get()]

    def _refresh_category_panel(self):
        # Nettoyer
        for child in list(self.cat_panel.scrollable_frame.children.values()):
            child.destroy()
        self.category_vars.clear()

        author = self._get_active_author()
        # Catégories disponibles selon l'auteur sélectionné
        cats = sorted({c for p in self.poems if (author is None or p.author == author) for c in p.categories})
        for c in cats:
            var = tk.BooleanVar(value=False)
            chk = ttk.Checkbutton(self.cat_panel.scrollable_frame, text=c, variable=var, command=self.refresh_table)
            chk.pack(anchor="w")
            self.category_vars[c] = var

        self.refresh_table()

    def _uncheck_all(self):
        for var in self.category_vars.values():
            var.set(False)
        self.refresh_table()

    def _check_all(self):
        for var in self.category_vars.values():
            var.set(True)
        self.refresh_table()

    def _reset_filters(self):
        self.author_var.set("Tous les auteurs")
        self._refresh_category_panel()
        self.search_var.set("")
        self.refresh_table()

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

    def refresh_table(self):
        # Filtrage
        author = self._get_active_author()
        active_cats = self._active_categories()
        q = self.search_var.get().strip().lower()

        def _match(poem: Poem) -> bool:
            if author is not None and poem.author != author:
                return False
            if active_cats:
                if not any(c in poem.categories for c in active_cats):
                    return False
            if q and q not in poem.title.lower():
                return False
            return True

        filtered = [p for p in self.poems if _match(p)]
        filtered.sort(key=lambda x: x.comments, reverse=True)

        # Table
        for row in self.tree.get_children():
            self.tree.delete(row)
        for p in filtered:
            cats = ", ".join(p.categories)
            self.tree.insert("", "end", values=(p.comments, p.title, p.author, cats, p.url))
        self.status.set(f"{len(filtered)} poème(s) affiché(s) – Auteur: {author or 'Tous'} – Catégories actives: {len(active_cats)}")

    def run(self):
        self.root.mainloop()


# --------------------------- Entry Point --------------------------------- #

def main():
    # 1) Charger les données existantes si présentes
    poems = load_existing_data(OUTPUT_JSON)
    if poems:
        print(f"[INFO] {len(poems)} poèmes chargés depuis {OUTPUT_JSON}")
    else:
        # 2) Sinon scraper tout le site (auteurs -> poèmes -> catégories)
        poems = scrape_all()
        if not poems:
            messagebox.showerror("Erreur", "Impossible de récupérer des données depuis poetica.fr")
            return

    # 3) Lancer l'interface
    app = PoeticaApp(poems)
    app.run()


if __name__ == "__main__":
    main()

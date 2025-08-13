import requests
from bs4 import BeautifulSoup
import json
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

BASE_URL = "https://www.poetica.fr"

def fetch_poems_by_author(author_url):
    resp = requests.get(author_url)
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for article in soup.select("article.post"):
        title_tag = article.select_one("h2.entry-title a")
        if not title_tag:
            continue
        title = title_tag.text.strip()
        url = title_tag['href']
        comments_tag = article.select_one("span.comments-link")
        if comments_tag:
            import re
            m = re.search(r"(\d+)", comments_tag.text)
            count = int(m.group(1)) if m else 0
        else:
            count = 0
        results.append({
            "title": title,
            "url": url,
            "comments": count
        })
    return results


def fetch_poems_by_author_name(author_slug):
    author_url = f"{BASE_URL}/categories/{author_slug}/"
    return fetch_poems_by_author(author_url)


def save_data(data, fname="poems.json"):
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_data(fname="poems.json"):
    try:
        with open(fname, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def build_gui(data):
    root = tk.Tk()
    root.title("Poèmes – Par popularité")

    authors = sorted(set(item["author"] for item in data))
    author_var = tk.StringVar(value="-- Tous les auteurs --")
    combo = ttk.Combobox(root, textvariable=author_var, values=["-- Tous les auteurs --"] + authors, state="readonly")
    combo.pack(fill='x', padx=10, pady=5)

    tree = ttk.Treeview(root, columns=("comments", "url"), show="headings")
    tree.heading("comments", text="Commentaires")
    tree.heading("url", text="URL")
    tree.pack(fill='both', expand=True, padx=10, pady=5)

    def refresh():
        sel = author_var.get()
        filtered = [item for item in data if sel == "-- Tous les auteurs --" or item["author"] == sel]
        sorted_list = sorted(filtered, key=lambda x: x["comments"], reverse=True)
        tree.delete(*tree.get_children())
        for item in sorted_list:
            tree.insert("", "end", values=(item["comments"], item["title"]), tags=(item["url"],))
        tree.tag_bind("", "<Double-1>", on_double_click)

    def on_double_click(event):
        item_id = tree.focus()
        if item_id:
            url = tree.item(item_id, "tags")[0]
            webbrowser.open(url)

    combo.bind("<<ComboboxSelected>>", lambda e: refresh())
    refresh()
    root.mainloop()


def main():
    auteurs = ["victor-hugo", "arthur-rimbaud", "pierre-de-ronsard"]
    all_data = []
    for auth in auteurs:
        poems = fetch_poems_by_author_name(auth)
        print(len(poems), "poems found for", auth)
        for p in poems:
            p["author"] = auth.replace("-", " ").title()
            all_data.append(p)
    save_data(all_data)
    data = load_data()
    build_gui(data)


if __name__ == "__main__":
    main()
import json
import time
import requests
from bs4 import BeautifulSoup

# -------------------
# Configuration
# -------------------
INPUT_FILE = "poetica_poems.json"   # fichier JSON d'origine
OUTPUT_FILE = "poetica_poems_with_content.json"  # fichier de sortie
DELAY_BETWEEN_REQUESTS = 0.1  # en secondes (politesse avec le site)

# -------------------
# Fonction pour extraire le texte du poème
# -------------------
def extract_poem_text(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"❌ Erreur en récupérant {url} : {e}")
        return ""

    soup = BeautifulSoup(r.text, "html.parser")

    # Sur poetica.fr, le contenu du poème est généralement dans un <div class="entry-content">
    entry_content = soup.find("div", class_="entry-content")
    if not entry_content:
        print(f"⚠️ Pas trouvé de contenu principal pour {url}")
        return ""

    # On prend tous les paragraphes après le marqueur <!--pstart -->
    text_parts = []
    capture = False
    for elem in entry_content.find_all(["p", "br"]):
        if elem.name == "p" and "<!--pstart" in str(elem):
            capture = True
            continue
        if capture:
            if elem.name == "p":
                # Remplace <br> par des sauts de ligne
                paragraph = elem.get_text(separator="\n", strip=True)
                if paragraph:
                    text_parts.append(paragraph)

    # Si aucun marqueur trouvé, on prend tout le texte
    if not text_parts:
        text_parts.append(entry_content.get_text(separator="\n", strip=True))

    return "\n".join(text_parts).strip()

# -------------------
# Traitement
# -------------------
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    poems = json.load(f)

updated_poems = []
for i, poem in enumerate(poems, start=1):
    print(f"[{i}/{len(poems)}] Récupération du contenu pour : {poem['title']}")
    content = extract_poem_text(poem["url"])
    poem["content"] = content
    updated_poems.append(poem)
    time.sleep(DELAY_BETWEEN_REQUESTS)

# -------------------
# Sauvegarde
# -------------------
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(updated_poems, f, ensure_ascii=False, indent=2)

print(f"\n✅ Fichier enrichi sauvegardé dans {OUTPUT_FILE}")

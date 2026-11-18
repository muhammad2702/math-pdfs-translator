"""Save links from the Tournament index and the linked yearly pages."""

from hashlib import sha256
from pathlib import Path
import sqlite3
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


index_url = "https://www.turgor.ru/en/problems/allproblems.php"
data_dir = Path("data")
raw_dir = data_dir / "raw"
raw_dir.mkdir(parents=True, exist_ok=True)

with sqlite3.connect(data_dir / "archive.sqlite") as db:
    db.execute("""CREATE TABLE IF NOT EXISTS source (
        id INTEGER PRIMARY KEY,
        href TEXT NOT NULL,
        locator TEXT NOT NULL DEFAULT '',
        filepath TEXT,
        UNIQUE(href, locator)
    )""")

    pages = [index_url]
    seen_pages = set()
    seen_files = set()

    while pages:
        page_url = pages.pop(0)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        page_path = raw_dir / (sha256(page_url.encode()).hexdigest() + ".html")
        if page_path.exists():
            html = page_path.read_bytes()
        else:
            with urlopen(Request(page_url, headers={"User-Agent": "math-pdfs-translator/1.0"}), timeout=30) as response:
                html = response.read()
            page_path.write_bytes(html)
        db.execute("INSERT OR IGNORE INTO source(href,locator,filepath) VALUES(?, '', ?)",
                   (page_url, page_path.as_posix()))
        db.execute("UPDATE source SET filepath=? WHERE href=?", (page_path.as_posix(), page_url))
        db.commit()

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("a", href=True):
            target = urljoin(page_url, tag["href"].strip())
            href, locator = urldefrag(target)
            suffix = urlsplit(href).path.lower()
            same_host = urlsplit(href).netloc.lower() == urlsplit(index_url).netloc.lower()
            filepath = None
            if same_host and suffix.endswith(".pdf"):
                pdf_path = raw_dir / (sha256(href.encode()).hexdigest() + ".pdf")
                if href not in seen_files:
                    seen_files.add(href)
                    if not pdf_path.exists():
                        try:
                            with urlopen(Request(href, headers={"User-Agent": "math-pdfs-translator/1.0"}), timeout=30) as response:
                                pdf = response.read()
                            if not pdf.lstrip().startswith(b"%PDF-"):
                                raise ValueError("The response is not a PDF")
                            pdf_path.write_bytes(pdf)
                        except (OSError, ValueError) as error:
                            print(f"PDF download failed: {href}: {error}")
                if pdf_path.exists():
                    filepath = pdf_path.as_posix()
            elif same_host and suffix.endswith("/index.php"):
                pages.append(href)
                filepath = (raw_dir / (sha256(href.encode()).hexdigest() + ".html")).as_posix()
            db.execute("INSERT OR IGNORE INTO source(href,locator,filepath) VALUES(?,?,?)",
                       (href, locator, filepath))
            if filepath:
                db.execute("UPDATE source SET filepath=? WHERE href=? AND locator=?",
                           (filepath, href, locator))
        db.commit()

    count = db.execute("SELECT count(*) FROM source").fetchone()[0]
    print(f"Saved {len(seen_pages)} webpages and {len(seen_files)} PDF links; {count} archive references in SQLite")

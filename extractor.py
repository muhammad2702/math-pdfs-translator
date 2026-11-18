"""Read saved sources, ask the model for English problems, and make checked PDFs."""

import base64
from collections import Counter
from hashlib import sha256
import json  # OpenRouter uses JSON in HTTP; no JSON files are written.
import os
from pathlib import Path
import re
import sqlite3
import textwrap
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, UnicodeDammit
from lingua import Language, LanguageDetectorBuilder
import pymupdf


database = Path("data/archive.sqlite")
if not database.exists():
    raise SystemExit("Run archive.py first")

key = os.environ.get("OPENROUTER_API_KEY", "").strip()
max_calls = int(os.environ.get("MAX_NEW_CALLS", "20"))
model = "google/gemini-2.5-flash"
prompt = Path("prompts/extract_translate.txt").read_text(encoding="utf-8")
detector = LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.RUSSIAN).build()
font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
calls = 0

with sqlite3.connect(database) as db:
    db.row_factory = sqlite3.Row
    columns = {row[1] for row in db.execute("PRAGMA table_info(source)")}
    for name, kind in (
        ("record_type", "TEXT"), ("status", "TEXT"), ("language", "TEXT"),
        ("source_hash", "TEXT"), ("age_group", "TEXT"), ("difficulty", "TEXT"),
        ("set_date", "TEXT"), ("question_number", "INTEGER"), ("points", "TEXT"),
        ("author", "TEXT"), ("source_text", "TEXT"), ("english", "TEXT"),
        ("output_filepath", "TEXT"), ("problem_count", "INTEGER"),
        ("model_name", "TEXT"), ("model_calls", "INTEGER"), ("model_spend", "REAL"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE source ADD COLUMN {name} {kind}")
    db.commit()

    pdfs = db.execute("""SELECT href,filepath FROM source WHERE filepath LIKE '%.pdf'
        AND locator='' GROUP BY href""").fetchall()
    for pdf in pdfs:
        if not Path(pdf["filepath"]).is_file():
            continue
        with pymupdf.open(pdf["filepath"]) as document:
            for page in document:
                db.execute("""INSERT OR IGNORE INTO source(href,locator,filepath,record_type)
                    VALUES(?,?,?,'pdf_page')""",
                    (pdf["href"], f"p{page.number + 1}", pdf["filepath"]))
    db.commit()

    units = db.execute("""SELECT * FROM source WHERE filepath IS NOT NULL
        AND (record_type='pdf_page' OR (filepath LIKE '%.html' AND locator!=''))
        AND (record_type IS NULL OR record_type!='question')
        ORDER BY CASE WHEN href LIKE '%/31/%' THEN 0 ELSE 1 END, href, locator""").fetchall()

    for unit in units:
        if unit["status"] in ("translated", "model_sent", "needs_review", "outside_scope"):
            continue
        if unit["record_type"] == "pdf_page" and re.search(
            r"(?:^|[/_-])(?:sol|solution|resh)[\w-]*\.pdf$", unit["href"], re.I
        ):
            db.execute("UPDATE source SET status='outside_scope' WHERE id=?", (unit["id"],))
            continue
        if not Path(unit["filepath"]).is_file():
            db.execute("UPDATE source SET status='missing_file' WHERE id=?", (unit["id"],))
            continue
        source_hash = sha256(Path(unit["filepath"]).read_bytes()).hexdigest()
        picture = None
        if unit["record_type"] == "pdf_page":
            page_number = int(unit["locator"][1:]) - 1
            with pymupdf.open(unit["filepath"]) as document:
                page = document[page_number]
                source = page.get_text(sort=True)
                has_graphics = bool(page.get_images() or any(
                    drawing["rect"].width > 20 and drawing["rect"].height > 20
                    for drawing in page.get_drawings()))
                picture = base64.b64encode(page.get_pixmap(dpi=110).tobytes("png")).decode()
        else:
            html = UnicodeDammit(Path(unit["filepath"]).read_bytes()).unicode_markup
            marker = re.search(r'<a\b[^>]*\b(?:name|id)\s*=\s*["\']?' +
                               re.escape(unit["locator"]) + r'["\'\s>]', html, re.I)
            if marker is None:
                db.execute("UPDATE source SET status='needs_review' WHERE id=?", (unit["id"],))
                continue
            later = []
            for other in db.execute("SELECT locator FROM source WHERE href=? AND locator!=''", (unit["href"],)):
                following = re.search(r'<a\b[^>]*\b(?:name|id)\s*=\s*["\']?' +
                                      re.escape(other[0]) + r'["\'\s>]', html[marker.end():], re.I)
                if following:
                    later.append(marker.end() + following.start())
            fragment = html[marker.start():min(later) if later else len(html)]
            has_graphics = bool(BeautifulSoup(fragment, "html.parser").find("img"))
            source = BeautifulSoup(fragment, "html.parser").get_text("\n")

        lines = [line.strip() for line in source.splitlines() if line.strip()]
        prose = " ".join(lines)
        language = detector.detect_language_of(prose) if len(prose) >= 80 else None
        code = "en" if language == Language.ENGLISH else "ru" if language == Language.RUSSIAN else "unknown"
        letters = [char for char in prose if char.isalpha()]
        if code == "en" and letters and sum(ord(char) > 127 for char in letters) / len(letters) > 0.10:
            code = "unknown"
        db.execute("UPDATE source SET source_hash=?,language=? WHERE id=?", (source_hash, code, unit["id"]))
        if code == "en":
            db.execute("UPDATE source SET status='skipped_english' WHERE id=?", (unit["id"],))
            db.commit()
            continue
        if code != "ru":
            db.execute("UPDATE source SET status='needs_review' WHERE id=?", (unit["id"],))
            db.commit()
            continue
        if not key or calls >= max_calls:
            db.commit()
            continue
        spent = db.execute("SELECT coalesce(sum(model_spend),0) FROM source").fetchone()[0]
        if spent >= 4.50:
            print("Local $4.50 model spending ceiling reached")
            break

        numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines))
        content = [{"type": "text", "text": prompt + "\n\nSOURCE URL: " + unit["href"] +
                    "\nLOCATOR: " + unit["locator"] + "\nSOURCE LINES:\n" + numbered}]
        if picture:
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + picture}})
        payload = {"model": model, "max_tokens": 5000,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "user", "content": content}]}
        request = Request("https://openrouter.ai/api/v1/chat/completions",
                          data=json.dumps(payload).encode(),
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        db.execute("UPDATE source SET status='model_sent',model_name=?,model_calls=1 WHERE id=?",
                   (model, unit["id"]))
        db.commit()
        calls += 1
        try:
            with urlopen(request, timeout=120) as response:
                result = json.load(response)
            answer = json.loads(result["choices"][0]["message"]["content"])
            cost = result.get("usage", {}).get("cost")
            db.execute("UPDATE source SET model_spend=? WHERE id=?", (cost, unit["id"]))
            if cost is None:
                print("OpenRouter returned no cost; spending must be reconciled before another call")
                db.commit()
                break
            groups = answer["sets"]
            if not groups:
                db.execute("UPDATE source SET status='outside_scope' WHERE id=?", (unit["id"],))
                db.commit()
                continue
            pending = []
            for group in groups:
                age = group["age_group"]
                difficulty = group["difficulty"]
                date = group["date"]
                questions = group["questions"]
                if age not in ("junior", "senior") or difficulty not in ("basic", "advanced"):
                    raise ValueError("Class or variant is not established by the source")
                if difficulty == "basic" and not re.search(r"базов|\bbasic\b", prose, re.I):
                    raise ValueError("Basic is not stated in the source")
                if difficulty == "advanced" and not re.search(r"сложн|\badvanced\b", prose, re.I):
                    raise ValueError("Advanced is not stated in the source")
                if age == "junior" and not re.search(r"8\s*[-–—]\s*9|\bjunior\b", prose, re.I):
                    raise ValueError("Junior is not stated in the source")
                if age == "senior" and not re.search(r"10\s*[-–—]\s*11|\bsenior\b", prose, re.I):
                    raise ValueError("Senior is not stated in the source")
                if not re.fullmatch(r"\d{4}-\d\d-\d\d", str(date)) or not group["complete"]:
                    raise ValueError("Date or complete set boundary is uncertain")
                if not questions or [q["number"] for q in questions] != list(range(1, len(questions) + 1)):
                    raise ValueError("Question numbers are incomplete or out of order")
                for question in questions:
                    first, last = question["first_line"], question["last_line"]
                    if not (isinstance(first, int) and isinstance(last, int) and 0 <= first <= last < len(lines)):
                        raise ValueError("Question line range is outside the source")
                    original = "\n".join(lines[first:last + 1])
                    points = str(question["points"])
                    author = str(question["author"])
                    english = str(question["english"]).strip()
                    if not english or not points or not author or author not in original:
                        raise ValueError("Problem text, score or author is missing")
                    source_numbers = Counter(re.findall(r"\d+", original))
                    english_numbers = Counter(re.findall(r"\d+", english))
                    if any(english_numbers[number] > source_numbers[number] for number in english_numbers):
                        raise ValueError("The English introduces a number absent from the source")
                    pending.append((unit["href"], unit["locator"] + f":{age}:{difficulty}:q{question['number']}",
                                    unit["filepath"], "question", "review_passed", code, source_hash,
                                    age, difficulty, date, question["number"], points, author, original,
                                    english, f"output/{age}/{difficulty}/{date}.pdf"))
            if has_graphics:
                raise ValueError("Figure found; its placement in the English PDF needs review")
            for entry in pending:
                db.execute("""INSERT OR IGNORE INTO source
                    (href,locator,filepath,record_type,status,language,source_hash,age_group,
                     difficulty,set_date,question_number,points,author,source_text,english,output_filepath)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", entry)
            db.execute("UPDATE source SET status='translated',problem_count=? WHERE id=?",
                       (len(pending), unit["id"]))
            db.commit()
            print(f"Translated {unit['href']}#{unit['locator']}: {len(pending)} questions, ${cost:.6f}")
        except (ValueError, KeyError, TypeError, OSError) as error:
            db.execute("UPDATE source SET status='needs_review' WHERE id=?", (unit["id"],))
            db.commit()
            print(f"Needs review: {unit['href']}#{unit['locator']}: {error}")

    outputs = db.execute("""SELECT output_filepath FROM source WHERE record_type='question'
        GROUP BY output_filepath ORDER BY output_filepath""").fetchall()
    for (destination,) in outputs:
        questions = db.execute("""SELECT * FROM source WHERE record_type='question'
            AND output_filepath=? ORDER BY question_number""", (destination,)).fetchall()
        if not questions or any(q["status"] not in ("review_passed", "verified") for q in questions):
            continue
        if [q["question_number"] for q in questions] != list(range(1, len(questions) + 1)):
            print(f"Needs review: incomplete output {destination}")
            continue
        path = Path(destination)
        if path.is_file() and all(q["status"] == "verified" for q in questions):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf = pymupdf.open()
        for question in questions:
            page = pdf.new_page(width=595, height=842)
            page.insert_font(fontname="dejavu", fontfile=font)
            page.insert_text((40, 50), "Tournament of Towns", fontname="dejavu", fontsize=16)
            page.insert_text((40, 75), f"{question['age_group'].title()} · {question['difficulty'].title()} · {question['set_date']}",
                             fontname="dejavu", fontsize=11)
            page.insert_text((40, 110), f"Problem {question['question_number']} ({question['points']} points)",
                             fontname="dejavu", fontsize=12)
            y = 145
            for paragraph in question["english"].splitlines():
                for line in textwrap.wrap(paragraph, width=82) or [""]:
                    if y > 765:
                        page = pdf.new_page(width=595, height=842)
                        page.insert_font(fontname="dejavu", fontfile=font)
                        y = 50
                    page.insert_text((40, y), line, fontname="dejavu", fontsize=11)
                    y += 17
            page.insert_text((40, min(y + 25, 810)), question["author"], fontname="dejavu", fontsize=10)
        pdf.save(path)
        pdf.close()
        with pymupdf.open(path) as check:
            if check.page_count < len(questions):
                raise ValueError(f"PDF page count too small: {path}")
        db.execute("UPDATE source SET status='verified' WHERE record_type='question' AND output_filepath=?",
                   (destination,))
        db.commit()
        print(f"Saved {path}")

    print(f"New model calls: {calls}; recorded spend: ${db.execute('SELECT coalesce(sum(model_spend),0) FROM source').fetchone()[0]:.6f}")

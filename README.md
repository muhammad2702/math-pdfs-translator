# Tournament of Towns PDF translator

This repository has two runnable scripts. Both are straight-line Python; neither
defines custom functions or uses a command-line interface.

1. Install packages: `python -m pip install -r requirements.txt`.
2. Run `python archive.py`. It reads the [official archive index](https://www.turgor.ru/en/problems/allproblems.php), follows yearly index pages, stores their links and locators in one SQLite table, and saves the linked webpages and PDFs in `data/raw/`.
3. Set `OPENROUTER_API_KEY` in your environment and run `python extractor.py`.
   `MAX_NEW_CALLS` limits new requests in that run (default 20; set it to `0` to inspect the archive without paying). The extractor detects language, reads HTML sections and individual PDF pages, sends Russian problem units to OpenRouter, checks the returned boundaries and fields, then writes eligible English PDFs to `output/`.

For GitHub Actions, add a repository secret named `OPENROUTER_API_KEY` with the
OpenRouter key as its value. The manual workflow defaults to zero paid calls
and only the four required 2009 units. Enter a call limit explicitly for a
live run. The workflow uploads the SQLite database, source files, and outputs
as an artifact and caches them for the next run, so a new runner resumes from
the saved database. Keep the artifact: GitHub caches can expire.

The one table is `data/archive.sqlite` → `source`. `archive.py` creates only
`id`, `href`, `locator`, and `filepath`. `extractor.py` adds columns for source
hash, language, classification, problem count, status, question text, English,
output path, model name, call count, and reported spend. Question rows use the
same table. No local JSON file or manifest is written; the SQLite table is the
inventory and resume record. OpenRouter's HTTP messages use JSON.

The source labels determine output folders: 8–9 classes → `junior`, 10–11 →
`senior`, `базовый вариант` → `basic`, and `сложный вариант` → `advanced`.
`тренировочный` and `основной` are not silently mapped to Basic or Advanced.
The four assignment examples are the Junior and Senior Basic sets dated
2009-10-18 and the Junior and Senior Advanced sets dated 2009-10-25.

Inspect progress with SQLite, for example:

```sql
SELECT status, count(*) FROM source GROUP BY status;
SELECT href, locator, status, problem_count, model_spend
FROM source WHERE status IN ('needs_review', 'model_sent');
SELECT output_filepath, count(*) FROM source
WHERE record_type='question' GROUP BY output_filepath;
```

`model_sent` records an uncertain request before contacting OpenRouter, so a
restart will not blindly pay for it again. Actual per-call cost is saved when
OpenRouter reports it. The script stops at $4.50 locally; the supplied key's
$5 hard cap remains the final spending limit. Missing cost data stops further
calls. Set `MAX_NEW_CALLS=0` for a free inspection run. Use a restricted key
for the new repository; GitHub secrets from another repository are not shared.

Sources with ambiguous labels, incomplete sets, unreadable text, or figures
needing placement are marked `needs_review`. Those sources cannot produce an
automatically accepted PDF. Generated PDFs are opened again and page counted;
their rows become `rendered`, which means a file exists and passed technical
checks. It does not mean the translation has had independent semantic or visual
review. That inspection is still required for final delivery.

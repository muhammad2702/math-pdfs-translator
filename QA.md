# Current run check

- `python archive.py` completed against the official archive on 18 November 2026:
  41 webpages saved, 73 PDF links found, 429 references recorded.
- `MAX_NEW_CALLS=0 python extractor.py` completed with zero model calls and
  zero reported spend. This checked the local extraction and language-routing
  path; no English output was generated.
- The 2009 Basic sources are HTML sections `turnir31otm` and `turnir31ots` in
  the 31st tournament webpage. The Advanced source is `os31sl.pdf`, whose
  first two pages cover the two class groups.
- Several historical PDFs expose garbled text through PyMuPDF. They require
  review rather than an English-language skip. Some historical webpages also
  contain broken or repeated section anchors. These remain unresolved.
- English control: `fall-37-A-eng-auth.pdf` pages 1 and 2 were marked
  `skipped_english`. Both have no model-call record and no output PDF path.
- The four required English PDFs have **not** been generated or visually
  inspected in this fresh repository. A paid model run and page-by-page review
  remain necessary before claiming assignment completion.

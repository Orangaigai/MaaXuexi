"""Extract article text, keeping exact overlapping OCR lines only once."""
import re


def article_page(records, *, first=False):
    rows = sorted(records, key=lambda row:(row['box'][1], row['box'][0]))
    reads = [r for r in rows if re.fullmatch(r'阅读\s*[\d,，]+', r['text'].strip())]
    likes = [r for r in rows if re.fullmatch(r'点赞\s*[\d,，]+', r['text'].strip())]
    end = any(abs(read['box'][1]-like['box'][1]) < 45 for read in reads for like in likes)
    footers = [r['box'][1] for r in rows if re.match(r'^(?:作者|审核|责任编辑|责编)\s*[:：]', r['text'].strip())]
    bottom = min(footers + [r['box'][1] for r in reads+likes]) if end else 1190
    sources = [r for r in rows if re.match(r'^来源\s*[:：]', r['text'].strip())]
    top = max((r['box'][1]+r['box'][3] for r in sources), default=120) if first else 120
    lines = [r['text'].strip() for r in rows if top < r['box'][1] < bottom
             and 15 <= r['box'][0] < 700 and r['text'].strip()]
    return lines, end


def append_page(previous, lines, prior_lines):
    # Do not fuzzy-match knowledge, numbers or negations across OCR frames.
    overlapping = {line for line in prior_lines if len(line) >= 8}
    return previous + [line for line in lines if line not in overlapping]

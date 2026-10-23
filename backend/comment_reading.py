"""Extract article text, keeping exact overlapping OCR lines only once."""
import re
import numpy as np


def article_page(records, *, first=False, body_closed=False):
    rows = sorted(records, key=lambda row:(row['box'][1], row['box'][0]))
    reads = [r for r in rows if re.fullmatch(r'阅读\s*[\d,，]+', r['text'].strip())]
    likes = [r for r in rows if re.fullmatch(r'点赞\s*[\d,，]+', r['text'].strip())]
    end = any(abs(read['box'][1]-like['box'][1]) < 45 for read in reads for like in likes)
    footers = [r['box'][1] for r in rows if re.match(r'^(?:作者|审核|责任编辑|责编)\s*[:：]', r['text'].strip())]
    credits=[r['box'][1] for r in rows if re.match(r'^(?:责任编辑|责编|审核)\s*[:：]',r['text'].strip())]
    comments=[r['box'][1] for r in rows if r['text'].strip()=='观点'
              and r['box'][0]<100 and 120<r['box'][1]<1190
              and (body_closed or (credits and r['box'][1]>min(credits)+35))]
    end=end or bool(comments)
    boundaries=footers+[r['box'][1] for r in reads+likes]+comments
    bottom=min(boundaries) if boundaries else 1190
    sources = [r for r in rows if re.match(r'^来源\s*[:：]', r['text'].strip())]
    top = max((r['box'][1]+r['box'][3] for r in sources), default=120) if first else 120
    lines = [r['text'].strip() for r in rows if top < r['box'][1] < bottom
             and 15 <= r['box'][0] < 700 and r['text'].strip()]
    return lines, end


def append_page(previous, lines, prior_lines):
    # Do not fuzzy-match knowledge, numbers or negations across OCR frames.
    overlapping = {line for line in prior_lines if len(line) >= 8}
    return previous + [line for line in lines if line not in overlapping]


def title_line_text(text, box, image):
    """Correct OCR 一/一一 only where the same frame shows a long dash.

    Do not remove Chinese characters by spelling or fuzzy title similarity.
    The stroke must span more than one ordinary glyph, and align with the
    candidate characters in this OCR line. Missing image evidence keeps text.
    """
    if not isinstance(image,np.ndarray) or image.ndim!=3 or image.shape[2]<3:
        return text
    candidates=list(re.finditer(r'一{1,2}',text))
    if not candidates or not text:
        return text
    x,y,width,height=map(int,box)
    if x<0 or y<0 or width<=0 or height<=0 or x+width>image.shape[1] or y+height>image.shape[0]:
        return text
    mask=np.all(image[y:y+height,x:x+width,:3]<160,axis=2)
    minimum=max(36,round(height*1.25))
    strokes=[]
    for line in mask:
        changes=np.diff(np.r_[False,line,False].astype(np.int8))
        runs=list(zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)))
        spans=[(start,end) for start,end in runs if end-start>=minimum]
        # During font loading the same em dash can render as two short hyphens.
        # Both strokes must be shorter than ordinary Chinese 一 glyphs, nearly
        # adjacent, aligned and thin; do not join arbitrary character strokes.
        glyph=width/len(text)
        spans.extend((a,d) for (a,b),(c,d) in zip(runs,runs[1:])
                     if glyph*.4<=b-a<=glyph*.75 and glyph*.4<=d-c<=glyph*.75
                     and 0<=c-b<=glyph*.3 and d-a>=minimum)
        for start,end in spans:
            solid_rows=np.count_nonzero(np.mean(mask[:,start:end],axis=1)>.9)
            if solid_rows<=max(3,round(height*.15)):
                strokes.append((int(start),int(end)))
    glyph=width/len(text)
    replacements=[]
    for match in candidates:
        start,end=match.start()*glyph,match.end()*glyph
        if any(abs(left-start)<=glyph*.8 and abs(right-end)<=glyph*.8 for left,right in strokes):
            replacements.append((match.start(),match.end()))
    for start,end in reversed(replacements):
        text=text[:start]+'—'+text[end:]
    return text


def editor_footer(records):
    """A terminal editor credit closes body collection, not settlement checks."""
    return any(re.match(r'^(?:责任编辑|责编|审核)\s*[:：]',row['text'].strip())
               and 120<row['box'][1]<1190 for row in records)

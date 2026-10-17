"""Text requests only for a complete, visible daily-choice page and hint."""
import re
import numpy as np
from daily_hints import clean
from question_visual import visual_evidence


def _rows(records, top, bottom):
    return sorted((r for r in records if top < r['box'][1] < bottom
                   and r['text'].strip() and r['box'][3] >= 16),
                  key=lambda r: (r['box'][1], r['box'][0]))


def _bounded(rows, bottom):
    return bool(rows) and all(20 <= r['box'][0] and r['box'][0]+r['box'][2] <= 700
                             and r['box'][1]+r['box'][3] < bottom for r in rows)


def choice_page(records, image, options, question_type):
    """A visible heading, every label, footer and hint link bound a single page.

    Scrolled/partial pages keep the existing full-image path. Option content is
    rebuilt from full-frame OCR, not the old one-line option recognition crop.
    """
    if (question_type not in ('单选题', '多选题') or not isinstance(image, np.ndarray)
            or image.shape != (1280, 720, 3) or not 2 <= len(options) <= 6):
        return None
    letters = sorted(options)
    if letters != list('ABCDEF'[:len(letters)]):
        return None
    headings = [r for r in records if r['text'].strip() == question_type
                and 130 <= r['box'][1] <= 400]
    links = [r for r in records if r['text'].strip() == '查看提示']
    labels = [r for r in records if re.match(r'^[A-F](?:[.．、:：\s]|$)', r['text'].strip())
              and 30 <= r['box'][0] <= 160 and 300 <= r['box'][1] < 1130]
    labels.sort(key=lambda r: r['box'][1])
    if len(headings) != 1 or len(links) != 1 or [r['text'].strip()[0] for r in labels] != letters:
        return None
    footers = [r for r in records if re.match(r'^出题[：:]', r['text'].strip())
               and labels[-1]['box'][1] < r['box'][1] < links[0]['box'][1]]
    if len(footers) != 1 or links[0]['box'][1]+links[0]['box'][3] >= 1200:
        return None
    start = headings[0]['box'][1]+headings[0]['box'][3]
    sources = [r['box'][1] for r in records if start < r['box'][1] < labels[0]['box'][1]
               and re.match(r'^(来源|出题|推荐单位)[：:]', r['text'].strip())]
    stem_end = min(sources) if sources else labels[0]['box'][1]-10
    stem = _rows(records, start+10, stem_end)
    if not _bounded(stem, stem_end) or len(clean(''.join(r['text'] for r in stem))) < 5:
        return None
    question = '\n'.join(r['text'].strip() for r in stem)
    rebuilt, boxes = {}, []
    for index, label in enumerate(labels):
        letter = letters[index]
        box = options[letter][1]
        if len(box) != 4 or abs(box[1]-label['box'][1]) > 25:
            return None
        bottom = labels[index+1]['box'][1]-10 if index+1 < len(labels) else footers[0]['box'][1]-10
        rows = _rows(records, label['box'][1]-8, bottom)
        if not _bounded(rows, bottom):
            return None
        text = '\n'.join(r['text'].strip() for r in rows)
        text = re.sub(r'^[A-F][.．、:：\s]*', '', text).strip()
        if not text or not clean(text).startswith(clean(options[letter][0])):
            return None
        rebuilt[letter] = text
        boxes.append(dict(box=[label['box'][0], label['box'][1],
                               650-label['box'][0], bottom-label['box'][1]]))
    visual, _ = visual_evidence(image, records, boxes, question, strict=True)
    if visual:
        return None
    return dict(question_type=question_type, question=question, options=rebuilt)


def complete_hint(records, image):
    """Read the open bottom sheet, with visible margins and a sentence ending."""
    if not isinstance(image, np.ndarray) or image.shape != (1280, 720, 3):
        return ''
    headings = [r for r in records if r['text'].strip() == '提示' and 300 <= r['box'][1] <= 900]
    if len(headings) != 1:
        return ''
    top = headings[0]['box'][1]+headings[0]['box'][3]+25
    rows = _rows(records, top, 1240)
    if not _bounded(rows, 1150):
        return ''
    text = '\n'.join(r['text'].strip() for r in rows)
    if not 2 <= len(text) <= 1600 or not re.search(r'[。！？!?][”’）)\]」』]*$', text):
        return ''
    # Non-text evidence (including a picture in the hint) keeps the image path.
    fake_options = [dict(box=[35, top, 650, 1200-top])]
    hint_records = [r for r in records if r['box'][1] > top]
    # Exclude the dimmed underlying question above the hint sheet.
    hint_image = image.copy()
    hint_image[:int(top)] = 255
    visual, _ = visual_evidence(hint_image, hint_records, fake_options, text, strict=True)
    return '' if visual else text


def request_text(page, hint):
    if not page or not hint:
        return None
    return dict(**page, hint=hint)

"""Exact daily-hint matches; no fuzzy ratios, all-select or length-based guesses."""
import re
import unicodedata


def exact_hint_parts(red_texts, options):
    """Return the only full ordered segmentation, using each visible button once."""
    if not red_texts or not options or len(options)>12:return None
    def normalize_part(text):
        return re.sub(r'\s+','',unicodedata.normalize('NFKC',text)).strip('，,。；;：“”‘’"\'、')
    target=''.join(normalize_part(text) for text in red_texts if isinstance(text,str))
    parts=[(text,normalize_part(text)) for text in options]
    if (not target or len(target)>100 or any(not value for _,value in parts)
            or len({value for _,value in parts})!=len(parts)):
        return None
    solutions=[]
    def search(offset,used,path):
        if len(solutions)>1:return
        if offset==len(target):
            solutions.append(path);return
        for index,(text,value) in enumerate(parts):
            if index not in used and target.startswith(value,offset):
                search(offset+len(value),used|{index},path+[text])
    search(0,set(),[])
    return solutions[0] if len(solutions)==1 else None


def clean(text):
    # NFC preserves superscripts, units and operators. Only full-width ASCII
    # and Chinese sentence punctuation have explicit typographic equivalents.
    text=unicodedata.normalize('NFC',text or '')
    text=''.join(chr(ord(char)-0xfee0) if '\uff01'<=char<='\uff5e' else char for char in text)
    text=text.translate(str.maketrans({'，':',','。':'.','；':';','：':':','“':'"','”':'"','‘':"'",'’':"'"}))
    return re.sub(r'\s+','',text).rstrip('。.,;')


def question_from_records(records, options):
    headings = [row['box'] for row in records if row['text'].strip() in ('单选题','多选题')
                and 130 <= row['box'][1] <= 400]
    if len(headings) != 1 or not options:
        return ''
    start = headings[0][1] + headings[0][3]
    end = min(value[1][1] for value in options.values())
    sources = [row['box'][1] for row in records if re.match(r'^(来源|出题|推荐单位)[：:]',row['text'].strip())]
    if sources:
        end = min(end, min(sources)-10)
    rows = sorted((row for row in records if start+10 < row['box'][1] < end
                   and row['box'][3] >= 18),key=lambda row:(row['box'][1],row['box'][0]))
    return ''.join(row['text'] for row in rows)


def exact_hint_choice(question_type, options, red_texts, question=''):
    if question_type not in ('单选题','多选题') or not 2 <= len(options) <= 6:
        return None
    texts = {letter:clean(value[0]) for letter,value in options.items()}
    if any(not re.fullmatch('[A-F]',letter) or not text for letter,text in texts.items()):
        return None
    hints = [clean(text) for text in red_texts if isinstance(text,str) and clean(text)]
    if not hints:
        return None
    combined = ''.join(hints)
    if question_type == '单选题':
        matched = [letter for letter,text in texts.items() if text == combined]
        if len(matched) == 1:
            return matched
        # The observed true/false hint repeats the whole statement with “如果是”.
        # Accept exact equality after this one prefix; never infer from length.
        statement = clean(question)
        supported = combined[3:] if combined.startswith('如果是') else combined
        if len(texts) == 2 and set(texts.values()) == {'正确','错误'} and len(statement) >= 15 and statement == supported:
            return [letter for letter,text in texts.items() if text == '正确']
        return None
    selected = []
    for hint in hints:
        matched = [letter for letter,text in texts.items() if text == hint]
        if len(matched) != 1 or matched[0] in selected:
            return None
        selected.append(matched[0])
    return sorted(selected) if selected else None

"""Extract one completely visible input gap from the hint's original sentence."""
import re
from daily_hints import clean

MARKER='\u27e6blank\u27e7'


def blank_context(records, boxes):
    """Require a single adjacent run of character slots and complete question."""
    if not boxes or len(boxes)>30:
        return None
    boxes=sorted((list(box) for box in boxes),key=lambda box:(box[1],box[0]))
    if any(len(box)!=4 or box[2]<=0 or box[3]<=0 for box in boxes):return None
    first=boxes[0]
    ordered=sorted(boxes,key=lambda box:box[0])
    # Multiple independent input fields need their own entry/selection adapter.
    # Do not concatenate their answers into the old single-input submission.
    if any(abs(box[1]-first[1])>max(8,first[3]*.35) for box in boxes):return None
    if any(right[0]-left[0]-left[2]>max(12,min(left[2],right[2])*.7)
           for left,right in zip(ordered,ordered[1:])):return None
    gap=[ordered[0][0],min(box[1] for box in boxes),
         max(box[0]+box[2] for box in boxes)-ordered[0][0],max(box[3] for box in boxes)]
    headings=[r for r in records if r['text'].strip()=='填空题' and 130<=r['box'][1]<=430]
    ends=[r['box'][1] for r in records if re.match(r'^(来源|出题|推荐单位)[：:]',r['text'].strip())]
    if len(headings)!=1 or not ends:return None
    top=headings[0]['box'][1]+headings[0]['box'][3];bottom=min(ends)
    if not top<gap[1]<gap[1]+gap[3]<=bottom:return None
    tokens=[]
    for row in records:
        x,y,w,h=row['box'];text=row['text'].strip()
        if not (top+5<y<bottom and h>=16):continue
        overlaps=(x<gap[0]+gap[2] and x+w>gap[0] and y<gap[1]+gap[3] and y+h>gap[1])
        if overlaps:
            if re.fullmatch(r'[_□口（）()\s]+',text):continue
            # OCR merged both sides across an invisible gap: its exact offset
            # cannot be recovered from the bounding box alone.
            return None
        if text:tokens.append((text,[x,y,w,h]))
    tokens.append((MARKER,gap))
    lines=[]
    for text,box in sorted(tokens,key=lambda item:item[1][1]+item[1][3]/2):
        center=box[1]+box[3]/2
        if not lines or abs(center-lines[-1][0])>max(12,box[3]*.55):lines.append((center,[]))
        lines[-1][1].append((box[0],text))
    template=''.join(text for _,line in lines for _,text in sorted(line))
    if template.count(MARKER)!=1 or len(template)>1200:return None
    return dict(template=template,length=len(boxes))


def hint_paragraph(records):
    headings=[r for r in records if r['text'].strip()=='提示' and 300<=r['box'][1]<=900]
    if len(headings)!=1:return ''
    top=headings[0]['box'][1]+headings[0]['box'][3]+25
    rows=sorted((r for r in records if top<r['box'][1]<1230 and r['box'][3]>=16),
                key=lambda r:(r['box'][1],r['box'][0]))
    text=''.join(r['text'] for r in rows)
    return text if len(text)<=1600 else ''


def exact_blank_hint(context, hint):
    if not context or not isinstance(hint,str):return None
    template=context.get('template','');length=context.get('length',0)
    if template.count(MARKER)!=1 or type(length) is not int or not 1<=length<=30:return None
    left,right=(clean(part) for part in template.split(MARKER))
    if len(left)+len(right)<6:return None
    # One match of the complete surrounding sentence, exact slot length.
    # No shortest-fragment, substring-answer or fuzzy/semantic guesses.
    pattern=re.escape(left)+f'(.{{{length}}})'+re.escape(right)
    matches=list(re.finditer('(?='+pattern+')',clean(hint)))
    if len(matches)!=1:return None
    answer=matches[0].group(1)
    return answer if len(answer)==length and MARKER not in answer else None

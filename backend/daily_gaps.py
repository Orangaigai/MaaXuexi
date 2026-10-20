"""Skip hints only when a complete question proves every choice must be used."""
import re
import numpy as np
from daily_hints import clean


def count_choice_gaps(records, image, options):
    """Count thin underline groups inside the stem, never underscore characters.

    A line reaching a text margin may continue on the next row. Such layouts
    keep the hint path instead of counting two fragments as separate blanks.
    """
    if (not isinstance(image,np.ndarray) or image.shape!=(1280,720,3) or not options):
        return None
    headings=[r for r in records if r['text'].strip()=='多选题' and 130<=r['box'][1]<=400]
    if len(headings)!=1:return None
    top=headings[0]['box'][1]+headings[0]['box'][3]+10
    bottom=min(value[1][1] for value in options.values())-10
    sources=[r['box'][1]-8 for r in records if top<r['box'][1]<bottom
             and re.match(r'^(来源|出题|推荐单位)[：:]',r['text'].strip())]
    if sources:bottom=min(sources)
    if bottom<=top:return None
    pixels=image[top:bottom,55:665,:3].astype(np.int16)
    low,high=pixels.min(axis=2),pixels.max(axis=2)
    mask=(low>=100)&(high<=220)&(high-low<=8)
    lines=[]
    for y in np.flatnonzero(mask.any(axis=1)):
        edges=np.diff(np.pad(mask[y].astype(np.int8),(1,1)))
        for left,right in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
            if 22<=right-left<=250:
                x=int(left)+55;right=int(right)+55;absolute_y=int(y)+top
                previous=next((line for line in reversed(lines) if line[0]==x and line[2]==right
                               and line[3]==absolute_y-1),None)
                if previous:previous[3]=absolute_y
                else:lines.append([x,absolute_y,right,absolute_y])
    if not 2<=len(lines)<=6:return None
    for left,y,right,last_y in lines:
        if last_y-y>2 or left<90 or right>635:return None
        # Text on both sides supports an interior gap. Margin fragments and
        # decorative rules cannot establish a complete logical blank.
        band=image[max(top,y-36):last_y+3,:,:3].min(axis=2)<100
        if (np.count_nonzero(band[:,max(55,left-70):left])<4
                or np.count_nonzero(band[:,right:min(665,right+70)])<4):return None
    return len(lines)


def all_options_from_gaps(page, records, image, options):
    if not page or page.get('question_type')!='多选题':return None
    text=clean(page.get('question',''))
    # Equal counts alone don't exclude repeated answers or distractors.
    if (re.search(r'并非|不是|不要求|无需|无须|不限|可重复|可以重复|允许重复|多次',text)
            or not re.search(r'(?:每个选项|各选项|每项)(?:仅|只|限)(?:能|可)?(?:使用|填入|选用|用|选)?一次',text)):
        return None
    letters=sorted(options)
    if not 2<=len(letters)<=6 or letters!=list('ABCDEF'[:len(letters)]):return None
    return letters if count_choice_gaps(records,image,options)==len(letters) else None

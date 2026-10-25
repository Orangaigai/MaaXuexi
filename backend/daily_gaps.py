"""Recognize complete logical gaps in daily multi-choice stems."""
import re
import numpy as np
from daily_hints import clean


def count_choice_gaps(records, image, options):
    """Count thin underline groups inside the stem, never underscore characters.

    Margin blanks need adjacent text-row evidence. A pair of end/start fragments
    on consecutive rows keeps the hint path rather than becoming two blanks.
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
    # Stop at the first paragraph's end, before reference screenshots. OCR in
    # pictures must not add decorative lines to the stem's logical blanks.
    rows=sorted((r for r in records if top<r['box'][1]<bottom and r['box'][3]>=24
                 and r['text'].strip()),key=lambda r:r['box'][1])
    if not rows:return None
    end=rows[0]['box'][1]+rows[0]['box'][3]
    for row in rows[1:]:
        if row['box'][1]-end>45:break
        end=max(end,row['box'][1]+row['box'][3])
    bottom=min(bottom,end+20)
    if bottom<=top:return None
    pixels=image[top:bottom,55:665,:3].astype(np.int16)
    low,high=pixels.min(axis=2),pixels.max(axis=2)
    mask=(low>=100)&(high<=220)&(high-low<=35)
    lines=[]
    for y in np.flatnonzero(mask.any(axis=1)):
        edges=np.diff(np.pad(mask[y].astype(np.int8),(1,1)))
        for left,right in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
            if 32<=right-left<=250:
                x=int(left)+55;right=int(right)+55;absolute_y=int(y)+top
                previous=next((line for line in reversed(lines) if abs(line[0]-x)<=2 and abs(line[2]-right)<=2
                               and line[3]==absolute_y-1),None)
                if previous:
                    previous[0]=min(previous[0],x);previous[2]=max(previous[2],right)
                    previous[3]=absolute_y
                else:lines.append([x,absolute_y,right,absolute_y])
    if not 2<=len(lines)<=6:return None
    if any(right>635 and other_left<90 and 25<=other_y-y<=70
           for _,y,right,_ in lines for other_left,other_y,_,_ in lines):
        return None
    for left,y,right,last_y in lines:
        if last_y-y>2:return None
        band=image[max(top,y-36):last_y+3,:,:3].min(axis=2)<100
        left_text=np.count_nonzero(band[:,max(55,left-70):left])>=4
        right_text=np.count_nonzero(band[:,right:min(665,right+70)])>=4
        if left<90:
            # The first stem line may begin with a blank; later lines need a
            # preceding full text row, so wrapped fragments stay ambiguous.
            preceding=[r for r in rows if 30<=y-r['box'][1]-r['box'][3]<=70]
            earlier_ink=image[top:max(top,y-42),55:665,:3].min(axis=2)<100
            first_line=(np.count_nonzero(earlier_ink)<4
                        and not any(r['box'][1]+r['box'][3]<y-20 for r in rows)
                        and any(-8<=y-r['box'][1]-r['box'][3]<=12
                                and r['box'][0]+r['box'][2]>right+10 for r in rows))
            end_y=max((r['box'][1]+r['box'][3] for r in preceding),default=0)
            wrapped_line=any(r['box'][0]+r['box'][2]>=620 and
                             abs(r['box'][1]+r['box'][3]-end_y)<=8 for r in preceding)
            if left>80 or not right_text or not(first_line or wrapped_line):return None
        elif not left_text:return None
        if right>635:
            following=[r for r in rows if last_y+4<r['box'][1]<last_y+55]
            if right<650 or not following or not left_text:return None
            first_y=min(r['box'][1] for r in following)
            if not any(r['box'][0]<=90 and abs(r['box'][1]-first_y)<=8 for r in following):return None
        elif not right_text:return None
    return len(lines)


def all_options_from_gaps(page, records, image, options, *, gap_count=None):
    if not page or page.get('question_type')!='多选题':return None
    text=clean(page.get('question',''))
    if re.search(r'可重复|可以重复|允许重复|反义词|不是每个选项|不要求每个选项',text):
        return None
    letters=sorted(options)
    if not 2<=len(letters)<=6 or letters!=list('ABCDEF'[:len(letters)]):return None
    count=count_choice_gaps(records,image,options) if gap_count is None else gap_count
    return letters if count==len(letters) else None

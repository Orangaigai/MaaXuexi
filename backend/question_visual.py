"""Conservative visual routing from visible non-text content, without another model."""
import hashlib
import re
import numpy as np


def text_needs_image(question):
    return bool(re.search(r'如图|图中|下图|图示|图案|图片|图标|图像|示意图|漫画|下表|表中|图表|所示|这幅|此图|照片|标志|符号|配图|插图|坐标图|曲线图',question or ''))


def question_area(image, records, options):
    """Include the whole question and answer block, not just recognized text lines."""
    if not isinstance(image,np.ndarray) or image.ndim!=3 or not options:
        return None
    boxes=[row.get('box',[]) for row in options]
    if any(len(box)!=4 for box in boxes):return None
    first=min(box[1] for box in boxes)
    numbered=[row['box'][1] for row in records if len(row.get('box',[]))==4
              and 350<=row['box'][1]<=550 and re.match(r'^\d{1,3}[.．、]\s*\S',row.get('text',''))]
    # Numbered competitive pages have a separate player panel above the white card.
    top=max(350,min(numbered)-45) if numbered else 150
    bottom=min(image.shape[0]-40,max(y+h for x,y,w,h in boxes)+35)
    if first<=top or bottom<=top:return None
    return [55,top,min(665,image.shape[1]-20),bottom]


def visual_evidence(image,records,options,question='',strict=False):
    """Residual photo/diagram ink after masking OCR lines forces full-image input.

    This is a conservative routing heuristic, not a promise to classify every image.
    Unknown geometry also uses vision. No visual content is cropped out of requests.
    """
    area=question_area(image,records,options)
    if area is None:return True,None
    x1,y1,x2,y2=area
    pixels=image[y1:y2,x1:x2,:3].astype(np.int16)
    if not pixels.size:return True,area
    edges=np.zeros(pixels.shape[:2],dtype=bool)
    edges[1:]|=np.max(np.abs(pixels[1:]-pixels[:-1]),axis=2)>24
    edges[:,1:]|=np.max(np.abs(pixels[:,1:]-pixels[:,:-1]),axis=2)>24
    residual=edges & (pixels.min(axis=2)<180)
    for row in records:
        box=row.get('box',[])
        if len(box)!=4:continue
        x,y,w,h=map(int,box)
        # Huge OCR boxes can be a misrecognized photograph; do not erase those.
        if not 1<=h<=90 or not 1<=w<=720:continue
        left,right=max(0,x-x1-6),min(x2-x1,x+w-x1+6)
        top,bottom=max(0,y-y1-6),min(y2-y1,y+h-y1+6)
        if right>left and bottom>top:residual[top:bottom,left:right]=False
    ys,xs=np.nonzero(residual)
    count,width,height=(40,8,8) if strict else (120,24,16)
    visible=(len(xs)>=count and len(np.unique(ys))>=min(12,height) and len(np.unique(xs))>=min(12,width)
             and int(xs.max()-xs.min())>=width and int(ys.max()-ys.min())>=height) if len(xs) else False
    return bool(visible or text_needs_image(question)),area


def visual_identity(image,area):
    if area is None:return None
    x1,y1,x2,y2=area
    return hashlib.sha256(np.ascontiguousarray(image[y1:y2,x1:x2]).tobytes()).hexdigest()

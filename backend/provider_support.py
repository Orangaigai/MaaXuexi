import base64
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from key_store import key_configured, load_key

MODEL = 'deepseek-flash'
VALIDATION_PATH = Path('config/deepseek-validation.json')

def provider_metadata():
    result = dict(provider='deepseek', name='DeepSeek 官方', model=MODEL,
                  base_url='https://api.deepseek.com', configured=key_configured(), validated=False)
    if VALIDATION_PATH.exists():
        try:
            result['validation'] = json.loads(VALIDATION_PATH.read_text('utf-8'))
            result['validated'] = result['configured'] and result['validation'].get('status') == 'success'
        except (OSError, ValueError):
            pass
    return result

def invalidate_validation():
    if VALIDATION_PATH.exists():
        VALIDATION_PATH.unlink()

def test_connection():
    from utils import AIResolver
    key = load_key()
    if not key:
        return dict(status='failed', message='请先保存 DeepSeek 官网 API Key。')
    image = Image.new('RGB', (320, 160), 'white')
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 100)
    ImageDraw.Draw(image).text((115, 12), 'B', fill='black', font=font)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    resolver = AIResolver(key, MODEL)
    try:
        response = resolver._post(dict(model=MODEL, max_tokens=16, messages=[
            dict(role='system', content=[dict(type='text', text='Read the single capital letter in the image. Reply with that letter only.')]),
            dict(role='user', content=[dict(type='image_url', image_url=dict(url='data:image/png;base64,'+encoded))])
        ]))
        if response is None:
            result = dict(status='failed', message='连接验证失败，请检查网络后重试。')
        elif response.status_code != 200:
            result = dict(status='failed', http_status=response.status_code,
                          message=f'验证未通过（HTTP {response.status_code}），请核对密钥、余额及接口权限。')
        else:
            answer = str(response.json()['choices'][0]['message']['content']).strip().strip(' .。')
            result = dict(status='success' if answer == 'B' else 'failed', image_input=answer == 'B',
                          message='验证通过：DeepSeek 图片接口可用。' if answer == 'B' else '接口已响应，但图片识别结果不符合测试预期。')
    except Exception:
        result = dict(status='failed', message='连接验证失败，请检查网络后重试。')
    finally:
        resolver.session.close()
    result.update(model=MODEL, checked_at=datetime.now().astimezone().isoformat())
    VALIDATION_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result

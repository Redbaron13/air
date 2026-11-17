"""Small, exact-scored local vision controls; does not approve real media."""
import base64
import json
import os
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen


def run(model, output):
    from PIL import Image, ImageDraw
    output.mkdir(parents=True, exist_ok=False)
    url = os.environ.get('OLLAMA_URL', 'http://ollama:11434').rstrip('/')
    fields = ['background_color', 'square_color', 'square_count', 'circle_color']
    schema = {'type': 'object', 'properties': {k: {'type': 'string'} for k in fields},
              'required': fields, 'additionalProperties': False}
    results = []
    for name, count, circle in [('one', 1, False), ('two', 2, False), ('detail', 1, True)]:
        im = Image.new('RGB', (384, 256), 'blue')
        draw = ImageDraw.Draw(im)
        draw.rectangle((50, 50, 110, 110), fill='red')
        if count == 2:
            draw.rectangle((190, 50, 250, 110), fill='red')
        if circle:
            draw.ellipse((330, 210, 350, 230), fill='yellow')
        path = output / (name + '.png')
        im.save(path)
        expected = dict(zip(fields, ['blue', 'red', str(count), 'yellow' if circle else 'none']))
        payload = {'model': model, 'stream': False, 'think': False, 'format': schema,
                   'keep_alive': 0, 'options': {'temperature': 0, 'num_ctx': 2048, 'num_predict': 128, 'num_thread': 2},
                   'messages': [{'role': 'user', 'content': 'Inspect only this image. Return JSON with '
                                 'background_color, square_color, square_count (digit string), '
                                 'circle_color (none if absent). Use lowercase color names.',
                                 'images': [base64.b64encode(path.read_bytes()).decode()]}]}
        start = time.monotonic()
        result = {'case': name, 'model': model, 'expected': expected, 'passed': False}
        try:
            req = Request(url + '/api/chat', data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=240) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('Response exceeds budget')
            data = json.loads(raw)
            (output / (name + '-raw.json')).write_text(json.dumps(data, indent=2))
            actual = json.loads(data['message']['content'])
            result.update(actual=actual, done_reason=data.get('done_reason'),
                          passed=data.get('done') is True and data.get('done_reason') == 'stop'
                          and actual == expected)
        except Exception as error:
            result['error'] = type(error).__name__ + ': ' + str(error)[:300]
        result['elapsed_seconds'] = round(time.monotonic() - start, 2)
        results.append(result)
        (output / 'report.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(result), flush=True)
        if 'error' in result:
            break  # Stop on runtime failure; do not queue more work or use cloud.
    return len(results) == 3 and all(r['passed'] for r in results)


if __name__ == '__main__':
    raise SystemExit(0 if run(sys.argv[1], Path(sys.argv[2])) else 1)

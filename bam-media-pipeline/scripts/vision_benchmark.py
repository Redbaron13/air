"""Generate labeled visual controls, or score model JSON against human-authored checks."""
import argparse
import json
from pathlib import Path


def create(directory):
    from PIL import Image, ImageDraw
    directory.mkdir(parents=True, exist_ok=False)
    cases = []
    for name, count, extra in [('one-square', 1, False), ('two-squares', 2, False),
                               ('square-with-small-circle', 1, True)]:
        image = Image.new('RGB', (1000, 700), 'blue')
        draw = ImageDraw.Draw(image)
        draw.rectangle((160, 170, 370, 380), fill='red')
        if count == 2:
            draw.rectangle((560, 170, 770, 380), fill='red')
        if extra:
            draw.ellipse((840, 540, 900, 600), fill='yellow')
        image.save(directory / (name + '.png'))
        cases.append({'id': name, 'file': name + '.png',
                      'expected_any': [['blue'], ['red'], ['square', 'rectangle'],
                                       ['two', '2'] if count == 2 else ['one', 'single', '1']],
                      'expected_detail': 'small yellow circle at lower right' if extra else 'no yellow circle',
                      'detail_any': [['yellow'], ['circle', 'circular']] if extra else [],
                      'forbidden': [] if extra else ['yellow'],
                      'ground_truth': 'Generated geometry and colors; no EXIF/GPS; no real location.'})
    (directory / 'manifest.json').write_text(json.dumps({'cases': cases}, indent=2) + '\n')


def score(manifest, runs):
    results = []
    for case in manifest['cases']:
        path = runs / case['id'] / 'result.json'
        if not path.exists():
            results.append({'id': case['id'], 'status': 'NOT_RUN'})
            continue
        data = json.loads(path.read_text())
        checks = []
        for round_result in data['rounds']:
            for reviewer in ('a', 'b'):
                text = ' '.join(round_result[reviewer].values()).lower()
                required = case['expected_any'] + case.get('detail_any', [])
                checks.append({'round': round_result['round'], 'reviewer': reviewer,
                               'missing': [options for options in required if not any(t in text for t in options)],
                               'unexpected': [term for term in case.get('forbidden', []) if term in text]})
        results.append({'id': case['id'], 'status': 'REQUIRES_HUMAN_SCORING', 'lexical_checks': checks,
                        'cloud_used': data.get('cloud_arbitration') is not None,
                        'elapsed_seconds': data.get('elapsed_seconds')})
    return {'results': results, 'note': 'Lexical checks are screening aids, not semantic accuracy scores. '
             'A phrase such as no yellow circle contains yellow. Human adjudication is required.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--runs', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.create:
        create(args.create)
    elif args.manifest and args.runs and args.report:
        args.report.write_text(json.dumps(score(json.loads(args.manifest.read_text()), args.runs), indent=2) + '\n')
    else:
        parser.error('Use --create DIR or --manifest FILE --runs DIR --report FILE')


if __name__ == '__main__':
    main()

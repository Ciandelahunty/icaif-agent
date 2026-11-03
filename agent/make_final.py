#!/usr/bin/env python3
"""Build starter-kit/private/final_submission.json from the saved team credentials.

The team token is read from starter-kit/.icaif/credentials.json and never printed.
Nothing is uploaded; submit afterwards with:
    cd starter-kit && ../.venv/bin/python tools/auto_submit.py final --file private/final_submission.json

Usage (from the icaif-agent folder):
    python agent/make_final.py --url "https://drive.google.com/drive/folders/..." \
        --name "Cian Delahunty" --email "you@example.com" --institution "University College London"
"""
import argparse
import json
import os
from pathlib import Path
import sys

KIT = Path(__file__).resolve().parents[1] / 'starter-kit'
sys.path.insert(0, str(KIT))
from kit.contracts import validate_payload  # noqa: E402

OTHER_MATERIALS = ['source.zip', 'README.md', 'requirements.txt', 'disclosures.md', 'decisions.jsonl']


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', required=True, help='shared Google Drive folder link')
    parser.add_argument('--name', required=True)
    parser.add_argument('--email', required=True, help='the email registered for the team member')
    parser.add_argument('--institution', required=True)
    parser.add_argument('--video', default='video.mov', help='video filename exactly as uploaded (default video.mov)')
    args = parser.parse_args()

    materials = [args.video] + OTHER_MATERIALS
    creds = json.loads((KIT / '.icaif' / 'credentials.json').read_text(encoding='utf-8'))
    payload = {
        'submission_type': 'final_submission',
        'team_id': creds['team_id'],
        'team_token': creds['team_token'],
        'team_members': [{'name': args.name, 'email': args.email, 'institution': args.institution}],
        'materials_url': args.url.strip(),
        'materials': materials,
    }
    validate_payload(payload, 'final_submission.json')   # the kit's own checks

    out = KIT / 'private' / 'final_submission.json'
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    os.chmod(out, 0o600)
    print(f'Written {out} (token not shown). It lists these files, which must all be in the Drive folder:')
    for name in materials:
        print('  ' + name)


if __name__ == '__main__':
    main()

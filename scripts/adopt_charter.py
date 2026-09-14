"""A new seed committee may adopt an existing charter whole, without rewriting it."""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict
from pathlib import Path

from factorylab.charter.committee import draw
from factorylab.cortex.assembly import _parse_json_object
from factorylab.runtime.cli import _load_dotenv
from factorylab.runtime.live import build_provider
from factorylab.runtime.worlds import load_manifest
from factorylab.world.models import ModelRequest
from scripts.draft_edition1 import charter_digest, roster_hash
from scripts.ratify_charter import select_cards
from scripts.rehearsal import voted_charter


def adoption_vote(response):
    """Only a complete boolean ballot with a reason counts; truncation is abstention."""
    raw = _parse_json_object(response.text)
    if (response.stop_reason != 'stop' or not isinstance(raw, dict)
            or type(raw.get('adopt')) is not bool
            or not isinstance(raw.get('reason'), str) or not raw['reason'].strip()):
        raise ValueError('invalid or incomplete adoption ballot')
    return raw['adopt'], raw['reason']


def approved(calls, seats):
    """A strict majority of distinct drawn seats must explicitly approve the whole charter."""
    expected = {seat.assembly_id for seat in seats}
    actual = [row['seat']['assembly_id'] for row in calls]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('ballots must cover each drawn seat exactly once')
    return sum(row.get('valid') is True and row.get('adopt') is True
               for row in calls) > len(seats) // 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('source-world', 'world', 'charter', 'out', 'report'):
        parser.add_argument('--' + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists() or args.report.exists():
        raise FileExistsError('adoption outputs already exist')
    original = load_manifest(str(args.source_world))
    manifest = load_manifest(str(args.world))
    raw = voted_charter(args.charter, original)
    select_cards(raw, [c['id'] for c in raw['cards']])
    source_text = args.charter.read_text()
    body = source_text[source_text.index('[charter]'):]
    seats = draw({a.id: a.role for a in manifest.assemblies}, random.Random(manifest.seed),
                 size=manifest.committee.seats)
    _load_dotenv()
    provider = build_provider(manifest)
    calls = []
    public_roster = [dict(id=a.id, role=a.role, accepts=list(a.accepts))
                     for a in manifest.assemblies]
    for seat in seats:
        spec = next(a for a in manifest.assemblies if a.id == seat.assembly_id)
        inputs = dict(charter=raw, you=seat.assembly_id, role=seat.role,
                      population=public_roster,
                      compute_supply='The starting population now includes OpenRouter and '
                      'Venice assemblies. OpenRouter has finite prepaid seed credit. Venice '
                      'has separate credit; treasury.transfer with direction to_venice buys '
                      'a $5 tranche from Base USDC. catalogue.search lists available models.')
        prompt = ('Vote on adopting this existing charter unchanged as edition 1 for this '
                  'starting population. You may approve or reject it. This is a vote on the '
                  'entire charter, not permission to edit or select a subset. Return JSON '
                  'with adopt (boolean) and reason (a short explanation).\n'
                  + json.dumps(inputs))
        req = ModelRequest(spec.model_id,
                           'Reply with one JSON object containing adopt and reason.',
                           ({'role': 'user', 'content': prompt},), max_tokens=2000,
                           effort=spec.effort, json_object=True)
        row = dict(seat=seat._asdict(), model=spec.model_id, request=asdict(req))
        try:
            response = provider.complete(req)
            row.update(text=response.text, stop=response.stop_reason,
                       cost_micro=response.cost_micro,
                       cost_source=response.raw.get('cost_source', 'provider'),
                       request_id=response.raw.get('request_id'),
                       input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                       served_by=response.model_id)
            vote, reason = adoption_vote(response)
            row.update(valid=True, adopt=vote, reason=reason)
        except Exception as exc:
            row.update(valid=False, adopt=None, failure=type(exc).__name__,
                       http_status=getattr(exc, 'status', None))
        calls.append(row)
        evidence = dict(source_roster_sha256=roster_hash(original),
                        roster_sha256=roster_hash(manifest), charter_sha256=charter_digest(raw),
                        method='Whole-charter adoption; strict majority of drawn seed committee',
                        seats=[s._asdict() for s in seats], calls=calls,
                        complete=len(calls) == len(seats), approved=False)
        if evidence['complete']:
            evidence['approved'] = approved(calls, seats)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(evidence, indent=2) + '\n')
        print(json.dumps({k: v for k, v in row.items() if k not in ('request', 'text')}),
              flush=True)
    if not evidence['approved']:
        raise ValueError('whole charter did not win a majority; no approval exported')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as stream:
        for key in ('source_roster_sha256', 'roster_sha256', 'charter_sha256'):
            stream.write(f'# {key} = {evidence[key]}\n')
        stream.write(body)
    assert voted_charter(args.out, manifest) == raw
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

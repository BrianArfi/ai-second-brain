#!/usr/bin/env python3
"""chunk_work.py - split a work list into Haiku-sized chunks and keep a handoff state file.

Haiku 5.5 charges 5x per token once a single request's prompt passes 100,000
tokens (input, cache read and cache write all count). One agent per chunk keeps
every request under that line. The state file is the handoff: a fresh agent
reads it, does one chunk, writes findings back, and stops.

    python3 chunk_work.py init  --name weekly-x --items items.txt --est-tokens 6000
    python3 chunk_work.py next  --name weekly-x          # prints the next chunk as JSON + prior findings
    python3 chunk_work.py done  --name weekly-x --chunk 2 --findings findings.md
    python3 chunk_work.py status --name weekly-x

items.txt: one item per line (path, URL, ticket key). --est-tokens is the
estimated tokens each item adds to the agent's context (default 6000).
Budget per agent = 100k minus 15k overhead (system prompt, tools, handoff) = 85k,
with 20% headroom -> about 68k of item content.
"""
import argparse, json, os, sys, time

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DIR = os.path.join(BASE, '_temp', 'chunk_work')
LIMIT, OVERHEAD, HEADROOM = 100_000, 15_000, 0.8

def path(name):
    os.makedirs(DIR, exist_ok=True)
    return os.path.join(DIR, name + '.json')

def load(name):
    with open(path(name), encoding='utf-8') as fh:
        return json.load(fh)

def save(name, st):
    tmp = path(name) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(st, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path(name))  # atomic, so a crashed agent never leaves half a file

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['init', 'next', 'done', 'status'])
    ap.add_argument('--name', required=True)
    ap.add_argument('--items')
    ap.add_argument('--est-tokens', type=int, default=6000)
    ap.add_argument('--chunk', type=int)
    ap.add_argument('--findings')
    a = ap.parse_args()

    if a.cmd == 'init':
        items = [l.strip() for l in open(a.items, encoding='utf-8') if l.strip()]
        per = max(1, int((LIMIT - OVERHEAD) * HEADROOM / a.est_tokens))
        chunks = [{'id': i + 1, 'items': items[s:s + per], 'status': 'todo', 'findings': ''}
                  for i, s in enumerate(range(0, len(items), per))]
        save(a.name, {'name': a.name, 'created': time.time(), 'items_per_chunk': per, 'chunks': chunks})
        print(f'{len(items)} items -> {len(chunks)} chunks of up to {per}. State: {path(a.name)}')
    else:
        st = load(a.name)
        if a.cmd == 'next':
            todo = [c for c in st['chunks'] if c['status'] == 'todo']
            if not todo:
                print(json.dumps({'done': True})); return
            prior = [{'chunk': c['id'], 'findings': c['findings']} for c in st['chunks'] if c['status'] == 'done']
            print(json.dumps({'chunk': todo[0], 'remaining_chunks': len(todo), 'prior_findings': prior}, indent=2))
        elif a.cmd == 'done':
            c = next(c for c in st['chunks'] if c['id'] == a.chunk)
            c['status'] = 'done'
            c['findings'] = open(a.findings, encoding='utf-8').read()
            save(a.name, st)
            print(f"chunk {a.chunk} done; {sum(x['status'] == 'todo' for x in st['chunks'])} left")
        else:
            for c in st['chunks']:
                print(c['id'], c['status'], len(c['items']), 'items')

if __name__ == '__main__':
    sys.exit(main())

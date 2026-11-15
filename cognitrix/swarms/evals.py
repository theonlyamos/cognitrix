"""A set of jobs for measuring swarms against one agent working alone.

Each job is run in a fresh chat with the agent you name, once with the Swarm
switch on and (with ``--baseline``) once without. A judge model scores the
final answer from 0 to 10 against the job's rubric, and the run records how
many workers were started, the tokens used and the time taken. The result is
a table that shows where splitting the work pays for itself and where it
does not (the "small" jobs should start no workers at all).

    cognitrix swarm-evals you@example.com --agent Assistant --jobs 3 --baseline

It makes real model calls on the account's provider keys.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

EVAL_TIMEOUT = 1200.0


@dataclass(frozen=True)
class EvalJob:
    id: str
    kind: str        # list | parts | best_of | small
    prompt: str
    rubric: str


_LANGS = 'Python, JavaScript, Go, Rust, Java, C#, Ruby, Swift, Kotlin, PHP'
_CITIES = 'Lagos, Nairobi, Accra, Cairo, Johannesburg, Kigali, Dakar, Addis Ababa'

JOBS: tuple[EvalJob, ...] = (
    # One worker per item.
    EvalJob('list-languages', 'list',
            f'For each of these languages, give its first release year, its main designer and one sentence on what '
            f'it is mostly used for today: {_LANGS}. Answer with one table.',
            'A single table with all 10 languages, each with a plausible year, designer and use. Deduct for missing '
            'rows, wrong well-known facts (e.g. Python 1991 Guido van Rossum, Go 2009 Google) or several tables.'),
    EvalJob('list-cities', 'list',
            f'For each of these cities give the country, the main official or working language and the currency: '
            f'{_CITIES}. One table.',
            'One table, 8 rows, correct country, language and currency for each city.'),
    EvalJob('list-http', 'list',
            'Explain each of these HTTP status codes in one line and say whether a client should retry: '
            '301, 304, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503.',
            'All 12 codes present, each with a correct meaning and a sensible retry verdict (429/502/503 retry, '
            '400/401/403/404/422 not without a change).'),
    EvalJob('list-sorts', 'list',
            'For bubble sort, insertion sort, merge sort, quicksort, heapsort and counting sort give best, average '
            'and worst time complexity, whether it is stable, and one case where you would pick it.',
            'Six algorithms, correct complexities (quicksort worst n^2, merge n log n, counting n+k), correct '
            'stability (merge/insertion/bubble/counting stable; quick/heap not), a sensible use each.'),
    EvalJob('list-planets', 'list',
            'For each of the eight planets give its average distance from the Sun in AU, the length of its year in '
            'Earth years and its number of known moons (say that moon counts change). One table.',
            'Eight rows, distances and years roughly right (Earth 1 AU/1 yr, Jupiter ~5.2 AU/~11.9 yr, Neptune ~30 '
            'AU/~165 yr), moon counts plausible with the caveat stated.'),
    EvalJob('list-licences', 'list',
            'Compare these software licences in one table: MIT, Apache 2.0, GPLv3, LGPLv3, MPL 2.0, BSD 3-Clause, '
            'AGPLv3. Columns: copyleft strength, patent grant, may be used in closed-source products.',
            'Seven rows; MIT/BSD permissive without explicit patent grant, Apache permissive with patent grant, '
            'GPL/AGPL strong copyleft, LGPL/MPL weak copyleft; closed-source answers consistent with that.'),
    EvalJob('list-regex', 'list',
            'Write a regular expression for each of these and one matching and one non-matching example: an IPv4 '
            'address, a 24-hour time HH:MM, a hex colour, a UK-style date DD/MM/YYYY, a slug of lowercase words '
            'joined by hyphens, a semantic version number.',
            'Six patterns that are actually correct for the stated examples, each with a match and a non-match.'),
    # Independent parts.
    EvalJob('parts-db', 'parts',
            'I am choosing a database for a small SaaS with 50k users: compare PostgreSQL, MySQL, MongoDB and '
            'SQLite on transactions, scaling out, JSON support, operations burden and hosting cost, then recommend '
            'one and say what would change your mind.',
            'Covers all four databases on all five criteria, a clear recommendation with reasons, and a stated '
            'condition that would change it. One coherent answer, not four pasted reports.'),
    EvalJob('parts-launch', 'parts',
            'Plan the launch of a note-taking mobile app: give a one-paragraph positioning statement, a pricing '
            'proposal with three tiers, a four-week launch timeline and the five biggest risks with a mitigation '
            'each.',
            'All four parts present and consistent with each other (pricing matches positioning, timeline is four '
            'weeks, exactly five risks with mitigations).'),
    EvalJob('parts-frameworks', 'parts',
            'Compare React, Vue, Svelte and Angular for a team of three building an internal dashboard: learning '
            'curve, ecosystem, performance, hiring. End with a recommendation.',
            'Four frameworks by four criteria, accurate broad characterisation, one recommendation that follows '
            'from the comparison.'),
    EvalJob('parts-review', 'parts',
            'Review this function for bugs, for performance and for readability, as three separate short '
            'sections, then give a corrected version:\n\n'
            'def dedupe(items):\n    out = []\n    for i in range(len(items)):\n        if items[i] not in out:\n'
            '            out.append(items[i])\n    return out.sort()\n',
            'Finds that out.sort() returns None (bug), that `in` on a list makes it quadratic (performance), and '
            'gives readability notes; the corrected version is right and keeps order or says it sorts.'),
    EvalJob('parts-cloud', 'parts',
            'For a startup running a containerised web app, compare AWS, Google Cloud and Azure on managed '
            'container services, managed Postgres, free tier and typical pitfalls. Finish with a pick for a team '
            'that knows none of them.',
            'Names the right services (ECS/EKS/Fargate, GKE/Cloud Run, AKS/Container Apps; RDS, Cloud SQL, Azure '
            'Database for PostgreSQL), fair pitfalls, a reasoned pick.'),
    EvalJob('parts-interview', 'parts',
            'Prepare me for a backend engineer interview: five system design questions with what a good answer '
            'covers, five coding topics to revise with one practice problem each, and five questions I should ask '
            'the interviewer.',
            'Three sections with exactly five entries each, each entry concrete and useful.'),
    EvalJob('parts-incident', 'parts',
            'Our checkout API was down for 40 minutes because a database migration locked a large table. Write: a '
            'customer-facing status note, an internal timeline template, the root cause in plain words, and five '
            'follow-up actions with an owner role each.',
            'All four pieces, the status note is free of blame and jargon, the root cause matches the facts given, '
            'five actions each with an owner role.'),
    # Several attempts, best one kept.
    EvalJob('best-tagline', 'best_of',
            'Write a tagline of at most eight words for a budgeting app aimed at students. Try several different '
            'angles and give me the single best one with one line on why.',
            'Exactly one tagline of eight words or fewer, fitting students and budgeting, with a one-line reason. '
            'Deduct for a list of options instead of a pick.'),
    EvalJob('best-sql', 'best_of',
            'Write the clearest correct SQL query for: from orders(id, customer_id, total, created_at) return each '
            'customer\'s most recent order and its total. Consider more than one approach and give the best one.',
            'One query that is correct (window function with ROW_NUMBER, or a correlated/joined MAX(created_at)), '
            'with a brief note on ties or why it was chosen.'),
    EvalJob('best-email', 'best_of',
            'Write a three-sentence email declining a meeting invitation politely while proposing an async '
            'update instead. Give only the best version.',
            'Exactly one email of three sentences, polite, declines, proposes an async update.'),
    # Too small to split: starting workers here is a mistake.
    EvalJob('small-capital', 'small', 'What is the capital of Australia?',
            'Says Canberra, briefly. No workers should have been started.'),
    EvalJob('small-convert', 'small', 'Convert 72 degrees Fahrenheit to Celsius.',
            'About 22.2 degrees Celsius, briefly. No workers should have been started.'),
    EvalJob('small-rewrite', 'small',
            'Rewrite this sentence to be clearer: "The meeting, which was about budgets, it got moved by them to '
            'Thursday."',
            'One clear sentence such as "They moved the budget meeting to Thursday." No workers should have been '
            'started.'),
)


def select(spec: str | int | None) -> list[EvalJob]:
    """Jobs by a count ("3": the first of each kind in turn), ids or kinds, comma separated."""
    if spec in (None, '', 'all'):
        return list(JOBS)
    text = str(spec).strip()
    if text.isdigit():
        kinds: dict[str, list[EvalJob]] = {}
        for job in JOBS:
            kinds.setdefault(job.kind, []).append(job)
        picked: list[EvalJob] = []
        while len(picked) < min(int(text), len(JOBS)):
            for rows in kinds.values():
                if rows and len(picked) < int(text):
                    picked.append(rows.pop(0))
        return picked
    wanted = {part.strip() for part in text.split(',') if part.strip()}
    return [job for job in JOBS if job.id in wanted or job.kind in wanted]


async def judge(llm: Any, job: EvalJob, answer: str) -> tuple[float, str]:
    """(score 0-10, one line why). -1 when the judge could not be reached."""
    from cognitrix.swarms.service import _ask

    text = await _ask(
        llm, 'You grade answers strictly against a rubric. Reply exactly as instructed.',
        f'The request:\n\n{job.prompt}\n\nThe rubric:\n\n{job.rubric}\n\nThe answer to grade:\n\n'
        f'{answer[:20000] or "(no answer)"}\n\n'
        'Reply with "SCORE: <whole number 0-10>" on the first line and one sentence of reasons on the second.')
    match = re.search(r'SCORE:\s*(\d+(?:\.\d+)?)', text or '')
    if not match:
        return -1.0, (text or 'The judge gave no score.').strip()[:300]
    note = text[match.end():].strip().splitlines()
    return min(10.0, float(match.group(1))), (note[0].strip() if note else '')[:300]


def _last_answer(chat: list[dict[str, Any]]) -> str:
    from cognitrix.swarms.service import _strip_think

    for entry in reversed(chat or []):
        if str(entry.get('role') or '').lower() == 'assistant' and str(entry.get('type') or 'text') == 'text':
            text = _strip_think(str(entry.get('content') or ''))
            if text:
                return text
    return ''


async def run_job(user_key: str, agent: Any, job: EvalJob, *, swarm: bool, timeout: float = EVAL_TIMEOUT
                  ) -> dict[str, Any]:
    """Run one job in a new chat and return its answer and what it cost."""
    from cognitrix import usage
    from cognitrix.session_ownership import claim_new
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session
    from cognitrix.swarms import service
    from cognitrix.tools.swarm import swarm_requested
    from cognitrix.tools.utils import ToolExecutionContext

    session = Session(agent_id=str(agent.id), user_id=user_key, title=f'Swarm eval: {job.id}')
    await session.save()
    await claim_new(str(session.id), user_key, str(agent.id))
    sid = str(session.id)
    spent = {'tokens': 0}

    def count(used: dict) -> None:
        try:
            spent['tokens'] += int(used.get('prompt_tokens') or 0) + int(used.get('completion_tokens') or 0)
        except (TypeError, ValueError):
            pass

    async def quiet(*args: Any, **kwargs: Any) -> None:
        return None

    async def turn() -> None:
        owner = usage.set_owner(user_key, str(agent.id))
        usage.set_sink(count)
        flag = swarm_requested.set(bool(swarm))
        try:
            await session(job.prompt, agent, interface='web', stream=True, output=quiet,
                          wsquery={'type': 'generate', 'action': 'chat_message'},
                          tool_context=ToolExecutionContext(session_id=sid, user_id=user_key, agent_id=str(agent.id)))
        finally:
            swarm_requested.reset(flag)
            usage.reset_owner(owner)

    started = time.monotonic()
    error = ''
    try:
        await asyncio.wait_for(asyncio.create_task(turn()), timeout=timeout)
        # Workers report into the chat as a new turn of the lead: wait for it.
        while time.monotonic() - started < timeout:
            running = [s for s in await service.swarms_for_chat(user_key, sid) if s.status == 'running']
            if not running and not agent_chats.is_running(user_key, sid):
                await asyncio.sleep(1.0)
                if not agent_chats.is_running(user_key, sid):
                    break
            await asyncio.sleep(1.0)
        else:
            error = 'timed out'
    except TimeoutError:
        error = 'timed out'
    except Exception as exc:
        error = str(exc)[:300]
    swarms = await service.swarms_for_chat(user_key, sid)
    for run in swarms:
        if run.status == 'running':
            await service.stop_swarm(user_key, str(run.id), reason='The eval ended.')
    workers = [w for run in swarms for w in await service.workers_of(str(run.id))]
    fresh = await Session.get(sid)
    return {
        'job': job.id, 'kind': job.kind, 'swarm': bool(swarm), 'session_id': sid, 'error': error,
        'answer': _last_answer(getattr(fresh, 'chat', None) or []),
        'workers': len(workers), 'workers_done': sum(1 for w in workers if w.status == 'done'),
        'tokens': spent['tokens'] + sum(int(w.tokens or 0) for w in workers),
        'seconds': round(time.monotonic() - started, 1),
    }


async def run(user_key: str, agent: Any, jobs: list[EvalJob], *, baseline: bool = False,
              judge_llm: Any = None, timeout: float = EVAL_TIMEOUT, log=print) -> dict[str, Any]:
    """Run the jobs one after another and score them. Returns {'rows', 'summary'}."""
    judge_llm = judge_llm or agent.llm
    rows: list[dict[str, Any]] = []
    for job in jobs:
        for use_swarm in ((True, False) if baseline else (True,)):
            row = await run_job(user_key, agent, job, swarm=use_swarm, timeout=timeout)
            row['score'], row['note'] = await judge(judge_llm, job, row['answer'])
            if job.kind == 'small' and row['workers']:
                row['note'] = f'Started {row["workers"]} worker(s) for a small job. ' + row['note']
            rows.append(row)
            log(f'{job.id:18} {"swarm" if use_swarm else "alone":6} score {row["score"]:>4} '
                f'workers {row["workers"]:>2} tokens {row["tokens"]:>8} {row["seconds"]:>6}s {row["error"]}')
    return {'rows': rows, 'summary': summarise(rows)}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mode in (True, False):
        mine = [r for r in rows if r['swarm'] is mode]
        scored = [r['score'] for r in mine if r['score'] >= 0]
        if not mine:
            continue
        out['swarm' if mode else 'alone'] = {
            'jobs': len(mine),
            'mean_score': round(sum(scored) / len(scored), 2) if scored else None,
            'tokens': sum(r['tokens'] for r in mine),
            'seconds': round(sum(r['seconds'] for r in mine), 1),
            'workers': sum(r['workers'] for r in mine),
            'small_jobs_that_started_workers': sum(1 for r in mine if r['kind'] == 'small' and r['workers']),
            'failed': sum(1 for r in mine if r['error']),
        }
    return out


async def command(args: Any) -> None:
    """`cognitrix swarm-evals <email>`."""
    import sys

    from cognitrix.agents import access
    from cognitrix.config import initialize_database
    from cognitrix.models import User

    await initialize_database()
    user = await User.find_one({'email': str(args.email).strip().lower()}) or await User.find_one(
        {'email': str(args.email).strip()})
    if user is None:
        print(f'No account with the email {args.email}.')
        sys.exit(1)
    user_key = access.owner_key(user)
    agent = await access.find_visible_agent(user_key, name=args.agent)
    if agent is None:
        print(f'{args.email} has no agent called {args.agent}.')
        sys.exit(1)
    jobs = select(args.jobs)
    if not jobs:
        print('No such jobs. Kinds: list, parts, best_of, small. Ids: ' + ', '.join(j.id for j in JOBS))
        sys.exit(1)
    print(f'Running {len(jobs)} job(s) with {agent.name}{" and alone for comparison" if args.baseline else ""}.')
    report = await run(user_key, agent, jobs, baseline=bool(args.baseline), timeout=float(args.timeout))
    print(json.dumps(report['summary'], indent=2))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as handle:
            json.dump({'jobs': [asdict(j) for j in jobs], **report}, handle, indent=2)
        print(f'Saved to {args.out}')

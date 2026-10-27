"""Deterministic coordinator. The planner never sees credentials or lease tokens."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from desk.ai_policy import MAX_QUERIES, MAX_RESULT_BYTES, PolicyError, canonical, cli_arguments, exact_keys, validate_plan
from ai_ticket_model import CodexPlanner, WorkerError, run_bounded, stop_children

REASONS = {'sap_unavailable', 'authorization_missing', 'context_incomplete', 'evidence_incomplete', 'runtime_error', 'time_limit'}


class LeaseLost(WorkerError):
    pass


class Gateway:
    def __init__(self, node): self.node = node

    def __call__(self, operation, **values):
        raw = run_bounded([self.node, str(ROOT / 'scripts/ai_ticket_transport.cjs')],
            input_text=json.dumps({'operation': operation, **values}, ensure_ascii=False), timeout=90, cwd=ROOT)
        result = json.loads(raw)
        if not isinstance(result, dict): raise WorkerError()
        return result


class SAPReader:
    def __init__(self, node, workspace, approved_user=''):
        self.node, self.workspace, self.approved_user = node, str(Path(workspace).resolve()), approved_user

    def __call__(self, reserved):
        plan, scope = reserved['plan'], reserved['scope']
        args = cli_arguments(plan, scope)  # independent revalidation before signing/execution
        request = {'workspace': self.workspace, 'args': args, 'approved_user': self.approved_user}
        try:
            if datetime.fromisoformat(reserved['deadline_at']) <= datetime.now(timezone.utc):
                raise WorkerError('time_limit')
            raw = run_bounded([self.node, str(ROOT / 'scripts/ai_ticket_sap.mjs')],
                input_text=canonical(request), timeout=min(55, reserved['timeout_seconds']), cwd=ROOT)
            envelope = json.loads(raw)
            if isinstance(envelope, dict) and envelope.get('error') in REASONS:
                raise WorkerError(envelope['error'])
            if len(raw.encode()) > MAX_RESULT_BYTES: raise WorkerError('evidence_incomplete')
            metadata = envelope.get('_evidence', {})
            if (metadata.get('system') != scope['system'] or metadata.get('client') != scope['client']
                    or metadata.get('command') != plan['command'] or metadata.get('object') != plan['object']
                    or metadata.get('trust') != 'UNTRUSTED_EVIDENCE' or metadata.get('instructionAuthority') is not False):
                raise WorkerError('evidence_incomplete')
            result = {key: metadata[key] for key in ['system', 'client', 'object', 'command']}
            result['sha256'] = hashlib.sha256(raw.encode()).hexdigest()
            return envelope, result
        except (ValueError, TypeError, KeyError):
            raise WorkerError('evidence_incomplete') from None


def normalize_decision(value):
    exact_keys(value, {'action', 'plan', 'body', 'evidence_ids', 'reason'})
    if value['action'] not in {'query', 'reply', 'clarification', 'defer'}:
        raise PolicyError('invalid_action')
    if not isinstance(value['body'], str) or len(value['body']) > 10000:
        raise PolicyError('invalid_body')
    if not isinstance(value['evidence_ids'], list) or len(value['evidence_ids']) > MAX_QUERIES or any(
            not isinstance(item, str) for item in value['evidence_ids']): raise PolicyError('invalid_evidence')
    if value['action'] == 'query':
        plan = value['plan']
        exact_keys(plan, {'command', 'object'}, {'kind', 'method', 'fields', 'filters', 'rows'})
        # Structured output has required neutral fields; reject non-neutral unused
        # values instead of silently discarding potential extra instructions.
        used = {'source': {'kind', 'method'}, 'data': {'fields', 'filters', 'rows'}}.get(plan['command'], set())
        clean = {key: item for key, item in plan.items() if key in {'command', 'object'} | used}
        for key in set(plan) - set(clean):
            if plan[key] not in ('', [], 0): raise PolicyError('invalid_options')
        if value['body'] or value['reason'] or value['evidence_ids']: raise PolicyError('invalid_query')
        return {**value, 'plan': clean}
    if value['plan'] is not None: raise PolicyError('unexpected_plan')
    if value['action'] == 'defer':
        if value['reason'] not in REASONS or value['body'] or value['evidence_ids']: raise PolicyError('invalid_defer')
    elif value['reason'] or not value['body'].strip(): raise PolicyError('invalid_reply')
    return value


class TicketProcessor:
    def __init__(self, gateway, planner, reader, monotonic=time.monotonic, now=lambda: datetime.now(timezone.utc)):
        self.gateway, self.planner, self.reader = gateway, planner, reader
        self.monotonic, self.now = monotonic, now

    def process(self, lease):
        auth = {key: lease[key] for key in ['run_id', 'lease_token']}
        deadline = datetime.fromisoformat(lease['deadline_at'])
        last_renewed = self.monotonic()

        def pulse(force=False):
            nonlocal last_renewed
            if self.now() >= deadline: raise WorkerError('time_limit')
            if force or self.monotonic() - last_renewed >= 180:
                started = self.monotonic()
                if self.gateway('renew', **auth).get('outcome') != 'renewed': raise LeaseLost()
                last_renewed = started

        receipts, observations = {}, []
        scope, decision = {}, None
        try:
            scope = lease['read_scope']
            if scope.get('restriction_unresolved'): raise WorkerError('context_incomplete')
            for _ in range(MAX_QUERIES + 2):
                pulse(True)
                # Only this ticket's bounded material, frozen policy and completed
                # observations enter the model. Never pass the lease or transport.
                decision = normalize_decision(self.planner({
                    'untrusted_ticket': lease['context'], 'read_scope': scope,
                    'observations': observations, 'queries_remaining': max(0, MAX_QUERIES - lease.get('queries_used', 0) - len(observations)),
                }, pulse))
                pulse(True)
                if decision['action'] == 'query':
                    if (deadline - self.now()).total_seconds() < 300: raise WorkerError('time_limit')
                    plan = validate_plan(decision['plan'], scope)
                    reservation_started = self.monotonic()
                    reserved = self.gateway('query_reserve', **auth, request_key=str(uuid.uuid4()), plan=plan)
                    if reserved.get('outcome') != 'reserved': raise WorkerError('evidence_incomplete')
                    # Use the server-normalized plan, never the original model JSON.
                    try:
                        # Deduct the full transport duration using the local monotonic
                        # clock. A late reservation response cannot start a stale read,
                        # even if the Mac and server wall clocks differ.
                        remaining = min(60, reserved['timeout_seconds']) - (self.monotonic() - reservation_started) - 5
                        if remaining <= 0: raise WorkerError('time_limit')
                        reserved = {**reserved, 'timeout_seconds': remaining}
                        envelope, result = self.reader(reserved)
                    except WorkerError as error:
                        pulse(True)
                        self.gateway('query_complete', **auth, query_id=reserved['query_id'], result=None,
                            error=error.code if error.code in REASONS - {'context_incomplete'} else 'runtime_error')
                        raise
                    pulse()
                    receipt = self.gateway('query_complete', **auth, query_id=reserved['query_id'], result=result, error='')
                    if receipt.get('outcome') != 'recorded': raise WorkerError('evidence_incomplete')
                    receipts[reserved['query_id']] = receipt['evidence']
                    observations.append({'evidence_id': reserved['query_id'], 'collected_at': receipt['collected_at'], 'result': envelope})
                elif decision['action'] == 'defer':
                    return self.gateway('defer', **auth, reason=decision['reason'])
                else:
                    ids = decision['evidence_ids']
                    if len(set(ids)) != len(ids) or any(item not in receipts for item in ids): raise WorkerError('evidence_incomplete')
                    if decision['action'] == 'reply' and not ids: raise WorkerError('evidence_incomplete')
                    return self.gateway('reply', **auth, payload={
                        'category': 'query' if decision['action'] == 'reply' else 'clarification',
                        'body': decision['body'], 'evidence': [receipts[item] for item in ids]})
            raise WorkerError('time_limit')
        except LeaseLost:
            return {'outcome': 'skipped', 'reason': 'lease_changed'}
        except WorkerError as error:
            details = {'detail_code': error.detail_code} if error.detail_code else {}
            return self.gateway('defer', **auth, reason=error.code, **details)
        except PolicyError as error:
            code = str(error)
            if code == 'source_kind_outside_scope':
                plan = decision.get('plan', {}) if isinstance(decision, dict) else {}
                obj = plan.get('object') if isinstance(plan, dict) else None
                kinds = scope.get('source_kinds') if isinstance(scope, dict) else None
                known = kinds.get(obj, []) if isinstance(kinds, dict) else None
                objects = scope.get('objects', []) if isinstance(scope, dict) else []
                detail = 'source_type_missing' if isinstance(objects, list) and obj in objects and isinstance(known, list) and not known else 'source_type_not_authorized'
            elif code in {'object_outside_scope', 'document_outside_scope', 'business_object_outside_scope', 'scope_unresolved'}:
                detail = 'plan_outside_scope'
            else:
                detail = 'plan_invalid'
            return self.gateway('defer', **auth, reason='authorization_missing', detail_code=detail)
        except (ValueError, TypeError, KeyError, OSError):
            return self.gateway('defer', **auth, reason='runtime_error', detail_code='executor_error')


def run_queue(gateway, planner, reader):
    processor, after, handled = TicketProcessor(gateway, planner, reader), 0, 0
    for _ in range(100):
        page = gateway('list', after_id=after, limit=50)
        if not page.get('enabled'): return
        for item in page['results']:
            if handled >= 3: return
            lease = gateway('claim', ticket_id=item['id'], version=item['version'])
            if lease.get('outcome') == 'claimed':
                processor.process(lease)
                handled += 1
        if not page['has_more']: return
        next_after = page['next_after_id']
        if type(next_after) is not int or next_after <= after: raise WorkerError()
        after = next_after
    raise WorkerError('time_limit')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', required=True)
    args = parser.parse_args()
    if not (ROOT / 'release/ai-ticket-execution-boundary-enabled').is_file():
        return 1
    node = shutil.which('node')
    workspace = os.environ.get('AI_TICKET_SAP_WORKSPACE')
    if not node or not workspace: return 1
    def interrupted(signum, _frame):
        stop_children()
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        run_queue(Gateway(node), CodexPlanner(args.codex, (ROOT / 'docs/AI_TICKET_PLANNER_PROMPT.md').read_text()),
            SAPReader(node, workspace, os.environ.get('AI_TICKET_PS4_APPROVED_USER', '')))
        return 0
    except (WorkerError, ValueError, KeyError, OSError):
        print('AI_TICKET_WORKER_FAILED', file=sys.stderr)
        return 1
    finally:
        stop_children()


if __name__ == '__main__':
    raise SystemExit(main())

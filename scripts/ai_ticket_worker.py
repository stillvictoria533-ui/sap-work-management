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
from desk.ai_policy import (MAX_QUERIES, MAX_RESULT_BYTES, SOURCE_TYPE_TABLES, PolicyError, canonical,
    cli_arguments, exact_keys, source_type_cli_arguments, source_type_matches, validate_plan)
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
        metadata_read = plan.get('command') == 'source-type'
        # Metadata has its own executor-only closed plan, never the model's data policy.
        args = source_type_cli_arguments(plan, scope) if metadata_read else cli_arguments(plan, scope)
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
                    or metadata.get('command') != ('data' if metadata_read else plan['command'])
                    or metadata.get('object') != (plan['table'] if metadata_read else plan['object'])
                    or metadata.get('trust') != 'UNTRUSTED_EVIDENCE' or metadata.get('instructionAuthority') is not False):
                raise WorkerError('evidence_incomplete')
            result = {key: metadata[key] for key in ['system', 'client', 'object', 'command']}
            if metadata_read:
                try:
                    matches = source_type_matches(plan, scope, envelope)
                except PolicyError:
                    raise WorkerError('evidence_incomplete') from None
                result.update(command='source-type', object=plan['object'], source_type_matches=matches)
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
        if plan['command'] == 'source':
            kind = clean.get('kind', '')
            if not isinstance(kind, str): raise PolicyError('invalid_source_kind')
            # Compatibility is a finite semantic translation; it grants no type
            # and still passes the unchanged source/scope validation afterwards.
            kind = {'function': 'fm', 'program': 'prog'}.get(kind, kind)
            if kind not in {'', 'fm', 'prog', 'class'}: raise PolicyError('invalid_source_kind')
            clean['kind'] = kind
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
        queries_used = lease.get('queries_used', 0)
        scope, decision = {}, None

        def execute_reserved(reserved):
            nonlocal queries_used
            # Deduct the transport time before starting any signed read.
            remaining = min(60, reserved['timeout_seconds']) - (self.monotonic() - reservation_started) - 5
            try:
                if remaining <= 0: raise WorkerError('time_limit')
                envelope, result = self.reader({**reserved, 'timeout_seconds': remaining})
            except WorkerError as error:
                pulse(True)
                self.gateway('query_complete', **auth, query_id=reserved['query_id'], result=None,
                    error=error.code if error.code in REASONS - {'context_incomplete'} else 'runtime_error')
                raise
            pulse()
            receipt = self.gateway('query_complete', **auth, query_id=reserved['query_id'], result=result, error='')
            queries_used += 1
            return envelope, receipt

        def resolve_source_type(obj):
            nonlocal scope, queries_used, reservation_started
            # Only an original-title candidate can reach the fixed lookup route.
            source_type_cli_arguments({'command': 'source-type', 'object': obj, 'table': 'DD02L'}, scope)
            for _ in SOURCE_TYPE_TABLES:
                pulse(True)
                if (deadline - self.now()).total_seconds() < 300: raise WorkerError('time_limit')
                reservation_started = self.monotonic()
                reserved = self.gateway('source_type_reserve', **auth, request_key=str(uuid.uuid4()), object=obj)
                if reserved.get('outcome') != 'reserved': raise WorkerError('evidence_incomplete')
                _, receipt = execute_reserved(reserved)
                if receipt.get('outcome') != 'source_type_checked': raise WorkerError('evidence_incomplete')
                if receipt.get('queries_used') != queries_used: raise WorkerError('evidence_incomplete')
                binding = receipt.get('binding')
                if binding == 'bound':
                    resolved = receipt['read_scope']
                    kinds = resolved.get('source_kinds', {}).get(obj)
                    if not isinstance(kinds, list) or len(kinds) != 1 or kinds[0] not in {'fm', 'prog', 'class'}:
                        raise WorkerError('evidence_incomplete')
                    # The gateway may fill this one type, never change the target,
                    # business keys, system, source hash, or other source types.
                    previous = {k: v for k, v in scope.items() if k not in {'source_kinds', 'source_kind_bindings'}}
                    current = {k: v for k, v in resolved.items() if k not in {'source_kinds', 'source_kind_bindings'}}
                    expected_kinds = {**scope.get('source_kinds', {}), obj: kinds}
                    if previous != current or resolved.get('source_kinds') != expected_kinds:
                        raise WorkerError('evidence_incomplete')
                    scope = resolved
                    return
                if binding in {'ambiguous', 'unresolved'}:
                    raise WorkerError('authorization_missing', 'source_type_ambiguous' if binding == 'ambiguous' else 'source_type_missing')
                if binding != 'pending': raise WorkerError('evidence_incomplete')
            raise WorkerError('evidence_incomplete')

        reservation_started = self.monotonic()
        try:
            scope = lease['read_scope']
            if scope.get('restriction_unresolved'): raise WorkerError('context_incomplete')
            for _ in range(MAX_QUERIES + 2):
                pulse(True)
                # Only this ticket's bounded material, frozen policy and completed
                # observations enter the model. Never pass the lease or transport.
                decision = normalize_decision(self.planner({
                    'untrusted_ticket': lease['context'], 'read_scope': scope,
                    'observations': observations, 'queries_remaining': max(0, MAX_QUERIES - queries_used),
                }, pulse))
                pulse(True)
                if decision['action'] == 'query':
                    if (deadline - self.now()).total_seconds() < 300: raise WorkerError('time_limit')
                    proposed = decision['plan']
                    if proposed.get('command') == 'source' and not scope.get('source_kinds', {}).get(proposed['object']):
                        if proposed['object'] in scope.get('objects', []) and proposed['object'] not in scope.get('source_type_objects', []):
                            raise WorkerError('authorization_missing', 'source_type_missing')
                        resolve_source_type(proposed['object'])
                        if not proposed.get('kind'):
                            proposed = {**proposed, 'kind': scope['source_kinds'][proposed['object']][0]}
                    plan = validate_plan(proposed, scope)
                    reservation_started = self.monotonic()
                    reserved = self.gateway('query_reserve', **auth, request_key=str(uuid.uuid4()), plan=plan)
                    if reserved.get('outcome') != 'reserved': raise WorkerError('evidence_incomplete')
                    # Use the server-normalized plan, never the original model JSON.
                    envelope, receipt = execute_reserved(reserved)
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

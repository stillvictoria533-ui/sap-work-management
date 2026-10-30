"""Pure, fail-closed policy shared by the server and the trusted SAP executor.

Business text supplies identifiers, never executable instructions or capabilities.
The model cannot add targets to the scope frozen at administrator acceptance.
"""
import hashlib
import json
import re

SYSTEM_CLIENTS = {'DS4': '110', 'QS4': '120', 'PS4': '100'}
MAX_QUERIES = 8
QUERY_SECONDS = 60
MAX_RESULT_BYTES = 64 * 1024
IDENTIFIER = re.compile(r'^[A-Z][A-Z0-9_/$]{0,69}$')
FIELD = re.compile(r'^[A-Z][A-Z0-9_]{0,29}$')

# Data projection and key policy are intentionally closed. New business domains
# require reviewed code, not a table name suggested by a model or a SAP result.
DATA_TABLES = {
    'MARA': ('物料', 'MATNR', 'MATNR MTART MATKL MEINS LVORM MSTAE'),
    'MAKT': ('物料', 'MATNR', 'MATNR SPRAS MAKTX'),
    'MARC': ('物料', 'MATNR WERKS', 'MATNR WERKS DISMM DISPO BESKZ SOBSL LVORM'),
    'EKKO': ('采购订单', 'EBELN', 'EBELN BUKRS BSTYP BSART LIFNR EKORG EKGRP BEDAT LOEKZ'),
    'EKPO': ('采购订单', 'EBELN', 'EBELN EBELP MATNR WERKS LGORT MENGE MEINS LOEKZ ELIKZ'),
    'VBAK': ('销售订单', 'VBELN', 'VBELN AUART VKORG VTWEG SPART ERDAT'),
    'VBAP': ('销售订单', 'VBELN', 'VBELN POSNR MATNR WERKS KWMENG VRKME'),
    'LIKP': ('交货单', 'VBELN', 'VBELN LFART WADAT WADAT_IST'),
    'LIPS': ('交货单', 'VBELN', 'VBELN POSNR MATNR WERKS LGORT LFIMG VRKME'),
    'BKPF': ('会计凭证', 'BUKRS BELNR GJAHR', 'BUKRS BELNR GJAHR BLART BLDAT BUDAT'),
}
KEY_LABELS = {
    'MATNR': ('物料编码', '物料编号', '物料', 'MATNR'), 'WERKS': ('工厂', 'WERKS'),
    'EBELN': ('采购订单号', '采购订单', 'EBELN'), 'VBELN': ('销售订单号', '销售订单', '交货单号', '交货单', 'VBELN'),
    'BELNR': ('会计凭证号', '会计凭证', 'BELNR'), 'BUKRS': ('公司代码', 'BUKRS'),
    'GJAHR': ('会计年度', '年度', 'GJAHR'), 'SPRAS': ('语言代码', 'SPRAS'),
}

# Executor-only object identity checks, never model-selectable business tables.
# Each lookup is one normal, signed/identity-checked data read with an exact key.
SOURCE_TYPE_TABLES = {
    'DD02L': ('TABNAME', ('TABNAME',), 'table'),
    'TRDIR': ('NAME', ('NAME',), 'prog'),
    'TFDIR': ('FUNCNAME', ('FUNCNAME',), 'fm'),
    'SEOCLASS': ('CLSNAME', ('CLSNAME', 'CLSTYPE'), 'class'),
}


class PolicyError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def required_data_keys(obj, scope):
    """Derive from reviewed code, including for scopes accepted before this rule.

    A supplied organization/year/language limit is mandatory on every table
    that supports it. The model may choose a scoped value, never omit the limit.
    """
    _label, keys, fields = DATA_TABLES[obj]
    return set(keys.split()) | (set(fields.split()) & set(scope.get('document_keys', {})))


def exact_keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise PolicyError('invalid_fields')


def document_keys(text, objects):
    fields = set().union(*(set(DATA_TABLES[obj][2].split()) for obj in objects if obj in DATA_TABLES))
    result = {}
    for field in sorted(fields & set(KEY_LABELS)):
        labels = '|'.join(re.escape(value) for value in KEY_LABELS[field])
        values = re.findall(r'(?:' + labels + r')[\s:：=#]*["`“「]?([A-Za-z0-9_./-]{1,80})', text)
        values = sorted({value for value in values if not value.startswith(('/', '-')) and value not in objects})
        if values: result[field] = values[:20]
    return result


def same_value(value, original, field):
    # Preserve the existing document/material ID padding allowance. Organization,
    # fiscal-year and language constraints must match the accepted text exactly.
    return value == original or (field in {'MATNR', 'EBELN', 'VBELN', 'BELNR'}
        and value.isascii() and value.isdigit()
        and original.isascii() and original.isdigit() and value.lstrip('0') == original.lstrip('0'))


def object_keys(obj, keys):
    fields = set(DATA_TABLES[obj][2].split())
    return {field: values for field, values in keys.items() if field in fields}


def single_object(keys):
    """One value per field has only one possible relationship; never zip lists."""
    if any(len(values) != 1 for values in keys.values()):
        return None
    return {field: values[0] for field, values in keys.items()}


def primary_data_key(obj):
    return next(field for field in ('MATNR', 'EBELN', 'VBELN', 'BELNR') if field in DATA_TABLES[obj][1].split())


def document_objects(text, obj, objects):
    keys = object_keys(obj, document_keys(text, objects))
    singleton = single_object(keys)
    if singleton is not None:
        return [singleton]
    primary = primary_data_key(obj)
    if set(keys) == {primary}:
        return [{primary: value} for value in keys[primary]]
    records = []
    # Explicitly labelled records on separate lines/clauses preserve relations.
    # Partial records retain missing keys; prose/list order conveys no authority.
    for part in re.split(r'[\r\n;；。]+', text):
        part_keys = object_keys(obj, document_keys(part, objects))
        if not part_keys:
            continue
        record = single_object(part_keys)
        if record is None or primary not in record:
            return []
        if record not in records:
            records.append(record)
        if len(records) > 20:
            return []
    # Every extracted value must belong to a record; never discard unpaired
    # qualifiers, including a shared header that could apply to only one row.
    covered = {field: {row[field] for row in records if field in row} for field in keys}
    return records if all(covered[field] == set(values) for field, values in keys.items()) else []


def accepted_data_objects(scope, obj):
    if scope.get('schema') == 'ticket-read-scope/v1':
        # Old flattened multi-object scopes lack evidence of any pairing.
        keys = object_keys(obj, scope.get('document_keys', {}))
        if set(keys) == {primary_data_key(obj)}:
            return [{primary_data_key(obj): value} for value in keys[primary_data_key(obj)]]
        singleton = single_object(keys)
        return [singleton] if singleton is not None else []
    records = scope.get('data_objects', {}).get(obj, [])
    return records if isinstance(records, list) and all(isinstance(row, dict) for row in records) else []


def upgrade_data_scope(scope, original=None):
    if scope.get('schema') == 'ticket-read-scope/v1':
        recovered = bool(original and original.get('source_hash') == scope.get('source_hash')
                         and original.get('accepted_event_id') == scope.get('accepted_event_id'))
        records = {}
        for obj in scope['objects']:
            if obj not in DATA_TABLES:
                continue
            records[obj] = accepted_data_objects(scope, obj)
            if not records[obj] and recovered:
                source_records = original['data_objects'].get(obj, [])
                if all(all(allowed_value(value, scope, field) for field, value in row.items()) for row in source_records):
                    records[obj] = [dict(row) for row in source_records]
        scope['data_objects'] = records
        scope['schema'] = 'ticket-read-scope/v2'



def recover_original_source_kinds(scope, original):
    """Recover only an unbound type explicitly present in unchanged accepted material."""
    if (not isinstance(original, dict) or scope.get('restriction_unresolved') or original.get('restriction_unresolved')
            or scope.get('source_hash') != original.get('source_hash')
            or scope.get('accepted_event_id') != original.get('accepted_event_id')
            or scope.get('system') != original.get('system') or scope.get('client') != original.get('client')):
        return
    kinds = scope.get('source_kinds')
    if not isinstance(kinds, dict) or not isinstance(scope.get('objects'), list):
        return
    for obj, values in original.get('source_kinds', {}).items():
        if obj not in scope['objects'] or obj not in original.get('objects', []) or obj in DATA_TABLES:
            continue
        if kinds.get(obj) not in (None, []) or not isinstance(values, list) or len(values) != 1 or values[0] not in {'fm','prog','class'}:
            continue
        kinds[obj] = list(values)
        scope.setdefault('source_kind_recoveries', {})[obj] = {
            'source': 'original_accepted_material', 'accepted_event_id': scope['accepted_event_id']}


def recover_source_type_objects(scope, original):
    """Upgrade old scopes only from the same, unchanged accepted title."""
    if (scope.get('source_hash') != original.get('source_hash')
            or scope.get('accepted_event_id') != original.get('accepted_event_id')
            or scope.get('system') != original.get('system') or scope.get('client') != original.get('client')
            or scope.get('restriction_unresolved') or original.get('restriction_unresolved')):
        return
    scope['source_type_objects'] = [obj for obj in original['source_type_objects'] if obj in scope['objects']]


def complete_data_objects(scope, text, event_id):
    """Fill a missing key once on one identified accepted object, never remap it."""
    for obj in scope.get('objects', []):
        if obj not in DATA_TABLES:
            continue
        records = accepted_data_objects(scope, obj)
        required = required_data_keys(obj, scope)
        if not records or all(required <= set(row) for row in records):
            continue
        primary = primary_data_key(obj)
        candidates = document_objects(text, obj, scope['objects'])
        additions = {}
        for candidate in candidates:
            matches = [i for i, row in enumerate(records)
                       if (len(records) == 1 or primary in candidate)
                       and all(same_value(candidate[field], value, field) for field, value in row.items() if field in candidate)]
            if len(matches) != 1:
                continue
            i = matches[0]
            for field in required - set(records[i]):
                if field in candidate:
                    additions.setdefault((i, field), []).append(candidate[field])
        for (i, field), values in additions.items():
            if not all(same_value(values[0], value, field) for value in values):
                continue
            records[i][field] = values[0]
            known = scope.setdefault('document_keys', {}).setdefault(field, [])
            if values[0] not in known:
                known.append(values[0])
                known.sort()
            scope.setdefault('key_completion_events', {}).setdefault(field, event_id)
            scope.setdefault('data_object_completion_events', {}).setdefault(obj, {}).setdefault(str(i), {})[field] = event_id


def source_kinds(text, objects):
    result = {}
    labels = {'fm': r'函数(?:模块)?|function(?:\s+module)?|source\s+fm',
        'prog': r'程序|报表|report|program|source\s+prog',
        'class': r'类|class|source\s+class'}
    for obj in sorted(objects - set(DATA_TABLES)):
        kinds = set()
        if obj.startswith(('ZCL_', 'YCL_', 'CL_', 'ZIF_', 'YIF_', 'IF_')): kinds.add('class')
        for kind, label in labels.items():
            before = r'(?:' + label + r')[ \t:：]*["`“「]?' + re.escape(obj) + r'(?![A-Za-z0-9_/$])'
            # Do not associate an object's suffix with another identifier or a
            # label on the next line. A real delimiter is required after it.
            after = (r'(?<![A-Za-z0-9_/$])' + re.escape(obj) + r'(?![A-Za-z0-9_/$])[ \t:：]*[（(]?[ \t]*'
                     + r'(?:' + label + r')[ \t]*[）)]?(?=$|[\s,，.。;；:：!！?？])'
                     + r'(?![ \t:：]*["`“「]?[A-Z][A-Z0-9_/$]{2,69}(?![A-Za-z0-9_/$]))')
            if re.search(before, text, re.I) or re.search(after, text, re.I):
                kinds.add(kind)
        if kinds: result[obj] = sorted(kinds)
    return result



def has_source_read_restriction(text):
    """Do not infer source permission from a type label in explicitly restricted text."""
    negation = r'(?:禁止|严禁|不得|不允许|不要|不应|不可|无需|不必|勿|do\s+not|must\s+not|never|prohibit)'
    action = r'(?:读取|查看|查询|分析|检查|访问|下载|获取|阅读|read|view|inspect|access|fetch|query)'
    source = r'(?:源代码|源码|代码|函数|程序|类|source(?:\s+code)?|code|function|program|class)'
    return bool(re.search(negation + r'[^\n。；;]{0,12}' + action + r'[^\n。；;]{0,32}' + source, text, re.I)
                or re.search(source + r'[^\n。；;]{0,12}' + negation + r'[^\n。；;]{0,12}' + action, text, re.I))


def freeze_scope(system, title, description, acceptance_note, accepted_event_id):
    text = title + '\n' + description
    objects = set(re.findall(r'(?<![A-Za-z0-9_/$])[A-Z][A-Z0-9_/$]{2,69}(?![A-Za-z0-9_/$])', text))
    objects -= set(SYSTEM_CLIENTS)
    for table, (label, _key, _fields) in DATA_TABLES.items():
        if label in text:
            objects.add(table)
    keys = document_keys(text, objects)
    # Natural-language narrowing cannot safely be interpreted by another model.
    # Preserve the restriction and stop automatic queries instead of discarding it.
    note = re.sub(r'\n开发预计完成日期：(待评估|\d{4}-\d{2}-\d{2}) → (待评估|\d{4}-\d{2}-\d{2})$', '', acceptance_note)
    note = re.sub(r'^开始处理[：:]\s*', '', note)
    note = re.sub(r'禁止(?:写入|修改|传输)', '', note).strip(' \n\t。.!！；;')
    unresolved = bool(note and not re.fullmatch(
        r'开始处理|(?:(?:DS4|QS4|PS4)\s*)?只读(?:查询|分析)?(?:测试)?', note)) or has_source_read_restriction(text)
    return {
        'schema': 'ticket-read-scope/v2', 'accepted_event_id': accepted_event_id,
        'system': system, 'client': SYSTEM_CLIENTS.get(system, ''),
        'objects': sorted(objects)[:100], 'document_keys': keys, 'source_kinds': source_kinds(text, objects),
        # An untyped target must be named in the original title. JSON parameter
        # names, returned identifiers and follow-up text cannot add candidates.
        'source_type_objects': sorted(obj for obj in objects - set(DATA_TABLES)
            if re.search(r'(?<![A-Za-z0-9_/$])' + re.escape(obj) + r'(?![A-Za-z0-9_/$])', title))[:100],
        'data_objects': {obj: document_objects(text, obj, objects) for obj in sorted(objects & set(DATA_TABLES))},
        'data_fields': {obj: DATA_TABLES[obj][2].split() for obj in sorted(objects & set(DATA_TABLES))},
        'data_keys': {obj: sorted(required_data_keys(obj, {'document_keys': keys}))
                      for obj in sorted(objects & set(DATA_TABLES))},
        'source_hash': digest([system, title, description, acceptance_note]),
        'restriction_unresolved': unresolved,
    }


def allowed_value(value, scope, field):
    values = scope.get('document_keys', {}).get(field, [])
    return any(same_value(value, original, field) for original in values)



def validate_catalog_attestation(scope, obj, kind, envelope):
    """Validate bounded operator-supplied metadata, never a model instruction.

    This is not a signature: only the trusted, explicitly requested operator
    command may accept an attestation after an independent SAP identity check.
    Production catalogue search remains disabled.
    """
    if scope.get('system') not in {'DS4', 'QS4'} or scope.get('client') != SYSTEM_CLIENTS.get(scope.get('system')):
        raise PolicyError('catalog_system_not_allowed')
    # SAP catalogue search uses LIKE, where underscore is a wildcard.
    if not isinstance(obj, str) or not re.fullmatch(r'[A-Z][A-Z0-9]{2,69}', obj):
        raise PolicyError('catalog_object_not_exact')
    if obj not in scope.get('objects', []) or obj in DATA_TABLES:
        raise PolicyError('object_outside_scope')
    kinds = scope.get('source_kinds')
    if not isinstance(kinds, dict) or not isinstance(kind, str) or kind not in {'fm', 'prog', 'class'} or kinds.get(obj):
        raise PolicyError('source_kind_already_bound')
    exact_keys(envelope, {'pattern', 'results', '_evidence'})
    evidence = envelope['_evidence']
    exact_keys(evidence, {'trust', 'instructionAuthority', 'system', 'client', 'user', 'command', 'object', 'contentSha256'})
    if (evidence['trust'] != 'UNTRUSTED_EVIDENCE' or evidence['instructionAuthority'] is not False
            or evidence['system'] != scope['system'] or evidence['client'] != scope['client']
            or evidence['command'] != 'search' or evidence['object'] != obj or envelope['pattern'] != obj
            or not isinstance(evidence['user'], str) or not evidence['user']):
        raise PolicyError('catalog_evidence_mismatch')
    content = {'pattern': envelope['pattern'], 'results': envelope['results']}
    if not isinstance(evidence['contentSha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', evidence['contentSha256']) or digest(content) != evidence['contentSha256']:
        raise PolicyError('catalog_evidence_mismatch')
    results = envelope['results']
    exact_keys(results, {'table', 'prog', 'fm', 'class'})
    matches = []
    for category, rows in results.items():
        if not isinstance(rows, list) or len(rows) > 2:
            raise PolicyError('catalog_evidence_invalid')
        for row in rows:
            if not isinstance(row, dict) or row.get('name') != obj:
                raise PolicyError('catalog_evidence_invalid')
            if category == 'class' and row.get('kind') != 'class':
                raise PolicyError('catalog_evidence_invalid')
            matches.append(category)
    if matches != [kind]:
        raise PolicyError('catalog_type_ambiguous')
    return evidence['contentSha256']


def validate_plan(plan, scope):
    exact_keys(plan, {'command', 'object'}, {'kind', 'method', 'fields', 'filters', 'rows'})
    if scope.get('schema') not in {'ticket-read-scope/v1', 'ticket-read-scope/v2'} or scope.get('restriction_unresolved'):
        raise PolicyError('scope_unresolved')
    system = scope.get('system')
    if system not in SYSTEM_CLIENTS or scope.get('client') != SYSTEM_CLIENTS[system]:
        raise PolicyError('system_missing')
    command, obj = plan['command'], plan['object']
    if command not in {'ddic', 'source', 'data', 'tcode'}:
        raise PolicyError('operation_not_allowed')
    if not isinstance(obj, str) or not IDENTIFIER.fullmatch(obj) or obj not in scope.get('objects', []):
        raise PolicyError('object_outside_scope')
    result = {'command': command, 'object': obj}
    if command == 'source':
        if set(plan) - {'command', 'object', 'kind', 'method'}:
            raise PolicyError('invalid_source_options')
        kind = plan.get('kind')
        if kind not in {'fm', 'prog', 'class'}:
            raise PolicyError('invalid_source_kind')
        if kind not in scope.get('source_kinds', {}).get(obj, []):
            raise PolicyError('source_kind_outside_scope')
        result['kind'] = kind
        if plan.get('method'):
            method = plan['method']
            if kind != 'class' or not isinstance(method, str) or not FIELD.fullmatch(method) or method not in scope['objects']:
                raise PolicyError('method_outside_scope')
            result['method'] = method
        if system == 'PS4' and kind == 'class' and not result.get('method'):
            raise PolicyError('production_method_required')
    elif command == 'data':
        if set(plan) - {'command', 'object', 'fields', 'filters', 'rows'} or obj not in DATA_TABLES:
            raise PolicyError('data_table_not_allowed')
        _label, _keys, field_list = DATA_TABLES[obj]
        fields, filters, rows = plan.get('fields'), plan.get('filters'), plan.get('rows', 20)
        if not isinstance(fields, list) or not 1 <= len(fields) <= 16 or any(
            not isinstance(field, str) or field not in field_list.split() for field in fields
        ) or len(set(fields)) != len(fields):
            raise PolicyError('fields_not_allowed')
        if not isinstance(filters, list) or not 1 <= len(filters) <= 5:
            raise PolicyError('filters_required')
        seen = set()
        clean_filters = []
        for item in filters:
            exact_keys(item, {'field', 'value'})
            field, value = item['field'], item['value']
            if not isinstance(field, str) or field not in field_list.split() or field in seen:
                raise PolicyError('filter_field_not_allowed')
            if not isinstance(value, str) or not 1 <= len(value) <= 80 or not allowed_value(value, scope, field):
                raise PolicyError('document_outside_scope')
            seen.add(field)
            clean_filters.append({'field': field, 'value': value})
        required = required_data_keys(obj, scope)
        if not required <= seen:
            raise PolicyError('required_filters_missing')
        if not any(required <= set(record) and all(
                item['field'] in record and same_value(item['value'], record[item['field']], item['field']) for item in clean_filters)
                for record in accepted_data_objects(scope, obj)):
            raise PolicyError('business_object_outside_scope')
        if isinstance(rows, bool) or not isinstance(rows, int) or not 1 <= rows <= 50:
            raise PolicyError('bounded_key_required')
        result.update(fields=fields, filters=sorted(clean_filters, key=lambda item: item['field']), rows=rows)
    elif set(plan) != {'command', 'object'}:
        raise PolicyError('invalid_options')
    return result


def cli_arguments(plan, scope):
    plan = validate_plan(plan, scope)
    args = [plan['command']]
    if plan['command'] == 'source':
        args += [plan['kind']]
    args += [plan['object'], '--conn', scope['system']]
    if plan.get('method'):
        args += ['--method', plan['method']]
    if plan['command'] == 'data':
        args += ['--fields', ','.join(plan['fields']), '--rows', str(plan['rows'])]
        for item in plan['filters']:
            args += ['--filter', item['field'] + '=' + item['value']]
    return args


def source_type_cli_arguments(plan, scope):
    """A separate closed protocol; validate_plan deliberately still rejects it."""
    exact_keys(plan, {'command', 'object', 'table'})
    obj, table, system = plan['object'], plan['table'], scope.get('system')
    if (scope.get('schema') not in {'ticket-read-scope/v1', 'ticket-read-scope/v2'}
            or scope.get('restriction_unresolved')):
        raise PolicyError('scope_unresolved')
    if system not in SYSTEM_CLIENTS or scope.get('client') != SYSTEM_CLIENTS[system]:
        raise PolicyError('system_missing')
    if (plan['command'] != 'source-type' or not isinstance(obj, str) or not IDENTIFIER.fullmatch(obj)
            or obj not in scope.get('objects', []) or obj not in scope.get('source_type_objects', []) or obj in DATA_TABLES):
        raise PolicyError('object_outside_scope')
    if not isinstance(table, str) or table not in SOURCE_TYPE_TABLES:
        raise PolicyError('metadata_table_not_allowed')
    key, fields, _kind = SOURCE_TYPE_TABLES[table]
    args = ['data', table, '--conn', system, '--fields', ','.join(fields), '--rows', '2', '--filter', key + '=' + obj]
    if table == 'DD02L': args += ['--filter', 'AS4LOCAL=A']
    return args


def source_type_matches(plan, scope, envelope):
    source_type_cli_arguments(plan, scope)
    exact_keys(envelope, {'table', 'row_limit', 'row_count', 'rows', '_evidence'})
    metadata = envelope['_evidence']
    content = {key: value for key, value in envelope.items() if key != '_evidence'}
    table, obj = plan['table'], plan['object']
    if (not isinstance(metadata, dict) or metadata.get('trust') != 'UNTRUSTED_EVIDENCE'
            or metadata.get('instructionAuthority') is not False
            or metadata.get('system') != scope['system'] or metadata.get('client') != scope['client']
            or metadata.get('command') != 'data' or metadata.get('object') != table
            or metadata.get('contentSha256') != digest(content)
            or envelope['table'] != table or type(envelope['row_limit']) is not int or envelope['row_limit'] != 2
            or not isinstance(envelope['rows'], list) or len(envelope['rows']) > 2
            or type(envelope['row_count']) is not int or envelope['row_count'] != len(envelope['rows'])):
        raise PolicyError('metadata_evidence_invalid')
    key, fields, kind = SOURCE_TYPE_TABLES[table]
    matches = []
    for row in envelope['rows']:
        exact_keys(row, set(fields))
        if row[key] != obj or any(not isinstance(value, str) for value in row.values()):
            raise PolicyError('metadata_evidence_invalid')
        matches.append('interface' if table == 'SEOCLASS' and row['CLSTYPE'] == '1' else kind)
    return matches

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


def source_kinds(text, objects):
    result = {}
    labels = {'fm': r'函数(?:模块)?|function(?:\s+module)?|source\s+fm',
        'prog': r'程序|报表|report|program|source\s+prog',
        'class': r'类|class|source\s+class'}
    for obj in sorted(objects - set(DATA_TABLES)):
        kinds = set()
        if obj.startswith(('ZCL_', 'YCL_', 'CL_', 'ZIF_', 'YIF_', 'IF_')): kinds.add('class')
        for kind, label in labels.items():
            if re.search(r'(?:' + label + r')[\s:：]*["`“「]?' + re.escape(obj) + r'(?![A-Za-z0-9_/$])', text, re.I):
                kinds.add(kind)
        if kinds: result[obj] = sorted(kinds)
    return result


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
        r'开始处理|(?:(?:DS4|QS4|PS4)\s*)?只读(?:查询|分析)?(?:测试)?', note))
    return {
        'schema': 'ticket-read-scope/v1', 'accepted_event_id': accepted_event_id,
        'system': system, 'client': SYSTEM_CLIENTS.get(system, ''),
        'objects': sorted(objects)[:100], 'document_keys': keys, 'source_kinds': source_kinds(text, objects),
        'data_fields': {obj: DATA_TABLES[obj][2].split() for obj in sorted(objects & set(DATA_TABLES))},
        'data_keys': {obj: sorted(required_data_keys(obj, {'document_keys': keys}))
                      for obj in sorted(objects & set(DATA_TABLES))},
        'source_hash': digest([system, title, description, acceptance_note]),
        'restriction_unresolved': unresolved,
    }


def allowed_value(value, scope, field):
    values = scope.get('document_keys', {}).get(field, [])
    if value in values:
        return True
    return value.isascii() and value.isdigit() and any(
        original.isascii() and original.isdigit() and value.lstrip('0') == original.lstrip('0')
        for original in values)


def validate_plan(plan, scope):
    exact_keys(plan, {'command', 'object'}, {'kind', 'method', 'fields', 'filters', 'rows'})
    if scope.get('schema') != 'ticket-read-scope/v1' or scope.get('restriction_unresolved'):
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
        if not required_data_keys(obj, scope) <= seen:
            raise PolicyError('required_filters_missing')
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

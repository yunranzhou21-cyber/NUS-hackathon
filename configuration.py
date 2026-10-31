"""Shared input contract. Preserve booleans and keep identifier types distinct."""
import re
from datetime import date, datetime

TEXT_FIELDS = {'make', 'model', 'serial_number', 'line_number', 'manufacturer_serial_number'}
COMPLETION_FIELD = re.compile(
    r'([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)_(\d{4})_(\d{2})_(\d{2})_(actions_)?completed')


def supported_field(key):
    return isinstance(key, str) and (key in TEXT_FIELDS or key == 'external_conditions'
                                    or COMPLETION_FIELD.fullmatch(key) is not None)


def normalize_configuration(configuration):
    if not isinstance(configuration, dict) or len(configuration) > 30:
        raise ValueError('Invalid configuration')
    result = {}
    for key, value in configuration.items():
        if not supported_field(key):
            raise ValueError('Unsupported configuration field')
        if key in TEXT_FIELDS:
            if not isinstance(value, str) or len(value) > 150:
                raise ValueError('Invalid identifier')
            if value.strip():
                result[key] = value.strip()
        elif key == 'external_conditions':
            if not isinstance(value, dict) or len(value) > 20:
                raise ValueError('Invalid condition answers')
            if any(not isinstance(k, str) or len(k) > 200
                   or not (type(v) is bool or isinstance(v, str) and v.lower() in ('yes', 'no'))
                   for k, v in value.items()):
                raise ValueError('Invalid condition answer')
            result[key] = dict(value)
        else:
            match = COMPLETION_FIELD.fullmatch(key)
            date(int(match[2]), int(match[3]), int(match[4]))
            if type(value) is not bool:
                raise ValueError('Completion must be a boolean')
            result[key] = value
    return result


def dated_condition(document, document_id, kind):
    """Bind an answer to the exact bulletin number, date and action semantics."""
    match = re.search(r'\bdated\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})', document, re.I)
    if not match:
        return None, None
    try:
        issued = datetime.strptime(re.sub(r'\s+', ' ', match[1]), '%B %d, %Y').date()
    except ValueError:
        return None, None
    field = None
    if kind in ('MODIFIED_PER', 'ACTIONS_DONE'):
        suffix = 'actions_completed' if kind == 'ACTIONS_DONE' else 'completed'
        field = f"{document_id.replace('-', '_')}_{issued:%Y_%m_%d}_{suffix}"
    return issued.isoformat(), field

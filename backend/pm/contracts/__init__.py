"""Strict, deliberately small JSON Schema vocabulary; no external dependency."""
import json
from pathlib import Path


def validate(value, schema, path='$'):
    types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'number': (int, float), 'boolean': bool, 'null': type(None)}
    kind = schema.get('type')
    if kind and (not isinstance(value, types[kind]) or (kind in ('integer', 'number') and isinstance(value, bool))):
        raise ValueError(path + ': type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path + ': enum')
    if isinstance(value, dict):
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError(path + ': required')
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError(path + ': additionalProperties')
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], path + '.' + key)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', len(value)):
            raise ValueError(path + ': items length')
        for i, item in enumerate(value):
            validate(item, schema.get('items', {}), path + '[' + str(i) + ']')
    if isinstance(value, str):
        if len(value.strip()) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', len(value)):
            raise ValueError(path + ': string length')
    if kind in ('integer', 'number') and (value < schema.get('minimum', value) or value > schema.get('maximum', value)):
        raise ValueError(path + ': range')
    return value


def validate_contract(name, value, direction):
    if not name.replace('_', '').isalnum() or direction not in ('input', 'output'):
        raise ValueError('Unknown contract')
    schema = json.loads(Path(__file__).with_name(name + '.json').read_text(encoding='utf-8'))
    return validate(value, schema[direction])

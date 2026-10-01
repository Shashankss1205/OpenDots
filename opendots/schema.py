"""Small strict JSON value schema for trusted extension contracts."""
import json


def validate(value, schema, path='arguments'):
    if 'anyOf' in schema:
        for alternative in schema['anyOf']:
            try:
                validate(value, alternative, path)
                return
            except ValueError:
                pass
        raise ValueError(f'{path} does not match an allowed variant')
    kind=schema.get('type')
    valid={'string':lambda v:isinstance(v,str),'boolean':lambda v:type(v) is bool,
           'integer':lambda v:type(v) is int,'number':lambda v:type(v) in (int,float),
           'object':lambda v:isinstance(v,dict),'array':lambda v:isinstance(v,list),
           'null':lambda v:v is None}
    if kind not in valid or not valid[kind](value): raise ValueError(f'{path} must be {kind}')
    if 'enum' in schema and not any(type(value) is type(v) and value==v for v in schema['enum']):
        raise ValueError(f'{path} is not an allowed value')
    if kind=='object':
        properties=schema.get('properties',{})
        if set(schema.get('required',[]))-set(value): raise ValueError(f'{path} is missing required fields')
        if schema.get('additionalProperties',False) is False and set(value)-set(properties):
            raise ValueError(f'{path} contains unknown fields')
        for key,item in value.items():
            if key in properties: validate(item,properties[key],path+'.'+key)
    elif kind=='array':
        for index,item in enumerate(value):validate(item,schema['items'],f'{path}[{index}]')
    if kind in {'number','integer'}:
        if value<schema.get('minimum',float('-inf')) or value>schema.get('maximum',float('inf')):
            raise ValueError(f'{path} is outside its configured range')
    if len(json.dumps(value,allow_nan=False).encode())>256000:raise ValueError(f'{path} exceeds argument size limit')

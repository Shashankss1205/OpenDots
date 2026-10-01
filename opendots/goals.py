"""Owner-defined observable success conditions, independent of model claims."""
import json
from .tools import ToolRegistry


def evaluate(target):
    results=[]
    for condition in target.success_conditions:
        text=ToolRegistry.read_file(target,{'path':condition['path']})['content']
        observed=text
        if condition.get('format','text') == 'json':
            observed=json.loads(text)
            pointer=condition.get('pointer','')
            if pointer and not pointer.startswith('/'):
                raise ValueError('Goal JSON pointer must be empty or start with /')
            for part in pointer.split('/')[1:]:
                key=part.replace('~1','/').replace('~0','~')
                observed=observed[int(key)] if isinstance(observed,list) else observed[key]
        if 'equals' in condition:
            passed=type(observed) is type(condition['equals']) and observed == condition['equals']
        elif 'minimum' in condition:
            passed=type(observed) in (int,float) and observed >= condition['minimum']
        else:
            raise ValueError('Goal condition needs equals or minimum')
        results.append({'name':condition['name'],'passed':passed,'observed':observed})
    return results

import json
from pathlib import Path


def document():
    directory=Path(__file__).resolve().parent
    result=json.loads((directory/'openapi.json').read_text(encoding='utf-8'))
    registration=json.loads((directory/'registration-openapi.json').read_text(encoding='utf-8'))
    def adapt(value):
        if isinstance(value,list):return [adapt(item) for item in value]
        if isinstance(value,dict):
            if value.get('$ref','').startswith('#/components/responses/'):
                name=value['$ref'].split('/')[-1]
                return adapt({**registration['components']['responses'][name],**{k:v for k,v in value.items() if k!='$ref'}})
            output={key:adapt(item) for key,item in value.items()}
            if '$ref' in output and output['$ref'].startswith('#/components/'):
                category,name=output['$ref'].split('/')[-2:]
                output['$ref']=f'#/components/{category}/Registration{name}'
            if 'operationId' in output:output['operationId']='registration_'+output['operationId']
            if 'security' in output:output['security']=[{'BearerKey':[]}]
            if 'tags' in output:output['tags']=['注册 · '+tag for tag in output['tags']]
            return output
        return value
    result['paths'].update({'/registration'+path:adapt(value) for path,value in registration['paths'].items()})
    for category,values in registration['components'].items():
        if category!='securitySchemes':
            result['components'].setdefault(category,{}).update({'Registration'+name:adapt(value) for name,value in values.items()})
    result['tags'].extend({**tag,'name':'注册 · '+tag['name']} for tag in registration['tags'])
    result['info'].update(title='星桥统一开放 API',version='2.0.0')
    result['x-registration-order-states']=registration.get('x-order-states',{})
    result['x-registration-item-states']=registration.get('x-item-states',{})
    return result

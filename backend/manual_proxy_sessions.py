"""Supplier-specific manual proxy credentials, generated once per order."""
import re
import secrets
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .common import decrypt, integer, require, uid

LIMITS = {'iproyal': (60, 1, 10080), 'cliproxy': (15, 1, 120), '1024proxy': (120, 1, 120), '711proxy': (180, 5, 180), 'other': (60, 1, 10080)}


def session_minutes(resource, endpoint=None):
    vendor=resource.get('vendor') or 'other'
    default, minimum, maximum=LIMITS.get(vendor,LIMITS['other'])
    value=resource.get('session_minutes')
    if value is not None:
        return integer(value,minimum,maximum,f'粘性会话有效期须为 {minimum}～{maximum} 分钟')
    # Existing resources retain the duration embedded in their saved address.
    parsed=urlsplit(endpoint if endpoint is not None else decrypt(resource['endpoint_enc']))
    username,password=unquote(parsed.username or ''),unquote(parsed.password or '')
    if vendor=='iproyal':
        match=re.search(r'(?i)_lifetime-(\d+(?:\.\d+)?)([smhd])(?:_|$)',password)
        if match:
            minutes=float(match[1])*{'s':1/60,'m':1,'h':60,'d':1440}[match[2].lower()]
            if minutes.is_integer() and minimum<=minutes<=maximum:return int(minutes)
    elif vendor in ('cliproxy','1024proxy','711proxy'):
        match=re.search(r'(?i)-'+('sessTime' if vendor=='711proxy' else 't')+r'-(\d+)(?:-|$)',username)
        if match and minimum<=int(match[1])<=maximum:return int(match[1])
    return default


def validate(resource, endpoint):
    minutes=session_minutes(resource,endpoint)
    if resource.get('ip_mode','dynamic')=='dynamic':
        parsed=urlsplit(endpoint)
        require(parsed.username and parsed.password,'动态代理需要填写账号和密码')
        if resource.get('vendor') not in ('iproyal','cliproxy','1024proxy','711proxy'):
            credentials=unquote(parsed.username or '')+':'+unquote(parsed.password or '')
            require(all(token in credentials for token in ('{country}','{session}','{minutes}')),
                    '其他动态代理请在账号或密码中使用 {country}、{session}、{minutes} 占位符')
    return minutes


def session_reference(resource, endpoint):
    if resource.get('ip_mode','dynamic')!='dynamic':return ''
    parsed=urlsplit(endpoint)
    vendor=resource.get('vendor') or 'other'
    if vendor=='iproyal':match=re.search(r'_session-([^_]+)',unquote(parsed.password or ''))
    elif vendor=='711proxy':match=re.search(r'-session-([^-]+)',unquote(parsed.username or ''))
    elif vendor in ('cliproxy','1024proxy'):match=re.search(r'-sid-([^-]+)',unquote(parsed.username or ''))
    else:return ''
    return match[1] if match else ''


def endpoint_for_order(resource, country):
    endpoint=decrypt(resource['endpoint_enc'])
    if resource.get('ip_mode','dynamic')!='dynamic':return endpoint,3600
    require(isinstance(country,str) and re.fullmatch('[A-Z]{2}',country),'代理国家无效')
    minutes=validate(resource,endpoint)
    parsed=urlsplit(endpoint)
    username,password=unquote(parsed.username or ''),unquote(parsed.password or '')
    vendor=resource.get('vendor') or 'other'
    session=uid()[:8]
    if vendor=='iproyal':
        password=re.sub(r'(?i)_(?:country|session|lifetime)-[^_]*','',password)
        duration=f'{minutes//60}h' if minutes%60==0 else f'{minutes}m'
        password+=f'_country-{country.lower()}_session-{session}_lifetime-{duration}'
    elif vendor in ('cliproxy','1024proxy'):
        username=re.sub(r'(?i)-(?:region|sid|t)-[^-]*','',username)
        username+=f'-region-{country}-sid-{session}-t-{minutes}'
    elif vendor=='711proxy':
        session=''.join(str(secrets.randbelow(10)) for _ in range(8))
        auto=re.search(r'(?i)-sessAuto-([^-]*)',username)
        username=re.sub(r'(?i)-(?:region|session|sessTime|sessAuto)-[^-]*','',username)
        username+=f'-region-{country}-session-{session}-sessTime-{minutes}'
        if auto:username+='-sessAuto-'+auto[1]
    else:
        for key,value in {'country':country,'session':session,'minutes':str(minutes)}.items():
            username=username.replace('{'+key+'}',value);password=password.replace('{'+key+'}',value)
    host=parsed.hostname
    if ':' in host:host='['+host+']'
    netloc=f'{quote(username,safe="")}:{quote(password,safe="")}@{host}:{parsed.port}'
    return urlunsplit((parsed.scheme,netloc,'','','')),minutes*60

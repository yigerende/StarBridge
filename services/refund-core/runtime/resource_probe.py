"""Read-only resource checks. Never return upstream prose, URLs or headers."""
import ipaddress
import time
from urllib.parse import urlencode
from curl_cffi import requests
from manager_oauth import upstream

def failure(status=0, network=0):
    if status == 401:return "unauthorized"
    if status == 403:return "forbidden"
    if status == 407:return "proxy_auth"
    if status == 429:return "rate_limited"
    if network == 77:return "tls_config"
    if network == 60:return "tls_verify"
    if network in (5,6):return "dns"
    if network == 28:return "timeout"
    if network == 7:return "connect"
    return "http" if status else "network"

def capacity(body):
    if not isinstance(body,dict) or not isinstance(body.get("seat_capacity"),list):return None
    result={}
    assigned=body.get("assigned") or {}
    for entry in body["seat_capacity"]:
        if not isinstance(entry,dict):continue
        kind=entry.get("type")
        if not isinstance(kind,str):return None
        kind=kind.strip().lower()
        key={"default":"standard","standard":"standard","prolite":"premium","premium":"premium","5x":"premium"}.get(kind)
        if not key:continue
        paid,held,available=entry.get("paid"),entry.get("held",0),entry.get("available")
        if any(type(n) is not int or not 0<=n<=1000000 for n in (paid,held,available)):return None
        used=assigned.get("default" if key=="standard" else "prolite") if isinstance(assigned,dict) else None
        total=max(0,paid-held)
        if available>total:return None
        if type(used) is not int or used<0:used=max(0,total-available)
        result[key]={"total":total,"remaining":available,"used":used,"held":held}
    return result or None

def check_mother(job, factory, upstream_error):
    started=time.monotonic(); mother=None
    result={"valid":False,"http_status":0,"code":"shape"}
    try:
        mother=factory(job)
        response=mother.call("GET","/users?offset=0&limit=1",timeout=12)
        result["http_status"]=response.status_code
        data=response.json()
        result["valid"]=isinstance(data,dict) and any(isinstance(data.get(k),list) for k in ("items","users","members","data"))
        if result["valid"]:
            result["code"]="ok"
            try:
                response=mother.session.request("GET","https://chatgpt.com/backend-api/subscriptions?"+urlencode({"account_id":job["mother"]["team_id"]}),headers=mother.headers,timeout=12,allow_redirects=False)
                if 200<=response.status_code<300:
                    seats=capacity(response.json())
                    if seats:result["seats"]=seats
                    else:result["capacity_code"]="shape"
                else:result["capacity_code"]=failure(response.status_code)
            except Exception as exc:result["capacity_code"]=failure(network=getattr(exc,"code",0))
    except upstream_error as exc:
        result["http_status"]=exc.status
        result["code"]="identity" if exc.code=="mother_identity" else failure(exc.status,exc.network_code)
    except (TypeError,ValueError):result["code"]="shape"
    except Exception:result["code"]="runtime"
    finally:
        if mother:mother.session.close()
    result["latency_ms"]=int((time.monotonic()-started)*1000)
    return result

def check_proxy(job):
    started=time.monotonic()
    result={"valid":False,"http_status":0,"code":"network"}
    proxy=job.get("proxy")
    if not proxy:return {**result,"code":"missing","latency_ms":0}
    with requests.Session(impersonate="chrome",proxies=upstream._cffi_proxies(proxy)) as session:
        for target in ("https://api64.ipify.org?format=json","https://api.ipify.org?format=json"):
            try:
                response=session.get(target,timeout=10,allow_redirects=False)
                result["http_status"]=response.status_code
                if response.status_code!=200:
                    result["code"]=failure(response.status_code);continue
                result["ip_address"]=str(ipaddress.ip_address(response.json().get("ip","")))
                result.update(valid=True,code="ok");break
            except (ValueError,TypeError):result["code"]="shape"
            except Exception as exc:result["code"]=failure(network=getattr(exc,"code",0))
    result["latency_ms"]=int((time.monotonic()-started)*1000)
    return result

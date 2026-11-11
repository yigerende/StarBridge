export type Period = 'day' | 'week' | 'month' | 'year';
export const periods: {key:Period;label:string}[] = [{key:'day',label:'每日'},{key:'week',label:'每周'},{key:'month',label:'每月'},{key:'year',label:'每年'}];
export const todayBeijing = () => new Date(Date.now()+8*3600000).toISOString().slice(0,10);
export const beijingTime = (value:string) => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '—';
export function shiftPeriod(anchor:string,period:Period,direction:number):string {
 const d=new Date(`${anchor}T00:00:00Z`);
 if(!Number.isFinite(d.getTime()))return todayBeijing();
 if(period==='year'){d.setUTCMonth(0,1);d.setUTCFullYear(d.getUTCFullYear()+direction)}
 else if(period==='month'){d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()+direction)}
 else d.setUTCDate(d.getUTCDate()+direction*(period==='week'?7:1));
 const result=d.toISOString().slice(0,10);
 return result<'1900-01-01'||result>'9998-12-31'?anchor:result;
}

export const rangePresets=[{id:'all',label:'全部'},{id:'today',label:'今天'},{id:'yesterday',label:'昨天'},{id:'week',label:'本周'},{id:'month',label:'本月'},{id:'custom',label:'自定义'}];
export function presetRange(id:string, today=todayBeijing()):{start:string;end:string}{
 if(id==='all'||id==='custom')return {start:'',end:''};
 const d=new Date(today+'T00:00:00Z');
 if(id==='yesterday'){d.setUTCDate(d.getUTCDate()-1);const day=d.toISOString().slice(0,10);return {start:day,end:day}}
 if(id==='week')d.setUTCDate(d.getUTCDate()-((d.getUTCDay()+6)%7));
 if(id==='month')d.setUTCDate(1);
 return {start:d.toISOString().slice(0,10),end:today};
}

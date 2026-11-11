export function duration(value:unknown):string {
 if(typeof value!=='number'||!Number.isFinite(value)||value<0)return '未记录';
 const seconds=Math.floor(value),days=Math.floor(seconds/86400),hours=Math.floor(seconds%86400/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
 if(days)return `${days}天 ${hours}小时 ${minutes}分`;
 if(hours)return `${hours}小时 ${minutes}分 ${rest}秒`;
 if(minutes)return `${minutes}分 ${rest}秒`;
 return `${rest}秒`;
}

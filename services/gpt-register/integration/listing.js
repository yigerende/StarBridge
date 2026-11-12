"use strict";
const {fail}=require('../src/platform/security');
function orderBy(query,columns,fallback,stable='id') {
 const field=query.sort_by||fallback, direction=query.sort_dir||'desc';
 fail(Object.hasOwn(columns,field)&&['asc','desc'].includes(direction),'排序参数无效');
 return `${columns[field]} ${direction.toUpperCase()} NULLS LAST, ${stable.split(',').map(k=>k.trim()+' '+direction.toUpperCase()).join(', ')}`;
}
module.exports={orderBy};

function pageRows(rows,query={}) {
 const limit=Number(query.limit||10),requested=Number(query.offset||0);
 fail(Number.isSafeInteger(limit)&&limit>0&&limit<=500&&Number.isSafeInteger(requested)&&requested>=0&&requested<=1000000,'分页参数无效');
 const total=rows.length,offset=Math.min(requested,Math.max(0,Math.ceil(total/limit)-1)*limit);
 return {items:rows.slice(offset,offset+limit),total,offset,limit};
}
function sortRows(rows,query,columns,fallback){
 const field=query.sort_by||fallback,dir=query.sort_dir||'desc';fail(Object.hasOwn(columns,field)&&['asc','desc'].includes(dir),'排序参数无效');
 return rows.sort((a,b)=>{let x=columns[field](a),y=columns[field](b);if(x==null||x==='')return y==null||y===''?String(a.id).localeCompare(String(b.id)):1;if(y==null||y==='')return -1;const delta=typeof x==='number'?x-y:String(x).localeCompare(String(y),undefined,{numeric:true});return (dir==='asc'?1:-1)*(delta||String(a.id||a.countryCode).localeCompare(String(b.id||b.countryCode)))});
}
module.exports.pageRows=pageRows;module.exports.sortRows=sortRows;

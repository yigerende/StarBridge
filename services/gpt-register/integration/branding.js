'use strict';
const fs=require('node:fs');
const path=require('node:path');
const defaultIcon='data:image/svg+xml;base64,'+fs.readFileSync(path.join(__dirname,'starbridge-icon.svg')).toString('base64');
function unifiedBrand(site={}) {
  return {english_name:site.english_name || 'StarBridge',
    tagline:site.tagline || '连接每一份服务，让订阅与注册清晰有序。',
    support_url:site.support_url || '',terms:site.terms || '',icon:site.icon || defaultIcon};
}
module.exports={unifiedBrand,defaultIcon};

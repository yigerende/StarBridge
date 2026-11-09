'use strict';
const { Database } = require('../src/platform/db');
async function main() {
  if(process.env.XUFU_INTEGRATION!=='1')throw new Error('Unified integration mode is required');
  const db=new Database(process.env.DATABASE_URL);
  try {await db.initialize();console.log('Registration schema initialized; no worker or runtime was started.');}
  finally {await db.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});

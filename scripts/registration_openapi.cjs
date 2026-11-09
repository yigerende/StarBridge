'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { openapi } = require('../services/gpt-register/src/platform/openapi');
fs.writeFileSync(path.join(__dirname, '../backend/registration-openapi.json'), JSON.stringify(openapi, null, 2) + '\n');

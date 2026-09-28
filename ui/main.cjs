'use strict';
const { app, dialog } = require('electron');
const external = process.argv.includes('--external');
require(external ? './external-app.cjs' : './panel-app.cjs').start().catch(() => {
  dialog.showErrorBox(external ? 'ControlCoding External' : 'ControlCoding Panel', external ? 'External mode could not start. Check its isolated runtime, Core, and profile paths.' : 'The local panel could not start. Check its runtime and profile paths.');
  app.quit();
});

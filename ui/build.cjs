/* Build only into an explicitly supplied external directory; no dev web server. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {physicalDirectory,overlaps} = require('./profile-paths.cjs');
async function build() {
  const [modules, output, parserPackage] = process.argv.slice(2).map(p => physicalDirectory(p));
  const root = path.resolve(__dirname, '..');
  if (!modules || !output || overlaps(output,physicalDirectory(root)) || overlaps(output,modules) || (parserPackage && overlaps(output,parserPackage)))
    throw new Error('Provide external module and output directories');
  const esbuild = require(path.join(modules, 'esbuild'));
  fs.mkdirSync(output, { recursive: true });
  const renderer = await esbuild.build({ entryPoints: [path.join(__dirname, 'src/app.tsx')],
    outfile: path.join(output, 'app.js'), bundle: true, platform: 'browser',
    nodePaths: [modules], jsx: 'automatic', minify: true, metafile: true,
    define: { 'process.env.NODE_ENV': '"production"' }, logLevel: 'warning' });
  const externalRenderer = await esbuild.build({ entryPoints: [path.join(__dirname, 'src/external.tsx')],
    outfile: path.join(output, 'external.js'), bundle: true, platform: 'browser',
    nodePaths: [modules], jsx: 'automatic', minify: true, metafile: true,
    define: { 'process.env.NODE_ENV': '"production"' }, logLevel: 'warning' });
  const parser = parserPackage || path.join(modules, '@babel/parser');
  const available = fs.existsSync(path.join(parser, 'package.json'));
  if(available) {
    const metadata=JSON.parse(fs.readFileSync(path.join(parser,'package.json'),'utf8'));
    if(metadata.name!=='@babel/parser'||metadata.version!=='7.29.7'||metadata.license!=='MIT')throw Error('Unsupported parser');
    await esbuild.build({entryPoints:[path.join(__dirname,'map-language-worker.cjs')],outfile:path.join(output,'map-language-worker.cjs'),
      bundle:true,platform:'node',format:'cjs',minify:true,alias:{'@babel/parser':path.join(parser,'lib/index.js')},logLevel:'warning'});
    fs.copyFileSync(path.join(parser,'LICENSE'),path.join(output,'BABEL-PARSER-LICENSE.txt'));
  } else fs.writeFileSync(path.join(output,'map-language-worker.cjs'),"process.stdout.write(JSON.stringify({error:'parser_unavailable'}));");
  fs.writeFileSync(path.join(output,'analysis-runtime.json'),JSON.stringify({available,parser:'babel-7.29.7'}));
  for (const name of ['main.cjs', 'panel-app.cjs', 'preload.cjs', 'bridge-client.cjs', 'config-contract.cjs', 'config-state.cjs', 'work-manage.cjs', 'knowledge-contract.cjs', 'knowledge-state.cjs', 'knowledge-catalog.cjs', 'knowledge-outbox.cjs', 'knowledge-context.cjs', 'knowledge-evidence.cjs', 'knowledge-answer.cjs', 'knowledge-followup.cjs', 'knowledge-consolidation.cjs', 'knowledge-consolidation-runner.cjs', 'consolidation-headless.cjs', 'documentation-state.cjs', 'document-reader.cjs', 'markdown-model.cjs', 'panel-jobs.cjs', 'ai-provider.cjs', 'ai-session.cjs', 'role-config.cjs', 'ai-roles.cjs', 'role-prompts.cjs', 'ai-capabilities.cjs', 'manual-handoff.cjs', 'panel-state.cjs', 'map-refresh.cjs', 'map-watch.cjs', 'profile-paths.cjs', 'external-app.cjs', 'external-client.cjs', 'external-preload.cjs', 'observer_paths.py', 'index.html', 'style.css', 'external.html', 'external.css'])
    fs.copyFileSync(path.join(__dirname, name), path.join(output, name));
  fs.mkdirSync(path.join(output,'vendor'),{recursive:true});
  for(const name of ['markdown-it.cjs','MARKDOWN-IT-LICENSE.txt'])fs.copyFileSync(path.join(__dirname,'vendor',name),path.join(output,'vendor',name));
  fs.writeFileSync(path.join(output, 'package.json'), JSON.stringify({ name: 'controlcoding-panel', version: '0.1.0', main: 'main.cjs' }));
  const dependencies={};
  for(const [name,version] of Object.entries({react:'19.3.0','react-dom':'19.3.0',scheduler:'0.28.0',esbuild:'0.28.2'})){
    const packageRoot=path.join(modules,name),metadata=JSON.parse(fs.readFileSync(path.join(packageRoot,'package.json'),'utf8'));
    if(metadata.version!==version||metadata.license!=='MIT')throw Error('Unsupported build dependency');
    dependencies[name]=version;
    if(name!=='esbuild')fs.copyFileSync(path.join(packageRoot,'LICENSE'),path.join(output,name.toUpperCase()+'-LICENSE.txt'));
  }
  const sourceHashes={};
  const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
  const sourceNames=[...Object.keys(renderer.metafile.inputs),...Object.keys(externalRenderer.metafile.inputs)].map(p=>path.resolve(p)).filter(p=>p.startsWith(__dirname+path.sep));
  for(const name of ['build.cjs','main.cjs','panel-app.cjs','preload.cjs','bridge-client.cjs', 'config-contract.cjs', 'config-state.cjs', 'work-manage.cjs', 'knowledge-contract.cjs', 'knowledge-state.cjs', 'knowledge-catalog.cjs', 'knowledge-outbox.cjs', 'knowledge-evidence.cjs', 'knowledge-answer.cjs', 'knowledge-followup.cjs', 'knowledge-consolidation.cjs', 'knowledge-consolidation-runner.cjs', 'consolidation-headless.cjs', 'documentation-state.cjs', 'document-reader.cjs', 'markdown-model.cjs', 'panel-jobs.cjs', 'ai-provider.cjs', 'ai-session.cjs', 'role-config.cjs', 'ai-roles.cjs', 'role-prompts.cjs', 'ai-capabilities.cjs', 'manual-handoff.cjs','panel-state.cjs','map-refresh.cjs','map-watch.cjs','map-language-worker.cjs','profile-paths.cjs','external-app.cjs','external-client.cjs','external-preload.cjs','observer_paths.py','index.html','style.css','external.html','external.css'])sourceNames.push(path.join(__dirname,name));
  sourceNames.push(path.join(__dirname,'vendor/markdown-it.cjs'));
  sourceNames.push(path.join(__dirname,'knowledge-context.cjs'));
  dependencies['markdown-it']='12.3.2';
  for(const source of sourceNames.sort())sourceHashes[path.relative(root,source).replaceAll(path.sep,'/')]=hash(source);
  const assets={};
  for(const name of fs.readdirSync(output).sort())if(name!=='BUILD-INFO.json'&&fs.statSync(path.join(output,name)).isFile())assets[name]=hash(path.join(output,name));
  for(const name of fs.readdirSync(path.join(output,'vendor')).sort())assets['vendor/'+name]=hash(path.join(output,'vendor',name));
  fs.writeFileSync(path.join(output,'BUILD-INFO.json'),JSON.stringify({schemaVersion:1,dependencies,parser:available?'babel-7.29.7':null,sourceHashes,assets},null,2)+'\n');
  console.log(`Panel built: ${output}`);
}
build().catch(() => { console.error('Panel build failed; check external paths and dependencies.'); process.exitCode = 1; });

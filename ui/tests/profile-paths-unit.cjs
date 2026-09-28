'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {physicalDirectory,separateProfile}=require('../profile-paths.cjs');
const {PanelState}=require('../panel-state.cjs');
const base=process.env.CC_PATH_TEST_ROOT;
if(!base)throw Error('CC_PATH_TEST_ROOT must point to a fresh external fixture');
fs.mkdirSync(base,{recursive:true});
test('physical overlap rejects equality and either direction, permits prefix siblings',()=>{
 const root=path.join(base,'project');fs.mkdirSync(root);
 for(const [a,b] of [[root,root],[root,path.join(root,'child')],[path.join(root,'child'),root]])assert.throws(()=>separateProfile(a,b));
 assert.equal(separateProfile(root,root+'-other'),physicalDirectory(root));
 assert.equal(fs.readdirSync(root).length,0);
});
test('Windows namespaces fail closed even when physically pointing at ordinary folders',{skip:process.platform!=='win32'},()=>{
 for(const prefix of ['\\\\?\\','\\\\.\\','//?/','\\\\localhost\\'])assert.throws(()=>physicalDirectory(prefix+base));
});
test('junction ancestor is rejected',{skip:process.platform!=='win32'},()=>{
 const link=path.join(base,'junction');fs.symlinkSync(path.join(base,'project'),link,'junction');
 assert.throws(()=>physicalDirectory(path.join(link,'new-profile')));
});
test('an absent drive is rejected instead of repeatedly searching its root',{skip:process.platform!=='win32'},t=>{
 const root=[...'ZYXWVUTSRQPONMLKJIHGFEDCBA'].map(c=>c+':\\').find(p=>!fs.existsSync(p));
 if(!root)return t.skip('Every drive letter exists');
 assert.throws(()=>physicalDirectory(path.join(root,'noncreated-profile')),/root does not exist/);
});
test('existing short names resolve before overlap comparisons',{skip:process.platform!=='win32'},t=>{
 const python=process.env.CC_TEST_PYTHON;if(!python)return t.skip('No existing Python for Windows alias fixture');
 const code='import ctypes,sys; b=ctypes.create_unicode_buffer(32768); ctypes.windll.kernel32.GetShortPathNameW(sys.argv[1],b,len(b)); print(b.value)';
 const short=p=>require('node:child_process').execFileSync(python,['-I','-B','-c',code,p],{encoding:'utf8',windowsHide:true}).trim();
 let original=base,alias=short(base);
 if(alias.toLowerCase()===base.toLowerCase()){original=process.env.ProgramFiles;alias=short(original);}
 if(alias.toLowerCase()===original.toLowerCase())return t.skip('No existing 8.3 alias available');
 assert.equal(physicalDirectory(alias),physicalDirectory(original));
 assert.throws(()=>separateProfile(alias,path.join(original,'noncreated-child')));
});
test('all state selections enforce guard before cancellation or observation',async()=>{
 let calls=0;const profile=path.join(base,'profile');
 const state=new PanelState({cancel(){calls++;},async run(){calls++;return {status:'ok',result:{}};}},()=>{},{},root=>separateProfile(profile,root));
 await state.select(path.join(base,'project'));const old=state.value,generation=state.generation,count=calls;
 await assert.rejects(state.select(base));
 assert.equal(state.value,old);assert.equal(state.generation,generation);assert.equal(calls,count);
 state.refresh.dispose();
});

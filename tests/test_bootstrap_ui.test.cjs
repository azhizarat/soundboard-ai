const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('ui/app.js','utf8');
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function setup(bridge){
 const frames=[],calls=[],elements=new Map();
 const document={getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',title:'',classList:{add(){},remove(){}}});return elements.get(id);}};
 const context={state:{environment:null},document,api:()=>bridge,setTimeout,
   window:{addEventListener(){}},requestAnimationFrame:fn=>frames.push(fn),
   renderDevices(){calls.push('devices');},renderQuickImportBar(){calls.push('folders');},
   renderSidebar(){calls.push('sidebar');},renderGrid(){calls.push('grid');},bindEvents(){calls.push('bind');},
   captureEnvOriginalTitles(){},renderEnvironment(value){context.state.environment=value;calls.push('environment');},openEnvModal(){calls.push('modal');}};
 for(const name of ['loadStartupDetails','initApp']){
   const fn=source.match(new RegExp('async function '+name+'\\(\\) \\{[\\s\\S]*?\\n}\\n'));
   if(fn)vm.runInNewContext(fn[0],context);
 }
 return {context,frames,calls,document};
}
test('biblioteca e handlers ficam prontos sem esperar diagnóstico; prewarm inicia após pintura',async()=>{
 const details=deferred();let diagnosed=0,ready=0,full=0;
 const bridge={get_bootstrap_state:async()=>({clips:[{id:'c'}],config:{model:'local'},startup_details_pending:true}),
   get_initial_state:async()=>{full++;return {};},get_startup_details:()=>{diagnosed++;return details.promise;},notify_ui_ready:async()=>{ready++;}};
 const {context,frames,calls}=setup(bridge);await context.initApp();
 assert.ok(calls.includes('grid')&&calls.includes('bind'));assert.equal(full,0);assert.equal(diagnosed,0);assert.equal(ready,0);
 frames.shift()();assert.equal(diagnosed,0);frames.shift()();await Promise.resolve();
 assert.equal(diagnosed,1);assert.equal(ready,1);
 details.resolve({environment:{checks:[]},discovered_separados:[{path:'new'}]});await new Promise(setImmediate);
 assert.equal(context.state.clips[0].id,'c');assert.equal(context.state.discoveredSeparados[0].path,'new');
 assert.equal(calls.includes('environment'),true);
});
test('diagnóstico tardio não sobrescreve configuração ou ambiente atualizado pelo usuário',async()=>{
 const details=deferred(),{context}=setup({get_startup_details:()=>details.promise});
 context.state.config={revision:1};const pending=context.loadStartupDetails();
 context.state.config={revision:2};context.state.environment={new:true};context.state.discoveredSeparados=[{path:'chosen'}];
 details.resolve({environment:{old:true},discovered_separados:[{path:'old'}]});await pending;
 assert.deepEqual(context.state.environment,{new:true});assert.equal(context.state.discoveredSeparados[0].path,'chosen');
});
test('falha do diagnóstico deixa biblioteca utilizável e mostra aviso acessível',async()=>{
 const {context,document}=setup({get_startup_details:async()=>{throw Error('Offline');}});
 context.state.config={};context.state.clips=[{id:'saved'}];await context.loadStartupDetails();
 assert.equal(context.state.clips[0].id,'saved');assert.match(document.getElementById('envBadge').textContent,/indisponível/);
});

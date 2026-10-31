// Dependency-free local preview bridge. 127.0.0.1 only, GET-only, no shell, no writes.
import http from 'node:http';
import {readFile, stat} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join, resolve} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const execFileAsync=promisify(execFile),root=dirname(fileURLToPath(import.meta.url));
const argv=process.argv.slice(2);
function arg(name,def=null){const i=argv.indexOf('--'+name);return i<0?def:argv[i+1]??def}
if(argv.includes('--help')){console.log('node server.mjs [--port 8787] [--repo PATH --campaign-id ID|--state-dir PATH] [--python PYTHON]');process.exit(0)}
const port=Number(arg('port','8787'));
const repo=arg('repo');
const campaign=arg('campaign-id');
const stateDir=arg('state-dir');
const python=arg('python',process.platform==='win32'?'python':'python3');
if(!Number.isInteger(port)||port<1024||port>65535)throw Error('port must be between 1024 and 65535');
if((campaign||stateDir)&&!repo)throw Error('--campaign-id/--state-dir requires --repo');
if(campaign&&stateDir)throw Error('Provide --campaign-id or --state-dir, not both');
if(repo&&!campaign&&!stateDir)throw Error('--repo requires --campaign-id or --state-dir');
if(repo&&!existsSync(repo))throw Error('Repo path does not exist');
if(campaign&&!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(campaign))throw Error('Invalid campaign ID');
const connected=Boolean(repo&&(campaign||stateDir));
const selection=connected?['--repo',resolve(repo),...(campaign?['--campaign-id',campaign]:['--state-dir',resolve(stateDir)])]:[];
async function cli(command,...args){
  try{
    const {stdout}=await execFileAsync(python,['-m','bananloop.cli',command,...selection,...args],{cwd:resolve(repo),shell:false,windowsHide:true,timeout:8000,maxBuffer:4_000_000,env:process.env});
    const obj=JSON.parse(stdout);if(!obj||typeof obj!=='object')throw Error('Invalid JSON');return obj;
  }catch(e){const code=e.code?String(e.code).slice(0,30):'ERROR';throw new Error('BananLoop CLI unavailable / invalid output ('+code+'). Check installed package, --python, repo and campaign ID.');}
}
const allowed={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/styles.css':'styles.css','/model.js':'model.js','/mascot.png':'mascot.png'};
function json(res,status,payload){const body=JSON.stringify(payload);res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(body)}
const server=http.createServer(async(req,res)=>{
 try{
  const hostname=(req.headers.host||'').split(':')[0].toLowerCase();
  if(!['localhost','127.0.0.1','[::1]'].includes(hostname)){json(res,403,{error:'Localhost only'});return;}
  if(req.method!=='GET'){json(res,405,{error:'Read-only preview'});return;}
  const url=new URL(req.url||'/',`http://localhost:${port}`),path=url.pathname;
  if(path==='/api/config'){json(res,200,{connected,mode:connected?'read-only':'demo'});return;}
  if(path==='/api/snapshot'){if(!connected){json(res,404,{error:'No live campaign selected'});return;}json(res,200,await cli('operator-snapshot'));return;}
  if(path==='/api/events'){
    if(!connected){json(res,404,{error:'No live campaign selected'});return;}
    const after=Number(url.searchParams.get('after')??'0');if(!Number.isSafeInteger(after)||after<0){json(res,400,{error:'Invalid event cursor'});return;}
    json(res,200,await cli('operator-events','--after',String(after),'--limit','100'));return;
  }
  const filename=allowed[path];if(!filename){json(res,404,{error:'Not found'});return;}
  const body=await readFile(join(root,filename));
  const contentType=filename.endsWith('.html')?'text/html; charset=utf-8':filename.endsWith('.css')?'text/css; charset=utf-8':filename.endsWith('.png')?'image/png':'application/javascript; charset=utf-8';
  res.writeHead(200,{'Content-Type':contentType,'Content-Length':body.length,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"});res.end(body);
 }catch(e){json(res,503,{error:String(e.message).slice(0,220)})}
});
server.listen(port,'127.0.0.1',()=>{
 console.log('BananLoop UI preview: http://127.0.0.1:'+port);
 console.log(connected?'LIVE READ-ONLY: '+resolve(repo)+' / '+(campaign||stateDir):'DEMO MODE: no real campaign connected');
 console.log('No model calls, no CLI writes. Ctrl+C to stop.');
});
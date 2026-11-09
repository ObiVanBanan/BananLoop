import type {CampaignList,CampaignView,CampaignSnapshot,CodexStatus,EngineEvent,EventPage} from '../domain/live';

const endpoint='/api';
export async function getJson<T>(path:string, token:string='', signal?:AbortSignal):Promise<T>{
 const response=await fetch(endpoint+path,{method:'GET',headers:token?{'Authorization':`Bearer ${token}`}:{},signal,cache:'no-store'});
 if(!response.ok)throw new Error(response.status===401?'Требуется токен локального BananLoop API':`BananLoop API: HTTP ${response.status}`);
 return await response.json() as T;
}
export type DeviceLogin = {phase:'idle'|'pending'|'completed'|'failed'|'expired'|'cancelled';verification_url:string|null;user_code:string|null};
async function loginPost(path:string, token:string):Promise<DeviceLogin>{
 const response=await fetch(endpoint+path,{
  method:'POST',headers:{'Content-Type':'application/json',...(token?{'Authorization':`Bearer ${token}`}:{})},
  body:'{}',cache:'no-store'
 });
 if(!response.ok)throw new Error(response.status===401?'Требуется токен локального API':
  response.status===409?'Codex app-server недоступен или вход запрещён настройками':
  `BananLoop login: HTTP ${response.status}`);
 return await response.json() as DeviceLogin;
}
export const startCodexLogin=(token:string)=>loginPost('/v1/providers/codex/login/start',token);
export const cancelCodexLogin=(token:string)=>loginPost('/v1/providers/codex/login/cancel',token);
export const getCodexLogin=(token:string,signal?:AbortSignal)=>getJson<DeviceLogin>('/v1/providers/codex/login/status',token,signal);
const urlFor=(id:string)=>`/v1/campaigns/${encodeURIComponent(id)}`;
export const getCampaigns=(token:string,signal?:AbortSignal)=>getJson<CampaignList>('/v1/campaigns',token,signal);
export const getView=(id:string,token:string,signal?:AbortSignal)=>getJson<CampaignView>(urlFor(id)+'/view',token,signal);
export const getSnapshot=(id:string,token:string,signal?:AbortSignal)=>getJson<CampaignSnapshot>(urlFor(id)+'/snapshot',token,signal);
export const getEvents=(id:string,after:number,token:string,signal?:AbortSignal)=>getJson<EventPage>(urlFor(id)+`/events?after=${after}&limit=100`,token,signal);
export const getCodexStatus=(token:string,signal?:AbortSignal)=>getJson<CodexStatus>('/v1/providers/codex/status',token,signal);
/** Authoritative replay cursor. SSE is a notification channel, not a command receipt. */
export async function streamEvents(id:string,after:number,token:string,onEvent:(event:EngineEvent)=>void,signal:AbortSignal):Promise<void>{
 const response=await fetch(endpoint+urlFor(id)+`/events/stream?after=${after}`,{
  headers:token?{'Authorization':`Bearer ${token}`}:{},signal,cache:'no-store'});
 if(!response.ok||!response.body)throw Error(`SSE HTTP ${response.status}`);
 const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
 try{
  while(true){
   const {done,value}=await reader.read();if(done)break;
   buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');
   if(buffer.length>262144)throw Error('SSE frame too large');
   let index;
   while((index=buffer.indexOf('\n\n'))>=0){
    const chunk=buffer.slice(0,index);buffer=buffer.slice(index+2);
    const raw=chunk.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trim()).join('\n');
    if(!raw)continue;
    try{const event=JSON.parse(raw) as EngineEvent;if(Number.isSafeInteger(event.seq)&&event.seq>0)onEvent(event)}
    catch{throw Error('Invalid committed event data')}
   }
  }
 }finally{reader.releaseLock()}
}
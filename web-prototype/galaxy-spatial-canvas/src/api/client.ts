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
/** A model discussion proposes values; only an explicit second click can start Engine. */
export type StudioMode='full_auto'|'supervised';
export type StudioMessage={role:'user'|'assistant';content:string};
export type StudioBudgets={max_experiments:number;max_attempts:number;max_agent_calls:number;max_evaluator_calls:number;deadline_seconds:number};
export type StudioDraft={goal:string;budgets:StudioBudgets;missing:string[];ready_for_bootstrap:boolean};
export type StudioInfo={enabled:boolean;workspace_kind:string|null;planning_provider:string|null;modes:StudioMode[]};
export type StudioReply={reply:string;draft:StudioDraft|null;provider:'codex_cli';model_called:boolean};
export type StudioLaunch={request_id:string;campaign_id:string;phase:string;mode:StudioMode;exit_code:number|null;note:string|null};
async function studioPost<T>(path:string,token:string,body:object):Promise<T>{
 const response=await fetch(endpoint+path,{method:'POST',headers:{'Content-Type':'application/json',...(token?{'Authorization':`Bearer ${token}`}:{})},
  cache:'no-store',body:JSON.stringify(body)});
 if(!response.ok){
  const payload=await response.json().catch(()=>null) as {error?:string}|null;
  const errors:Record<string,string>={
   unsafe_config:'Codex отклонил изолированный режим чата. Проверь конфигурацию app-server и отключение инструментов для Studio.',
   app_server_unavailable:'Codex app-server не запустился. Проверь установленный Codex CLI: codex --version.',
   model_timeout:'Codex не ответил вовремя. Попробуй повторить сообщение.',
   model_incomplete:'Codex прервал ответ. Попробуй повторить сообщение.',
   provider_unavailable:'Codex не смог обработать сообщение. Проверь codex login status и доступность модели.',
   studio_provider_unavailable:'Локальный планировщик Codex недоступен.',
   origin_or_content_type_rejected:'API отклонил источник запроса. Открой UI через http://127.0.0.1:5173 и проверь адрес BananLoop API.',
   invalid_studio_request:'Некорректный запрос Studio. Перезагрузи страницу и попробуй снова.',
  };
  throw new Error(
   response.status===401?'Нужен токен локального API':
   response.status===404?'Studio не включён. Запусти bananloop serve --studio':
   payload?.error&&errors[payload.error]?errors[payload.error]:
   response.status===503?'Локальный Codex планировщик недоступен':
   response.status===409?'Запуск уже выполняется или не завершена подготовка':
   `Studio API: HTTP ${response.status}`);
 }
 return await response.json() as T;
}
export const getStudioInfo=(token:string)=>getJson<StudioInfo>('/v1/studio/info',token);
export const discussStudio=(token:string,body:{message:string;history:StudioMessage[]})=>
 studioPost<StudioReply>('/v1/studio/discuss',token,body);
export const launchStudio=(token:string,body:{goal:string;mode:StudioMode;budgets:StudioBudgets;request_id:string})=>
 studioPost<StudioLaunch>('/v1/studio/launch',token,body);
export const confirmStudioLaunch=(token:string,request_id:string)=>
 studioPost<StudioLaunch>('/v1/studio/launch/confirm',token,{request_id});
export const getStudioLaunch=(token:string,request_id:string)=>
 getJson<StudioLaunch>('/v1/studio/launch/status?request_id='+encodeURIComponent(request_id),token);

/** Browser-local Studio chat history. Never stores Codex credentials or API tokens. */
import type {StudioDraft,StudioLaunch,StudioMessage,StudioMode} from '../api/client';

export type StudioThread={
 id:string;title:string;messages:StudioMessage[];draft:StudioDraft|null;
 mode:StudioMode;createdAt:number;updatedAt:number;
 requestId:string|null;launch:StudioLaunch|null;
};
export type StudioChatState={version:1;activeId:string;threads:StudioThread[]};
export const STUDIO_STORAGE_KEY='bananloop.studio.chats.v1';
const MAX_THREADS=30;
const MAX_MESSAGES=60;
const MAX_CONTENT=3500;
const cut=(value:unknown,max:number)=>typeof value==='string'?value.slice(0,max):'';
export const newStudioThread=(id:string,now=Date.now()):StudioThread=>({
 id,title:'Новый чат',messages:[],draft:null,mode:'full_auto',createdAt:now,updatedAt:now,requestId:null,launch:null
});
export const emptyStudioChats=():StudioChatState=>({version:1,activeId:'',threads:[]});
export const studioChatTitle=(input:string)=>{
 const line=input.replace(/\s+/g,' ').trim();
 return line?line.slice(0,48)+(line.length>48?'…':''):'Новый чат';
};
const safeMode=(v:unknown):StudioMode=>v==='supervised'?'supervised':'full_auto';
const safeMessages=(source:unknown):StudioMessage[]=>{
 if(!Array.isArray(source))return [];
 return source.slice(-MAX_MESSAGES).filter(v=>v&&typeof v==='object'&&
  (v.role==='user'||v.role==='assistant')&&typeof v.content==='string')
  .map(v=>({role:v.role as 'user'|'assistant',content:cut(v.content,MAX_CONTENT)}));
};
const safeDraft=(source:unknown):StudioDraft|null=>{
 if(!source||typeof source!=='object')return null;
 const v=source as StudioDraft;
 if(typeof v.goal!=='string'||v.goal.length<12||v.goal.length>1200||!v.budgets||typeof v.budgets!=='object')return null;
 const fields=['max_experiments','max_attempts','max_agent_calls','max_evaluator_calls','deadline_seconds'] as const;
 const limits=[[1,100],[1,200],[1,500],[1,500],[60,86400]];
 for(let i=0;i<fields.length;i++){
  const n=v.budgets[fields[i]];
  if(!Number.isSafeInteger(n)||n<limits[i][0]||n>limits[i][1])return null;
 }
 return {goal:v.goal,budgets:Object.fromEntries(fields.map(f=>[f,v.budgets[f]])) as StudioDraft['budgets'],
  missing:Array.isArray(v.missing)?v.missing.filter(x=>typeof x==='string').slice(0,8).map(x=>x.slice(0,180)):[],
  ready_for_bootstrap:v.ready_for_bootstrap===true};
};
const safeLaunch=(source:unknown):StudioLaunch|null=>{
 if(!source||typeof source!=='object')return null;
 const v=source as StudioLaunch;
 if(typeof v.request_id!=='string'||!/^[-_a-zA-Z0-9]{8,100}$/.test(v.request_id)||typeof v.phase!=='string')return null;
 return {request_id:v.request_id,campaign_id:cut(v.campaign_id,120),phase:cut(v.phase,100),
  mode:safeMode(v.mode),exit_code:Number.isSafeInteger(v.exit_code)?v.exit_code:null,note:cut(v.note,300)||null};
};
export function parseStudioChats(raw:string|null):StudioChatState{
 if(!raw||raw.length>450_000)return emptyStudioChats();
 try{
  const obj=JSON.parse(raw);
  if(!obj||obj.version!==1||!Array.isArray(obj.threads))return emptyStudioChats();
  const unique=new Set<string>();
  const threads:StudioThread[]=[];
  for(const source of obj.threads.slice(0,MAX_THREADS)){
   if(!source||typeof source!=='object'||typeof source.id!=='string'||
       !/^[a-zA-Z0-9_-]{6,100}$/.test(source.id)||unique.has(source.id))continue;
   unique.add(source.id);
   const messages=safeMessages(source.messages);
   const createdAt=Number.isSafeInteger(source.createdAt)&&source.createdAt>=0?source.createdAt:0;
   const updatedAt=Number.isSafeInteger(source.updatedAt)&&source.updatedAt>=0?source.updatedAt:createdAt;
   const draft=safeDraft(source.draft);
   const launch=safeLaunch(source.launch);
   const requestId=typeof source.requestId==='string'&&/^[-_a-zA-Z0-9]{8,100}$/.test(source.requestId)?source.requestId:null;
   threads.push({id:source.id,title:cut(source.title,72)||studioChatTitle(messages[0]?.content||''),
    messages,draft,mode:safeMode(source.mode),createdAt,updatedAt,requestId,launch});
  }
  return {version:1,activeId:typeof obj.activeId==='string'&&unique.has(obj.activeId)?obj.activeId:(threads[0]?.id||''),threads};
 }catch{return emptyStudioChats()}
}
export function readStudioChats(storage:Pick<Storage,'getItem'>|null):StudioChatState{
 if(!storage)return emptyStudioChats();
 try{return parseStudioChats(storage.getItem(STUDIO_STORAGE_KEY))}catch{return emptyStudioChats()}
}
export function saveStudioChats(storage:Pick<Storage,'setItem'>|null,state:StudioChatState):void{
 if(!storage)return;
 try{
  const data={version:1,activeId:state.activeId,threads:state.threads.slice(0,MAX_THREADS).map(t=>({
   ...t,messages:t.messages.slice(-MAX_MESSAGES),
  }))};
  const raw=JSON.stringify(data);
  if(raw.length<=450_000)storage.setItem(STUDIO_STORAGE_KEY,raw);
 }catch{/* private browsing and quota errors must not break chat */}
}
export function updateStudioThread(state:StudioChatState,id:string,change:(t:StudioThread)=>StudioThread):StudioChatState{
 return {...state,threads:state.threads.map(t=>t.id===id?change(t):t)};
}
export function insertStudioThread(state:StudioChatState,thread:StudioThread):StudioChatState{
 return {...state,activeId:thread.id,threads:[thread,...state.threads.filter(t=>t.id!==thread.id)].slice(0,MAX_THREADS)};
}
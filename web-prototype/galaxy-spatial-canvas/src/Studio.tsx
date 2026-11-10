import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {confirmStudioLaunch,discussStudio,getStudioLaunch,launchStudio} from './api/client';
import type {StudioLaunch,StudioMessage} from './api/client';
import {emptyStudioChats,insertStudioThread,newStudioThread,readStudioChats,saveStudioChats,studioChatTitle,updateStudioThread} from './state/studioChats';
import type {StudioChatState,StudioThread} from './state/studioChats';

type StudioProps={token:string;onBack:()=>void;onOpenCampaign:(id:string)=>void};
const budgetLabels={max_experiments:'Эксперименты',max_attempts:'Попытки',max_agent_calls:'Вызовы Codex',max_evaluator_calls:'Проверки',deadline_seconds:'Время, сек'} as const;
const statusText:Record<string,string>={
 PREPARING:'Подготавливаем кампанию',BOOTSTRAPPING_OR_RUNNING:'BananLoop выполняет исследование',
 NEEDS_INPUT:'Нужны данные или проверяемый evaluator',AWAITING_CONFIRMATION:'Подготовка готова к подтверждению',
 FAILED:'Запуск завершился с ошибкой',FINISHED:'Запуск завершён',UNKNOWN:'Статус после перезапуска неизвестен',
};
const safeStore=()=>{try{return window.localStorage}catch{return null}};
const createChatId=()=>`chat-${crypto.randomUUID().replaceAll('-','')}`;
const newRequestId=()=>`ui-${crypto.randomUUID().replaceAll('-','')}`;

export function Studio({token,onBack,onOpenCampaign}:StudioProps){
 const [chats,setChats]=useState<StudioChatState>(()=>typeof window==='undefined'?emptyStudioChats():readStudioChats(safeStore()));
 const [input,setInput]=useState('');
 const [busyId,setBusyId]=useState<string|null>(null);
 const [pendingLaunchId,setPendingLaunchId]=useState<string|null>(null);
 const [review,setReview]=useState(false);
 const [error,setError]=useState<{id:string;message:string}|null>(null);
 const [retryMessage,setRetryMessage]=useState<string|null>(null);
 const [sidebarOpen,setSidebarOpen]=useState(()=>typeof window==='undefined'||typeof window.matchMedia!=='function'||!window.matchMedia('(max-width: 760px)').matches);
 const scrollAnchor=useRef<HTMLDivElement|null>(null);
 const inputRef=useRef<HTMLTextAreaElement|null>(null);
 const current=useMemo(()=>chats.threads.find(t=>t.id===chats.activeId)||null,[chats]);
 const isBusy=busyId!==null||pendingLaunchId!==null;

 useEffect(()=>{saveStudioChats(safeStore(),chats)},[chats]);
 useEffect(()=>{
  // scrollIntoView can return a Promise in some browsers; NEVER return it from useEffect.
  scrollAnchor.current?.scrollIntoView?.({behavior:'smooth',block:'end'});
 },[current?.messages.length,busyId,current?.launch?.phase]);
 const selectChat=(id:string)=>{setChats(s=>({...s,activeId:id}));setInput('');setReview(false);setError(null);setRetryMessage(null)};
 const newChat=()=>{
  const t=newStudioThread(createChatId());
  setChats(s=>insertStudioThread(s,t));setInput('');setReview(false);setError(null);setRetryMessage(null);
  requestAnimationFrame(()=>inputRef.current?.focus());
 };
 const deleteChat=(id:string)=>{
  if(busyId===id||pendingLaunchId===id)return;
  setChats(s=>{
   const threads=s.threads.filter(t=>t.id!==id);
   return {...s,threads,activeId:s.activeId===id?(threads[0]?.id||''):s.activeId};
  });
  setReview(false);setError(null);setRetryMessage(null);
 };
 const send=useCallback(async(retry=false)=>{
  if(isBusy)return;
  const message=(retry?retryMessage:input)?.trim()||'';
  if(!message||message.length>3000)return;
  const target=current??newStudioThread(createChatId());
  const conversationId=target.id;
  const history=target.messages.slice(-16);
  if(!retry){
   setChats(s=>{
    const present=s.threads.some(t=>t.id===conversationId);
    const state=present?s:insertStudioThread(s,target);
    return updateStudioThread(state,conversationId,t=>({...t,
     title:t.messages.length?t.title:studioChatTitle(message),
     messages:[...t.messages,{role:'user',content:message} as StudioMessage].slice(-60),
     updatedAt:Date.now(),draft:null,requestId:null,launch:null}));
   });
  }
  setInput('');setError(null);setRetryMessage(null);setReview(false);setBusyId(conversationId);
  try{
   const answer=await discussStudio(token,{message,history:retry?history.slice(0,-1):history});
   setChats(s=>updateStudioThread(s,conversationId,t=>({...t,
    messages:[...t.messages,{role:'assistant',content:answer.reply} as StudioMessage].slice(-60),
    draft:answer.draft??t.draft,updatedAt:Date.now()})));
  }catch(e){
   setError({id:conversationId,message:String(e)});
   setRetryMessage(message);
  }finally{setBusyId(null)}
 },[isBusy,current,input,retryMessage,token]);
 const changeMode=(mode:StudioThread['mode'])=>{
  if(!current){const thread={...newStudioThread(createChatId()),mode};setChats(s=>insertStudioThread(s,thread));return;}
  if(current.launch)return;
  setChats(s=>updateStudioThread(s,current.id,t=>({...t,mode,updatedAt:Date.now()})));
 };
 const start=useCallback(async()=>{
  if(!current?.draft||isBusy||current.launch)return;
  const t=current;setPendingLaunchId(t.id);setError(null);
  const requestId=t.requestId||newRequestId();
  setChats(s=>updateStudioThread(s,t.id,o=>({...o,requestId})));
  try{
   const answer=await launchStudio(token,{goal:t.draft!.goal,mode:t.mode,budgets:t.draft!.budgets,request_id:requestId});
   setChats(s=>updateStudioThread(s,t.id,o=>({...o,launch:answer,updatedAt:Date.now()})));
   setReview(false);
  }catch(e){setError({id:t.id,message:String(e)})}
  finally{setPendingLaunchId(null)}
 },[current,isBusy,token]);
 const confirmPrepared=useCallback(async()=>{
  if(!current?.requestId||isBusy)return;
  const id=current.id;setPendingLaunchId(id);setError(null);
  try{
   const answer=await confirmStudioLaunch(token,current.requestId);
   setChats(s=>updateStudioThread(s,id,o=>({...o,launch:answer,updatedAt:Date.now()})));
  }catch(e){setError({id,message:String(e)})}
  finally{setPendingLaunchId(null)}
 },[current,isBusy,token]);
 useEffect(()=>{
  const pending=chats.threads.filter(t=>t.launch&&t.requestId&&['PREPARING','BOOTSTRAPPING_OR_RUNNING'].includes(t.launch.phase));
  if(!pending.length)return;
  let live=true;
  const timer=window.setInterval(()=>{
   for(const t of pending){if(!t.requestId)continue;
    void getStudioLaunch(token,t.requestId).then(answer=>{
     if(live)setChats(s=>updateStudioThread(s,t.id,o=>({...o,launch:answer})));
    }).catch(()=>{});
   }
  },2500);
  return()=>{live=false;window.clearInterval(timer)};
 },[chats.threads.map(t=>`${t.id}:${t.launch?.phase??''}`).join('|'),token]);

 return <div className="studio-desktop">
  {sidebarOpen&&<button type="button" className="studio-mobile-scrim" onClick={()=>setSidebarOpen(false)} aria-label="Закрыть историю чатов"/>}
  <aside className={`studio-chat-sidebar ${sidebarOpen?'':'studio-chat-sidebar-collapsed'}`} aria-label="История диалогов">
   <div className="studio-sidebar-top">
    <button className="studio-icon-control" onClick={()=>setSidebarOpen(x=>!x)} aria-label={sidebarOpen?'Свернуть панель чатов':'Открыть панель чатов'} title="Боковая панель">☰</button>
    {sidebarOpen&&<strong className="studio-logo">banana<span>loop</span><i>.</i></strong>}
   </div>
   <button className="studio-new-chat" onClick={newChat} title="Новый чат" aria-label="Новый чат"><span>＋</span>{sidebarOpen&&'Новый чат'}</button>
   {sidebarOpen&&<><div className="studio-chat-list-label">Недавние</div><nav className="studio-chat-list" aria-label="Чаты">
    {chats.threads.map(t=><div key={t.id} className={`studio-chat-item ${t.id===chats.activeId?'active':''}`}>
     <button className="studio-chat-select" onClick={()=>selectChat(t.id)} aria-current={t.id===chats.activeId?'page':undefined} title={t.title}><span>◷</span><span className="studio-chat-title">{t.title}</span></button>
     <button className="studio-chat-remove" onClick={()=>deleteChat(t.id)} aria-label={`Удалить чат: ${t.title}`} title="Удалить чат" disabled={busyId===t.id||pendingLaunchId===t.id}>×</button>
    </div>)}
   </nav></>}
   <div className="studio-sidebar-bottom"><button onClick={onBack} disabled={isBusy} className="studio-back"><span>⌂</span>{sidebarOpen&&'К исследованиям'}</button></div>
  </aside>
  <main className="studio-chat-main" aria-label="Новая кампания">
   <header className="studio-chat-header">
    <button className="studio-mobile-menu" onClick={()=>setSidebarOpen(x=>!x)} aria-label="Показать историю чатов">☰</button>
    <span className="studio-chat-heading">{current?.messages.length?current.title:'BananLoop'} <i>▾</i></span>
    <div className="studio-chat-header-actions">
     {current?.draft&&<button onClick={()=>setReview(true)} className="studio-plan-open">◇ <span>План кампании</span></button>}
     <span className="studio-provider-pill">Codex CLI</span>
    </div>
   </header>
   <div className="studio-chat-scroll">
    {(!current||current.messages.length===0)&&<div className="studio-empty-state">
     <div className="studio-empty-copy">
      <div className="studio-brand-mark" aria-hidden="true">✳</div>
      <h1>С чего начнём?</h1>
      <p>Опиши исследовательскую задачу. Мы обсудим подход перед запуском.</p>
      <p className="studio-empty-hint">Цель / гипотеза / независимая проверка</p>
     </div>
     <div className="studio-orbit-art" aria-hidden="true">
      <span className="studio-orbit-node first"/><span className="studio-orbit-node second"/><span className="studio-orbit-node third"/>
      <span className="studio-orbit-core">?</span>
      <span className="studio-orbit-caption">от идеи к проверке</span>
     </div>
    </div>}
    {!!current?.messages.length&&<div className="studio-chat-transcript">
     {current.messages.map((m,i)=><div key={i} className={`studio-transcript-turn ${m.role==='user'?'turn-user':'turn-assistant'}`}>
      {m.role==='assistant'&&<div className="studio-assistant-sign">✳</div>}
      <div className="studio-turn-text">{m.content}</div>
     </div>)}
     {current.draft&&!current.launch&&<div className="studio-inline-plan">
       <div><strong>◇ Предварительный план готов</strong><span>Цель и бюджет можно изменить в чате.</span></div>
       <button onClick={()=>setReview(true)}>Посмотреть план</button>
      </div>}
     {current.launch&&<div className="studio-inline-plan">
       <div><strong>{statusText[current.launch.phase]||current.launch.phase}</strong><span>{current.launch.campaign_id||'Кампания подготавливается'}</span></div>
       <button onClick={()=>setReview(true)}>Подробнее</button>
      </div>}
    </div>}
    {busyId===chats.activeId&&<div className="studio-chat-thinking" role="status"><span className="studio-spinner"/> Codex отвечает…</div>}
    {error?.id===chats.activeId&&<div className="studio-chat-error" role="alert"><strong>Не удалось получить ответ</strong><span>{error.message}</span>{retryMessage&&<button onClick={()=>void send(true)} disabled={isBusy}>Повторить</button>}</div>}
    <div ref={scrollAnchor}/>
   </div>
   <div className="studio-chat-bottom">
    <form className="studio-chat-composer" onSubmit={event=>{event.preventDefault();void send()}}>
     <textarea ref={inputRef} rows={2} value={input} maxLength={3000} placeholder="Сообщение BananLoop…" aria-label="Сообщение в чате"
      disabled={isBusy||Boolean(current?.launch)} onChange={e=>setInput(e.target.value)}
      onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();void send()}}}/>
     <div className="studio-chat-composer-bar">
      <div className="studio-mode-switch" role="group" aria-label="Режим работы">
       <button type="button" aria-pressed={(current?.mode??'full_auto')==='full_auto'} onClick={()=>changeMode('full_auto')} disabled={Boolean(current?.launch)}>Full Auto</button>
       <button type="button" aria-pressed={current?.mode==='supervised'} onClick={()=>changeMode('supervised')} disabled={Boolean(current?.launch)}>Supervised</button>
      </div>
      <button type="submit" className="studio-chat-send" aria-label="Отправить" disabled={!input.trim()||isBusy||Boolean(current?.launch)}>↑</button>
     </div>
    </form>
    <div className="studio-chat-footnote">Обсуждение не запускает исследование. Запуск требует подтверждения.</div>
   </div>
  </main>
  {review&&current&&<div className="studio-review-backdrop" role="presentation" onMouseDown={e=>{if(e.currentTarget===e.target)setReview(false)}}>
   <aside className="studio-review-panel" role="dialog" aria-modal="true" aria-label="План кампании">
    <header><div><small>CAMPAIGN PLAN</small><h2>План исследования</h2></div><button onClick={()=>setReview(false)} aria-label="Закрыть план">×</button></header>
    {current.draft&&<>
     <div className="studio-review-goal">{current.draft.goal}</div>
     <h3>Бюджет</h3><dl>{Object.entries(current.draft.budgets).map(([k,v])=><div key={k}><dt>{budgetLabels[k as keyof typeof budgetLabels]}</dt><dd>{v}</dd></div>)}</dl>
     {!!current.draft.missing.length&&<section className="studio-review-missing"><h3>Ещё потребуется</h3>{current.draft.missing.map((m,i)=><p key={i}>{m}</p>)}</section>}
     {!current.launch&&<><p className="studio-review-warning">Запуск расходует лимиты Codex. Если для исследования нет evaluator, BananLoop запросит дополнительные данные.</p>
      <button className="studio-review-launch" disabled={isBusy} onClick={()=>void start()}>{pendingLaunchId===current.id?'Запуск…':current.mode==='supervised'?'Подготовить кампанию':'Начать исследование'}</button></>}
    </>}
    {current.launch&&<section className="studio-review-result" aria-live="polite"><h3>Статус</h3><strong>{statusText[current.launch.phase]||current.launch.phase}</strong>
     {current.launch.note&&<p>{current.launch.note}</p>}
     {current.launch.phase==='AWAITING_CONFIRMATION'&&<button className="studio-review-launch" disabled={isBusy} onClick={()=>void confirmPrepared()}>Подтвердить выполнение</button>}
     {!!current.launch.campaign_id&&<button className="studio-review-secondary" onClick={()=>onOpenCampaign(current.launch!.campaign_id)}>Открыть Mission Control</button>}
    </section>}
    {error?.id===current.id&&<p className="studio-chat-error" role="alert">{error.message}</p>}
   </aside>
  </div>}
 </div>;
}
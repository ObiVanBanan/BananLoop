import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {confirmStudioLaunch,discussStudio,getStudioLaunch,launchStudio} from './api/client';
import type {StudioDraft,StudioLaunch,StudioMessage,StudioMode} from './api/client';

type StudioProps={token:string;onOpenCampaign:(id:string)=>void};
const statuses:Record<string,string>={
  PREPARING:'Подготовка кампании и проверка evaluator',
  BOOTSTRAPPING_OR_RUNNING:'Engine выполняет bootstrap или исследование',
  NEEDS_INPUT:'Нужны дополнительные данные для проверяемого исследования',
  AWAITING_CONFIRMATION:'Подготовка завершена — необходимо твоё подтверждение',
  FAILED:'Запуск завершился с ошибкой (проверь локальный журнал)',
  FINISHED:'Процесс Engine завершился. Результат смотри в Mission Control',
  UNKNOWN:'Статус неизвестен после перезапуска API; проверь кампанию в списке',
};
const budgetTitles:Record<keyof StudioDraft['budgets'],string>={
  max_experiments:'Эксперименты',max_attempts:'Попытки',max_agent_calls:'Вызовы агента',
  max_evaluator_calls:'Оценки',deadline_seconds:'Время, сек',
};
export function Studio({token,onOpenCampaign}:StudioProps){
 const [messages,setMessages]=useState<StudioMessage[]>([]);
 const [input,setInput]=useState('');
 const [mode,setMode]=useState<StudioMode>('full_auto');
 const [draft,setDraft]=useState<StudioDraft|null>(null);
 const [busy,setBusy]=useState(false);
 const [launching,setLaunching]=useState(false);
 const [confirm,setConfirm]=useState(false);
 const [error,setError]=useState('');
 const [requestId,setRequestId]=useState<string|null>(null);
 const [launch,setLaunch]=useState<StudioLaunch|null>(null);
 const bottom=useRef<HTMLDivElement|null>(null);
 useEffect(()=>{
  // scrollIntoView may return a Promise in modern browsers. React effects
  // must return only undefined or a cleanup function, never that Promise.
  bottom.current?.scrollIntoView({behavior:'smooth',block:'end'});
 },[messages,busy,launch]);
 const sentMessages=useMemo(()=>messages.slice(-16),[messages]);
 const send=useCallback(async()=>{
  const message=input.trim();
  if(!message||busy||launching||message.length>3000)return;
  setInput('');setError('');setConfirm(false);setBusy(true);
  setMessages(old=>[...old,{role:'user',content:message}]);
  try{
   const reply=await discussStudio(token,{message,history:sentMessages});
   setMessages(old=>[...old,{role:'assistant',content:reply.reply}]);
   if(reply.draft){setDraft(reply.draft);setRequestId(null)}
  }catch(e){setError(String(e))}
  finally{setBusy(false)}
 },[input,busy,launching,sentMessages,token]);
 const start=useCallback(async()=>{
  if(!draft||busy||launching)return;
  if(!confirm){setConfirm(true);return}
  setLaunching(true);setError('');
  const key=requestId??`ui-${crypto.randomUUID().replace(/-/g,'')}`;
  setRequestId(key);
  try{
   const response=await launchStudio(token,{goal:draft.goal,mode,budgets:draft.budgets,request_id:key});
   setLaunch(response);setConfirm(false);
  }catch(e){setError(String(e))}
  finally{setLaunching(false)}
 },[draft,busy,launching,confirm,mode,token,requestId]);
 const continuePrepared=useCallback(async()=>{
  if(!requestId||launching)return;
  setLaunching(true);setError('');
  try{setLaunch(await confirmStudioLaunch(token,requestId))}
  catch(e){setError(String(e))}
  finally{setLaunching(false)}
 },[token,requestId,launching]);
 useEffect(()=>{
  if(!requestId||!launch||!['PREPARING','BOOTSTRAPPING_OR_RUNNING'].includes(launch.phase))return;
  let live=true;
  const tick=()=>void getStudioLaunch(token,requestId).then(v=>{if(live)setLaunch(v)}).catch(()=>{});
  const timer=window.setInterval(tick,2000);
  return()=>{live=false;window.clearInterval(timer)};
 },[launch?.phase,requestId,token]);
 return <div className="studio-shell">
   <header className="studio-header"><div><div className="studio-kicker">NEW CAMPAIGN / PLANNING</div><h1>Начни с идеи<span>.</span></h1><p>Обсуди цель с Codex, согласуй метрику и лимиты. Папку проекта указывать не нужно.</p></div>
    <div className="studio-sigil" aria-hidden="true">✳</div></header>
   <div className="studio-content">
    <section className="studio-dialog" aria-label="Обсуждение кампании">
     <div className="studio-history">
      {!messages.length&&<div className="studio-welcome"><div className="studio-welcome-symbol">✦</div><h2>Что будем исследовать?</h2><p>Например: «Хочу улучшить качество RAG-поиска». Codex уточнит способ измерения результата, доступные данные и предложит разумный бюджет.</p><small>Это реальный вызов Codex через твою авторизацию. Без вызова модели кампания не планируется автоматически.</small></div>}
      {messages.map((message,i)=><div key={i} className={`studio-message ${message.role==='user'?'from-user':'from-agent'}`}><div className="studio-avatar">{message.role==='user'?'YOU':'✳'}</div><div className="studio-bubble"><small>{message.role==='user'?'ТЫ':'CODEX · ПЛАНИРОВАНИЕ'}</small><p>{message.content}</p></div></div>)}
      {busy&&<div className="studio-thinking" role="status"><span className="studio-spinner"/> Codex уточняет параметры исследования…</div>}
      <div ref={bottom}/>
     </div>
     <form className="studio-composer" onSubmit={event=>{event.preventDefault();void send()}}>
      <textarea rows={2} value={input} maxLength={3000} disabled={busy||launching||Boolean(launch)} onChange={e=>setInput(e.target.value)} placeholder="Расскажи, чего хочешь достичь…" aria-label="Сообщение планировщику" onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();void send()}}}/>
      <div className="studio-composer-footer"><div className="studio-mode" role="group" aria-label="Режим запуска">
        <button type="button" disabled={Boolean(launch)} className={mode==='full_auto'?'chosen':''} aria-pressed={mode==='full_auto'} onClick={()=>{setMode('full_auto');setConfirm(false)}}>Full Auto</button>
        <button type="button" disabled={Boolean(launch)} className={mode==='supervised'?'chosen':''} aria-pressed={mode==='supervised'} onClick={()=>{setMode('supervised');setConfirm(false)}}>Supervised</button>
       </div><button type="submit" disabled={!input.trim()||busy||launching||Boolean(launch)} className="studio-send" aria-label="Отправить сообщение">↑</button></div>
     </form>
     <p className="studio-mode-description">{mode==='supervised'?'Supervised: сначала подготовка, затем отдельное подтверждение запуска. Это не замена v0.2 Human-in-the-Loop.':'Full Auto: после твоего подтверждения запускается существующий Engine с выбранными лимитами.'}</p>
    </section>
    <aside className="studio-plan" aria-label="Черновик кампании"><div className="studio-plan-top"><span className="studio-kicker">CAMPAIGN BLUEPRINT</span><span className="studio-plan-state">{draft?'DRAFT':'AWAITING DISCUSSION'}</span></div>
      {!draft?<div className="studio-plan-placeholder"><div className="studio-orbit">◇</div><h2>Пока нет плана</h2><p>В переписке обсудим гипотезу, измеримую метрику, evaluator и доступный бюджет. Предложение появится здесь.</p></div>:<div className="studio-proposal"><h2>Предварительный план</h2><p className="studio-proposal-goal">{draft.goal}</p><div className="studio-plan-rule"/><h3>Рекомендованные ограничения</h3><dl>{Object.entries(draft.budgets).map(([key,value])=><div key={key}><dt>{budgetTitles[key as keyof StudioDraft['budgets']]}</dt><dd>{value}</dd></div>)}</dl><p className="studio-plan-hint">Чтобы изменить бюджет или цель, просто попроси Codex в чате. Числа не применяются без подтверждения.</p>
      {!!draft.missing.length&&<div className="studio-missing"><strong>Нужно уточнить</strong>{draft.missing.map((item,i)=><p key={i}>• {item}</p>)}</div>}
      {!launch&&<div className="studio-launch-actions">{confirm&&<div className="studio-confirm">Запустить штатный BananLoop Engine с этой целью и этими лимитами? Это может расходовать лимиты Codex. Без гарантии, что evaluator уже доступен.</div>}<button disabled={busy||launching||!messages.some(m=>m.role==='assistant')} onClick={()=>void start()}>{launching?'Запуск…':confirm?'Подтвердить запуск':'Review & Start'}</button>{confirm&&<button className="studio-link" onClick={()=>setConfirm(false)}>Отмена</button>}</div>}
      </div>}
      {launch&&<div className="studio-launch-result" role="status"><div className="studio-plan-rule"/><h3>Статус запуска</h3><strong>{statuses[launch.phase]||launch.phase}</strong><p>Campaign ID: <code>{launch.campaign_id}</code></p>{launch.note&&<p>{launch.note}</p>}
       {launch.phase==='AWAITING_CONFIRMATION'&&<button disabled={launching} onClick={()=>void continuePrepared()}>{launching?'Запуск…':'Подтвердить выполнение'}</button>}
       {launch.campaign_id&&<button className="studio-link" onClick={()=>onOpenCampaign(launch.campaign_id)}>Открыть Mission Control</button>}
      </div>}
      {error&&<p className="studio-error" role="alert">{error}</p>}
      <div className="studio-plan-footer">LOCAL STUDIO · CODEX CLI · NO ENGINE WRITES UNTIL START</div>
    </aside>
   </div>
  </div>;
}
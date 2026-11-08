import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {Galaxy} from './Galaxy';
import {toGalaxyFixture} from './domain/live';
import type {CampaignSummary,CampaignView,CampaignSnapshot,EngineEvent,CodexStatus} from './domain/live';
import {getCampaigns,getCodexStatus,getEvents,getSnapshot,getView,streamEvents} from './api/client';

type Screen='galaxy'|'mission'|'decisions'|'settings';
const sleep=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms));
function formatMetric(n:unknown){return typeof n==='number'&&Number.isFinite(n)?n.toFixed(4):'—'}
function providerText(provider:CodexStatus|null):string{
 if(!provider)return 'Проверяется…';
 if(!provider.installed)return 'Codex CLI не обнаружен на компьютере, где запущен API.';
 if(provider.auth_mode==='chatgpt')return 'ChatGPT подключён через Codex CLI. Используются лимиты ChatGPT-плана.';
 if(provider.auth_mode==='api_key')return 'Codex подключён через API key. Это отдельная тарификация, не ChatGPT-подписка.';
 if(provider.auth_mode==='unknown')return 'Codex авторизован, но тип оплаты не подтверждён. Не считаем это подпиской.';
 return 'ChatGPT не подключён. Авторизуй Codex в терминале компьютера, на котором запущен API.';
}
const maxEvents=100;
function mergeEvents(old:EngineEvent[],incoming:EngineEvent[]):EngineEvent[]{
 const map=new Map<number,EngineEvent>();for(const e of old.concat(incoming))if(Number.isSafeInteger(e.seq))map.set(e.seq,e);
 return [...map.values()].sort((a,b)=>a.seq-b.seq).slice(-maxEvents);
}
export function App(){
 const [screen,setScreen]=useState<Screen>('galaxy');
 const [token,setToken]=useState(''); // deliberately memory-only; no credential persistence
 const [campaigns,setCampaigns]=useState<CampaignSummary[]>([]);
 const [campaignId,setCampaignId]=useState('');
 const [view,setView]=useState<CampaignView|null>(null);
 const [snapshot,setSnapshot]=useState<CampaignSnapshot|null>(null);
 const [events,setEvents]=useState<EngineEvent[]>([]);
 const [codex,setCodex]=useState<CodexStatus|null>(null);
 const [error,setError]=useState('');
 const [loading,setLoading]=useState(true);
 const [connected,setConnected]=useState(false);
 const [eventStatus,setEventStatus]=useState('Подключение…');
 const cursor=useRef(0);
 const refreshProviders=useCallback(async()=>{
  try{setCodex(await getCodexStatus(token))}catch{setCodex(null)}
 },[token]);
 useEffect(()=>{void refreshProviders()},[refreshProviders]);
 useEffect(()=>{
  const abort=new AbortController();let alive=true;
  async function reload(){try{
   const result=await getCampaigns(token,abort.signal);
   if(!alive)return;
   setCampaigns(result.campaigns);
   setCampaignId(old=>result.campaigns.some(c=>c.campaign_id===old)?old:
    result.campaigns.find(c=>c.active)?.campaign_id||result.campaigns[0]?.campaign_id||'');
   setError('');setLoading(false);
  }catch(e){if(!alive)return;setError(String(e));setLoading(false);setConnected(false)}}
  void reload();const interval=window.setInterval(()=>void reload(),15000);
  return()=>{alive=false;abort.abort();window.clearInterval(interval)};
 },[token]);
 useEffect(()=>{
  cursor.current=0;setView(null);setSnapshot(null);setEvents([]);setConnected(false);setEventStatus('Подключение…');
  if(!campaignId)return;
  const abort=new AbortController();let alive=true;
  const reconcile=async()=>{
   try{
    const projection=await getView(campaignId,token,abort.signal);
    const snap=projection.source_schema===0?{schema_version:0,campaign_id:campaignId,status:'RESERVED'}:await getSnapshot(campaignId,token,abort.signal);
    if(!alive)return;
    setView(projection);setSnapshot(snap);setError('');
   }catch(e){if(!alive||abort.signal.aborted)return;setConnected(false);setError(String(e))}
  };
  async function catchUp(){
   // Gapless cursor replay. Does not treat an SSE notification as a command receipt.
   for(let i=0;i<10&&!abort.signal.aborted;i++){
    const page=await getEvents(campaignId,cursor.current,token,abort.signal);
    if(!alive)return;
    setEvents(old=>mergeEvents(old,page.events));
    cursor.current=Math.max(cursor.current,page.cursor);
    if(!page.has_more)break;
   }
  }
  const stream=async()=>{
   while(!abort.signal.aborted){
    try{
     await catchUp();if(!alive)return;
     setConnected(true);setEventStatus('SSE · LIVE');
     await streamEvents(campaignId,cursor.current,token,event=>{
       if(!alive||event.seq<=cursor.current)return;
       if(event.seq!==cursor.current+1){void catchUp().then(reconcile).catch(()=>{});return;}
       cursor.current=event.seq;setEvents(old=>mergeEvents(old,[event]));
       void reconcile();
     },abort.signal);
     if(!abort.signal.aborted)throw new Error('SSE closed');
    }catch(e){
     if(!alive||abort.signal.aborted)break;
     setConnected(false);setEventStatus('Повторное подключение');
     if(String(e).includes('400')){cursor.current=0;setEvents([])}
     await sleep(1700);
    }
   }
  };
  void reconcile();void stream();
  const refresh=window.setInterval(()=>{void reconcile()},6000);
  return()=>{alive=false;abort.abort();window.clearInterval(refresh)};
 },[campaignId,token]);
 const fixture=useMemo(()=>view?toGalaxyFixture(view):null,[view]);
 const navigation=(value:Screen)=>setScreen(value);
 if(screen==='galaxy'&&view&&fixture){
  return <Galaxy key={campaignId} fixture={fixture} campaigns={campaigns} onCampaignChange={setCampaignId} onNavigate={navigation} streamConnected={connected}/>;
 }
 return <div className="live-shell"><header className="live-topbar">
   <strong>bananaloop<span>.</span> <small className="live-tag">RESEARCH OS</small></strong>
   <nav aria-label="Главные экраны">{([['galaxy','Galaxy'],['mission','Mission Control'],['decisions','Decision Center'],['settings','Settings']] as const).map(([id,name])=><button key={id} aria-current={screen===id?'page':undefined} onClick={()=>setScreen(id)}>{name}</button>)}</nav>
   <select className="live-select" aria-label="Выбор кампании" value={campaignId} onChange={e=>setCampaignId(e.target.value)}><option value="">Выбрать кампанию</option>{campaigns.map(c=><option key={c.campaign_id} value={c.campaign_id}>{c.goal||c.campaign_id}</option>)}</select>
  </header>
  <main className="live-screen">
   {screen==='galaxy'&&<><div className="live-head"><span className="live-tag">RESEARCH GALAXY · REAL DATA</span><h1>Исследовательское пространство</h1><p>Интерфейс ожидает реальный snapshot BananLoop. Синтетическая Galaxy здесь не подставляется.</p></div>
    {loading?<div className="live-empty">Подключение к локальному API…</div>:!campaigns.length?<div className="live-empty"><strong>Пока нет кампаний.</strong><p>Запусти BananLoop в этой репозитории через CLI. Список обновляется автоматически.</p><p>API: <code>bananloop serve --repo /path/to/project</code></p></div>:!view?<div className="live-empty">Загрузка выбранной кампании…</div>:null}</>}
   {screen==='mission'&&<><div className="live-head"><span className="live-tag">MISSION CONTROL · COMMITTED EVENTS</span><h1>Пульс исследования</h1><p>Только данные существующей кампании. Отправка chat-команд и управление Engine здесь не имитируются.</p></div>
    <div className="live-cards"><section className="live-card"><h2>Кампания</h2><div className="live-metric">{snapshot?.status||view?.status||'—'}</div><p>{view?.goal||'Кампания не выбрана'}</p><p>Стадия: {snapshot?.display_stage||snapshot?.engine_stage||'Недоступна для этого формата'}</p><p>Ревизия: {view?.revision??'—'} · События: {events.length}</p><p className="live-status">{snapshot?.stale?'Снимок устарел · ':''}{eventStatus}</p></section>
      <section className="live-card"><h2>DEV / budget</h2><div className="live-metric">{formatMetric(snapshot?.metric?.champion)}</div><p>Champion DEV score · {snapshot?.metric?.name||'нет метрики'}</p><p>Baseline: {formatMetric(snapshot?.metric?.baseline)}</p><p>Эксперименты: {snapshot?.budget?.experiments??'—'} / {snapshot?.budget?.experiment_limit??'—'}</p><p>Вызовы агентов: {snapshot?.budget?.agent_calls??'—'} / {snapshot?.budget?.agent_call_limit??'—'}</p></section>
      <section className="live-card"><h2>Последние события</h2>{events.length?<ul className="live-events">{events.slice(-9).reverse().map(e=><li key={e.seq}>#{e.seq} · {e.type}<small>{e.created_at}</small></li>)}</ul>:<p>Пока нет подтверждённых событий.</p>}</section></div>
    </>}
   {screen==='decisions'&&<><div className="live-head"><span className="live-tag">DECISION CENTER · OBSERVATION</span><h1>Решения и контроль</h1><p>Текущие ожидающие решения отображаются только при наличии подтверждённых данных; кнопки approve/reject пока не подключены.</p></div>
    {snapshot?.pivot_review?.proposal?<section className="live-card" style={{marginTop:26,maxWidth:740}}><h2>Предложение смены архитектуры</h2><p><strong>{snapshot.pivot_review.proposal.proposed_architecture}</strong></p><p>{snapshot.pivot_review.proposal.rationale}</p><p>Ожидаемый эффект: {snapshot.pivot_review.proposal.expected_effect}</p><p>Статус: {snapshot.pivot_review.status} · Проверка целостности: {String(snapshot.pivot_review.integrity_valid)}</p><p className="live-note">Любое подтверждение требует отдельного доверенного операторского действия.</p></section>
    :<div className="live-empty">{view?.source_schema===2?'Для v2 Decision Center пока нет безопасной проекции решений в API. Не показываем синтетические approvals.':'Нет ожидающих подтверждений в доступном snapshot.'}</div>}</>}
   {screen==='settings'&&<><div className="live-head"><span className="live-tag">SETTINGS / AI PROVIDERS</span><h1>ChatGPT / Codex CLI</h1><p>BananLoop использует локальный Codex CLI. Авторизация ChatGPT проверяется на компьютере с запущенным BananLoop API, без передачи токенов браузеру.</p></div>
    <div className="live-cards"><section className="live-card"><h2>Подключение модели</h2><div className="live-metric">{codex?.auth_mode==='chatgpt'?'CONNECTED':codex?.auth_mode==='api_key'?'API KEY':'NOT READY'}</div><p>{providerText(codex)}</p><p className="live-note">Запусти в терминале, где работает BananLoop:</p><pre>codex login</pre><p>Для устройства без браузера: <code>codex login --device-auth</code></p><button onClick={()=>void refreshProviders()}>Обновить статус подключения</button><p className="live-note">BananLoop UI не хранит OAuth-токены и не использует неофициальные API ChatGPT. Прямая авторизация внутри интерфейса — отдельная интеграция через Codex app-server.</p></section>
     <section className="live-card"><h2>Локальный API BananLoop</h2><p>При необходимости введи bearer-токен от <code>bananloop serve --token</code>.</p><input type="password" autoComplete="off" aria-label="Токен локального API" value={token} onChange={e=>setToken(e.target.value)} placeholder="Не нужен без --token"/><p className="live-note">Токен хранится только в памяти этой вкладки и исчезает при перезагрузке. Никогда не вставляй сюда ChatGPT OAuth или Codex auth.json.</p><p className="live-status">{connected?'SSE подключён':'Ожидание соединения'}</p></section></div>
   </>}
   {error&&<div className="live-warning" role="alert">Связь с BananLoop: {error}. Проверь, что запущен <code>bananloop serve --repo /path/to/project</code>, и указана нужная директория с кампаниями.</div>}
  </main>
 </div>;
}
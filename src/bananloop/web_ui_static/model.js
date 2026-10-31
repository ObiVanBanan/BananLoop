/* Deliberately browser + Node compatible. Model projection is DEV-only and allowlisted. */
(function (root) {
  'use strict';
  const STAGES = [
    {name:'PROPOSE',icon:'✎',desc:'Идеи',description:'Агент формирует гипотезы с учётом предыдущих DEV-результатов.'},
    {name:'SELECT',icon:'▣',desc:'Выбор',description:'Отбор допустимых гипотез с детерминированными ограничениями.'},
    {name:'IMPLEMENT',icon:'⌘',desc:'Код',description:'Агент вносит изменения в изолированной рабочей копии.'},
    {name:'EVALUATE',icon:'▥',desc:'Метрики',description:'Независимая оценка DEV; ограничения и promotion проверяет supervisor.'},
    {name:'LEARN',icon:'♧',desc:'Память',description:'Фиксация результата и причин успеха или неудачи.'},
    {name:'REPEAT',icon:'↻',desc:'Повтор',description:'Переход к следующему эксперименту или корректное завершение.'}
  ];
  const stageMap = {PROPOSE:'PROPOSE',SELECT:'SELECT',EXECUTE:'IMPLEMENT',SNAPSHOT:'IMPLEMENT',VERIFY:'IMPLEMENT',EVALUATE:'EVALUATE',VALIDATE:'EVALUATE',DECIDE:'EVALUATE',PROMOTE:'LEARN',REJECT:'LEARN',ANALYZE:'LEARN',RECORD:'LEARN',REPEAT:'REPEAT'};
  function numeric(x){return (typeof x==='number'&&Number.isFinite(x))?x:null}
  function count(x){return Number.isSafeInteger(x)&&x>=0?x:null}
  function short(x,max=500){return typeof x==='string'?x.slice(0,max):''}
  function safeHtml(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
  function formatScore(v){return numeric(v)===null?'—':Number(v).toFixed(3)}
  function improvement(base, current, direction){if(numeric(base)===null||numeric(current)===null||base===0)return null;return (direction==='minimize'?(base-current):(current-base))/Math.abs(base)*100;}
  function formatImprovement(v){return v===null||!Number.isFinite(v)?'—':`${v>0?'+':''}${v.toFixed(1)}%`}
  function directionBetter(x,y,direction){return direction==='minimize'?x<y:x>y}
  function projected(raw){
    if(!raw||typeof raw!=='object'||raw.schema_version!==1||typeof raw.campaign_id!=='string')throw new Error('Требуется JSON из bananloop operator-snapshot (schema_version=1)');
    const m=raw.metric||{},b=raw.budget||{};
    const hypotheses=Array.isArray(raw.hypotheses)?raw.hypotheses.slice(0,1000).map((x,i)=>({ordinal:count(x.ordinal)??i+1,title:short(x.title,250)||`Гипотеза ${i+1}`,status:short(x.status,45)||'UNKNOWN',score:numeric(x.dev_score),id:count(x.experiment_id),candidateSnapshot:short(x.candidate_snapshot,128)})):[];
    const points=Array.isArray(raw.dev_points)?raw.dev_points.slice(0,1500).filter(x=>numeric(x.score)!==null).map((x,i)=>({ordinal:count(x.experiment_id)??i+1,score:x.score,evaluationId:count(x.evaluation_id)})):[];
    return {
      campaignId:short(raw.campaign_id,160),goal:short(raw.goal,1500)||'Цель не указана',status:short(raw.status,60)||'UNKNOWN',stage:stageMap[raw.engine_stage]||stageMap[raw.display_stage]||null,
      engineStage:short(raw.engine_stage,50)||'—',stale:raw.stale===true,revision:count(raw.revision)??0,
      updatedAt:short(raw.updated_at,60),experimentCount:count(raw.experiment_count)??hypotheses.length,completedEpochs:count(raw.completed_epochs),activeExperiment:count(raw.active_experiment_id),
      metric:{name:short(m.name,100)||'DEV score',direction:m.direction==='minimize'?'minimize':'maximize',baseline:numeric(m.baseline),champion:numeric(m.champion),target:numeric(m.target)},
      budget:{experiments:count(b.experiments),experimentLimit:count(b.experiment_limit),agentCalls:count(b.agent_calls),agentCallLimit:count(b.agent_call_limit),evaluatorCalls:count(b.evaluator_calls),evaluatorCallLimit:count(b.evaluator_call_limit)},
      points,hypotheses,missing:raw.missing&&typeof raw.missing==='object'?Object.keys(raw.missing).slice(0,25):[]
    };
  }
  function statusClass(v){const s=String(v??'').toUpperCase();if(/ACCEPT|MATCH|PROMOT|CHAMPION|SUCCESS/.test(s))return'accepted';if(/REJECT|FAIL|CANCEL|ERROR|INVALID/.test(s))return'rejected';if(/RUN|PEND|PROGRESS|EXECUTE|ACTIVE|QUEUED/.test(s))return'running';return'pending'}
  function eventProjection(raw){if(!raw||!Array.isArray(raw.events))throw new Error('Некорректный формат operator-events');return raw.events.slice(0,500).map(e=>({seq:count(e.seq),time:short(e.created_at,80),type:short(e.type,90),payload:e.payload&&typeof e.payload==='object'?e.payload:{}})).filter(x=>x.seq!==null)}
  const api={STAGES,stageMap,numeric,count,safeHtml,formatScore,improvement,formatImprovement,directionBetter,projected,statusClass,eventProjection};
  root.BananModel=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
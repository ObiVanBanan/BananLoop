/** UI-01.3: fixtures only. Do NOT import GraphStore or write to the Engine here. */
export type ResearchStatus = 'VERIFIED' | 'READY' | 'PENDING' | 'FAILED' | 'BLOCKED';
export type GraphEdgeType = 'REQUIRES' | 'DERIVED_FROM' | 'RELATED_TO' | 'GROUP_MEMBER';
export type ResearchVertex = Readonly<{
  id: string;
  title: string;
  description: string;
  phase: 'EXPLORATION' | 'OPTIMIZATION' | 'VERIFICATION' | 'METRIC_DISCOVERY';
  status: ResearchStatus;
  x: number;
  y: number;
}>;
export type LogicGate = Readonly<{ id: string; kind: 'AND' | 'OR'; x: number; y: number; detail: string }>;
export type GraphEdge = Readonly<{ id: string; source: string; target: string; type: GraphEdgeType }>;
export type Fixture = Readonly<{
  campaignId: string;
  title: string;
  revision: number;
  nodes: readonly ResearchVertex[];
  gates: readonly LogicGate[];
  edges: readonly GraphEdge[];
}>;
export const researchFixture: Fixture = Object.freeze({
  campaignId: 'demo-rag-quality',
  title: 'RAG Quality Boost',
  revision: 7,
  nodes: Object.freeze([
    {id:'cross',title:'Cross-encoder',description:'Precision-focused scoring model',phase:'EXPLORATION',status:'VERIFIED',x:65,y:100},
    {id:'dense',title:'Dense retrieval',description:'Vector candidate search',phase:'EXPLORATION',status:'VERIFIED',x:65,y:315},
    {id:'llm',title:'LLM reranking',description:'Prompt-based relevance ordering',phase:'OPTIMIZATION',status:'PENDING',x:65,y:535},
    {id:'hybrid',title:'Hybrid retrieval',description:'Merge sparse and dense signals',phase:'EXPLORATION',status:'READY',x:575,y:215},
    {id:'sparse',title:'Sparse retrieval',description:'Lexical matching branch',phase:'EXPLORATION',status:'READY',x:575,y:465},
    {id:'rerank',title:'Reranking quality',description:'Candidate ranking quality',phase:'VERIFICATION',status:'BLOCKED',x:575,y:665},
    {id:'latency',title:'Latency budget',description:'Runtime budget constraints',phase:'METRIC_DISCOVERY',status:'FAILED',x:1060,y:635},
    {id:'integration',title:'Integration check',description:'Independent end-to-end verification',phase:'VERIFICATION',status:'PENDING',x:1070,y:340}
  ] satisfies readonly ResearchVertex[]),
  gates: Object.freeze([
    {id:'gate-or',kind:'OR',x:412,y:225,detail:'Any verified member can satisfy the group'},
    {id:'gate-and',kind:'AND',x:914,y:373,detail:'All required members must be verified'}
  ] satisfies readonly LogicGate[]),
  edges: Object.freeze([
    {id:'e-cross-or',source:'cross',target:'gate-or',type:'GROUP_MEMBER'},
    {id:'e-dense-or',source:'dense',target:'gate-or',type:'GROUP_MEMBER'},
    {id:'e-or-hybrid',source:'gate-or',target:'hybrid',type:'REQUIRES'},
    {id:'e-hybrid-and',source:'hybrid',target:'gate-and',type:'GROUP_MEMBER'},
    {id:'e-sparse-and',source:'sparse',target:'gate-and',type:'GROUP_MEMBER'},
    {id:'e-and-integration',source:'gate-and',target:'integration',type:'REQUIRES'},
    {id:'e-llm-rerank',source:'llm',target:'rerank',type:'RELATED_TO'},
    {id:'e-rerank-integration',source:'rerank',target:'integration',type:'DERIVED_FROM'}
  ] satisfies readonly GraphEdge[])
});

export const statusCopy: Record<ResearchStatus, {name:string;detail:string}> = {
  VERIFIED:{name:'Завершено', detail:'Работа завершена, результат подтверждён независимой проверкой. Это не просто статус планировщика.'},
  READY:{name:'Готово',detail:'Предпосылки выполнены: можно запланировать работу. Это НЕ означает, что агент уже работает.'},
  PENDING:{name:'Ожидает',detail:'Работа ещё не готова к запуску или не назначена. Это не запрос подтверждения пользователя.'},
  FAILED:{name:'Не прошло',detail:'В этом демонстрационном сценарии проверка завершилась неуспешно.'},
  BLOCKED:{name:'Заблокировано',detail:'Переход невозможен до выполнения указанных зависимостей и ограничений.'}
};
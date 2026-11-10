import type { Fixture, ResearchStatus, ResearchVertex, GraphEdge, LogicGate } from './graph';

export type CampaignSummary={campaign_id:string;goal:string;active:boolean;initialized:boolean;origin:string};
export type CampaignList={schema_version:number;campaigns:CampaignSummary[]};
export type ViewNode={id:string;title?:string;phase:string;status:string;ready:boolean;blockers:string[];revision:number|null};
export type ViewEdge={source:string;target:string;type:string};
export type ViewGroup={id:string;owner:string;kind:'AND'|'OR';members:string[]};
export type CampaignView={schema_version:number;source_schema:number;campaign_id:string;goal:string;status:string;revision:number;nodes:ViewNode[];edges:ViewEdge[];groups:ViewGroup[];partial:boolean;capabilities:{graph:boolean;reasons:boolean;engine_writes:false}};
export type CampaignSnapshot={schema_version:number;campaign_id:string;revision?:number;status:string;goal?:string;display_stage?:string|null;engine_stage?:string|null;stale?:boolean;missing?:Record<string,string>;metric?:{name:string;baseline:number|null;champion:number|null;target:number|null};budget?:Record<string,number>;hypotheses?:Array<{ordinal:number;title:string|null;status:string;dev_score:number|null}>;pivot_review?:{status:string;integrity_valid:boolean;proposal?:{proposal_id:string;digest:string;rationale:string;proposed_architecture:string;expected_effect:string};decision?:{status:string;decision:string}}|null};
export type EngineEvent={seq:number;type:string;created_at:string;payload:Record<string,unknown>};
export type EventPage={events:EngineEvent[];cursor:number;latest_seq:number;has_more:boolean};
export type CodexStatus={provider:string;installed:boolean;authenticated:boolean;auth_mode:'chatgpt'|'api_key'|'unknown'|'none'|'unavailable';chatgpt_subscription:boolean;login_command:string};

export function statusFromEngine(node:ViewNode,version:number):ResearchStatus{
 if(version===2){
  if(node.status==='COMPLETED')return 'VERIFIED';
  if(node.status==='READY')return 'READY';
  if(node.status==='FAILED')return 'FAILED';
  if(node.status==='PENDING'&&node.blockers?.length)return 'BLOCKED';
  if(node.status==='PAUSED')return 'PAUSED';
  if(node.status==='CANCELLED')return 'CANCELLED';
  if(node.status==='PENDING')return 'PENDING';
  return 'UNKNOWN';
 }
 if(node.status==='COMPLETED')return 'COMPLETED';
 if(node.status==='FAILED')return 'FAILED';
 return 'UNKNOWN';
}
const safeId=(s:string)=>{
 const slug=s.replace(/[^a-zA-Z0-9_-]/g,'_').slice(0,65);
 let hash=2166136261;for(let i=0;i<s.length;i++)hash=Math.imul(hash^s.charCodeAt(i),16777619)>>>0;
 return `${slug}-${hash.toString(16).padStart(8,'0')}`;
};
/** Only projects the *supplied* graph facts. Never guesses research dependencies. */
export function toGalaxyFixture(view:CampaignView):Fixture{
 const nodes:ResearchVertex[]=view.nodes.map((n,index)=>({
  id:safeId(n.id),title:(n.title||n.id).slice(0,240),
  description:(n.blockers?.length?`Prerequisites: ${n.blockers.slice(0,5).join(', ')}`:`${n.phase} · ${n.status}`).slice(0,350),
  phase:n.phase,status:statusFromEngine(n,view.source_schema),
  x:80+(index%4)*360,y:110+Math.floor(index/4)*230,
 }));
 const nodeLookup=new Map(view.nodes.map((n,i)=>[n.id,nodes[i].id]));
 const groups:LogicGate[]=view.groups.filter(g=>nodeLookup.has(g.owner)).map((g,i)=>({id:`gate-${safeId(g.id)}`,kind:g.kind,
  x:260+(i%3)*420,y:220+Math.floor(i/3)*280,detail:`${g.kind}: ${g.members.length} prerequisites for ${g.owner}`}));
 const gateLookup=new Map(view.groups.map((g,i)=>[g.id,groups.find(x=>x.id===`gate-${safeId(g.id)}`)?.id]));
 const edges:GraphEdge[]=[];
 for(const [i,e] of view.edges.entries()){
  const source=nodeLookup.get(e.source),target=nodeLookup.get(e.target);
  if(source&&target&&['REQUIRES','RELATED_TO','DERIVED_FROM'].includes(e.type)){
   edges.push({id:`edge-${i}`,source,target,type:e.type as GraphEdge['type']});
  }
 }
 for(const [i,g] of view.groups.entries()){
  const gate=gateLookup.get(g.id),owner=nodeLookup.get(g.owner);
  if(!gate||!owner)continue;
  for(const [j,m] of g.members.entries()){
   const source=nodeLookup.get(m);
   if(source)edges.push({id:`group-${i}-member-${j}`,source,target:gate,type:'GROUP_MEMBER'});
  }
  edges.push({id:`group-${i}-owner`,source:gate,target:owner,type:'REQUIRES'});
 }
 return {campaignId:view.campaign_id,title:view.goal||view.campaign_id,revision:view.revision,nodes,gates:groups,edges};
}
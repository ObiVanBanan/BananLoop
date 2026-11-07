import { useCallback, useEffect, useMemo, useState } from 'react';
import type { MouseEvent } from 'react';
import {
  Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow,
  ReactFlowProvider, useEdgesState, useNodesState, useReactFlow
} from '@xyflow/react';
import type { Edge, Node, NodeProps, OnNodeDrag, OnNodesChange } from '@xyflow/react';
import { Button } from './components/ui/button';

type NodeStatus = 'verified'|'ready'|'pending'|'failed';
type ResearchData = { title:string; sub:string; stage:string; status:NodeStatus; nodeId:string };
type LogicData = {kind:'AND'|'OR'; note:string};
type PersonalNoteData = {text:string; change:(id:string,text:string)=>void};
type ResearchNodeT = Node<ResearchData, 'research'>;
type GateNodeT = Node<LogicData,'gate'>;
type PersonalNoteT = Node<PersonalNoteData,'note'>;
type GalaxyNode = ResearchNodeT | GateNodeT | PersonalNoteT;

function ResearchCard({data,selected}:NodeProps<ResearchNodeT>){return <div className={`research-card status-${data.status} ${selected?'selected':''}`}>
  <Handle type="target" position={Position.Left} isConnectable={false}/>
  <div className="research-top">{data.nodeId}<span>{data.stage}</span></div>
  <strong>{data.title}</strong><p>{data.sub}</p><div className="research-state">{data.status}</div>
  <Handle type="source" position={Position.Right} isConnectable={false}/>
</div>}
function GateCard({data}:NodeProps<GateNodeT>){return <div className="gate-card"><Handle type="target" position={Position.Left} isConnectable={false}/><strong>{data.kind}</strong><small>{data.note}</small><Handle type="source" position={Position.Right} isConnectable={false}/></div>}
function NoteCard({id,data}:NodeProps<PersonalNoteT>){return <div className="personal-note"><div>✎ PERSONAL NOTE</div><textarea className="nodrag nowheel" aria-label="Личная заметка" value={data.text} onChange={e=>data.change(id,e.target.value)}/></div>}
const nodeTypes={ research: ResearchCard, gate:GateCard, note:NoteCard };

const initialResearch:GalaxyNode[]=[
  {id:'cross',type:'research',position:{x:60,y:120},data:{nodeId:'N-CROSS',title:'Cross-encoder',sub:'Precision-focused scoring',stage:'EXPLORATION',status:'verified'}},
  {id:'dense',type:'research',position:{x:60,y:320},data:{nodeId:'N-DENSE',title:'Dense retrieval',sub:'Vector candidate search',stage:'EXPLORATION',status:'verified'}},
  {id:'llm',type:'research',position:{x:60,y:525},data:{nodeId:'N-LLM',title:'LLM reranking',sub:'Prompt-based relevance',stage:'OPTIMIZATION',status:'pending'}},
  {id:'or',type:'gate',position:{x:405,y:240},data:{kind:'OR',note:'ANY INPUT'}},
  {id:'hybrid',type:'research',position:{x:560,y:240},data:{nodeId:'N-HYBRID',title:'Hybrid retrieval',sub:'Merge sparse + dense',stage:'EXPLORATION',status:'ready'}},
  {id:'sparse',type:'research',position:{x:560,y:500},data:{nodeId:'N-SPARSE',title:'Sparse retrieval',sub:'Lexical retrieval branch',stage:'EXPLORATION',status:'ready'}},
  {id:'and',type:'gate',position:{x:895,y:355},data:{kind:'AND',note:'ALL INPUTS'}},
  {id:'integration',type:'research',position:{x:1075,y:350},data:{nodeId:'N-INTEGRATION',title:'Integration check',sub:'Independent verification',stage:'VERIFICATION',status:'pending'}}
];
const coreEdge = (id:string,source:string,target:string):Edge => ({id,source,target,type:'smoothstep',markerEnd:{type:MarkerType.ArrowClosed},className:'engine-edge'});
const initialEdges:Edge[]=[
  coreEdge('e1','cross','or'),coreEdge('e2','dense','or'),coreEdge('e3','or','hybrid'),
  coreEdge('e4','hybrid','and'),coreEdge('e5','sparse','and'),coreEdge('e6','and','integration'),
  {id:'e7',source:'llm',target:'integration',type:'smoothstep',className:'related-edge'}
];
function GalaxyCanvas(){
  const { screenToFlowPosition,fitView }=useReactFlow();
  const [notes, setNotes]=useState<Record<string,string>>({});
  const noteChange=useCallback((id:string,text:string)=>setNotes(prev=>({...prev,[id]:text})),[]);
  const starterNodes=useMemo(()=>initialResearch.map(n=>({...n})),[]);
  const [nodes,setNodes,onNodesChange]=useNodesState<GalaxyNode>(starterNodes);
  const [edges,setEdges,onEdgesChange]=useEdgesState(initialEdges);
  const [theme,setTheme]=useState<'studio'|'noir'|'signal'>('noir');
  const [selected,setSelected]=useState<GalaxyNode|null>(null);
  const [collapsed,setCollapsed]=useState(false);
  const [arrowStart,setArrowStart]=useState<string|null>(null);
  const [arrowTool,setArrowTool]=useState(false);
  const [pendingNote,setPendingNote]=useState(false);
  const addNote=useCallback((clientX:number,clientY:number)=>{
    const position=screenToFlowPosition({x:clientX,y:clientY});
    const id=`note-${Date.now()}`;
    setNodes(old=>[...old,{id,type:'note',position,data:{text:'',change:noteChange}}]);
  },[screenToFlowPosition,setNodes,noteChange]);
  useEffect(()=>{
    setNodes(old=>old.map(n=>n.type==='note'?{...n,data:{...n.data,text:notes[n.id]??n.data.text,change:noteChange}}:n));
  },[notes,setNodes,noteChange]);
  const nodeClick=useCallback((_event:MouseEvent,node:GalaxyNode)=>{
    if(arrowTool){
      if(!arrowStart){setArrowStart(node.id);return}
      if(arrowStart!==node.id){setEdges(current=>[...current,{id:`personal-${Date.now()}`,source:arrowStart,target:node.id,type:'smoothstep',className:'personal-edge',markerEnd:{type:MarkerType.ArrowClosed}}])}
      setArrowStart(null);setArrowTool(false);return;
    }
    setSelected(node);
  },[arrowStart,arrowTool,setEdges]);
  const onPaneDoubleClick=useCallback((ev:MouseEvent)=>addNote(ev.clientX,ev.clientY),[addNote]);
  const addCenterNote=useCallback(()=>{const width=window.innerWidth,height=window.innerHeight;addNote(width/2,height/2)},[addNote]);
  const selectedResearch=selected?.type==='research'?selected.data:null;
  const selectedGate=selected?.type==='gate'?selected.data:null;
  return <div className={`galaxy-app theme-${theme} ${collapsed?'collapsed':''}`}>
    <header><div className="brand"><span>◈</span> bananaloop<b>.</b><small>RESEARCH OS</small></div><span className="header-route">WORKSPACE / RESEARCH GALAXY</span><span className="demo-tag">SYNTHETIC RESEARCH</span></header>
    <aside><Button variant="quiet" onClick={()=>setCollapsed(x=>!x)}>{collapsed?'→':'←'} <span className="collapse-label">Collapse</span></Button><div className="rail-section">MISSIONS</div><div className="active-project">◈ <span>RAG Quality Boost<small>8 nodes / 2 logic gates</small></span></div><div className="rail-section">SPACES</div><div className="active-project">✦ <span>Research Galaxy</span></div></aside>
    <main><div className="headline"><div><div className="eyebrow">GALAXY / EXPERIMENT 004</div><h1>Ideas, in motion<span>.</span></h1><p>Spatial interface prototype with a real React Flow graph layer.</p></div></div><nav className="toolbar"><span>APPEARANCE</span>{(['studio','noir','signal'] as const).map(t=><Button key={t} variant={theme===t?'default':'quiet'} onClick={()=>setTheme(t)}>{t==='studio'?'01 Atelier':t==='noir'?'02 Banana Noir':'03 Signal Room'}</Button>)}<div className="spacer"/><Button variant="outline" onClick={addCenterNote}>+ Note</Button><Button variant="outline" onClick={()=>{setArrowTool(x=>!x);setArrowStart(null)}}>{arrowTool?'Cancel arrow':'↗ Arrow'}</Button><Button variant="outline" onClick={()=>fitView({padding:.18,duration:250})}>Fit view</Button></nav>
    <div className="flow-container"><ReactFlow<GalaxyNode,Edge> nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={nodeClick} onPaneDoubleClick={onPaneDoubleClick} onPaneClick={()=>setSelected(null)} fitView fitViewOptions={{padding:.18}} nodesDraggable nodesConnectable={false} zoomOnScroll panOnDrag panOnScroll={false} minZoom={.38} maxZoom={2.3}>
      <Background gap={28} size={1}/><MiniMap zoomable pannable nodeColor={n=>n.type==='gate'?'#f3c46d':'#7598a7'}/><Controls showInteractive={false}/>
    </ReactFlow>{arrowTool&&<div className="arrow-banner">{arrowStart?`SOURCE: ${arrowStart} · choose destination`:'ARROW TOOL · choose source and destination'}</div>}</div>
    <footer>DESIGN EXPLORATION / NO ENGINE MUTATIONS</footer></main>
    {selected&&<section className="inspect"><div className="inspect-meta">NODE INSPECTOR <Button variant="quiet" onClick={()=>setSelected(null)}>✕</Button></div><h2>{selectedResearch?.title??(selectedGate?`${selectedGate.kind} logic gate`:'Personal note')}</h2>{selectedResearch?<><p>{selectedResearch.sub}</p><div className="ins-badge">{selectedResearch.status.toUpperCase()}</div><hr/><small>STAGE</small><p>{selectedResearch.stage}</p></>:<p>{selectedGate?.note??'Local annotation; not an Engine object.'}</p>}</section>}
  </div>
}
export function Galaxy(){return <ReactFlowProvider><GalaxyCanvas/></ReactFlowProvider>}
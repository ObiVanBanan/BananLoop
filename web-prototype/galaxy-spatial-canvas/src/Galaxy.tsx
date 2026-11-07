import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import type {MouseEvent,PointerEvent as ReactPointerEvent,CSSProperties} from 'react';
import {
  applyNodeChanges,Background,Controls,MarkerType,MiniMap,ReactFlow,
  ReactFlowProvider,useReactFlow
} from '@xyflow/react';
import type {Edge,NodeChange,NodeMouseHandler,OnNodeDrag,Viewport} from '@xyflow/react';
import {researchFixture,statusCopy} from './domain/graph';
import {addPersonalArrow,defaultPersonalCanvas,loadCanvas,removePersonalNote,saveCanvas} from './state/personalCanvas';
import type {PersonalCanvas} from './state/personalCanvas';
import {Button} from './components/ui';
import {nodeTypes} from './components/CanvasNodes';
import type {GalaxyNode,NoteFlowNode} from './components/CanvasNodes';

const safeStorage=():Storage|null=>{try{return window.localStorage}catch{return null}};
const fixture=researchFixture;
const graphIds=[...fixture.nodes.map(n=>n.id),...fixture.gates.map(g=>g.id)];
const positionOf=(n:{id:string;x:number;y:number},state:PersonalCanvas)=>state.positions[n.id]??{x:n.x,y:n.y};
function initialNodes(state:PersonalCanvas):GalaxyNode[]{
  const research=fixture.nodes.map(n=>({id:n.id,type:'research' as const,position:positionOf(n,state),draggable:true,deletable:false,data:{nodeId:n.id.toUpperCase(),title:n.title,description:n.description,phase:n.phase,status:n.status}}));
  const gates=fixture.gates.map(g=>({id:g.id,type:'gate' as const,position:positionOf(g,state),draggable:true,deletable:false,data:{kind:g.kind,detail:g.detail}}));
  const notes=state.notes.map(n=>({id:n.id,type:'note' as const,position:{x:n.x,y:n.y},draggable:true,deletable:false,data:{text:n.text,onEdit:()=>undefined,onRemove:()=>undefined}}));
  return [...research,...gates,...notes] as GalaxyNode[];
}
const researchEdges:Edge[]=fixture.edges.map(e=>({
  id:e.id,source:e.source,target:e.target,type:'smoothstep',animated:false,
  selectable:true,deletable:false,markerEnd:{type:MarkerType.ArrowClosed,width:14,height:14},
  className:`engine-edge edge-${e.type.toLowerCase()}`,
  label:e.type==='RELATED_TO'?'RELATED':e.type==='DERIVED_FROM'?'DERIVED':undefined,
  labelStyle:{fontSize:9,letterSpacing:'0.07em'},
  labelBgPadding:[5,3],labelBgBorderRadius:2
}));

function GalaxyBody(){
  const initial=useMemo(()=>loadCanvas(typeof window!=='undefined'?safeStorage():null,fixture.campaignId,graphIds),[]);
  const [personal,setPersonal]=useState<PersonalCanvas>(initial);
  const [nodes,setNodes]=useState<GalaxyNode[]>(()=>initialNodes(initial));
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [arrowMode,setArrowMode]=useState(false);
  const [arrowSource,setArrowSource]=useState<string|null>(null);
  const [hint,setHint]=useState('');
  const {screenToFlowPosition,fitView}=useReactFlow();
  const railRef=useRef<{startX:number;startWidth:number;pointerId:number}|null>(null);
  const saveTimer=useRef<number|null>(null);
  const latestPersonal=useRef<PersonalCanvas>(initial);
  latestPersonal.current=personal;

  useEffect(()=>{
    if(saveTimer.current!==null)window.clearTimeout(saveTimer.current);
    saveTimer.current=window.setTimeout(()=>saveCanvas(safeStorage(),fixture.campaignId,personal),180);
    return ()=>{if(saveTimer.current!==null)window.clearTimeout(saveTimer.current)};
  },[personal]);

  // A final synchronous write prevents losing a drag/note on an immediate browser refresh.
  useEffect(()=>{const flush=()=>{saveCanvas(safeStorage(),fixture.campaignId,latestPersonal.current)};window.addEventListener('pagehide',flush);return()=>window.removeEventListener('pagehide',flush)},[]);

  const noteEdit=useCallback((id:string,text:string)=>setPersonal(current=>({...current,notes:current.notes.map(n=>n.id===id?{...n,text:text.slice(0,2000)}:n)})),[]);
  const noteDelete=useCallback((id:string)=>{
    setPersonal(current=>removePersonalNote(current,id));
    setNodes(current=>current.filter(n=>n.id!==id));
    setSelectedId(current=>current===id?null:current);
  },[]);
  const visibleNodes=useMemo(()=>nodes.map(n=>{
    if(n.type!=='note')return n;
    const note=personal.notes.find(item=>item.id===n.id);
    return {...n,data:{...n.data,text:note?.text??'',onEdit:noteEdit,onRemove:noteDelete}} as NoteFlowNode;
  }),[nodes,personal.notes,noteEdit,noteDelete]);
  const allowedIds=useMemo(()=>[...graphIds,...personal.notes.map(n=>n.id)],[personal.notes]);
  const personalEdges=useMemo<Edge[]>(()=>personal.arrows.map(e=>({id:e.id,source:e.source,target:e.target,type:'smoothstep',className:'personal-edge',deletable:false,markerEnd:{type:MarkerType.ArrowClosed,width:13,height:13}})),[personal.arrows]);
  const allEdges=useMemo(()=>[...researchEdges,...personalEdges],[personalEdges]);
  const onNodesChange=useCallback((changes:NodeChange<GalaxyNode>[])=>{
    // Only UI positions/selection may change. No node removal or Engine-side mutations.
    setNodes(current=>applyNodeChanges(changes.filter(c=>c.type!=='remove'),current));
  },[]);
  const onNodeDragStop:OnNodeDrag<GalaxyNode>=useCallback((_event,node)=>{
    if(node.type==='note'){
      setPersonal(current=>({...current,notes:current.notes.map(n=>n.id===node.id?{...n,...node.position}:n)}));
    }else if(graphIds.includes(node.id)){
      setPersonal(current=>({...current,positions:{...current.positions,[node.id]:node.position}}));
    }
  },[]);
  const onMoveEnd=useCallback((_:unknown,viewport:Viewport)=>{
    setPersonal(current=>({...current,viewport:{x:viewport.x,y:viewport.y,zoom:viewport.zoom}}));
  },[]);
  const addNote=useCallback((clientX:number,clientY:number)=>{
    if(personal.notes.length>=60){setHint('Лимит 60 личных заметок');return}
    const position=screenToFlowPosition({x:clientX,y:clientY});
    const id=`note-${Date.now()}-${Math.random().toString(36).slice(2,7)}`;
    setPersonal(current=>({...current,notes:[...current.notes,{id,...position,text:''}]}));
    setNodes(old=>[...old,{id,type:'note',position,draggable:true,deletable:false,data:{text:'',onEdit:noteEdit,onRemove:noteDelete}}]);
    setSelectedId(id);setHint('Новая заметка · только на этом устройстве');
  },[personal.notes.length,screenToFlowPosition,noteEdit,noteDelete]);
  const onPaneClick=useCallback((event:MouseEvent)=>{if(event.detail===2){addNote(event.clientX,event.clientY)}else if(!arrowMode){setSelectedId(null)}},[addNote,arrowMode]);
  const onNodeClick:NodeMouseHandler<GalaxyNode>=useCallback((_event,node)=>{
    if(arrowMode){
      if(!arrowSource){setArrowSource(node.id);setHint('Выбери конечную ноду для личной стрелки');return}
      setPersonal(current=>addPersonalArrow(current,arrowSource,node.id,[...graphIds,...current.notes.map(n=>n.id)]));
      setArrowMode(false);setArrowSource(null);setHint('Личная стрелка добавлена (не связь Engine)');return;
    }
    setSelectedId(node.id);setHint('');
  },[arrowMode,arrowSource]);
  const selectedNode=visibleNodes.find(n=>n.id===selectedId);
  const selectedResearch=selectedNode?.type==='research'?selectedNode.data:null;
  const selectedGate=selectedNode?.type==='gate'?selectedNode.data:null;
  const selectedNote=selectedNode?.type==='note';

  const onRailPointerDown=(ev:ReactPointerEvent<HTMLDivElement>)=>{
    if(personal.railCollapsed)return;
    railRef.current={startX:ev.clientX,startWidth:personal.railWidth,pointerId:ev.pointerId};ev.currentTarget.setPointerCapture(ev.pointerId);
  };
  const onRailPointerMove=(ev:ReactPointerEvent<HTMLDivElement>)=>{
    if(!railRef.current||railRef.current.pointerId!==ev.pointerId)return;
    const width=Math.max(212,Math.min(360,railRef.current.startWidth+ev.clientX-railRef.current.startX));
    setPersonal(current=>({...current,railWidth:Math.round(width)}));
  };
  const onRailPointerUp=(ev:ReactPointerEvent<HTMLDivElement>)=>{if(railRef.current?.pointerId===ev.pointerId){railRef.current=null;ev.currentTarget.releasePointerCapture(ev.pointerId)}};
  useEffect(()=>{
    const onKey=(event:KeyboardEvent)=>{
      const active=document.activeElement?.tagName;
      if(active==='TEXTAREA'||active==='INPUT'||active==='SELECT'||(document.activeElement as HTMLElement)?.isContentEditable)return;
      if(event.key==='Escape'){setSelectedId(null);setArrowMode(false);setArrowSource(null);setHint('')}
      if(event.key.toLowerCase()==='f'&&!event.metaKey&&!event.ctrlKey){event.preventDefault();void fitView({padding:.18,duration:300})}
    };
    document.addEventListener('keydown',onKey);return()=>document.removeEventListener('keydown',onKey);
  },[fitView]);
  return <div className={`galaxy-app ${personal.railCollapsed?'rail-collapsed':''}`} style={{'--rail-width':`${personal.railCollapsed?74:personal.railWidth}px`} as CSSProperties}>
    <header className="app-header"><div className="brand-icon" aria-hidden="true">◈</div><div className="brand"><strong>bananaloop<span>.</span></strong><small>RESEARCH OS</small></div><div className="route">WORKSPACE <span>/</span> RESEARCH GALAXY</div><div className="header-right"><span className="read-only-tag">SYNTHETIC · READ ONLY</span><span className="avatar-pixel">✦</span></div></header>
    <aside className="rail"><div className="rail-inner"><div className="rail-head"><span>MISSION INDEX</span><button aria-label={personal.railCollapsed?'Развернуть панель':'Свернуть панель'} title={personal.railCollapsed?'Развернуть':'Свернуть'} onClick={()=>setPersonal(current=>({...current,railCollapsed:!current.railCollapsed}))}>{personal.railCollapsed?'›':'‹'}</button></div>
      <div className="rail-body"><div className="rail-label">◦ ACTIVE CAMPAIGN <span>01</span></div><div className="campaign"><span className="campaign-symbol">◇</span><span className="campaign-copy"><strong>{fixture.title}</strong><small>{fixture.nodes.length} hypotheses · Graph Rev {fixture.revision}</small></span><i className="live-pip"/></div><div className="rail-label spaces-label">◦ SPACES</div><div className="nav-link nav-active"><span>⌘</span><b>Research Galaxy</b><em>↗</em></div><div className="nav-link inactive"><span>▤</span><b>Mission Control</b></div><div className="nav-link inactive"><span>◇</span><b>Decision Center</b></div></div>
      <div className="rail-foot"><div className="planet-row">✦　✳　✦　✧</div><p>YOUR RESEARCH,<br/>YOUR UNIVERSE.</p><div className="rail-pref">⚙ <span>Preferences · later</span></div></div></div>
      {!personal.railCollapsed&&<div className="rail-resize" role="separator" aria-label="Ширина левой панели" aria-orientation="vertical" tabIndex={0} aria-valuemin={212} aria-valuemax={360} aria-valuenow={personal.railWidth} onPointerDown={onRailPointerDown} onPointerMove={onRailPointerMove} onPointerUp={onRailPointerUp} onPointerCancel={onRailPointerUp} onKeyDown={e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();setPersonal(current=>({...current,railWidth:Math.max(212,Math.min(360,current.railWidth+(e.key==='ArrowRight'?12:-12))) }))}}}/>}
    </aside>
    <main className="workspace"><div className="work-intro"><div><div className="eyebrow"><span>―</span> GALAXY / EXPERIMENT 004 · RESEARCH GRAPH</div><h1>Ideas, in motion<span>.</span></h1><p>Explore hypotheses. Arrange the space. Leave yourself a note.</p></div><div className="study-info"><small>CURRENT STUDY</small><strong>{fixture.title}</strong><span>DEV dataset · illustrative graph</span></div></div>
      <div className="workspace-actions"><div className="workspace-title">RESEARCH GALAXY <span className="workspace-caption">/ BANANA NOIR</span></div><div className="tools"><Button variant="outline" onClick={()=>{setArrowMode(false);setArrowSource(null);addNote(window.innerWidth/2,window.innerHeight/2)}} title="Создать личную заметку">▣ <span>Note</span></Button><Button variant={arrowMode?'primary':'outline'} onClick={()=>{setArrowMode(x=>!x);setArrowSource(null);setHint('Выбери начальную ноду для личной стрелки')}} title="Нарисовать личную стрелку">↗ <span>{arrowMode?'Cancel':'Arrow'}</span></Button><Button variant="outline" onClick={()=>void fitView({padding:.17,duration:300})} title="Показать все узлы (F)">⤢ <span>Fit</span></Button></div></div>
      <div className="flow-shell"><div className="canvas-meta"><span className="orbital-pip"/> ORBITAL RESEARCH / PERSONAL VIEW <small>Rev {String(fixture.revision).padStart(2,'0')} · drag to rearrange</small></div><div className="counter">◇ {String(fixture.nodes.length).padStart(2,'0')} NODES <i/> {String(fixture.gates.length).padStart(2,'0')} LOGIC GATES</div>
        <ReactFlow<GalaxyNode,Edge> nodes={visibleNodes} edges={allEdges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onNodeDragStop={onNodeDragStop} onNodeClick={onNodeClick} onPaneClick={onPaneClick} onMoveEnd={onMoveEnd} defaultViewport={personal.viewport??{x:65,y:60,zoom:.8}} fitView={!personal.viewport} fitViewOptions={{padding:.15}} nodesDraggable nodesConnectable={false} elementsSelectable={true} nodesDeletable={false} edgesReconnectable={false} zoomOnScroll zoomOnDoubleClick={false} panOnDrag panOnScroll={false} zoomOnPinch minZoom={.35} maxZoom={2.5} proOptions={{hideAttribution:false}}>
          <Background gap={31} size={1} color="#294358"/><MiniMap pannable zoomable position="bottom-right" nodeColor={n=>n.type==='gate'?'#d4b16e':n.type==='note'?'#d8c493':'#648b9a'}/><Controls showInteractive={false} position="bottom-right"/>
        </ReactFlow>
        {arrowMode&&<div className="tool-hint">{arrowSource?`FROM ${arrowSource.toUpperCase()} · SELECT DESTINATION`:'ARROW TOOL · SELECT SOURCE NODE'}</div>}
        {hint&&!arrowMode&&<div className="action-hint" role="status">{hint}</div>}
        <div className="canvas-legend"><span className="legend-status"><i className="dot-verified"/> Завершено</span><span className="legend-status"><i className="dot-ready"/> Готово</span><span className="legend-status"><i className="dot-pending"/> Ожидает</span><span className="legend-status"><i className="dot-failed"/> Не прошло</span><span className="legend-split"/><span className="legend-engine">━ Engine</span><span className="legend-user">┄ Personal</span></div>
        <div className="canvas-shortcuts">Wheel to zoom <span>·</span> Drag nodes to arrange <span>·</span> Double-click for a note</div>
        {selectedNode&&<section className="inspector" key={selectedId} aria-label="Инспектор объекта"><div className="inspector-label">NODE INSPECTOR <button aria-label="Закрыть инспектор" onClick={()=>setSelectedId(null)}>×</button></div><div className="inspector-id">{selectedId?.toUpperCase()}</div><h2>{selectedResearch?.title??(selectedGate?`${selectedGate.kind} · logic gate`:'Personal note')}</h2>{selectedResearch?<><p>{selectedResearch.description}</p><span className={`inspector-status status-${selectedResearch.status.toLowerCase()}`}>{statusCopy[selectedResearch.status].name}</span><div className="inspector-rule"/><label>ЧТО ОЗНАЧАЕТ СТАТУС</label><p>{statusCopy[selectedResearch.status].detail}</p><label>PHASE</label><p>{selectedResearch.phase}</p></>:selectedGate?<><p>{selectedGate.detail}</p><label>LOGIC</label><p>{selectedGate.kind==='AND'?'Всё необходимое должно быть подтверждено.':'Достаточно одного подтверждённого входа.'}</p></>:selectedNote?<><p>Личная заметка. Она не меняет граф, исследовательские статусы или бюджет.</p><Button variant="outline" onClick={()=>selectedId&&noteDelete(selectedId)}>Удалить заметку</Button></>:null}</section>}
      </div><div className="workspace-footer"><span><i/> DESIGN MODE · NO ENGINE WRITES</span><span>SPACE TO PAN · F TO FIT · DOUBLE CLICK TO NOTE</span></div>
    </main>
  </div>;
}
export function Galaxy(){return <ReactFlowProvider><GalaxyBody/></ReactFlowProvider>}
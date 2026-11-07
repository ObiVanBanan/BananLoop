import {Handle,Position} from '@xyflow/react';
import type {Node,NodeProps} from '@xyflow/react';
import type {ResearchStatus} from '../domain/graph';
import {statusCopy} from '../domain/graph';
export type ResearchNodeData={nodeId:string;title:string;description:string;phase:string;status:ResearchStatus};
export type GateNodeData={kind:'AND'|'OR';detail:string};
export type NoteNodeData={text:string;onEdit:(id:string,text:string)=>void;onRemove:(id:string)=>void};
export type ResearchFlowNode=Node<ResearchNodeData,'research'>;
export type GateFlowNode=Node<GateNodeData,'gate'>;
export type NoteFlowNode=Node<NoteNodeData,'note'>;
export type GalaxyNode=ResearchFlowNode|GateFlowNode|NoteFlowNode;
function Port({type,position}: {type:'target'|'source';position:Position}){return <Handle type={type} position={position} isConnectable={false}/>}
export function ResearchCard({data,selected}:NodeProps<ResearchFlowNode>){
  return <article className={`research-node status-${data.status.toLowerCase()} ${selected?'node-selected':''}`} title={statusCopy[data.status].detail}>
    <Port type="target" position={Position.Left}/>
    <div className="node-kicker"><span>{data.nodeId}</span><span>{data.phase}</span></div>
    <h3>{data.title}</h3><p>{data.description}</p>
    <div className="node-bottom"><span className="status-dot"/><span>{statusCopy[data.status].name}</span><span aria-hidden="true" className="node-open">↗</span></div>
    <Port type="source" position={Position.Right}/>
  </article>;
}
export function GateCard({data,selected}:NodeProps<GateFlowNode>){
  return <div className={`gate-node ${selected?'node-selected':''}`} title={data.detail}>
    <Port type="target" position={Position.Left}/>
    <span className="gate-symbol" aria-hidden="true">◇</span>
    <strong>{data.kind}</strong><small>{data.kind==='AND'?'ALL VERIFIED':'ANY VERIFIED'}</small>
    <Port type="source" position={Position.Right}/>
  </div>;
}
export function StickyNoteCard({id,data,selected}:NodeProps<NoteFlowNode>){
  return <article className={`sticky-note ${selected?'node-selected':''}`}>
    <Port type="target" position={Position.Left}/>
    <div className="note-heading"><span className="note-grab">✎ PERSONAL NOTE</span><button className="note-remove nodrag" title="Удалить личную заметку" aria-label="Удалить заметку" onClick={(event)=>{event.stopPropagation();data.onRemove(id)}}>×</button></div>
    <textarea className="nodrag nowheel" maxLength={2000} aria-label="Текст личной заметки" placeholder="Запиши мысль…" value={data.text} onChange={e=>data.onEdit(id,e.target.value)}/>
    <Port type="source" position={Position.Right}/>
  </article>;
}
export const nodeTypes={research:ResearchCard,gate:GateCard,note:StickyNoteCard};
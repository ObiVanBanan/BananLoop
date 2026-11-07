/** Separate, *non-authoritative* user canvas state. Never write this into Engine graph data. */
export type Point = {x:number;y:number};
export type PersonalNote = {id:string;x:number;y:number;text:string};
export type PersonalArrow = {id:string;source:string;target:string;label:string};
export type ViewportState = {x:number;y:number;zoom:number};
export type PersonalCanvas = {
  version:1;
  positions:Record<string,Point>;
  notes:PersonalNote[];
  arrows:PersonalArrow[];
  viewport:ViewportState|null;
  railCollapsed:boolean;
  railWidth:number;
};
const MAX_TEXT=2000;
export const MAX_ARROW_LABEL=80;
// Labels are plain text. Never write them into Engine graph dependencies.
const cleanArrowLabel=(value:unknown):string=>typeof value==='string'
  ?value.replace(/[\u0000-\u001f\u007f]/g,' ').slice(0,MAX_ARROW_LABEL):'';
const asObject=(input:unknown):Record<string,unknown>|null=>typeof input==='object'&&input!==null&&!Array.isArray(input)?input as Record<string,unknown>:null;
const cleanCoord=(x:unknown):number|null=>typeof x==='number'&&Number.isFinite(x)&&Math.abs(x)<=100000?x:null;
const cleanId=(id:unknown):string|null=>typeof id==='string'&&/^[a-zA-Z0-9_-]{1,90}$/.test(id)?id:null;
const cleanPoint=(value:unknown):Point|null=>{const p=asObject(value);if(!p)return null;const x=cleanCoord(p.x),y=cleanCoord(p.y);return x===null||y===null?null:{x,y}};
export function defaultPersonalCanvas():PersonalCanvas{
  return {version:1,positions:{},notes:[],arrows:[],viewport:null,railCollapsed:false,railWidth:250};
}
export function decodePersonalCanvas(raw:string|null,knownGraphIds:readonly string[]):PersonalCanvas{
  if(!raw||raw.length>240000)return defaultPersonalCanvas();
  let input:unknown;
  try{input=JSON.parse(raw)}catch{return defaultPersonalCanvas()}
  const obj=asObject(input);if(!obj||obj.version!==1)return defaultPersonalCanvas();
  const graph=new Set(knownGraphIds);
  const notes:PersonalNote[]=[];
  const ids=new Set<string>();
  for(const value of Array.isArray(obj.notes)?obj.notes.slice(0,60):[]){
    const item=asObject(value),id=cleanId(item?.id),point=cleanPoint(item);
    if(!item||!id||!id.startsWith('note-')||!point||ids.has(id)||graph.has(id))continue;
    ids.add(id);notes.push({id,...point,text:typeof item.text==='string'?item.text.slice(0,MAX_TEXT):''});
  }
  const validIds=new Set([...graph,...ids]);
  const positions:Record<string,Point>={};
  for(const [id,p] of Object.entries(asObject(obj.positions)||{}).slice(0,400)){
    if(!graph.has(id))continue;
    const pos=cleanPoint(p);if(pos)positions[id]=pos;
  }
  const arrows:PersonalArrow[]=[];
  const seen=new Set<string>();
  for(const value of Array.isArray(obj.arrows)?obj.arrows.slice(0,150):[]){
    const item=asObject(value),id=cleanId(item?.id),source=cleanId(item?.source),target=cleanId(item?.target);
    if(!id||!source||!target||source===target||!validIds.has(source)||!validIds.has(target)||seen.has(source+':'+target))continue;
    seen.add(source+':'+target);arrows.push({id,source,target,label:cleanArrowLabel(item?.label)});
  }
  const view=asObject(obj.viewport);
  const viewport:ViewportState|null=view&&cleanCoord(view.x)!==null&&cleanCoord(view.y)!==null&&typeof view.zoom==='number'&&view.zoom>=.3&&view.zoom<=3
    ?{x:view.x as number,y:view.y as number,zoom:view.zoom as number}:null;
  return {version:1,positions,notes,arrows:arrows.slice(0,100),viewport,railCollapsed:obj.railCollapsed===true,railWidth:typeof obj.railWidth==='number'&&Number.isFinite(obj.railWidth)?Math.round(Math.max(212,Math.min(360,obj.railWidth))):250};
}
export const storageKey=(campaignId:string)=>`bananloop.personal-canvas.v1.${campaignId}`;
export function loadCanvas(storage:Pick<Storage,'getItem'>|null,campaignId:string,ids:readonly string[]):PersonalCanvas{
  try{return decodePersonalCanvas(storage?.getItem(storageKey(campaignId))??null,ids)}catch{return defaultPersonalCanvas()}
}
export function saveCanvas(storage:Pick<Storage,'setItem'>|null,campaignId:string,value:PersonalCanvas):boolean{
  try{storage?.setItem(storageKey(campaignId),JSON.stringify(value));return Boolean(storage)}catch{return false}
}
export function addPersonalArrow(canvas:PersonalCanvas,source:string,target:string,allowedIds:readonly string[],newId?:string):PersonalCanvas{
  if(source===target||!allowedIds.includes(source)||!allowedIds.includes(target)||canvas.arrows.length>=100||canvas.arrows.some(e=>e.source===source&&e.target===target))return canvas;
  const id=newId??`arrow-${Date.now()}-${Math.random().toString(36).slice(2,7)}`;
  if(!cleanId(id)||!id.startsWith('arrow-')||canvas.arrows.some(a=>a.id===id))return canvas;
  return {...canvas,arrows:[...canvas.arrows,{id,source,target,label:''}]};
}
export function renamePersonalArrow(canvas:PersonalCanvas,id:string,label:string):PersonalCanvas{
  if(!canvas.arrows.some(a=>a.id===id))return canvas;
  return {...canvas,arrows:canvas.arrows.map(a=>a.id===id?{...a,label:cleanArrowLabel(label)}:a)};
}
export function removePersonalArrow(canvas:PersonalCanvas,id:string):PersonalCanvas{
  if(!canvas.arrows.some(a=>a.id===id))return canvas;
  return {...canvas,arrows:canvas.arrows.filter(a=>a.id!==id)};
}
export function removePersonalNote(canvas:PersonalCanvas,noteId:string):PersonalCanvas{
  if(!noteId.startsWith('note-'))return canvas;
  return {...canvas,notes:canvas.notes.filter(n=>n.id!==noteId),arrows:canvas.arrows.filter(a=>a.source!==noteId&&a.target!==noteId)};
}
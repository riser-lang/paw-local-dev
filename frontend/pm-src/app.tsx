import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {TodayCockpit} from './design/pages/TodayCockpit';
import {ProjectDossier} from './design/pages/ProjectDossier';
import type {TodayViewModel,ProjectViewModel,PmCallbacks} from './design/types';
import {renderVersions} from './versions.js';
import {renderUpdates} from './updates.js';
import {renderReceipts} from './receipts.js';
import './design/styles/fonts.css';
import './design/styles/pm.css';
import './local.css';

let session:Promise<any>|undefined;
async function api(url:string,body?:unknown){
 let options:RequestInit={};
 if(body!==undefined){
  session??=fetch('/v1/session').then(async r=>{if(!r.ok)throw Error('本地会话暂不可用');return r.json();}).catch(e=>{session=undefined;throw e;});
  const local=await session;
  options={method:'POST',headers:{'Content-Type':'application/json','x-paw-local-token':local.local_token},body:JSON.stringify(body)};
 }
 const r=await fetch(url,options);const data=await r.json();if(!r.ok)throw Error(data.detail||'操作失败');return data;
}
function node(tag:string,text?:string,cls?:string){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
const sourceDialog=document.querySelector<HTMLDialogElement>('#source')!;
function notice(text:string){document.querySelector('#notice')!.textContent=text;}
async function navigate(href:string){
 if(href.startsWith('/v1/pm/sources/')){
  const source=await api(href);document.querySelector('#source-title')!.textContent=source.title;
  document.querySelector('#source-text')!.textContent=source.body;sourceDialog.showModal();
 }else if(href.startsWith('/app'))location.assign(href);
}
function action(label:string,callback:()=>Promise<unknown>){const b=node('button',label) as HTMLButtonElement;b.className='pm-btn';b.onclick=async()=>{b.disabled=true;try{await callback();}catch(e){notice((e as Error).message);}finally{b.disabled=false;}};return b;}
function reference(revision:any){const a=node('a','查看来源') as HTMLAnchorElement;a.href=revision.source_url;a.onclick=e=>{e.preventDefault();navigate(a.getAttribute('href')!).catch(e=>notice(e.message));};return a;}
document.querySelector<HTMLButtonElement>('#close-source')!.onclick=()=>sourceDialog.close();

function LegacyExtras({pid,version,refresh}:{pid:string,version:number,refresh:()=>Promise<void>}){
 const ref=useRef<HTMLDivElement>(null);
 useEffect(()=>{let active=true;
  (async()=>{
   const data=await api('/v1/pm/projects/'+encodeURIComponent(pid));
   const fragment=document.createElement('div');
   for(const draft of data.ai_generated){
    const card=node('article',undefined,'pm-card');card.append(node('strong','AI 生成 · 尚未成为正式事实'));
    for(const [k,label] of Object.entries({one_sentence:'一句话说明',current_state:'目前做到哪里',key_open_issue:'当前关键问题',next_step:'下一步建议'}))card.append(node('p',label+'：'+draft.content[k]));
    card.append(action('采纳',async()=>{await api('/v1/pm/projects/'+pid+'/derived/'+draft.id+'/adopt',{});notice('已生成候选，请到需要你决定中批准。');}));fragment.append(card);
   }
   const args={app:fragment,id:pid,node,action,api,reference,notice,refresh};
   await renderVersions(args);await renderUpdates({...args,sections:data.sections});
   if(active)ref.current?.replaceChildren(fragment);
  })().catch(e=>notice(e.message));
  return()=>{active=false;};
 },[pid,version]);
 return <div ref={ref} className="pm-root pm-page pm-legacy" aria-label="资料版本、覆盖与派生说明"/>;
}

function AssignmentDialog({requests,onConfirm,onCancel}:{requests:any[],onConfirm:(value:any)=>Promise<void>,onCancel:()=>void}){
 const [choices,setChoices]=useState<Record<string,any>>({});const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 return <div className="pm-modal-backdrop"><section role="dialog" aria-modal="true" aria-label="确认输入归属" className="pm-local-modal pm-root">
  <h2>确认输入归属</h2>
  {requests.map(item=><fieldset key={item.id}><legend>{item.title}</legend>
   <label>归属项目<select aria-label={'归属项目 '+item.title} value={choices[item.full]?.project_id||''} onChange={e=>setChoices({...choices,[item.full]:{...choices[item.full],project_id:e.target.value}})}>
    <option value="">请选择已有项目</option>{item.projects.map((p:any)=><option key={p.id} value={p.id}>{p.name}</option>)}
   </select></label>
   <label>文档族名称<input aria-label={'文档族名称 '+item.title} value={choices[item.full]?.family_title??item.family_key??''} onChange={e=>setChoices({...choices,[item.full]:{...choices[item.full],family_title:e.target.value}})}/></label>
   <p>同名文档族执行版本规则；独立资料请填独立名称。确认后按栏目规则处理。</p>
  </fieldset>)}
  {error&&<p role="alert">{error}</p>}
  <button className="pm-btn pm-btn-primary" disabled={busy} onClick={async()=>{setBusy(true);try{
   const final=Object.fromEntries(requests.map(r=>[r.full,{...choices[r.full],family_title:choices[r.full]?.family_title||r.family_key}]));
   if(requests.some(r=>!final[r.full]?.project_id||!final[r.full]?.family_title))throw Error('请为每份输入选择项目并填写文档族名称。');
   await onConfirm(final);
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>确认归属</button>
  <button className="pm-btn" disabled={busy} onClick={onCancel}>取消</button>
 </section></div>;
}

function Operations({refresh,version}:{refresh:()=>Promise<void>,version:number}){
 const [data,setData]=useState<any>(null);const [statuses,setStatuses]=useState<any[]>([]);const [count,setCount]=useState(7);const [mb,setMb]=useState(512);const [busy,setBusy]=useState(false);
 useEffect(()=>{Promise.all([api('/v1/pm/backups'),api('/v1/pm/system/status')]).then(([b,s])=>{setData(b);setCount(b.policy.max_count);setMb(b.policy.max_bytes/1024**2);setStatuses(s.items);}).catch(e=>notice(e.message));},[version]);
 async function run(fn:()=>Promise<any>){setBusy(true);try{const result=await fn();await refresh();notice(result.ok===false?'操作未成功，请查看系统状态。':'本地维护操作完成。');}catch(e){notice((e as Error).message);}finally{setBusy(false);}}
 return <section className="pm-root pm-page pm-maintenance" aria-label="本地维护"><h2>本地维护</h2>
  <ul>{statuses.map(s=><li key={s.key}><strong>{s.label} · {{ok:'● 正常',warn:'▲ 注意',error:'✕ 异常',unknown:'○ 未知'}[s.state]}</strong>：{s.summary}；最后成功：{s.last_success_at||'暂无记录'}{s.detail&&<span>；{s.detail}</span>}</li>)}</ul>
  {data&&<><p>自动备份 {data.items.filter((b:any)=>b.automatic).length} 份；手动及其他保留备份 {data.items.filter((b:any)=>!b.automatic).length} 份。手动备份不参与自动轮转。</p>
   <div className="pm-policy"><label>自动备份最多份数<input type="number" min="1" max="100" value={count} onChange={e=>setCount(Number(e.target.value))}/></label>
   <label>自动备份容量上限（MB）<input type="number" min="1" value={mb} onChange={e=>setMb(Number(e.target.value))}/></label></div>
   <button className="pm-btn" disabled={busy} onClick={()=>run(()=>api('/v1/pm/backups/policy',{max_count:count,max_bytes:Math.floor(mb*1024**2)}))}>保存备份上限</button>
   <button className="pm-btn" disabled={busy} onClick={()=>run(()=>api('/v1/local/backup/create',{tag:'manual'}))}>手动备份</button>
   <button className="pm-btn" disabled={busy||!data.items.length} onClick={()=>run(()=>api('/v1/pm/backups/selfcheck',{}))}>备份恢复自检</button>
   <button className="pm-btn" disabled={busy} onClick={()=>run(()=>api('/v1/local/provider/test_connection',{}))}>测试模型连通</button>
   <p className="pm-meta">恢复自检只检查临时副本，检查完成后清理临时文件。正式数据不会被恢复自检覆盖。</p>
  </>}
 </section>;
}

function App(){
 const pid=location.pathname.startsWith('/app/pm/projects/')?decodeURIComponent(location.pathname.split('/').pop()!):null;
 const [vm,setVm]=useState<TodayViewModel|ProjectViewModel|null>(null);const [configured,setConfigured]=useState(false);const [version,setVersion]=useState(0);
 const [selection,setSelection]=useState<{requests:any[],ids:string[],choice:string}|null>(null);
 async function refresh(){const [data,receipts]=await Promise.all([api(pid?'/v1/pm/view/projects/'+encodeURIComponent(pid):'/v1/pm/view/today'),api('/v1/pm/receipts')]);setVm(data);setConfigured(receipts.model_configured);setVersion(v=>v+1);}
 useEffect(()=>{refresh().catch(e=>notice(e.message));},[]);
 async function perform(ids:string[],choice:string,assignments={}){
  const result=await api('/v1/pm/inbox/decide',{ids,choice,assignments});await refresh();
  const failed=result.items.filter((r:any)=>!r.ok);if(failed.length)throw Error(failed.map((r:any)=>r.error).join('；'));
  notice(result.backup_error||(choice==='later'?'已记录稍后处理，事项仍保留在列表。':'决定已记录。'));
 }
 const cb:PmCallbacks={
  onNavigate:href=>{navigate(href).catch(e=>notice(e.message));},
  onDecide:async(ids,choice)=>{try{
   if(choice==='yes'&&ids.some(id=>/^(assignment|version):/.test(id))){
    const cards=(await api('/v1/pm/input-decisions')).items;
    const requests=cards.filter((r:any)=>ids.some(id=>id.endsWith(':'+r.id))).map((r:any)=>({...r,full:ids.find(id=>id.endsWith(':'+r.id))}));
    if(!requests.length)throw Error('归属事项已经变化，请刷新。');
    setSelection({requests,ids,choice});return;
   }
   await perform(ids,choice);
  }catch(e){notice((e as Error).message);}},
  onRevert:async id=>{try{await api('/v1/pm/changes/'+encodeURIComponent(id)+'/undo',{});await refresh();notice('已撤销，原结论已恢复。');}catch(e){notice((e as Error).message);}},
  onResolveConflict:async(id,choice)=>{try{
   if(choice==='later'){notice('冲突保留，等待之后裁决。');return;}
   const conflicts=(await api('/v1/pm/projects/'+pid+'/conflicts')).items;const c=conflicts.find((r:any)=>r.id===id);
   if(!c)throw Error('冲突已变化，请刷新。');await api('/v1/pm/assessments/'+c.assessment_id+'/resolve',{choice});await refresh();
  }catch(e){notice((e as Error).message);}}
 };
 if(!vm)return <p className="pm-root pm-page">正在读取本地档案…</p>;
 return <>
  {pid?<><ProjectDossier vm={vm as ProjectViewModel} cb={cb} backHref="/app/pm/today"/><LegacyExtras pid={pid} version={version} refresh={refresh}/></>:<><TodayCockpit vm={vm as TodayViewModel} cb={cb} modelConfigured={configured}/><Operations refresh={refresh} version={version}/></>}
  {selection&&<AssignmentDialog requests={selection.requests} onCancel={()=>setSelection(null)} onConfirm={async assignments=>{await perform(selection.ids,selection.choice,assignments);setSelection(null);}}/>}
 </>;
}

if(location.pathname==='/app/pm/receipts'){
 const app=document.querySelector<HTMLElement>('#app')!;app.classList.add('pm-legacy','pm-root','pm-page');
 const render=()=>renderReceipts({app,node,api,action,reference,notice,refresh:render});render().catch(e=>notice(e.message));
}else createRoot(document.querySelector('#app')!).render(<App/>);

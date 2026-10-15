const app = document.querySelector('#app');
import {renderVersions} from './versions.js';
import {renderUpdates} from './updates.js';
const id = decodeURIComponent(location.pathname.split('/').pop());
function node(tag, text, cls) {const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(cls)el.className=cls; return el;}
let session;
async function api(url, body) {
 let options={};
 if(body!==undefined){
  session??=fetch('/v1/session').then(async r=>{if(!r.ok)throw Error('本地会话暂不可用');return r.json();}).catch(e=>{session=undefined;throw e;});
  const local=await session;
  options={method:'POST',headers:{'Content-Type':'application/json','x-paw-local-token':local.local_token},body:JSON.stringify(body)};
 }
 const r=await fetch(url,options);const data=await r.json();if(!r.ok)throw Error(data.detail||'操作失败');return data;
}
function action(label, callback) {const b=node('button',label);b.onclick=async()=>{b.disabled=true;try{await callback();}catch(e){notice(e.message);}finally{b.disabled=false;}};return b;}
function notice(text){let p=document.querySelector('#notice');if(!p){p=node('p',undefined,'notice');p.id='notice';app.prepend(p);}p.textContent=text;}
function reference(revision) {const a=node('a','查看来源');a.href=revision.source_url;a.onclick=async e=>{e.preventDefault();try{const s=await api(a.getAttribute('href'));document.querySelector('#source-title').textContent=s.title;document.querySelector('#source-text').textContent=s.body;document.querySelector('#source').showModal();}catch(err){notice(err.message);}};return a;}
function references(revision){const frag=document.createDocumentFragment();frag.append(reference(revision));for(const source of revision.source_refs||[])if(source.source_version_id!==revision.source_version_id)frag.append(node('span',' · '),reference(source));return frag;}
async function render(){const data=await api('/v1/pm/projects/'+encodeURIComponent(id));app.replaceChildren(node('h1',data.project.name+' · 活的项目档案'));
 const grid=node('div',undefined,'sections');app.append(grid);
 for(const section of data.sections){const card=node('article');card.dataset.section=section.id;card.append(node('h2',section.title));if(section.current){card.append(node('p',section.current.text),references(section.current),node('small',' · 已核准'));
 card.append(action('撤销此次变更',async()=>{await api('/v1/pm/changes/'+encodeURIComponent(section.current.change_id)+'/undo',{});await render();notice('已撤销，原结论已恢复。');}));}else card.append(node('p','尚未记录','empty'));
 const history=node('details');history.append(node('summary','历史（'+section.history.length+'）'));for(const r of section.history){history.append(node('p',r.text+(r.withdrawn?'（已撤销）':'')),references(r));}card.append(history);grid.append(card);}
 for(const draft of data.ai_generated){const card=node('article',undefined,'ai');card.append(node('strong','AI 生成 · 尚未成为正式事实'));for(const [k,label]of Object.entries({one_sentence:'一句话说明',current_state:'目前做到哪里',key_open_issue:'当前关键问题',next_step:'下一步建议'}))card.append(node('p',label+'：'+draft.content[k]));card.append(action('采纳',async()=>{const r=await api('/v1/pm/projects/'+encodeURIComponent(id)+'/derived/'+encodeURIComponent(draft.id)+'/adopt',{});notice('已生成候选，前往“待我审核”核准后才会写入正式档案。候选：'+r.candidate_id);}));app.append(card);}
 if(!data.ai_generated.length)app.append(node('p','暂无 AI 生成说明。需要填写 Key 才能生成；已有正式事实可直接查看。','empty'));
 await renderVersions({app,id,node,action,api,reference,notice,refresh:render});
 await renderUpdates({app,id,node,action,api,reference,notice,refresh:render,sections:data.sections});
}
document.querySelector('#close-source').onclick=()=>document.querySelector('#source').close();
render().catch(e=>{app.replaceChildren(node('h1','项目档案暂不可用'),node('p',e.message));});

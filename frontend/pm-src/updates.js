export async function renderUpdates({app,id,node,action,api,reference,notice,refresh,sections,readonlyConflicts=false}){
 const base='/v1/pm/projects/'+encodeURIComponent(id);
 const title=sectionId=>sections.find(s=>s.id===sectionId)?.title||'待确认栏目';
 const conflicts=(await api(base+'/conflicts')).items;
 if(conflicts.length){const area=node('section');area.append(node('h2','冲突清单'));
 for(const conflict of conflicts){const card=node('article');card.append(node('h3',title(conflict.section_id)+' · '+(conflict.status==='open'?'▲ 待用户裁决':conflict.status==='stale'?'需重新判定':'✓ 已裁决')));
 const pair=node('div',undefined,'comparison');for(const [label,text,sid]of[['当前结论',conflict.left_text,conflict.left_source_id],['另一份有效资料',conflict.right_text,conflict.right_source_id]]){const col=node('div');col.append(node('strong',label),node('p',text),reference({source_url:'/v1/pm/sources/'+sid}));pair.append(col);}card.append(pair);
 if(conflict.status==='open'){card.append(node('p','采纳新资料会替换当前结论；保留当前则不改栏目。两种裁决都可撤销。'));if(!readonlyConflicts)for(const [label,choice]of[['采纳新资料','incoming'],['保留当前','current']])card.append(action(label,async()=>{await api('/v1/pm/assessments/'+conflict.assessment_id+'/resolve',{choice});await refresh();notice('裁决已记录。');}));}
 else if(conflict.resolved_by)card.append(action('撤销裁决',async()=>{await api('/v1/pm/changes/'+conflict.resolved_by+'/undo',{});await refresh();}));area.append(card);}app.append(area);}
 const decisions=(await api(base+'/assessments')).items.filter(a=>a.status==='needs_user');
 for(const decision of decisions){const card=node('article',undefined,'ai');card.append(node('h2','需要你决定 · 栏目结论'),node('p',decision.text),reference({source_url:'/v1/pm/sources/'+decision.source_version_id}),node('p','涉及用户专属事项或无法自动替换。采纳将替换该栏目的当前结论；拒绝则保持当前结论。'));
 for(const [label,choice]of[['确认采纳','incoming'],['拒绝','reject']])card.append(action(label,async()=>{await api('/v1/pm/assessments/'+decision.id+'/resolve',{choice});await refresh();}));app.append(card);}
 const sourceIds=new Set((await api(base+'/families')).items.flatMap(f=>f.versions.map(v=>v.source_version_id)));
 const coverage=(await api('/v1/pm/coverage')).items.filter(c=>sourceIds.has(c.source_version_id));
 if(coverage.length){const area=node('section');area.append(node('h2','资料读取覆盖'));
 for(const read of coverage){const card=node('article');card.append(node('h3',read.title),node('p',read.ratio===null?'覆盖率未知':`已读 ${read.read_count}/${read.total_count} 个结构单位（${Math.round(read.ratio*100)}%）`));for(const unit of read.units.filter(u=>!u.read))card.append(node('p',`未读 ${unit.kind} ${unit.id}：${unit.reason}`));const details=node('details');details.append(node('summary','范围与限制'));for(const limitation of read.limitations)details.append(node('p',limitation));card.append(details);area.append(card);}area.append(action('处理本轮定向复读（最多 5 条）',async()=>{const result=await api('/v1/pm/reread/run',{limit:5});await refresh();notice(`本轮处理 ${result.processed} 条；仅处理已触发的资料。`);}));app.append(area);}
}

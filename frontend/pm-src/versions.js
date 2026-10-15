export async function renderVersions({app,id,node,action,api,reference,notice,refresh}) {
 const base='/v1/pm/projects/'+encodeURIComponent(id);
 const families=(await api(base+'/families')).items;
 if(families.length){const area=node('section');area.append(node('h2','资料版本'));
 for(const family of families){const card=node('article');card.append(node('h3',family.title));
 for(const version of family.versions){card.append(node('p',version.title+' · '+(version.status==='current'?'● 当前版本':'历史 · 已替换')),reference(version));}
 const latest=family.rulings.find(r=>!r.undone_by);if(latest&&latest.previous_source_id)card.append(action('撤销版本裁定',async()=>{await api('/v1/pm/changes/'+latest.change_id+'/undo',{});await refresh();notice('版本及栏目引用已恢复。');}));area.append(card);}app.append(area);}
 const cards=(await api('/v1/pm/decisions')).items.filter(c=>c.project_id===id);
 for(const decision of cards){const card=node('article',undefined,'ai');card.append(node('h2','需要你决定'),node('p',decision.prompt));
 const select=node('select');const fresh=node('option','作为独立文档族');fresh.value='';select.append(fresh);
 for(const family of families){const option=node('option','归入：'+family.title);option.value=family.id;select.append(option);}card.append(select);
 const name=node('input');name.placeholder='独立文档族名称';name.setAttribute('aria-label','文档族名称');card.append(name);
 card.append(action('确认归属与版本裁定',async()=>{const family=families.find(f=>f.id===select.value);const title=family?family.title:name.value.trim();if(!title)throw Error('请填写文档族名称。');await api('/v1/pm/decisions/'+decision.id+'/resolve',{project_id:id,family_id:family?.id||null,family_title:title});await refresh();}));app.append(card);}
}

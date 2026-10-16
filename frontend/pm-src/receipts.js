export async function renderReceipts({app,node,api,action,reference,notice,refresh}){
 const data=await api('/v1/pm/receipts');
 const decisions=(await api('/v1/pm/input-decisions')).items;
 app.replaceChildren(node('h1','资料去向'));
 const input=node('section');input.append(node('h2','随手记'));
 const text=node('textarea');text.setAttribute('aria-label','随手记内容');text.rows=3;input.append(text);
 input.append(action('保存随手记',async()=>{
  if(!text.value.trim())throw Error('请先输入内容。');
  await api('/v1/web/workspace/captures',{raw_text:text.value.trim(),source:'local_pm'});
  await refresh();notice('已保存，后台正在处理。可刷新查看实际去向。');
 }),action('刷新回执',refresh));app.append(input);
 if(!data.model_configured)app.append(node('p','需要填写 Key 才能生成栏目结论；已存输入和来源仍可查看。','empty'));
 const pending=node('section');pending.append(node('h2','需要你决定'));
 for(const decision of decisions){
  const card=node('article',undefined,'ai');card.append(node('h3',decision.question),node('p',decision.title),reference({source_url:decision.source_url}));
  const select=node('select');select.setAttribute('aria-label','归属项目');select.append(node('option','请选择已有项目'));
  select.firstChild.value='';
  for(const project of decision.projects){const option=node('option',project.name);option.value=project.id;select.append(option);}
  if(decision.project_id)select.value=decision.project_id;
  card.append(select,node('p','确认：输入将归入所选项目，按同名文档族执行版本规则，并按栏目更新规则处理；暂不归入：正式记录保持不变。'));
  card.append(action('确认归属',async()=>{
   if(!select.value)throw Error('请选择已有项目。');
   await api('/v1/pm/input-decisions/'+decision.id+'/resolve',{project_id:select.value});await refresh();notice('归属已确认。栏目结果以回执和档案为准。');
  }),action('暂不归入',async()=>{await api('/v1/pm/input-decisions/'+decision.id+'/reject',{});await refresh();}));
  pending.append(card);
 }
 if(!decisions.length)pending.append(node('p','现在没有待确认的输入归属。','empty'));
 app.append(pending);
 const list=node('section');list.append(node('h2','输入回执'));
 for(const receipt of data.items){
  const card=node('article');card.dataset.receipt=receipt.id;
  card.append(node('h3',receipt.title),node('p',receipt.note));
  if(receipt.project_name){const link=node('a','已归入 '+receipt.project_name);link.href=receipt.href;card.append(link);}
  card.append(node('p',receipt.updated_sections.length?'更新栏目：'+receipt.updated_sections.join('、'):'尚无已提交的栏目更新'),reference({source_url:receipt.source_url}));
  if(receipt.authorization)card.append(node('p',receipt.authorization.label));
  if(receipt.rejection_change_id)card.append(action('撤销拒绝',async()=>{await api('/v1/pm/changes/'+receipt.rejection_change_id+'/undo',{});await refresh();}));
  list.append(card);
 }
 if(!data.items.length)list.append(node('p','还没有输入回执。把资料放入监控文件夹，或保存一条随手记。','empty'));
 app.append(list);
}

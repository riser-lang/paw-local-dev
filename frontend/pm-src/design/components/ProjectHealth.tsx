import type {PmCallbacks,HealthCheck,KnowledgeGap} from '../types';
import {shortDateTime} from '../time';

export function ProjectHealth({checks,gaps,cb,projectId}:{checks:HealthCheck[];gaps:KnowledgeGap[];cb?:PmCallbacks;projectId?:string}){
 return <section className="pm-block" aria-label="项目健康检查与知识缺口">
  <h2>项目健康检查</h2>
  {cb?.onRunHealthCheck&&<button className="pm-btn" onClick={()=>cb.onRunHealthCheck?.(projectId)}>{projectId?'检查这个项目':'检查本轮（最多10个项目）'}</button>}
  {!checks.length&&<p className="pm-meta">尚无检查记录；巡航会逐个项目进行每日检查。</p>}
  {checks.map(check=><article className="pm-card pm-health-report" key={check.id}>
   <h3>{check.project_name}</h3>
   <p className="pm-meta">{check.preview?'参考时间预演：'+shortDateTime(check.reference_at):'检查于 '+shortDateTime(check.checked_at)}{check.stale?'；结果已过期，请重新检查':''}</p>
   {check.preview&&<p className="pm-notice">此处按指定参考时间计算等待天数，未修改项目或资料的实际时间。</p>}
   <p className="pm-meta">栏目语义检查：{{draft:'已完成',needs_key:'需要填写 Key',not_enough_sections:'栏目内容不足',not_applicable:'不适用（先确认归属）',rejected:'输出校验未通过',unavailable:'模型暂不可用',budget_exceeded:'上下文预算不足'}[check.semantic_status]??'尚未完成'}</p>
   {!check.items.length&&<p>本次规则检查未发现异常。</p>}
   {check.items.map((issue,index)=><div className="pm-health-issue" key={index}><strong>▲ {issue.title}</strong><p>{issue.detail}</p>
    {issue.basis.map((basis,i)=><p className="pm-meta" key={i}><button className="pm-linkish" onClick={()=>cb?.onNavigate?.(basis.href)}>{basis.title}</button>：“{basis.quote}”</p>)}
   </div>)}
  </article>)}
  <h2>知识缺口</h2><p className="pm-meta">助手因依据不足未能回答的问题；补充相关资料后可以再次提问。</p>
  {!gaps.length&&<p>暂未记录依据不足的问题。</p>}
  {gaps.map(gap=><article className="pm-card pm-health-report" key={gap.id}><strong>{gap.project_name} · {gap.question}</strong>
   <p className="pm-meta">出现 {gap.occurrences} 次；最近 {shortDateTime(gap.last_seen_at)}</p>
   {cb?.onResolveKnowledgeGap&&<button className="pm-btn" onClick={()=>cb.onResolveKnowledgeGap?.(gap.id)}>标为已补充</button>}
  </article>)}
 </section>;
}

import type { Metric, WeeklyReport, CrossProjectReport, PmCallbacks } from '../types';
import { shortDateTime } from '../time';

export function EvolutionMetricsView({ metrics }: { metrics?: Metric[] }) {
  if (!metrics || !metrics.length) return null;
  return (
    <section className="pm-block" aria-label="系统进化指标">
      <h2>系统进化指标</h2>
      <p className="pm-meta">按周记录待决定数、资料覆盖率、冲突数、知识缺口数、建议采纳率与积压量趋势。</p>
      <div className="pm-two-col" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1rem' }}>
        {metrics.map((m) => {
          const curVal = m.current?.value ?? 0;
          const trendStr = m.trend && m.trend.length
            ? m.trend.map(t => `${t.period}: ${t.value}`).join(' → ')
            : '尚无历史趋势';
          return (
            <article className="pm-card pm-evolution-card" key={m.id} style={{ padding: '1rem', border: '1px solid var(--pm-border, #e2e8f0)', borderRadius: '8px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <strong style={{ fontSize: '1.05rem' }}>{m.name}</strong>
                <span className="pm-count" style={{ fontSize: '1.3rem', fontWeight: 'bold' }}>
                  {curVal} {m.unit || ''}
                </span>
              </div>
              <p className="pm-meta" style={{ marginTop: '0.5rem', marginBottom: '0.25rem' }}>
                目标：{m.target?.value ?? '—'} {m.unit || ''}
              </p>
              <div className="pm-trend" style={{ fontSize: '0.85rem', color: '#64748b' }}>
                <strong>周趋势：</strong>{trendStr}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function WeeklyReportsView({
  crossReport,
  reports,
  cb,
}: {
  crossReport?: CrossProjectReport;
  reports?: WeeklyReport[];
  cb?: PmCallbacks;
}) {
  const allReports = reports || crossReport?.projects || [];
  if (!crossReport && !allReports.length) return null;

  return (
    <section className="pm-block" aria-label="跨项目对比与项目周报">
      <h2>跨项目对比与项目周报</h2>
      {crossReport && (
        <article className="pm-card" style={{ marginBottom: '1.5rem', padding: '1rem' }}>
          <h3>跨项目对比 ({crossReport.period})</h3>
          <p className="pm-meta">跟踪 {crossReport.project_count} 个项目的进展、活动、风险及建议情况。</p>
          <div style={{ overflowX: 'auto', marginTop: '0.75rem' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.9rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #e2e8f0' }}>
                  <th style={{ padding: '8px' }}>项目</th>
                  <th style={{ padding: '8px' }}>状态</th>
                  <th style={{ padding: '8px' }}>进展项</th>
                  <th style={{ padding: '8px' }}>本周变更</th>
                  <th style={{ padding: '8px' }}>风险与冲突</th>
                  <th style={{ padding: '8px' }}>待决事项</th>
                  <th style={{ padding: '8px' }}>知识缺口</th>
                  <th style={{ padding: '8px' }}>AI 建议</th>
                </tr>
              </thead>
              <tbody>
                {crossReport.matrix.map((row) => (
                  <tr key={row.project_id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                    <td style={{ padding: '8px', fontWeight: 'bold' }}>
                      <button
                        className="pm-linkish"
                        onClick={() => cb?.onNavigate?.('/app/pm/projects/' + row.project_id)}
                      >
                        {row.project_name}
                      </button>
                    </td>
                    <td style={{ padding: '8px' }}>{row.status}</td>
                    <td style={{ padding: '8px' }}>{row.progress_count}</td>
                    <td style={{ padding: '8px' }}>{row.change_count}</td>
                    <td style={{ padding: '8px', color: row.risk_count > 0 ? '#b91c1c' : 'inherit' }}>
                      {row.risk_count}
                    </td>
                    <td style={{ padding: '8px' }}>{row.decision_count}</td>
                    <td style={{ padding: '8px' }}>{row.gap_count}</td>
                    <td style={{ padding: '8px' }}>{row.suggestion_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
      )}

      <h3>各项目本周周报</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '0.5rem' }}>
        {allReports.map((rep) => (
          <article className="pm-card pm-weekly-card" key={rep.id} style={{ padding: '1rem', border: '1px solid #e2e8f0' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h4 style={{ margin: 0, fontSize: '1.1rem' }}>
                <button
                  className="pm-linkish"
                  onClick={() => cb?.onNavigate?.('/app/pm/projects/' + rep.project_id)}
                >
                  {rep.project_name}
                </button> · 周报 ({rep.period})
              </h4>
              <span className="pm-meta">生成于 {shortDateTime(rep.generated_at)}</span>
            </div>

            <div style={{ marginTop: '0.75rem' }}>
              <strong>【进展】</strong>
              <ul style={{ margin: '0.25rem 0 0.5rem 1.25rem', padding: 0 }}>
                {rep.progress.map((p, i) => (
                  <li key={i}>
                    {p.text}
                    {p.sources && p.sources.length > 0 && (
                      <span className="pm-meta" style={{ marginLeft: '0.5rem' }}>
                        〔依据：{p.sources.map(s => s.title).join('、')}〕
                      </span>
                    )}
                  </li>
                ))}
              </ul>

              <strong>【变化】</strong>
              {rep.changes && rep.changes.length ? (
                <ul style={{ margin: '0.25rem 0 0.5rem 1.25rem', padding: 0 }}>
                  {rep.changes.map((c) => (
                    <li key={c.id}>
                      {c.title} ({shortDateTime(c.at)})
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="pm-meta" style={{ margin: '0.25rem 0 0.5rem 1rem' }}>本周暂无变更记录。</p>
              )}

              <strong>【风险与冲突】</strong>
              {rep.risks && rep.risks.length ? (
                <ul style={{ margin: '0.25rem 0 0.5rem 1.25rem', padding: 0 }}>
                  {rep.risks.map((r, i) => (
                    <li key={i} style={{ color: r.level === 'high' ? '#b91c1c' : 'inherit' }}>
                      <strong>{r.title}</strong>：{r.detail}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="pm-meta" style={{ margin: '0.25rem 0 0.5rem 1rem' }}>未发现已知风险或未裁决冲突。</p>
              )}

              <strong>【建议】</strong>
              {rep.suggestions.needs_key ? (
                <p className="pm-notice" style={{ margin: '0.25rem 0 0.5rem 1rem' }}>
                  {rep.suggestions.message || '需要填写 Key 才能生成建议。'}
                </p>
              ) : rep.suggestions.items && rep.suggestions.items.length ? (
                <ul style={{ margin: '0.25rem 0 0.5rem 1.25rem', padding: 0 }}>
                  {rep.suggestions.items.map((s) => (
                    <li key={s.id}>
                      {s.text}
                      {s.basis && s.basis.length > 0 && (
                        <span className="pm-meta" style={{ marginLeft: '0.5rem' }}>
                          〔依据：{s.basis.map(b => b.title).join('、')}〕
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="pm-meta" style={{ margin: '0.25rem 0 0.5rem 1rem' }}>暂无针对该项目的 AI 建议。</p>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export function ProjectWeeklyReportView({ report }: { report?: WeeklyReport }) {
  if (!report) return null;
  return (
    <section className="pm-block" aria-label="项目周报">
      <h2>项目周报 ({report.period})</h2>
      <article className="pm-card pm-weekly-card" style={{ padding: '1rem', border: '1px solid #e2e8f0' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="pm-meta">生成于 {shortDateTime(report.generated_at)}</span>
        </div>

        <div style={{ marginTop: '0.75rem' }}>
          <strong>【进展】</strong>
          <ul style={{ margin: '0.25rem 0 0.5rem 1.25rem', padding: 0 }}>
            {report.progress.map((p, i) => (
              <li key={i}>
                {p.text}
                {p.sources && p.sources.length > 0 && (
                  <span className="pm-meta" style={{ marginLeft: '0.5rem' }}>
                    〔依据：{p.sources.map(s => s.title).join('、')}〕
                  </span>
                )}
              </li>
            ))}
          </ul>

          <strong>【变化】</strong>
          {report.changes && report.changes.length ? (
            <ul style={{ margin: '0.25rem 0 0.5rem 1.25rem', padding: 0 }}>
              {report.changes.map((c) => (
                <li key={c.id}>
                  {c.title} ({shortDateTime(c.at)})
                </li>
              ))}
            </ul>
          ) : (
            <p className="pm-meta" style={{ margin: '0.25rem 0 0.5rem 1rem' }}>本周暂无变更记录。</p>
          )}

          <strong>【风险与冲突】</strong>
          {report.risks && report.risks.length ? (
            <ul style={{ margin: '0.25rem 0 0.5rem 1.25rem', padding: 0 }}>
              {report.risks.map((r, i) => (
                <li key={i} style={{ color: r.level === 'high' ? '#b91c1c' : 'inherit' }}>
                  <strong>{r.title}</strong>：{r.detail}
                </li>
              ))}
            </ul>
          ) : (
            <p className="pm-meta" style={{ margin: '0.25rem 0 0.5rem 1rem' }}>未发现已知风险或未裁决冲突。</p>
          )}

          <strong>【建议】</strong>
          {report.suggestions.needs_key ? (
            <p className="pm-notice" style={{ margin: '0.25rem 0 0.5rem 1rem' }}>
              {report.suggestions.message || '需要填写 Key 才能生成建议。'}
            </p>
          ) : report.suggestions.items && report.suggestions.items.length ? (
            <ul style={{ margin: '0.25rem 0 0.5rem 1.25rem', padding: 0 }}>
              {report.suggestions.items.map((s) => (
                <li key={s.id}>
                  {s.text}
                  {s.basis && s.basis.length > 0 && (
                    <span className="pm-meta" style={{ marginLeft: '0.5rem' }}>
                      〔依据：{s.basis.map(b => b.title).join('、')}〕
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="pm-meta" style={{ margin: '0.25rem 0 0.5rem 1rem' }}>暂无针对该项目的 AI 建议。</p>
          )}
        </div>
      </article>
    </section>
  );
}


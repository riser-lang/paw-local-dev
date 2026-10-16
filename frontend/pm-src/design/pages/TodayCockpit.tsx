/* 首页「今日驾驶舱」
   Desktop: status strip top-right → 需要你决定 → 项目总览 (Gantt) → AI 建议 | 去向回执
   Phone:   compact status → 需要你决定 → 项目总览 (vertical timeline) → the rest     */
import { useState } from "react";
import type { PmCallbacks, TimeUnit, TodayViewModel } from "../types";
import { shortDateTime } from "../time";
import { SectionHeading, usePhone } from "../components/common";
import { SystemStatus } from "../components/SystemStatus";
import { DecisionInbox } from "../components/DecisionInbox";
import { PortfolioGantt } from "../components/Gantt";
import { PortfolioTimeline } from "../components/VerticalTimeline";
import { AiSuggestionList, ReceiptList } from "../components/AiSuggestions";
import { EmptyState } from "../components/common";
import { ProjectHealth } from "../components/ProjectHealth";
import { EvolutionMetricsView, WeeklyReportsView } from "../components/EvolutionAndReports";

const WEEKDAY = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function todayHeading(today: string) {
  const [y, m, d] = today.split("-").map(Number);
  return `${m}月${d}日 · ${WEEKDAY[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]}`;
}

export function TodayCockpit({ vm, cb, initialUnit = "week", title = "今日驾驶舱", modelConfigured = false }: {
  vm: TodayViewModel; cb?: PmCallbacks; initialUnit?: TimeUnit; title?: string; modelConfigured?: boolean;
}) {
  const phone = usePhone();
  const [unit, setUnit] = useState<TimeUnit>(initialUnit);
  const projects = vm.portfolio.projects;
  const pending = vm.decisions.length;
  return (
    <div className="pm-root pm-page pm-today-page" data-pm-layout={phone ? 'phone' : 'desktop'}>
      <header className="pm-page-head">
        <div className="pm-page-title">
          <span className="pm-eyebrow">{todayHeading(vm.today)}</span>
          <h1>{title}</h1>
          <p className="pm-lede">
            {pending ? <>有 <strong className="pm-clay">{pending} 件事</strong>需要你决定</> : "今天没有需要你决定的事"}
            {projects.length ? `，${projects.length} 个项目在跟踪。` : "。"}
          </p>
        </div>
        <div><h2 className="pm-system-heading">系统状态</h2><SystemStatus items={vm.system_status} compact={phone} /></div>
      </header>

      <section className="pm-block" aria-labelledby="pm-h-decisions">
        <SectionHeading id="pm-h-decisions" title="需要你决定" extra={pending ? <span className="pm-count">{pending}</span> : null} />
        <DecisionInbox items={vm.decisions} cb={cb} />
      </section>

      <section className="pm-block" aria-labelledby="pm-h-portfolio">
        <SectionHeading id="pm-h-portfolio" title="项目总览" />
        {!projects.length ? (
          <EmptyState seed="portfolio-empty" text="还没有项目。新资料归入后，项目会出现在这里。" />
        ) : phone ? (
          <PortfolioTimeline today={vm.today} rows={projects} cb={cb} />
        ) : (
          <PortfolioGantt today={vm.today} rows={projects} unit={unit} onUnitChange={setUnit} cb={cb} />
        )}
      </section>

      <div className="pm-two-col">
        <section className="pm-block" aria-labelledby="pm-h-ai">
          <SectionHeading id="pm-h-ai" title="AI 建议" extra={<span className="pm-meta">仅供参考，不会自动执行</span>} />
          {!modelConfigured ? <p>需要填写 Key 才能生成建议。</p> : <AiSuggestionList items={vm.ai_suggestions} cb={cb} />}
        </section>
        <section className="pm-block" aria-labelledby="pm-h-receipts">
          <SectionHeading id="pm-h-receipts" title="资料去向" extra={<span className="pm-meta">最近收到的随手记和文件</span>} />
          {vm.receipts.length ? <ReceiptList items={vm.receipts} cb={cb} /> : <EmptyState seed="receipts-empty" text="最近没有新收到的资料。" />}
        </section>
      </div>

      <ProjectHealth checks={vm.health_checks??[]} gaps={vm.knowledge_gaps??[]} cb={cb}/>
      <EvolutionMetricsView metrics={vm.evolution_metrics} />
      <WeeklyReportsView crossReport={vm.cross_project_report} reports={vm.weekly_reports} cb={cb} />
      <footer className="pm-page-foot">数据生成于 {shortDateTime(vm.generated_at)}</footer>
    </div>
  );
}

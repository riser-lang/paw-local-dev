/* PAW 项目管理视图 — 对外入口。
   使用方式见 INTEGRATION_GUIDE.md。样式和字体需要在应用入口各引入一次：
     import "./pm/styles/fonts.css";
     import "./pm/styles/pm.css";                                             */
export * from "./types";
export { TodayCockpit, todayHeading } from "./pages/TodayCockpit";
export { ProjectDossier, DEFAULT_EMPTY } from "./pages/ProjectDossier";
export { PortfolioGantt, MilestoneGantt, UnitToggle, delayDays, milestoneOrder, changeNumbers } from "./components/Gantt";
export { PortfolioTimeline, MilestoneTimeline, VTimeline, ProgressStrip } from "./components/VerticalTimeline";
export { DecisionInbox, DecisionCard, sortDecisions } from "./components/DecisionInbox";
export { SectionCard, EntryList, EntryItem, MetricList, ChangeList, ConflictCompare, ConflictList, SourceList } from "./components/Sections";
export { ReceiptCard, ReceiptList, AiSuggestionList } from "./components/AiSuggestions";
export { SystemStatus } from "./components/SystemStatus";
export { EventIcon, EventLegend, EVENT_META } from "./components/icons";
export { EmptyState, DetailPopover, SourceLink, SourceDetail, ProjectStatusMark, usePhone } from "./components/common";

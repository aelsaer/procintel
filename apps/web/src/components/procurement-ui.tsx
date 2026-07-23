import Link from "next/link";
import type { ComponentType, ReactNode } from "react";
import { AlertTriangle, ArrowLeft, Loader2 } from "lucide-react";

type Tone = "neutral" | "green" | "blue" | "amber" | "red";

export function getErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string") {
    return error.message;
  }
  return "Άγνωστο σφάλμα.";
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function IconLabel({
  icon: Icon,
  children,
}: {
  icon: ComponentType<{ size?: number; strokeWidth?: number }>;
  children: ReactNode;
}) {
  return (
    <span className="icon-label">
      <Icon size={16} strokeWidth={2} />
      {children}
    </span>
  );
}

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
  children,
}: {
  eyebrow?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-heading">
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
      {children}
    </header>
  );
}

export function Section({
  title,
  eyebrow,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  eyebrow?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`content-section${className ? ` ${className}` : ""}`}>
      <div className="section-heading">
        <div>
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h2>{title}</h2>
        </div>
        {actions && <div className="section-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  icon?: ComponentType<{ size?: number; strokeWidth?: number }>;
  tone?: Tone;
}) {
  return (
    <div className={`metric-card metric-${tone}`}>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        {detail && <small>{detail}</small>}
      </div>
      {Icon && (
        <div className="metric-icon" aria-hidden="true">
          <Icon size={20} strokeWidth={2} />
        </div>
      )}
    </div>
  );
}

export function KeyValueList({ children }: { children: ReactNode }) {
  return <dl className="key-value-list">{children}</dl>;
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="key-value-row">
      <dt>{label}</dt>
      <dd>{value ?? "—"}</dd>
    </div>
  );
}

export function LoadingState({ label = "Ανάγνωση δεδομένων" }: { label?: string }) {
  return (
    <div className="state-panel state-loading">
      <Loader2 size={20} className="spin" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({
  error,
  title = "Δεν είναι διαθέσιμα τα δεδομένα",
  action,
}: {
  error: unknown;
  title?: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-panel state-error">
      <AlertTriangle size={22} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <span>{getErrorMessage(error)}</span>
        {action}
      </div>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: ReactNode }) {
  return (
    <div className="state-panel state-empty">
      <strong>{title}</strong>
      {detail && <span>{detail}</span>}
    </div>
  );
}

export function BackLink({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="button button-ghost">
      <ArrowLeft size={16} aria-hidden="true" />
      Πίσω
    </Link>
  );
}

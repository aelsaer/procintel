"use client";

import { useState } from "react";
import { useCustom } from "@refinedev/core";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileCheck2,
  FileWarning,
  Loader2,
  ShieldCheck,
  Target,
  UsersRound,
  X,
} from "lucide-react";
import {
  downloadApiFile,
  type BidReportResponse,
} from "@/lib/api";
import { Badge, ErrorState, LoadingState, getErrorMessage } from "@/components/procurement-ui";
import { formatAmount, formatDate } from "@/lib/format";

function text(record: Record<string, unknown>, key: string, fallback = "—") {
  const value = record[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
}

function recommendationTone(value: BidReportResponse["recommendation"]) {
  if (value === "BID") return "green" as const;
  if (value === "NO_BID") return "red" as const;
  return "amber" as const;
}

export function BidReportPanel({
  processId,
  onClose,
}: {
  processId: string;
  onClose: () => void;
}) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const report = useCustom<BidReportResponse>({
    url: `/v1/bid-reports/${processId}`,
    method: "get",
    queryOptions: { retry: 1 },
  });
  const data = report.query.isSuccess ? report.result.data : null;

  async function download() {
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadApiFile(`/v1/bid-reports/${processId}/pdf`, `procintel-bid-report-${processId}.pdf`);
    } catch (error) {
      setDownloadError(getErrorMessage(error));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="bid-report-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <section className="bid-report-drawer" role="dialog" aria-modal="true" aria-labelledby="bid-report-title">
        <header className="bid-report-header">
          <div>
            <span className="eyebrow">Committee report</span>
            <h2 id="bid-report-title">BID / NO-BID</h2>
          </div>
          <div>
            <button className="button button-secondary" type="button" onClick={() => void download()} disabled={!data || downloading}>
              {downloading ? <Loader2 className="spin" size={15} /> : <Download size={15} />}
              PDF
            </button>
            <button className="icon-button" type="button" onClick={onClose} aria-label="Κλείσιμο report"><X size={18} /></button>
          </div>
        </header>

        <div className="bid-report-body">
          {report.query.isLoading && <LoadingState label="Σύνθεση τεκμηριωμένης απόφασης" />}
          {report.query.isError && <ErrorState title="Δεν δημιουργήθηκε το BID / NO-BID report" error={report.query.error} />}
          {downloadError && <div className="form-error" role="alert">{downloadError}</div>}
          {data && (
            <>
              <section className={`bid-report-verdict verdict-${data.recommendation.toLocaleLowerCase()}`}>
                <div className="bid-report-verdict-score">
                  <strong>{Math.round(data.confidence)}</strong>
                  <span>confidence</span>
                </div>
                <div>
                  <Badge tone={recommendationTone(data.recommendation)}>{data.recommendation.replace("_", " ")}</Badge>
                  <h3>{data.title}</h3>
                  <p>{data.buyer_name ?? "Μη ταυτοποιημένος φορέας"} · {formatAmount(data.budget === null ? null : Number(data.budget), data.currency)} · {formatDate(data.deadline)}</p>
                </div>
              </section>

              <section className="bid-report-rationale">
                {data.recommendation_reasons.map((reason) => <p key={reason}><ShieldCheck size={15} />{reason}</p>)}
              </section>

              <section className="bid-report-fit" aria-label="Παράγοντες καταλληλότητας">
                {Object.entries(data.fit).map(([key, value]) => (
                  <div key={key}>
                    <span>{key}</span>
                    <strong>{Math.round(value)}</strong>
                    <i><b style={{ width: `${Math.max(2, value)}%` }} /></i>
                  </div>
                ))}
              </section>

              <div className="bid-report-columns">
                <section>
                  <header><AlertTriangle size={16} /><h3>Κίνδυνοι και blockers</h3><Badge tone={data.risks.length ? "amber" : "green"}>{data.risks.length}</Badge></header>
                  <div className="bid-report-list">
                    {data.risks.map((risk, index) => (
                      <div key={`${text(risk, "code")}-${index}`}>
                        <FileWarning size={14} />
                        <span><strong>{text(risk, "label")}</strong><small>{text(risk, "code")}</small></span>
                      </div>
                    ))}
                    {!data.risks.length && <p className="bid-report-clear"><CheckCircle2 size={15} />Δεν εντοπίστηκαν blockers.</p>}
                  </div>
                </section>
                <section>
                  <header><FileCheck2 size={16} /><h3>Υποχρεωτικές απαιτήσεις</h3><Badge>{data.mandatory_requirements.length}</Badge></header>
                  <div className="bid-report-list">
                    {data.mandatory_requirements.slice(0, 12).map((requirement, index) => (
                      <div key={`${text(requirement, "id")}-${index}`}>
                        <span className={`requirement-dot status-${text(requirement, "status").toLocaleLowerCase()}`} />
                        <span><strong>{text(requirement, "title")}</strong><small>{text(requirement, "requirement_type")} · {text(requirement, "status")}</small></span>
                        {requirement.evidence_page ? <Badge>p. {String(requirement.evidence_page)}</Badge> : null}
                      </div>
                    ))}
                  </div>
                </section>
              </div>

              <div className="bid-report-columns">
                <section>
                  <header><FileWarning size={16} /><h3>Πιστοποιητικά που λείπουν</h3><Badge tone={data.missing_certificates.length ? "red" : "green"}>{data.missing_certificates.length}</Badge></header>
                  <div className="bid-report-list">
                    {data.missing_certificates.map((certificate, index) => (
                      <div key={`${text(certificate, "id")}-${index}`}><FileWarning size={14} /><span><strong>{text(certificate, "title")}</strong><small>{text(certificate, "status")}</small></span></div>
                    ))}
                    {!data.missing_certificates.length && <p className="bid-report-clear"><CheckCircle2 size={15} />Δεν λείπει ταυτοποιημένο πιστοποιητικό.</p>}
                  </div>
                </section>
                <section>
                  <header><UsersRound size={16} /><h3>Incumbent και ανταγωνιστές</h3><Badge>{data.competitors.length}</Badge></header>
                  <div className="bid-report-list">
                    {data.competitors.map((competitor, index) => (
                      <div key={`${text(competitor, "name")}-${index}`}><UsersRound size={14} /><span><strong>{text(competitor, "name")}</strong><small>{text(competitor, "role")} · {Math.round(Number(competitor.confidence ?? 0) * 100)}%</small></span></div>
                    ))}
                  </div>
                </section>
              </div>

              <section className="bid-report-actions">
                <header><Target size={16} /><h3>Επόμενες ενέργειες</h3></header>
                <ol>
                  {data.next_actions.map((action, index) => (
                    <li key={`${action.type}-${index}`}><span>{index + 1}</span><strong>{action.label}</strong><Badge tone={action.priority === "URGENT" || action.priority === "HIGH" ? "amber" : "neutral"}>{action.priority}</Badge></li>
                  ))}
                </ol>
              </section>

              <section className="bid-report-evidence">
                <header><ShieldCheck size={16} /><h3>Επίσημα τεκμήρια</h3><Badge tone="blue">{data.evidence.length}</Badge></header>
                <div>
                  {data.evidence.map((evidence, index) => (
                    evidence.url ? (
                      <a key={`${text(evidence, "document_id")}-${index}`} href={String(evidence.url)} target="_blank" rel="noreferrer">
                        <FileCheck2 size={14} /><span><strong>{text(evidence, "label")}</strong><small>{text(evidence, "source_system")}</small></span>
                      </a>
                    ) : (
                      <span key={`${text(evidence, "document_id")}-${index}`}><FileCheck2 size={14} />{text(evidence, "label")}</span>
                    )
                  ))}
                </div>
              </section>

              <footer className="bid-report-limitations">
                {data.limitations.map((limitation) => <p key={limitation}>{limitation}</p>)}
                <time>Δημιουργήθηκε {new Intl.DateTimeFormat("el-GR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(data.generated_at))}</time>
              </footer>
            </>
          )}
        </div>
      </section>
    </div>
  );
}

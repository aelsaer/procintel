import { Download, ExternalLink, FileCheck2, FileText, Sparkles } from "lucide-react";
import type { OfficialRecord, TenderDocument, TenderSummary } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { Badge, EmptyState, Section } from "@/components/procurement-ui";

export function TenderSummarySection({ summary }: { summary: TenderSummary }) {
  return (
    <Section title="Σύνοψη προκήρυξης" eyebrow="Τεκμηριωμένη εικόνα" className="tender-summary-section">
      <div className="tender-summary-lead">
        <Sparkles size={19} aria-hidden="true" />
        <p>{summary.text}</p>
      </div>
      {summary.key_points.length > 0 && (
        <dl className="tender-key-points">
          {summary.key_points.map((point) => (
            <div key={`${point.label}-${point.value}`}>
              <dt>{point.label}</dt>
              <dd>{point.value}</dd>
              <small>{point.source}</small>
            </div>
          ))}
        </dl>
      )}
      {summary.document_excerpt && (
        <div className="tender-document-excerpt">
          <FileText size={17} aria-hidden="true" />
          <div>
            <strong>Απόσπασμα εγγράφου</strong>
            <p>{summary.document_excerpt}</p>
          </div>
        </div>
      )}
      <p className="tender-summary-method">Σύνοψη από τα δημοσιευμένα δομημένα πεδία και το ανακτημένο έγγραφο.</p>
    </Section>
  );
}

export function PublicationSources({
  records,
  documents,
}: {
  records: OfficialRecord[];
  documents: TenderDocument[];
}) {
  if (records.length === 0 && documents.length === 0) {
    return <EmptyState title="Δεν υπάρχουν συνδεδεμένες δημοσιεύσεις ή έγγραφα" />;
  }

  return (
    <div className="publication-sources">
      {records.length > 0 && (
        <div className="publication-source-group">
          <h3>Επίσημες δημοσιεύσεις</h3>
          <div className="publication-record-list">
            {records.map((record) => (
              <article className="publication-record" key={`${record.act_id}-${record.identifier ?? record.source_system}`}>
                <FileCheck2 size={18} aria-hidden="true" />
                <div>
                  <span className="badge-row">
                    <Badge tone="blue">{record.source_system}</Badge>
                    <Badge>{record.act_type}</Badge>
                  </span>
                  <strong>{record.title ?? record.identifier ?? "Επίσημη εγγραφή"}</strong>
                  <small>{[record.identifier, formatDate(record.event_date)].filter(Boolean).join(" · ")}</small>
                </div>
                <div className="publication-record-actions">
                  {record.official_url && (
                    <a className="button button-secondary" href={record.official_url} target="_blank" rel="noreferrer">
                      <ExternalLink size={15} aria-hidden="true" />
                      Επίσημη σελίδα
                    </a>
                  )}
                  {record.document_url && (
                    <a className="button button-ghost" href={record.document_url} target="_blank" rel="noreferrer">
                      <Download size={15} aria-hidden="true" />
                      Έγγραφο
                    </a>
                  )}
                </div>
              </article>
            ))}
          </div>
        </div>
      )}

      {documents.length > 0 && (
        <div className="publication-source-group">
          <h3>Ανακτημένα αρχεία</h3>
          <div className="publication-document-list">
            {documents.map((document) => (
              <article className="publication-document" key={document.document_id}>
                <FileText size={17} aria-hidden="true" />
                <div>
                  <strong>{document.title ?? document.document_type}</strong>
                  <small>
                    {[
                      document.mime_type,
                      document.page_count ? `${document.page_count} σελίδες` : null,
                      document.text_extraction_status,
                    ].filter(Boolean).join(" · ")}
                  </small>
                </div>
                {document.source_url && (
                  <a
                    className="icon-button"
                    href={document.source_url}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`Άνοιγμα ${document.title ?? document.document_type}`}
                    title="Άνοιγμα αρχείου"
                  >
                    <ExternalLink size={17} aria-hidden="true" />
                  </a>
                )}
              </article>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

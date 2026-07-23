"use client";

import { use } from "react";
import { useOne } from "@refinedev/core";
import Link from "next/link";
import { Building2, Download, ExternalLink, FileText, Landmark, Network, ReceiptText } from "lucide-react";
import {
  BackLink,
  Badge,
  EmptyState,
  ErrorState,
  KeyValue,
  KeyValueList,
  LoadingState,
  MetricCard,
  PageHeader,
  Section,
} from "@/components/procurement-ui";
import type { ContractResponse } from "@/lib/api";
import { formatAmount } from "@/lib/format";
import { PublicationSources, TenderSummarySection } from "@/components/tender-publication";

export default function ContractPage({ params }: { params: Promise<{ adam: string }> }) {
  const { adam: encodedIdentifier } = use(params);
  let identifier = encodedIdentifier;
  try {
    identifier = decodeURIComponent(encodedIdentifier);
  } catch {
    // Keep the original route value when it is not valid percent-encoding.
  }
  const contractQuery = useOne<ContractResponse>({
    resource: "contracts",
    id: identifier,
    queryOptions: {
      retry: 1,
    },
  });

  if (contractQuery.query.isLoading) return <LoadingState label="Φόρτωση πράξης" />;
  if (contractQuery.query.isError) return <ErrorState error={contractQuery.query.error} />;

  const contract = contractQuery.result;
  if (!contract) return <EmptyState title="Δεν βρέθηκε πράξη" />;

  const identifierEntries = Object.entries(contract.identifiers);
  const primaryPublication = contract.official_records.find((record) => record.official_url || record.document_url);

  return (
    <div className="detail-layout">
      <PageHeader
        eyebrow="Πράξη"
        title={contract.title ?? "(χωρίς τίτλο)"}
        subtitle={identifier}
        actions={
          <>
            <BackLink />
            {primaryPublication?.official_url && (
              <a className="button button-secondary" href={primaryPublication.official_url} target="_blank" rel="noreferrer">
                <ExternalLink size={16} aria-hidden="true" />
                Επίσημη σελίδα
              </a>
            )}
            {primaryPublication?.document_url && (
              <a className="button button-ghost" href={primaryPublication.document_url} target="_blank" rel="noreferrer">
                <Download size={16} aria-hidden="true" />
                Έγγραφο
              </a>
            )}
            {contract.process_id && (
              <Link href={`/processes/${contract.process_id}`} className="button button-primary">
                <Network size={16} aria-hidden="true" />
                Διαδικασία 360
              </Link>
            )}
          </>
        }
      >
        <div className="badge-row">
          <Badge tone="blue">{contract.act_type}</Badge>
          {contract.status && <Badge tone="green">{contract.status}</Badge>}
          {contract.procedure_type && <Badge tone="amber">{contract.procedure_type}</Badge>}
        </div>
      </PageHeader>

      <div className="metric-grid">
        <MetricCard label="Καθαρό" value={formatAmount(contract.amounts.net, contract.amounts.currency)} icon={ReceiptText} />
        <MetricCard label="ΦΠΑ" value={formatAmount(contract.amounts.vat, contract.amounts.currency)} icon={FileText} />
        <MetricCard
          label="Μικτό"
          value={formatAmount(contract.amounts.gross, contract.amounts.currency)}
          icon={Landmark}
          tone="green"
        />
      </div>

      <TenderSummarySection summary={contract.summary} />

      <div className="two-column">
        <Section title="Αναθέτουσα αρχή">
          {contract.buyer ? (
            <KeyValueList>
              <KeyValue
                label="Όνομα"
                value={
                  <Link href={`/buyers/${contract.buyer.id}`} className="text-link">
                    {contract.buyer.name}
                  </Link>
                }
              />
              <KeyValue label="ΑΦΜ" value={contract.buyer.vat} />
            </KeyValueList>
          ) : (
            <EmptyState title="Δεν υπάρχει αναθέτουσα αρχή" />
          )}
        </Section>

        <Section title="Ανάδοχοι">
          {contract.suppliers.length > 0 ? (
            <div className="compact-list">
              {contract.suppliers.map((supplier) => (
                <Link key={supplier.id} href={`/companies/${supplier.id}`} className="compact-row">
                  <Building2 size={16} aria-hidden="true" />
                  <span>{supplier.name}</span>
                  <strong>{formatAmount(supplier.amount, contract.amounts.currency)}</strong>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState title="Δεν υπάρχουν ανάδοχοι" />
          )}
        </Section>
      </div>

      <div className="two-column">
        <Section title="Αναγνωριστικά">
          {identifierEntries.length > 0 ? (
            <KeyValueList>
              {identifierEntries.map(([scheme, values]) => (
                <KeyValue key={scheme} label={scheme} value={values.join(", ")} />
              ))}
            </KeyValueList>
          ) : (
            <EmptyState title="Δεν υπάρχουν αναγνωριστικά" />
          )}
        </Section>

        <Section title="Προέλευση">
          {contract.provenance.length > 0 ? (
            <KeyValueList>
              {contract.provenance.map((entry, index) => (
                <KeyValue
                  key={`${entry.source}-${entry.source_native_id ?? index}`}
                  label={entry.source}
                  value={entry.source_native_id ?? entry.retrieved_at ?? "—"}
                />
              ))}
            </KeyValueList>
          ) : (
            <EmptyState title="Δεν υπάρχει προέλευση" />
          )}
        </Section>
      </div>

      <Section title="Επίσημες πηγές και αρχεία">
        <PublicationSources records={contract.official_records} documents={contract.documents} />
      </Section>
    </div>
  );
}

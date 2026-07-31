"use client";

import { useCustom } from "@refinedev/core";
import Link from "next/link";
import { ArrowLeft, CheckCircle2, CircleAlert } from "lucide-react";

import { ErrorState, LoadingState } from "@/components/procurement-ui";
import type { PublicStatusResponse } from "@/lib/api";

export default function StatusPage() {
  const query = useCustom<PublicStatusResponse>({ url: "/v1/status", method: "get", queryOptions: { retry: 1, refetchInterval: 60_000 } });
  const status = query.query.isSuccess ? query.result.data : null;
  return (
    <main className="public-product-page status-page">
      <header className="public-product-header"><Link href="/" className="button button-secondary"><ArrowLeft size={15} />Procintel</Link><small>Updated automatically every minute</small></header>
      {query.query.isLoading ? <LoadingState label="Έλεγχος υπηρεσιών" /> : null}
      {query.query.isError ? <ErrorState error={query.query.error} /> : null}
      {status ? <><section className={`status-overview is-${status.status.toLowerCase()}`}>{status.status === "OPERATIONAL" ? <CheckCircle2 size={30} /> : <CircleAlert size={30} />}<div><span>SERVICE STATUS</span><h1>{status.status === "OPERATIONAL" ? "Όλα τα συστήματα λειτουργούν" : "Υπάρχει υποβάθμιση υπηρεσίας"}</h1><small>{new Intl.DateTimeFormat("el-GR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(status.generated_at))}</small></div></section><section className="status-components">{status.components.map((component) => <div key={component.name}><span>{component.name}</span><strong className={component.status === "OPERATIONAL" ? "status-good" : "status-warn"}>{component.status}</strong></div>)}</section><section className="status-incidents"><h2>Ενεργά περιστατικά</h2>{status.incidents.length ? status.incidents.map((incident, index) => <article key={String(incident.id ?? index)}><strong>{String(incident.title ?? "Service incident")}</strong><p>{String(incident.public_message ?? "")}</p></article>) : <p>Δεν υπάρχουν ενεργά περιστατικά.</p>}</section></> : null}
    </main>
  );
}

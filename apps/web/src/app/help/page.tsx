"use client";

import { useCustom } from "@refinedev/core";
import Link from "next/link";
import { ArrowLeft, BookOpenCheck, Headphones } from "lucide-react";

import { ErrorState, LoadingState } from "@/components/procurement-ui";

type Article = { slug: string; title: string; category: string; steps: string[] };

export default function HelpPage() {
  const query = useCustom<Article[]>({ url: "/v1/help/articles", method: "get", queryOptions: { retry: 1 } });
  const articles = query.query.isSuccess ? query.result.data : [];
  return (
    <main className="public-product-page">
      <header className="public-product-header"><Link href="/" className="button button-secondary"><ArrowLeft size={15} />Workspace</Link><Link href="/status">Service status</Link></header>
      <section className="help-heading"><BookOpenCheck size={26} /><span>HELP CENTRE</span><h1>Σύντομες, πρακτικές ροές εργασίας</h1></section>
      {query.query.isLoading ? <LoadingState label="Φόρτωση help centre" /> : null}
      {query.query.isError ? <ErrorState error={query.query.error} /> : null}
      <section className="help-grid">
        {articles.map((article) => <article key={article.slug}><span>{article.category}</span><h2>{article.title}</h2><ol>{article.steps.map((step) => <li key={step}>{step}</li>)}</ol></article>)}
      </section>
      <section className="help-support"><Headphones size={20} /><div><strong>Χρειάζεστε ανθρώπινη βοήθεια;</strong><p>Ανοίξτε SLA-tracked ticket από Ρυθμίσεις → Πλάνο και υποστήριξη.</p></div><Link className="button button-primary" href="/settings">Support</Link></section>
    </main>
  );
}

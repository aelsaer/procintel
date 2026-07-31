"use client";

import { useState } from "react";
import { Archive, FileOutput, Loader2 } from "lucide-react";

import { downloadApiFile, type TenderDocument } from "@/lib/api";

export function ProcessDocumentTools({
  processId,
  documents,
}: {
  processId: string;
  documents: TenderDocument[];
}) {
  const [selectedId, setSelectedId] = useState(documents[0]?.document_id ?? "");
  const [busy, setBusy] = useState<"bulk" | "word" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(kind: "bulk" | "word", operation: () => Promise<void>) {
    setBusy(kind);
    setError(null);
    try { await operation(); } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Η μετατροπή απέτυχε.");
    } finally { setBusy(null); }
  }

  if (!documents.length) return null;
  return (
    <div className="process-document-tools">
      <div><Archive size={17} /><span><strong>Document tools</strong><small>Τα bundles περιλαμβάνουν manifest με αποτυχίες και επίσημα URLs.</small></span></div>
      <button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => void run("bulk", () => downloadApiFile(`/v1/document-tools/process/${processId}/bulk.zip`, `documents-${processId}.zip`))}>{busy === "bulk" ? <Loader2 className="spin" size={15} /> : <Archive size={15} />}Bulk ZIP</button>
      <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} aria-label="Έγγραφο για μετατροπή σε Word">{documents.map((document) => <option value={document.document_id} key={document.document_id}>{document.title ?? document.document_type}</option>)}</select>
      <button className="button button-secondary" type="button" disabled={busy !== null || !selectedId} onClick={() => void run("word", () => downloadApiFile(`/v1/document-tools/documents/${selectedId}/convert.docx`, "document.docx"))}>{busy === "word" ? <Loader2 className="spin" size={15} /> : <FileOutput size={15} />}Word</button>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </div>
  );
}

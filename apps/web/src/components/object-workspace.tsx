"use client";

import { FormEvent, useEffect, useState } from "react";
import { MessageSquare, Plus, Tag, Trash2, X } from "lucide-react";
import { api, type NoteResponse, type TagResponse } from "@/lib/api";
import { Badge, EmptyState } from "@/components/procurement-ui";

export function ObjectWorkspace({ objectType, objectId }: { objectType: string; objectId: string }) {
  const [notes, setNotes] = useState<NoteResponse[]>([]);
  const [tags, setTags] = useState<TagResponse[]>([]);
  const [allTags, setAllTags] = useState<TagResponse[]>([]);
  const [note, setNote] = useState("");
  const [tagName, setTagName] = useState("");

  async function refresh() {
    const [nextNotes, nextTags, availableTags] = await Promise.all([api.getNotes(objectType, objectId), api.getObjectTags(objectType, objectId), api.getTags()]);
    setNotes(nextNotes); setTags(nextTags); setAllTags(availableTags);
  }
  useEffect(() => {
    let active = true;
    void Promise.all([api.getNotes(objectType, objectId), api.getObjectTags(objectType, objectId), api.getTags()]).then(([nextNotes, nextTags, availableTags]) => {
      if (!active) return;
      setNotes(nextNotes); setTags(nextTags); setAllTags(availableTags);
    });
    return () => { active = false; };
  }, [objectId, objectType]);

  async function addNote(event: FormEvent) {
    event.preventDefault();
    if (!note.trim()) return;
    await api.createNote(objectType, objectId, note.trim());
    setNote(""); await refresh();
  }

  async function addTag(event: FormEvent) {
    event.preventDefault();
    if (!tagName.trim()) return;
    const tag = allTags.find((candidate) => candidate.name.toLocaleLowerCase("el-GR") === tagName.trim().toLocaleLowerCase("el-GR")) ?? await api.createTag(tagName.trim());
    await api.linkTag(tag.id, objectType, objectId);
    setTagName(""); await refresh();
  }

  return <div className="object-workspace">
    <section aria-labelledby="object-notes-title"><div className="panel-heading"><div><span className="eyebrow">Private workspace</span><h2 id="object-notes-title">Σημειώσεις</h2></div><MessageSquare size={17} /></div><form className="note-composer" onSubmit={addNote}><textarea aria-label="Νέα σημείωση" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Επόμενο βήμα, ερώτηση προς τον φορέα ή bid/no-bid rationale" /><button className="button button-primary" type="submit"><Plus size={15} />Προσθήκη</button></form><div className="notes-list">{notes.map((item) => <article key={item.id}><p>{item.body}</p><small>{new Intl.DateTimeFormat("el-GR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.updated_at))}</small><button className="icon-button" type="button" onClick={() => void api.deleteNote(item.id).then(refresh)} aria-label="Διαγραφή σημείωσης"><Trash2 size={14} /></button></article>)}{!notes.length ? <EmptyState title="Δεν υπάρχουν σημειώσεις" /> : null}</div></section>
    <aside aria-labelledby="object-tags-title"><div className="panel-heading"><div><span className="eyebrow">Taxonomy</span><h2 id="object-tags-title">Tags</h2></div><Tag size={17} /></div><div className="object-tags">{tags.map((tag) => <Badge key={tag.id}>{tag.name}<button type="button" onClick={() => void api.unlinkTag(tag.id, objectType, objectId).then(refresh)} aria-label={`Αφαίρεση ${tag.name}`}><X size={12} /></button></Badge>)}</div><form className="tag-composer" onSubmit={addTag}><input aria-label="Νέο tag" list="workspace-tags" value={tagName} onChange={(event) => setTagName(event.target.value)} placeholder="π.χ. στρατηγικό" /><datalist id="workspace-tags">{allTags.map((tag) => <option key={tag.id} value={tag.name} />)}</datalist><button className="icon-button" type="submit" aria-label="Προσθήκη tag"><Plus size={15} /></button></form></aside>
  </div>;
}
